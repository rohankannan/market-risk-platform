"""Dump the OpenAPI document to web/openapi.json - the committed typegen input.

    python -m api.openapi_export [path]

The frontend generates its TypeScript types from this file offline; CI
regenerates it and fails on drift, so the generated types can never fall out
of step with the running API.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from api.main import app

DEFAULT_PATH = "web/openapi.json"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0] if args else DEFAULT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"[openapi] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
