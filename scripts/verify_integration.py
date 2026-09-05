"""End-to-end check that the AI Layer and the backend agree.

Runs inside the api container against the seeded database:
    docker compose exec api python scripts/verify_integration.py

Every KPI is re-derived in plain Python that shares no SQL with the KPI
code, because a number that computes without erroring is not the same as a
number that is right — and these are what a judge reads off the screen.
"""

import sys
from pathlib import Path

# Run as `python scripts/verify_integration.py` and sys.path[0] is scripts/,
# which does not contain the app package. Put the project root on the path
# so the documented command works without a PYTHONPATH incantation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func  # noqa: E402

from app.ai_layer import constants as c  # noqa: E402
from app.ai_layer.constants import anchor_date
from app.ai_layer.kpis import compute_kpis
from app.ai_layer.loaders import load_cases
from app.ai_layer.rules import REGISTRY, run_all_rules
from app.core.enums import (
    AlertSeverity,
    CompensationStatus,
    ObjectionStatus,
    ParcelStatus,
    RnRStatus,
    Stage,
)
from app.database import SessionLocal
from app.models import (
    AffectedFamily,
    Alert,
    AuditLog,
    Case,
    Compensation,
    District,
    Parcel,
    Project,
    RnRRecord,
)
from app.services.alerts import regenerate_alerts

results: list[bool] = []


def check(label: str, actual, expected) -> bool:
    ok = actual == expected
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<44} got={actual!r:<16} expected={expected!r}")
    return ok


