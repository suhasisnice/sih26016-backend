"""CLI entry point: `python -m app.ai_layer.seed [--allow-remote] [--rebuild]`.

See DEPLOYMENT.md section 2 for when `--allow-remote` is required and what
`--rebuild` costs.
"""

import argparse

from app.ai_layer.seed import run_seed
from app.database import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe and regenerate the demo dataset.")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow running against a DATABASE_URL that is not the local compose database.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Drop every table and recreate the schema before seeding. Only needed after a model change.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = run_seed(db, allow_remote=args.allow_remote, rebuild=args.rebuild)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Seeded as of {summary['as_of']}:")
    print(f"  {summary['states']} states, {summary['districts']} districts, {summary['villages']} villages")
    print(f"  {summary['projects']} projects, {summary['people']} people")
    print(f"  {summary['cases']} cases, {summary['parcels']} parcels")
    print(
        f"  {summary['alerts_generated']} alerts generated "
        f"across {summary['cases_evaluated']} cases evaluated"
    )
    for rule, count in sorted(summary.get("by_rule", {}).items()):
        print(f"    {rule}: {count}")


if __name__ == "__main__":
    main()
