"""Run from back/: python scripts/run_local.py [--port 8000]."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FinanZen APIs + frontend + local asynchronous worker")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("app.suite:app", host="127.0.0.1", port=args.port)
