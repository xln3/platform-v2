#!/usr/bin/env python3
"""Deterministically compile a verified SiliconIndex release for local runtime use."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.siliconindex import project_brand_domain  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT.parent / "GEO-auto-analysis" / "siliconindex-consumer",
    )
    parser.add_argument("--analysis-domain", default="cybersecurity")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "domain"
        / "brandrank"
        / "rules_data"
        / "siliconindex_projection_cybersecurity.json",
    )
    args = parser.parse_args()
    projection = project_brand_domain(args.source, analysis_domain=args.analysis_domain)
    rendered = json.dumps(projection, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "source_release_id": projection["source_release_id"],
                "source_content_hash": projection["source_content_hash"],
                "entity_count": len(projection["entities"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
