"""Export current contracts without starting services or touching databases."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "libraries/finanzen_common/src"))

from app.suite import create_app

if __name__ == "__main__":
    destination = ROOT / "reports/openapi"
    destination.mkdir(parents=True, exist_ok=True)
    application = create_app(start_worker=False)
    contracts = {"integrated": application}
    for route in application.routes:
        if route.path.startswith("/services/"):
            contracts[route.path.rsplit("/", 1)[-1]] = route.app
    for name, app in contracts.items():
        (destination / f"{name}.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print("Exported:", ", ".join(contracts))
