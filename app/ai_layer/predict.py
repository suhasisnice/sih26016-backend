"""Forecasting: when a case will finish, and how likely it is to slip.

The five rules in app/ai_layer/rules answer "what is wrong right now". This
module answers "what is about to go wrong", which is the part the problem
statement calls predictive analytics and asks for to support policy
formulation.

**What this is.** An empirical survival model built from the system's own
completed stage transitions. For each stage we take the observed
distribution of how long it actually took, and use the median to project
forward and the 75th percentile to bound the pessimistic case. A case's
delay risk is then a function of three observable things: how far into its
allowance it already is, how much history says the remaining stages cost,
and how much trouble the case is already carrying (open objections, missing
documents, unpaid awards).

**What this is not.** It is not a neural network and does not pretend to be.
A learned model over a few hundred transitions would be a worse estimator
than the median and impossible to explain to the officer whose case it just
flagged — and on a system that makes decisions about people's land, "the
model said so" is not an acceptable answer. Every number here can be traced
back to the transitions it came from, which is why `evidence` is part of the
return value rather than an afterthought.

**Cold start.** With no history for a stage the module falls back to the SLA
target and says so, via `confidence`. Reporting a confident forecast built
on four data points would be worse than reporting an honest weak one.
"""

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from sqlalchemy.orm import Session

from app.core.enums import CompensationStatus, ObjectionStatus, RiskBand, Stage
from app.models import (
    Case,
    CaseStageHistory,
    Compensation,
    Document,
    Objection,
    RequiredDocument,
)
from app.services import sla

STAGE_ORDER: list[Stage] = list(Stage)

# Below this many observed transitions we do not trust the empirical median
# and blend it with the SLA target instead. Twelve is a judgement call, not
# a derived threshold: it is roughly where the median of a right-skewed
# duration sample stops swinging wildly on one more observation.
MIN_OBSERVATIONS = 12

# How much each signal contributes to the risk score. Weights are stated
# here, in one place, so the score can be argued with rather than reverse
# engineered from behaviour.
WEIGHTS = {
    "elapsed_fraction": 0.35,   # how far through its allowance the stage is
    "history_pressure": 0.20,   # this stage historically overruns
    "open_objections": 0.20,    # unanswered objections stop everything
    "missing_documents": 0.10,  # paperwork gaps block the next transition
    "unpaid_awards": 0.15,      # money not moving stalls possession
}

RISK_BANDS = (
    (0.25, RiskBand.LOW),
    (0.50, RiskBand.MODERATE),
    (0.75, RiskBand.ELEVATED),
)


def observed_stage_durations(db: Session, case_ids: list[int] | None = None) -> dict[Stage, list[int]]:
    """How long each stage actually took, from completed transitions.

    Read from case_stage_history, which is append-only and is the legal
    timeline of every case — so this learns from what the office genuinely
    did, not from what the SLA table wishes it did.

    Only forward transitions count. A case sent back a stage and then
    re-advanced would otherwise contribute a spuriously short duration for
    the stage it repeated.
    """
    rows = (
        db.query(
            CaseStageHistory.case_id,
            CaseStageHistory.from_stage,
            CaseStageHistory.to_stage,
            CaseStageHistory.changed_on,
        )
        .order_by(CaseStageHistory.case_id, CaseStageHistory.changed_on, CaseStageHistory.id)
        .all()
    )
    if case_ids is not None:
        wanted = set(case_ids)
        rows = [r for r in rows if r.case_id in wanted]

    durations: dict[Stage, list[int]] = defaultdict(list)
    previous_by_case: dict[int, tuple[Stage, date]] = {}

    for row in rows:
        prior = previous_by_case.get(row.case_id)
        if prior is not None:
            prior_stage, prior_date = prior
            # Forward only, and never negative: history rows are dated by
            # the office, and a backdated entry must not produce a negative
            # duration that drags the median below zero.
            if STAGE_ORDER.index(row.to_stage) > STAGE_ORDER.index(prior_stage):
                days = (row.changed_on - prior_date).days
                if days >= 0:
                    durations[prior_stage].append(days)
        previous_by_case[row.case_id] = (row.to_stage, row.changed_on)

    return dict(durations)


