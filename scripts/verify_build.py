"""End-to-end check of everything added in the second build.

Runs against a live, seeded API over HTTP rather than in-process, because the
things most likely to be wrong here are wiring: a route that imports fine and
500s, a response model that does not match what the route returns, an
entitlement that leaks across a scope boundary. None of those show up in an
import check.

    docker compose exec api python scripts/verify_build.py
    python scripts/verify_build.py --base-url http://localhost:8000

Exit code is non-zero if anything fails, so it can gate a commit.

This script writes: it opens a proposal, drives it to sanction (which mints a
case) and registers a parcel. Those extra cases carry no documents yet, so
running this BEFORE scripts/verify_integration.py inflates that script's
exact anomaly counts. Run verify_integration.py first on a fresh seed, or
reseed between the two.
"""

import argparse
import json
import os
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://localhost:8000"
# Matches the seed. Override both together, or every sign-in here 401s.
PASSWORD = os.environ.get("SEED_PASSWORD") or "demo1234"

results: list[tuple[bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    results.append((condition, f"{label}{(' — ' + detail) if detail else ''}"))
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    return condition


def request(method: str, path: str, token: str | None = None, body=None, form=None,
            expect_status: int | None = None):
    """Returns (status, parsed_body_or_text). Never raises on an HTTP error —
    a 403 is frequently the thing being asserted."""
    url = f"{BASE_URL}{path}"
    data = None
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except Exception as exc:  # noqa: BLE001 — a connection failure is a result
        return 0, str(exc)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    return status, parsed


def login(username: str) -> str | None:
    status, body = request(
        "POST", "/auth/login", form={"username": username, "password": PASSWORD}
    )
    if status != 200 or not isinstance(body, dict):
        return None
    return body.get("access_token")


def main() -> int:
    global BASE_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    BASE_URL = args.base_url.rstrip("/")

    print(f"\nVerifying {BASE_URL}\n" + "=" * 70)

    # ---------------------------------------------------------------- auth
    print("\n[ auth and the new roles ]")
    tokens = {}
    for username in (
        "admin",
        "dc.bengaluru",
        "field.bengaluru",
        "landowner",
        "state.karnataka",
        "ministry",
        "nhai",
    ):
        token = login(username)
        tokens[username] = token
        check(f"login as {username}", token is not None)

    admin = tokens.get("admin")
    if not admin:
        print("\nCannot continue without an admin token.")
        return 1

    status, me = request("GET", "/auth/me", tokens["state.karnataka"])
    check(
        "state officer carries a state_id",
        status == 200 and me.get("state_id") is not None,
        f"state={me.get('state_name')}" if isinstance(me, dict) else str(me),
    )
    status, me = request("GET", "/auth/me", tokens["nhai"])
    check(
        "requiring body carries an organisation",
        status == 200 and bool(me.get("organisation")),
        me.get("organisation") if isinstance(me, dict) else str(me),
    )

    # ------------------------------------------------------------ reference
    print("\n[ states and nationwide reference data ]")
    status, states = request("GET", "/states", admin)
    check("GET /states", status == 200 and isinstance(states, list) and len(states) > 1,
          f"{len(states) if isinstance(states, list) else '?'} states")
    karnataka = next((s for s in states if s["code"] == "KA"), None) if isinstance(states, list) else None
    check("Karnataka has its real LGD code",
          karnataka is not None and karnataka.get("lgd_code") == "29",
          karnataka.get("lgd_code") if karnataka else "missing")

    status, districts = request("GET", "/districts", admin)
    check("districts publish state_id and lgd_code",
          status == 200 and all("state_id" in d for d in districts)
          and any(d.get("lgd_code") for d in districts))

    # ----------------------------------------------------------------- kpis
    print("\n[ dashboard KPIs — the figures the audit flagged as missing ]")
    status, kpis = request("GET", "/dashboard/kpis", admin)
    check("GET /dashboard/kpis", status == 200)
    if status == 200:
        for field in (
            "displaced_families_count",
            "timeline_adherence_pct",
            "timeline_breached_count",
            "notifications_issued_count",
            "awards_declared_count",
        ):
            check(f"  {field} present", field in kpis, str(kpis.get(field)))
        check("displaced <= affected",
              kpis["displaced_families_count"] <= kpis["affected_families_count"],
              f"{kpis['displaced_families_count']} of {kpis['affected_families_count']}")
        check("notifications >= declarations >= awards (cumulative register)",
              kpis["notifications_issued_count"] >= kpis["declarations_issued_count"]
              >= kpis["awards_declared_count"],
              f"{kpis['notifications_issued_count']}/{kpis['declarations_issued_count']}"
              f"/{kpis['awards_declared_count']}")
        buckets = (kpis["timeline_on_time_count"] + kpis["timeline_at_risk_count"]
                   + kpis["timeline_breached_count"] + kpis["timeline_untracked_count"])
        check("timeline buckets sum to the case count",
              buckets == kpis["scope"]["case_count"],
              f"{buckets} vs {kpis['scope']['case_count']}")

    if karnataka:
        status, scoped = request("GET", f"/dashboard/kpis?state_id={karnataka['id']}", admin)
        check("state-scoped KPIs narrow the case count",
              status == 200 and scoped["scope"]["case_count"] < kpis["scope"]["case_count"],
              f"{scoped['scope']['case_count']} of {kpis['scope']['case_count']}")

    status, bad = request("GET", "/dashboard/kpis?state_id=999999", admin)
    check("unknown state_id is rejected, not silently ignored", bad and status == 400)

    # ------------------------------------------------------------- trends
    print("\n[ trends and forecast ]")
    status, trends = request("GET", "/dashboard/trends?months=6", admin)
    check("GET /dashboard/trends", status == 200 and len(trends.get("points", [])) == 6,
          f"{len(trends.get('points', [])) if status == 200 else status} points")

    status, forecast = request("GET", "/dashboard/forecast?limit=5", admin)
    check("GET /dashboard/forecast", status == 200)
    if status == 200:
        check("forecast returns ranked items", len(forecast["items"]) > 0,
              f"{forecast['cases_forecast']} cases forecast")
        if forecast["items"]:
            first = forecast["items"][0]
            check("  forecast item carries evidence", bool(first.get("evidence")))
            check("  forecast item names a driver", bool(first.get("primary_driver")),
                  first.get("primary_driver"))
            check("  risk ordered worst first",
                  all(forecast["items"][i]["risk_score"] >= forecast["items"][i + 1]["risk_score"]
                      for i in range(len(forecast["items"]) - 1)))
        check("stage model is published for inspection", bool(forecast.get("stage_model")))

    # ---------------------------------------------------------- proposals
    print("\n[ proposal pipeline ]")
    status, proposals = request("GET", "/proposals", admin)
    check("GET /proposals", status == 200 and proposals.get("total", 0) > 0,
          f"{proposals.get('total')} proposals")
    check("pipeline counts every status",
          status == 200 and len(proposals.get("by_status", {})) >= 5,
          str(proposals.get("by_status")))

    # A requiring body sees only its own.
    status, own = request("GET", "/proposals", tokens["nhai"])
    check("requiring body sees only its own proposals",
          status == 200 and own["total"] < proposals["total"] and all(
              item["requiring_body"] == "National Highways Authority of India"
              for item in own["items"]
          ),
          f"{own.get('total')} of {proposals.get('total')}")

    # Landowners have no business here.
    status, _ = request("GET", "/proposals", tokens["landowner"])
    check("landowner sees no proposals", status == 200 and _["total"] == 0)

    # Full submit -> scrutiny -> sanction chain.
    status, villages = request("GET", "/villages", admin)
    village_id = villages[0]["id"] if status == 200 and villages else None
    created_id = None
    if village_id:
        status, created = request("POST", "/proposals", tokens["nhai"], body={
            "title": "Verification test corridor acquisition",
            "purpose": "Automated end-to-end verification of the proposal approval chain. " * 2,
            "village_id": village_id,
            "estimated_area_ha": 12.5,
            "estimated_families": 30,
        })
        check("requiring body can open a proposal", status == 201,
              created.get("proposal_number") if status == 201 else str(created)[:120])
        if status == 201:
            created_id = created["id"]
            check("  opens as draft", created["status"] == "draft")
            check("  draft is held by the requiring body", created["held_by"] == "Requiring body")

    if created_id:
        # The state must not be able to approve — that is the ministry's call.
        status, refused = request(
            "POST", f"/proposals/{created_id}/transition", tokens["state.karnataka"],
            body={"to_status": "approved"},
        )
        check("state cannot skip to approved", status == 400,
              str(refused.get("detail"))[:90] if isinstance(refused, dict) else str(status))

        status, _ = request("POST", f"/proposals/{created_id}/transition", tokens["nhai"],
                            body={"to_status": "submitted"})
        check("requiring body submits", status == 200)

        status, refused = request(
            "POST", f"/proposals/{created_id}/transition", tokens["nhai"],
            body={"to_status": "under_scrutiny"},
        )
        check("requiring body cannot scrutinise its own submission", status == 403,
              str(refused.get("detail"))[:90] if isinstance(refused, dict) else str(status))

        status, _ = request("POST", f"/proposals/{created_id}/transition",
                            tokens["state.karnataka"],
                            body={"to_status": "under_scrutiny", "note": "Verified."})
        check("state takes it up for scrutiny", status == 200)

        status, approved = request("POST", f"/proposals/{created_id}/transition",
                                   tokens["ministry"],
                                   body={"to_status": "approved", "note": "Sanctioned."})
        check("ministry sanctions it", status == 200,
              str(approved.get("detail"))[:90] if status != 200 else "")
        if status == 200:
            check("  sanction created a case", approved.get("case_id") is not None,
                  approved.get("case_number"))
            check("  review trail records every hand-off",
                  len(approved.get("reviews", [])) >= 4,
                  f"{len(approved.get('reviews', []))} entries")

            # The new case must be a first-class case.
            new_case_id = approved["case_id"]
            status, case = request("GET", f"/cases/{new_case_id}", admin)
            check("  the sanctioned case opens normally", status == 200)
            if status == 200:
                check("  it starts at preliminary notification",
                      case["stage"] == "preliminary_notification")
                check("  it has a stage deadline from birth",
                      case.get("stage_due_on") is not None, str(case.get("stage_due_on")))
                check("  it records its provenance",
                      case.get("proposal_number") is not None, case.get("proposal_number"))

    # ------------------------------------------------------------- notices
    print("\n[ statutory notice register ]")
    status, cases = request("GET", "/cases?limit=50", admin)
    award_case = None
    if status == 200:
        for item in cases["items"]:
            s, register = request("GET", f"/notices/register?case_id={item['id']}", admin)
            if s == 200 and register["total"] >= 2:
                award_case = item
                check("notice register returns a case's instruments",
                      True, f"{register['total']} on {item['case_number']}")
                check("  each instrument cites a section",
                      all(n["section_reference"] for n in register["items"]))
                break
    check("found a case with a multi-instrument register", award_case is not None)

    status, public = request("GET", "/notices")
    check("public notice board is still unauthenticated", status == 200,
          f"{public.get('total')} notices" if status == 200 else str(status))

    # ------------------------------------------------------- notifications
    print("\n[ notifications ]")
    # Re-run the rules first so there is fresh unread mail regardless of what
    # a previous run of this script already marked read — otherwise the
    # mark-read check below picks an already-read item and marks nothing.
    request("POST", "/admin/run-rules", admin)
    status, inbox = request("GET", "/notifications?unread_only=true", tokens["dc.bengaluru"])
    check("district officer has unread notifications", status == 200 and inbox["total"] > 0,
          f"{inbox['total']} unread")
    status, count = request("GET", "/notifications/unread-count", tokens["dc.bengaluru"])
    check("unread-count endpoint agrees with the list",
          status == 200 and count["unread_count"] == inbox["unread_count"])

    if inbox.get("items"):
        first_id = inbox["items"][0]["id"]
        status, marked = request("POST", "/notifications/mark-read", tokens["dc.bengaluru"],
                                 body={"notification_ids": [first_id]})
        check("marking one read works", status == 200 and marked["marked"] == 1,
              f"{marked.get('unread_count')} left unread")

        # Cross-user isolation: another officer's id must mark nothing.
        status, other = request("POST", "/notifications/mark-read", tokens["field.bengaluru"],
                                body={"notification_ids": [first_id]})
        check("cannot mark another user's notification read",
              status == 200 and other["marked"] == 0)

    # ------------------------------------------------------------- parcels
    print("\n[ field data collection ]")
    status, case_list = request("GET", "/cases?limit=1", tokens["field.bengaluru"])
    if status == 200 and case_list["items"]:
        case_id = case_list["items"][0]["id"]
        status, parcels = request("GET", f"/parcels?case_id={case_id}", tokens["field.bengaluru"])
        owner_id = parcels[0]["owner_id"] if status == 200 and parcels else None

        # Unique per run: the duplicate check below is asserting that the
        # SERVER refuses a repeat, which only means something if the first
        # insert was not itself a leftover from the previous run.
        survey = f"VER{uuid.uuid4().hex[:6].upper()}/1"
        if owner_id:
            status, parcel = request("POST", "/parcels", tokens["field.bengaluru"], body={
                "case_id": case_id,
                "survey_number": survey,
                "area_ha": 1.25,
                "owner_id": owner_id,
                "longitude": 77.5,
                "latitude": 13.2,
                "gps_accuracy_m": 4.5,
            })
            check("field officer can register a geo-tagged parcel", status == 201,
                  str(parcel.get("detail"))[:90] if status != 201 else f"id={parcel['id']}")

            if status == 201:
                parcel_id = parcel["id"]
                check("  coordinates round-trip through PostGIS",
                      abs(parcel["longitude"] - 77.5) < 1e-6
                      and abs(parcel["latitude"] - 13.2) < 1e-6)

                status, dup = request("POST", "/parcels", tokens["field.bengaluru"], body={
                    "case_id": case_id, "survey_number": survey, "area_ha": 1.0,
                    "owner_id": owner_id, "longitude": 77.5, "latitude": 13.2,
                })
                check("  duplicate survey number on the same case is refused", status == 409)

                status, half = request("PATCH", f"/parcels/{parcel_id}",
                                       tokens["field.bengaluru"], body={"longitude": 77.6})
                check("  latitude without longitude is refused", status == 400)

                status, moved = request("PATCH", f"/parcels/{parcel_id}",
                                        tokens["field.bengaluru"],
                                        body={"longitude": 77.6, "latitude": 13.25,
                                              "status": "under_acquisition"})
                check("  parcel can be corrected and advanced", status == 200
                      and moved["status"] == "under_acquisition")

                status, refused = request("POST", "/parcels", tokens["landowner"], body={
                    "case_id": case_id, "survey_number": "HACK/1", "area_ha": 1.0,
                    "owner_id": owner_id, "longitude": 77.5, "latitude": 13.2,
                })
                check("  landowner cannot register a parcel", status == 403)

    # ------------------------------------------------------------- exports
    print("\n[ MIS exports ]")
    for path, label in (
        ("/exports/cases.csv", "cases"),
        ("/exports/compensation.csv", "compensation"),
        ("/exports/families.csv", "families"),
        ("/exports/kpis.csv?group_by=state", "KPIs by state"),
        ("/exports/kpis.csv?group_by=district", "KPIs by district"),
    ):
        status, body = request("GET", path, admin)
        lines = body.count("\n") if isinstance(body, str) else 0
        check(f"export {label}", status == 200 and lines > 1, f"{lines} lines")

    status, body = request("GET", "/exports/compensation.csv", tokens["landowner"])
    check("landowner cannot export the compensation register", status == 403)

    status, body = request("GET", "/exports/cases.csv", tokens["dc.bengaluru"])
    if isinstance(body, str):
        district_lines = [ln for ln in body.splitlines()[1:] if ln.strip()]
        check("district officer's export is scoped to their district",
              all("Bengaluru Rural" in ln for ln in district_lines),
              f"{len(district_lines)} rows")

    # ----------------------------------------------------------- documents
    print("\n[ document versioning ]")
    status, docs = request("GET", "/documents?case_id=1", admin)
    check("document list responds", status == 200,
          f"{docs.get('total')} current, {docs.get('superseded_count')} superseded"
          if status == 200 else str(status))
    if status == 200 and docs["items"]:
        check("  documents carry a version", "version" in docs["items"][0],
              f"v{docs['items'][0].get('version')}")
        doc_type = docs["items"][0]["doc_type"]
        status, history = request("GET", f"/documents/versions?case_id=1&doc_type={doc_type}", admin)
        check("  version history endpoint responds", status == 200,
              f"{len(history.get('versions', []))} versions")

    # ------------------------------------------------------------- scoping
    print("\n[ access control ]")
    status, national = request("GET", "/cases?limit=1", admin)
    status2, district_scoped = request("GET", "/cases?limit=1", tokens["dc.bengaluru"])
    status3, state_scoped = request("GET", "/cases?limit=1", tokens["state.karnataka"])
    check("admin sees the most cases",
          national["total"] >= state_scoped["total"] >= district_scoped["total"],
          f"admin={national['total']} state={state_scoped['total']} "
          f"district={district_scoped['total']}")
    check("state officer sees more than one district but fewer than the nation",
          district_scoped["total"] < state_scoped["total"] < national["total"])

    status, ministry_cases = request("GET", "/cases?limit=1", tokens["ministry"])
    check("ministry reads nationally", status == 200
          and ministry_cases["total"] == national["total"])

    status, refused = request("POST", "/cases", tokens["ministry"], body={
        "title": "Ministry should not be able to open this", "project_id": 1, "village_id": 1,
    })
    check("ministry cannot open a case operationally", status == 403)

    status, overdue = request("GET", "/cases?overdue_only=true", admin)
    check("overdue filter works", status == 200,
          f"{overdue.get('total')} overdue of {national['total']}")

    # ----------------------------------------------------------- meta/enums
    print("\n[ published contract ]")
    status, enums = request("GET", "/meta/enums")
    for key in ("proposal_statuses", "notice_types", "timeline_statuses", "risk_bands"):
        check(f"/meta/enums publishes {key}", status == 200 and key in enums)
    check("roles include the three new tiers",
          status == 200 and {"requiring_body", "state_officer", "ministry_officer"}
          <= set(enums["roles"]))

    # ------------------------------------------------------------- summary
    print("\n" + "=" * 70)
    passed = sum(1 for ok, _ in results if ok)
    failed = [label for ok, label in results if not ok]
    print(f"{passed}/{len(results)} checks passed")
    if failed:
        print("\nFAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