def main() -> int:
    db = SessionLocal()
    try:
        print("KPIs cross-checked against independent recomputation")
        national = compute_kpis(db)
        parcels = db.query(Parcel).all()
        comps = db.query(Compensation).all()
        families = db.query(AffectedFamily).all()
        rnrs = db.query(RnRRecord).all()

        acquired = (ParcelStatus.ACQUIRED, ParcelStatus.POSSESSION_TAKEN)
        check("area_notified_ha", national["area_notified_ha"],
              round(sum(p.area_ha for p in parcels), 4))
        check("area_acquired_ha", national["area_acquired_ha"],
              round(sum(p.area_ha for p in parcels if p.status in acquired), 4))
        check("compensation_awarded_total", national["compensation_awarded_total"],
              sum(c.amount_awarded for c in comps))
        check("compensation_paid_total", national["compensation_paid_total"],
              sum(c.amount_paid for c in comps))
        check("compensation_pending_total", national["compensation_pending_total"],
              sum(c.amount_awarded - c.amount_paid for c in comps))
        check("affected_families_count", national["affected_families_count"], len(families))
        check("affected_families_landowner_count", national["affected_families_landowner_count"],
              sum(1 for f in families if f.is_landowner))
        check("rnr_completed_count", national["rnr_completed_count"],
              sum(1 for r in rnrs if r.status is RnRStatus.COMPLETED))
        check("possession_taken_count", national["possession_taken_count"],
              sum(1 for p in parcels if p.status is ParcelStatus.POSSESSION_TAKEN))
        check("possession_pending_count", national["possession_pending_count"],
              sum(1 for p in parcels if p.status is not ParcelStatus.POSSESSION_TAKEN))

        print("\nCompensation and R&R are never merged")
        check("no key sums compensation with rnr",
              any("rnr" in k and "compensation" in k for k in national), False)

        print("\nDistrict totals sum to the national figure")
        district_ids = [d.id for d in db.query(District).order_by(District.id).all()]
        per_district = [compute_kpis(db, district_id=d) for d in district_ids]
        for d_id, kpi in zip(district_ids, per_district):
            print(f"    district {d_id}: {kpi['scope']['case_count']:>3} cases, "
                  f"{kpi['area_notified_ha']:>9,.2f} ha, {kpi['affected_families_count']:>4} families")
        check("sum(district cases)", sum(k["scope"]["case_count"] for k in per_district),
              national["scope"]["case_count"])
        check("sum(district families)", sum(k["affected_families_count"] for k in per_district),
              national["affected_families_count"])
        check("sum(district compensation)",
              sum(k["compensation_awarded_total"] for k in per_district),
              national["compensation_awarded_total"])

        print("\nScope filters narrow, and never widen")
        project_id = db.query(Project.id).order_by(Project.id).first()[0]
        scoped = compute_kpis(db, project_id=project_id)
        check("project scope is narrower",
              scoped["scope"]["case_count"] < national["scope"]["case_count"], True)
        one_district = district_ids[0]
        entitled = [c.id for c in db.query(Case).filter(Case.district_id == one_district).all()]
        crossed = compute_kpis(db, district_id=district_ids[1], base_case_ids=entitled)
        check("filter cannot escape caller's entitlement", crossed["scope"]["case_count"], 0)

        print("\nUnknown filters raise rather than silently returning national totals")
        for bad in ({"district_id": 9999}, {"project_id": 9999}):
            try:
                compute_kpis(db, **bad)
                check(f"rejects {bad}", "no error", "ValueError")
            except ValueError:
                check(f"rejects {bad}", "ValueError", "ValueError")

        print("\nRules fire exactly on the injected anomalies")
        # These counts describe a freshly seeded database, so check that is
        # what we are looking at before comparing any of them.
        #
        # The seed writes no audit rows; the API writes one for every change
        # it makes. So a mutating audit row means somebody has driven the API
        # against this database since the seed, and the counts below are
        # measuring that too. scripts/verify_build.py is the usual culprit:
        # sanctioning a proposal creates a real case, at the first stage,
        # with none of its documents on file yet — which correctly trips
        # document_missing and moves that number three at a time.
        #
        # Without this check, that arrives as "document_missing got=6
        # expected=3", which reads like a broken rule and is not one.
        read_only_actions = {"auth.login", "admin.run_rules"}
        mutations = [
            (action, count)
            for action, count in db.query(AuditLog.action, func.count(AuditLog.id))
            .group_by(AuditLog.action)
            .order_by(AuditLog.action)
            .all()
            if action not in read_only_actions and not action.startswith("export.")
        ]
        if not check("database is in seed state", not mutations, True):
            print(
                "       API writes since the seed: "
                + ", ".join(f"{action} x{count}" for action, count in mutations)
            )
            print("       Reseed before trusting the counts below:")
            print("         docker compose exec api python -m app.ai_layer.seed")

        cases = load_cases(db)
        alerts = run_all_rules(cases, anchor_date())
        by_rule: dict[str, int] = {}
        for a in alerts:
            by_rule[a["rule"]] = by_rule.get(a["rule"], 0) + 1
        # Read off the seed's anomaly constants rather than repeated here as
        # literals: a literal silently goes stale the moment someone tunes a
        # constant, which is how this check last drifted.
        #
        # case_stalled counts the timeline breaches too, and that is correct,
        # not double-reporting. A breach is seeded by pushing stage_changed_at
        # back by a full stage allowance plus a margin — thirty-five days at
        # the very least — and STALLED_DAYS is a flat ten. A case that has not
        # moved in five weeks genuinely is stalled as well as past its
        # deadline; the two rules answer different questions about it ("nobody
        # has touched this" vs "this stage is over its allowance") and an
        # officer needs both.
        stalled_seeded = c.ANOMALY_STALLED_CRITICAL_CASES + c.ANOMALY_STALLED_WARNING_CASES
        expected_counts = {
            "case_stalled": stalled_seeded + c.ANOMALY_TIMELINE_BREACHED,
            "document_missing": c.ANOMALY_DOCUMENTS_REMOVED,
            "objection_unanswered": c.ANOMALY_OBJECTIONS_FORCED_OPEN,
            "award_unpaid": c.ANOMALY_AWARDS_FORCED_UNPAID,
            "possession_before_rnr": c.ANOMALY_POSSESSION_BEFORE_RNR,
            "timeline_breach": c.ANOMALY_TIMELINE_BREACHED,
        }
        # A rule registered with nothing seeded for it is a hole in this
        # check, so say so instead of raising KeyError halfway through.
        check("every registered rule has a seeded expectation",
              sorted(REGISTRY), sorted(expected_counts))
        for rule_name in REGISTRY:
            check(f"{rule_name} count", by_rule.get(rule_name, 0),
                  expected_counts.get(rule_name, "<nothing seeded>"))

        print("\nRules are deterministic and severities are valid")
        again = run_all_rules(load_cases(db), anchor_date())
        # Compared as a boolean, not by printing both lists — a failure
        # here needs a diff, not two screens of identical JSON.
        check("same input -> same output", again == alerts, True)
        check("all severities in AlertSeverity",
              all(a["severity"] in {s.value for s in AlertSeverity} for a in alerts), True)
        check("no alert leaks a person name",
              any("phone" in str(a["details"]).lower() or "name" in str(a["details"]).lower()
                  for a in alerts), False)

        print("\nEvery rule uses the backend's vocabulary, not the old AI Layer's")
        stage_values = {s.value for s in Stage}
        check("stages referenced are real Stage values",
              all(a["details"].get("stage", next(iter(stage_values))) in stage_values
                  for a in alerts), True)
        check("ObjectionStatus.FILED exists", ObjectionStatus.FILED.value, "filed")
        check("CompensationStatus.AWARDED exists", CompensationStatus.AWARDED.value, "awarded")

        print("\nregenerate_alerts is idempotent")
        first = regenerate_alerts(db)
        db.commit()
        count_first = db.query(Alert).count()
        second = regenerate_alerts(db)
        db.commit()
        count_second = db.query(Alert).count()
        check("alert count stable across reruns", count_first, count_second)
        check("summary stable across reruns", first["by_rule"], second["by_rule"])
        check("persisted count matches produced", count_second, second["alerts_generated"])

        passed = sum(results)
        print(f"\n{passed}/{len(results)} checks passed.")
        return 0 if passed == len(results) else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
