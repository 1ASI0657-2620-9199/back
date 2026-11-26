"""Explicit local recovery of events that exhausted their retry budget."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "libraries/finanzen_common/src"))

from app.eventing import utcnow
from app.suite import create_app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Requeue one locally persisted failed event without changing its ID.")
    parser.add_argument("--service", required=True, choices=["financial", "budgets", "devices"])
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    args = parser.parse_args()
    # Initialization is intentionally omitted: recovery must not create databases.
    application = create_app(args.data_dir, start_worker=False)
    store = application.state.services[args.service]
    if not Path(store.database.database_path).is_file():
        raise SystemExit("The selected local service database does not exist.")
    with store.database.connect() as connection:
        event = connection.execute("SELECT dead_letter FROM outbox WHERE event_id = ?", (args.event_id,)).fetchone()
        if not event or not event["dead_letter"]:
            raise SystemExit("No failed event with that ID.")
        connection.execute("""
            UPDATE outbox SET dead_letter = 0, attempts = 0, last_error = NULL, next_attempt_at = ?
            WHERE event_id = ? AND delivered_at IS NULL
        """, (utcnow().isoformat(), args.event_id))
    print("Event requeued with its original identifier.")