def _percentile(values: list[int], fraction: float) -> float:
    """Nearest-rank percentile. Written out rather than pulled from numpy:
    this is the only statistic beyond a median that the module needs, and it
    is not worth a dependency the container would have to carry."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return float(ordered[index])


def stage_model(db: Session) -> dict[Stage, dict]:
    """Expected and pessimistic duration for every stage.

    Blends the observed median with the SLA target when observations are
    thin, weighted by how much history there is. That keeps a brand-new
    deployment usable — it simply predicts the target — and lets the
    forecast move toward reality as the office accumulates a record.
    """
    observed = observed_stage_durations(db)
    targets = sla.load_sla(db)

    model: dict[Stage, dict] = {}
    for stage in STAGE_ORDER:
        samples = observed.get(stage, [])
        target = float((targets.get(stage) or sla.DEFAULT_SLA[stage])["standard_days"])

        if not samples:
            model[stage] = {
                "expected_days": target,
                "pessimistic_days": target * 1.5,
                "observations": 0,
                "confidence": "target_only",
                "overrun_ratio": 1.0,
            }
            continue

        observed_median = float(median(samples))
        p75 = _percentile(samples, 0.75)

        if len(samples) < MIN_OBSERVATIONS:
            # Linear blend toward the observed median as evidence accrues.
            weight = len(samples) / MIN_OBSERVATIONS
            expected = observed_median * weight + target * (1 - weight)
            confidence = "blended"
        else:
            expected = observed_median
            confidence = "empirical"

        model[stage] = {
            "expected_days": round(expected, 1),
            "pessimistic_days": round(max(p75, expected), 1),
            "observations": len(samples),
            "confidence": confidence,
            # How much this stage historically overruns its target. Above
            # 1.0 means the office routinely takes longer than planned here,
            # which is a policy signal in its own right.
            "overrun_ratio": round(observed_median / target, 2) if target else 1.0,
        }
    return model


def _case_pressure(db: Session, case_ids: list[int]) -> dict[int, dict]:
    """Per-case counts of the things that visibly block progress.

    Three grouped queries for the whole set rather than three per case —
    this runs over an entire district's caseload on a dashboard request.
    """
    if not case_ids:
        return {}

    pressure: dict[int, dict] = {
        cid: {"open_objections": 0, "missing_documents": 0, "unpaid_awards": 0}
        for cid in case_ids
    }

    open_objections = (
        db.query(Objection.case_id)
        .filter(
            Objection.case_id.in_(case_ids),
            Objection.status.in_((ObjectionStatus.FILED, ObjectionStatus.UNDER_REVIEW)),
        )
        .all()
    )
    for (case_id,) in open_objections:
        pressure[case_id]["open_objections"] += 1

    unpaid = (
        db.query(Compensation.case_id)
        .filter(
            Compensation.case_id.in_(case_ids),
            Compensation.status.in_(
                (CompensationStatus.AWARDED, CompensationStatus.ASSESSED)
            ),
            Compensation.amount_paid < Compensation.amount_awarded,
        )
        .all()
    )
    for (case_id,) in unpaid:
        pressure[case_id]["unpaid_awards"] += 1

    # Missing documents needs the case's stage, so it is computed against the
    # required_documents lookup the same way the alert rule does.
    stages = dict(db.query(Case.id, Case.stage).filter(Case.id.in_(case_ids)).all())
    required_by_stage: dict[Stage, set] = defaultdict(set)
    for stage, doc_type in db.query(RequiredDocument.stage, RequiredDocument.doc_type).all():
        required_by_stage[stage].add(doc_type)

    present: dict[int, set] = defaultdict(set)
    for case_id, doc_type in (
        db.query(Document.case_id, Document.doc_type)
        .filter(Document.case_id.in_(case_ids), Document.is_current.is_(True))
        .all()
    ):
        present[case_id].add(doc_type)

    for case_id, stage in stages.items():
        missing = required_by_stage.get(stage, set()) - present.get(case_id, set())
        pressure[case_id]["missing_documents"] = len(missing)

    return pressure


def forecast_case(
    case: Case,
    model: dict[Stage, dict],
    pressure: dict,
    targets: dict[Stage, dict],
    as_of: date,
) -> dict:
    """Projected completion date and delay risk for one case.

    Every component of the score is returned in `evidence`. An officer whose
    case has just been flagged is entitled to see which signal did it, and a
    score with no explanation is not usable in an administrative process
    where decisions have to be justified.
    """
    current_index = STAGE_ORDER.index(case.stage)
    remaining_stages = STAGE_ORDER[current_index:]

    days_in_stage = max(0, (as_of - case.stage_changed_at).days)
    current_entry = model[case.stage]
    target_entry = targets.get(case.stage) or sla.DEFAULT_SLA[case.stage]

    # Time still expected in the current stage, floored at zero: a case that
    # has already overrun does not get negative remaining time.
    remaining_here = max(0.0, current_entry["expected_days"] - days_in_stage)
    remaining_after = sum(model[s]["expected_days"] for s in remaining_stages[1:])
    pessimistic_after = sum(model[s]["pessimistic_days"] for s in remaining_stages[1:])

    projected = as_of + timedelta(days=round(remaining_here + remaining_after))
    pessimistic = as_of + timedelta(
        days=round(
            max(0.0, current_entry["pessimistic_days"] - days_in_stage) + pessimistic_after
        )
    )

    # --- risk components, each normalised to 0..1 ---
    elapsed_fraction = min(
        1.0, days_in_stage / target_entry["standard_days"] if target_entry["standard_days"] else 0.0
    )
    history_pressure = min(1.0, max(0.0, current_entry["overrun_ratio"] - 1.0))
    # Caps below are saturation points, not maxima in the data: past three
    # open objections the case is stuck regardless, and a score that keeps
    # climbing would rank "very stuck" above "stuck" for no useful reason.
    objection_signal = min(1.0, pressure.get("open_objections", 0) / 3.0)
    document_signal = min(1.0, pressure.get("missing_documents", 0) / 3.0)
    award_signal = min(1.0, pressure.get("unpaid_awards", 0) / 5.0)

    score = (
        WEIGHTS["elapsed_fraction"] * elapsed_fraction
        + WEIGHTS["history_pressure"] * history_pressure
        + WEIGHTS["open_objections"] * objection_signal
        + WEIGHTS["missing_documents"] * document_signal
        + WEIGHTS["unpaid_awards"] * award_signal
    )
    score = round(min(1.0, score), 3)

    band = RiskBand.SEVERE
    for threshold, candidate in RISK_BANDS:
        if score < threshold:
            band = candidate
            break

    # The single largest contributor, so the UI can say WHY in four words
    # instead of showing five bars nobody reads.
    contributions = {
        "Stage running long": WEIGHTS["elapsed_fraction"] * elapsed_fraction,
        "This stage usually overruns": WEIGHTS["history_pressure"] * history_pressure,
        "Objections unanswered": WEIGHTS["open_objections"] * objection_signal,
        "Documents missing": WEIGHTS["missing_documents"] * document_signal,
        "Awards undisbursed": WEIGHTS["unpaid_awards"] * award_signal,
    }
    primary = max(contributions, key=contributions.get)

    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "stage": case.stage.value,
        "days_in_stage": days_in_stage,
        "projected_completion": projected.isoformat(),
        "pessimistic_completion": pessimistic.isoformat(),
        "projected_days_remaining": round(remaining_here + remaining_after),
        "risk_score": score,
        "risk_band": band.value,
        "primary_driver": primary if contributions[primary] > 0 else "No delay signal",
        "confidence": current_entry["confidence"],
        "evidence": {
            "elapsed_fraction": round(elapsed_fraction, 3),
            "stage_overrun_ratio": current_entry["overrun_ratio"],
            "observations_for_stage": current_entry["observations"],
            "open_objections": pressure.get("open_objections", 0),
            "missing_documents": pressure.get("missing_documents", 0),
            "unpaid_awards": pressure.get("unpaid_awards", 0),
        },
    }


def forecast(db: Session, case_ids: list[int], as_of: date | None = None, limit: int = 200) -> dict:
    """Forecast a set of cases, worst risk first.

    The stage model is built once for the whole batch rather than per case:
    it is a property of the office's history, not of any one file.
    """
    as_of = as_of or date.today()
    if not case_ids:
        return {"as_of": as_of.isoformat(), "items": [], "stage_model": {}, "summary": {}}

    model = stage_model(db)
    targets = sla.load_sla(db)
    pressure = _case_pressure(db, case_ids)

    cases = (
        db.query(Case)
        .filter(Case.id.in_(case_ids), Case.stage != Stage.MONITORING)
        .order_by(Case.id)
        .all()
    )

    items = [
        forecast_case(case, model, pressure.get(case.id, {}), targets, as_of)
        for case in cases
    ]
    items.sort(key=lambda item: item["risk_score"], reverse=True)

    summary: dict[str, int] = {band.value: 0 for band in RiskBand}
    for item in items:
        summary[item["risk_band"]] += 1

    return {
        "as_of": as_of.isoformat(),
        "cases_forecast": len(items),
        "summary": summary,
        "items": items[:limit],
        # Published so the forecast is inspectable: a reviewer can see the
        # durations the projection was built from rather than taking it on
        # faith.
        "stage_model": {
            stage.value: model[stage] for stage in STAGE_ORDER
        },
    }
