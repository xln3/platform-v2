#!/usr/bin/env python3
"""Read-only shadow replay of stored brand extractions through the new read model."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.brandrank.entities import load_entity_master, normalize_answer_entities  # noqa: E402
from domain.brandrank.rules import load_domain, normalize_brand_list  # noqa: E402

_PUBLIC_ID = re.compile(r"^[a-z]{3}_[A-Z0-9]{20,32}$")
_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _project_ref(project_pub_id: str) -> str:
    return "sha256:" + hashlib.sha256(project_pub_id.encode()).hexdigest()


def _load_rows(
    *,
    container: str,
    database: str,
    tenant_pub_id: str,
    project_pub_id: str,
    domain: str,
) -> list[dict[str, Any]]:
    if not _CONTAINER.fullmatch(container):
        raise ValueError("invalid_container_name")
    if not _PUBLIC_ID.fullmatch(tenant_pub_id) or not _PUBLIC_ID.fullmatch(project_pub_id):
        raise ValueError("invalid_public_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", domain):
        raise ValueError("invalid_domain")
    statement = f"""
    BEGIN READ ONLY;
    SELECT set_config('app.tenant_pub_id', '{tenant_pub_id}', true);
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'answer_pub_id', a.pub_id,
      'brands', e.brands
    ) ORDER BY a.capture_time, a.pub_id), '[]'::jsonb)
    FROM analytics.answer a
    JOIN analytics.answer_brand_extract e
      ON e.tenant_pub_id=a.tenant_pub_id AND e.answer_pub_id=a.pub_id
    WHERE a.tenant_pub_id='{tenant_pub_id}'
      AND a.project_pub_id='{project_pub_id}'
      AND a.eligible AND NOT a.degraded
      AND e.domain='{domain}' AND e.status='ok';
    ROLLBACK;
    """
    process = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "geo",
            "-d",
            database,
            "-X",
            "-qAt",
            "-c",
            statement,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0:
        raise RuntimeError("read_only_query_failed")
    lines = [line for line in process.stdout.splitlines() if line.startswith("[")]
    if len(lines) != 1:
        raise RuntimeError("read_only_query_shape_invalid")
    rows = json.loads(lines[0])
    if not isinstance(rows, list):
        raise RuntimeError("read_only_query_shape_invalid")
    return rows


def replay(
    rows: list[dict[str, Any]],
    *,
    domain: str,
    comparison_scopes: tuple[str, ...],
    knowledge_release_dir: str,
    project_pub_id: str,
) -> dict[str, Any]:
    rules = load_domain(domain)
    master = load_entity_master(domain, knowledge_release_dir=knowledge_release_dir)
    raw_mentions = legacy_mentions = governed_mentions = eligible_mentions = 0
    unresolved_mentions = alias_collapses = changed_answers = target_legacy = target_governed = 0
    ineligible_by_type: Counter[str] = Counter()
    knowledge_status_counts: Counter[str] = Counter()
    for row in rows:
        brands = row.get("brands") or []
        if not isinstance(brands, list) or not all(isinstance(value, str) for value in brands):
            raise RuntimeError("stored_brand_extract_shape_invalid")
        legacy = normalize_brand_list(brands, rules)
        governed = normalize_answer_entities(
            brands,
            rules=rules,
            master=master,
            comparison_scopes=comparison_scopes,
        )
        eligible = [str(item["canonical_name"]) for item in governed if item["competitor_eligible"]]
        raw_mentions += len(brands)
        legacy_mentions += len(legacy)
        governed_mentions += len(governed)
        eligible_mentions += len(eligible)
        unresolved_mentions += sum(item["knowledge_status"] == "unresolved" for item in governed)
        for item in governed:
            knowledge_status_counts[str(item["knowledge_status"])] += 1
            if not item["competitor_eligible"]:
                ineligible_by_type[str(item["entity_type"])] += 1
        alias_collapses += max(0, len(brands) - len(governed))
        changed_answers += int(legacy != eligible)
        target_legacy += int("盛邦安全" in legacy)
        target_governed += int("盛邦安全" in eligible)
    return {
        "schema_version": "brand-knowledge-shadow-replay-v1",
        "mode": "read_only_stored_extractions",
        "project_ref": _project_ref(project_pub_id),
        "analysis_domain": domain,
        "comparison_scopes": list(comparison_scopes),
        "knowledge_release_id": master.source_release_id,
        "knowledge_content_hash": master.source_content_hash,
        "answers_replayed": len(rows),
        "raw_mentions": raw_mentions,
        "legacy_normalized_mentions": legacy_mentions,
        "governed_canonical_mentions": governed_mentions,
        "governed_eligible_mentions": eligible_mentions,
        "unresolved_mentions": unresolved_mentions,
        "knowledge_status_counts": dict(sorted(knowledge_status_counts.items())),
        "ineligible_by_entity_type": dict(sorted(ineligible_by_type.items())),
        "alias_collapses": alias_collapses,
        "answers_with_changed_eligible_projection": changed_answers,
        "target_answer_mentions": {
            "legacy": target_legacy,
            "governed": target_governed,
        },
        "mutations": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", default="geo_platform")
    parser.add_argument("--tenant-pub-id", required=True)
    parser.add_argument("--project-pub-id", required=True)
    parser.add_argument("--domain", default="cybersecurity")
    parser.add_argument("--comparison-scopes", default="cybersecurity")
    parser.add_argument("--knowledge-release-dir", default="data/knowledge-releases")
    args = parser.parse_args()
    rows = _load_rows(
        container=args.container,
        database=args.database,
        tenant_pub_id=args.tenant_pub_id,
        project_pub_id=args.project_pub_id,
        domain=args.domain,
    )
    report = replay(
        rows,
        domain=args.domain,
        comparison_scopes=tuple(
            value.strip() for value in args.comparison_scopes.split(",") if value.strip()
        ),
        knowledge_release_dir=args.knowledge_release_dir,
        project_pub_id=args.project_pub_id,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
