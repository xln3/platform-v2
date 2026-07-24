"""Shadow legacy persisted facts against current V2 scoring from the same raw inputs."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from domain.scoring.analyzer import CitationInput, analyze_answer
from domain.scoring.eligibility import measurement_eligible
from tools.reconciliation.compare import compare, markdown


def _kpis(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    denominator = len(rows)
    mentioned = sum(bool(row["mentioned"]) for row in rows)
    ranked = [int(row["rank"]) for row in rows if row["rank"] is not None]
    definitions = {
        "mention_rate": (mentioned, denominator, mentioned / denominator if denominator else None),
        "average_rank": (
            sum(ranked),
            len(ranked),
            sum(ranked) / len(ranked) if ranked else None,
        ),
        "top1_rate": (sum(rank <= 1 for rank in ranked), denominator, None),
        "top3_rate": (sum(rank <= 3 for rank in ranked), denominator, None),
        "top10_rate": (sum(rank <= 10 for rank in ranked), denominator, None),
    }
    result = []
    for name, (numerator, metric_denominator, value) in definitions.items():
        if value is None and name.startswith("top"):
            value = numerator / metric_denominator if metric_denominator else None
        result.append(
            {
                "key": name,
                "numerator": numerator,
                "denominator": metric_denominator,
                "value": round(value, 8) if value is not None else None,
            }
        )
    return result


def build_snapshots(source: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        analysis_rows = connection.execute(
            """
            SELECT a.pub_id AS answer_pub_id,a.response_text,a.references_json,
                   a.captcha_mode,a.geo_source,a.account_source,a.rate_policy,
                   a.degraded_flag,a.observed_gb_code,a.eligible,
                   aa.brand_mentioned,aa.rank_position,aa.sentiment,aa.status,
                   b.name AS brand_name,b.id AS brand_id,b.own_domains_json
            FROM answer a
            JOIN answer_analysis aa ON aa.answer_id=a.id
            JOIN brand b ON b.id=aa.brand_id
            WHERE aa.status='ready'
            ORDER BY a.pub_id
            """
        ).fetchall()
        legacy_answers: list[dict[str, Any]] = []
        v2_answers: list[dict[str, Any]] = []
        legacy_eligibility: list[dict[str, Any]] = []
        v2_eligibility: list[dict[str, Any]] = []
        legacy_citations: list[dict[str, Any]] = []
        v2_citations: list[dict[str, Any]] = []
        for row in analysis_rows:
            competitors = tuple(
                item["name"]
                for item in connection.execute(
                    "SELECT name FROM competitor WHERE brand_id=? ORDER BY id",
                    (row["brand_id"],),
                ).fetchall()
            )
            raw_references = json.loads(row["references_json"] or "[]")
            citation_inputs = tuple(
                CitationInput(
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    cited_text=item.get("text"),
                )
                for item in raw_references
                if isinstance(item, dict) and item.get("url")
            )
            result = analyze_answer(
                answer_pub_id=row["answer_pub_id"],
                text=row["response_text"] or "",
                brand=row["brand_name"],
                competitors=competitors,
                citations=citation_inputs,
                dimensions={},
                own_domains=tuple(
                    str(domain).lower()
                    for domain in json.loads(row["own_domains_json"] or "[]")
                    if isinstance(domain, str)
                ),
            )
            legacy_answers.append(
                {
                    "key": row["answer_pub_id"],
                    "mentioned": bool(row["brand_mentioned"]),
                    "rank": row["rank_position"],
                    "sentiment": row["sentiment"],
                }
            )
            v2_answers.append(
                {
                    "key": row["answer_pub_id"],
                    "mentioned": result.fact.mentioned,
                    "rank": result.fact.rank,
                    "sentiment": result.fact.sentiment,
                }
            )
            eligibility_input = {
                "captcha_mode": row["captcha_mode"],
                "geo_source": row["geo_source"],
                "account_source": row["account_source"],
                "rate_policy": row["rate_policy"],
                "degraded_flag": row["degraded_flag"],
                "observed_gb_code": row["observed_gb_code"],
            }
            legacy_eligibility.append(
                {"key": row["answer_pub_id"], "eligible": bool(row["eligible"])}
            )
            v2_eligibility.append(
                {
                    "key": row["answer_pub_id"],
                    "eligible": measurement_eligible(eligibility_input),
                }
            )
            persisted_citations = connection.execute(
                """
                SELECT reference_index,canonical_url,host,is_own_source
                FROM citation_fact
                WHERE answer_id=(SELECT id FROM answer WHERE pub_id=?)
                ORDER BY reference_index
                """,
                (row["answer_pub_id"],),
            ).fetchall()
            for citation in persisted_citations:
                # The legacy persistence model is zero-based while the V2
                # public citation contract is one-based.
                key = f"{row['answer_pub_id']}:{citation['reference_index'] + 1}"
                legacy_citations.append(
                    {
                        "key": key,
                        "canonical_url": citation["canonical_url"],
                        "host": citation["host"],
                        "own_source": bool(citation["is_own_source"]),
                    }
                )
            for citation in result.citations:
                key = f"{row['answer_pub_id']}:{citation['ordinal']}"
                v2_citations.append(
                    {
                        "key": key,
                        "canonical_url": citation["canonical_url"],
                        "host": citation["host"],
                        "own_source": citation["own_source"],
                    }
                )
        task_rows = [
            {"key": row["state"], "count": row["count"]}
            for row in connection.execute(
                "SELECT state,count(*) AS count FROM work_item GROUP BY state ORDER BY state"
            ).fetchall()
        ]
        evidence_rows = [
            {
                "key": f"{row['answer_pub_id']}:{row['kind']}:{row['sha256']}",
                "sha256": row["sha256"],
            }
            for row in connection.execute(
                """
                SELECT a.pub_id AS answer_pub_id,e.kind,e.sha256
                FROM evidence_ref e JOIN answer a ON a.id=e.answer_id
                ORDER BY a.pub_id,e.kind,e.sha256
                """
            ).fetchall()
        ]
    legacy = {
        "task_matrix": task_rows,
        "answers": legacy_answers,
        "eligibility": legacy_eligibility,
        "citations": legacy_citations,
        "kpis": _kpis(legacy_answers),
        "reports": [],
        "evidence": evidence_rows,
    }
    v2 = {
        "task_matrix": task_rows,
        "answers": v2_answers,
        "eligibility": v2_eligibility,
        "citations": v2_citations,
        "kpis": _kpis(v2_answers),
        "reports": [],
        "evidence": evidence_rows,
    }
    return legacy, v2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--approvals", type=Path)
    args = parser.parse_args()
    legacy, v2 = build_snapshots(args.source)
    approvals = json.loads(args.approvals.read_text()) if args.approvals else {}
    result = compare(legacy, v2, approvals)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    (args.output_dir / "reconciliation.md").write_text(markdown(result))
    if not result["summary"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
