#!/usr/bin/env python3
"""Build a deterministic brand-rank projection from one SiliconIndex release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.siliconindex import project_brand_domain  # noqa: E402


def build_projection(snapshot: Path, *, domain: str) -> dict[str, object]:
    """Compatibility wrapper around the governed adapter projector."""

    return project_brand_domain(snapshot, analysis_domain=domain)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--domain", default="cybersecurity")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    projection = build_projection(args.snapshot, domain=args.domain)
    entities = projection.get("entities")
    if not isinstance(entities, list):
        raise SystemExit("projection_entities_invalid")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "revision": projection["revision"],
                "entities": len(entities),
                "reviewed": sum(
                    isinstance(row, dict) and row.get("review_status") == "reviewed"
                    for row in entities
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
