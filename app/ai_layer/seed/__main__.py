"""Reset the database to the known good demo state:

    docker compose exec api python -m app.ai_layer.seed

--rebuild drops and recreates the schema first, needed only when the
models have changed shape, because create_all cannot alter a live table.
"""

import argparse

from app.ai_layer.seed import run_seed

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe and regenerate the demo database.")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="drop every table and rebuild the schema first (destroys all data)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit wiping a database that is not on this machine",
    )
    args = parser.parse_args()

    summary = run_seed(rebuild=args.rebuild, allow_remote=args.allow_remote)
    print("Seed complete:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
