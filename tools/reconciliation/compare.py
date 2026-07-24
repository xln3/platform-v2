"""Produce secret-free JSON and Markdown reconciliation evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SECTIONS = (
    "task_matrix",
    "answers",
    "eligibility",
    "citations",
    "kpis",
    "reports",
    "evidence",
)
FORBIDDEN_KEYS = {
    "cookie",
    "authorization",
    "token",
    "otp",
    "password",
    "profile_path",
    "storage_state",
    "har",
    "biometric",
}


@dataclass(frozen=True)
class Difference:
    section: str
    key_digest: str
    kind: str
    legacy_hash: str | None
    v2_hash: str | None
    approved: bool
    approval_id: str | None


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _assert_safe(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_KEYS or any(
                token in normalized for token in ("secret", "credential", "refresh_token")
            ):
                raise ValueError(f"secret-bearing reconciliation key rejected at {'.'.join(path)}")
            _assert_safe(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe(child, (*path, str(index)))


def _index(rows: Any, section: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{section} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str):
            raise ValueError(f"{section} rows require a string key")
        key = row["key"]
        if key in result:
            raise ValueError(f"{section} contains duplicate key")
        result[key] = row
    return result


def compare(
    legacy: dict[str, Any],
    v2: dict[str, Any],
    approvals: dict[str, str] | None = None,
) -> dict[str, Any]:
    _assert_safe(legacy)
    _assert_safe(v2)
    approvals = approvals or {}
    differences: list[Difference] = []
    section_counts: dict[str, dict[str, int]] = {}
    for section in SECTIONS:
        legacy_rows = _index(legacy.get(section, []), section)
        v2_rows = _index(v2.get(section, []), section)
        counts = {"legacy": len(legacy_rows), "v2": len(v2_rows), "differences": 0}
        for key in sorted(legacy_rows.keys() | v2_rows.keys()):
            legacy_row = legacy_rows.get(key)
            v2_row = v2_rows.get(key)
            if legacy_row == v2_row:
                continue
            kind = "changed"
            if legacy_row is None:
                kind = "v2_only"
            elif v2_row is None:
                kind = "legacy_only"
            approval_key = f"{section}:{_digest(key)}"
            differences.append(
                Difference(
                    section=section,
                    key_digest=_digest(key),
                    kind=kind,
                    legacy_hash=_digest(legacy_row) if legacy_row is not None else None,
                    v2_hash=_digest(v2_row) if v2_row is not None else None,
                    approved=approval_key in approvals,
                    approval_id=approvals.get(approval_key),
                )
            )
            counts["differences"] += 1
        section_counts[section] = counts
    unapproved = sum(not difference.approved for difference in differences)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "legacy_snapshot_hash": _digest(legacy),
        "v2_snapshot_hash": _digest(v2),
        "secret_values_included": False,
        "raw_business_values_included": False,
        "sections": section_counts,
        "differences": [asdict(difference) for difference in differences],
        "summary": {
            "differences": len(differences),
            "approved": len(differences) - unapproved,
            "unapproved": unapproved,
            "passed": unapproved == 0,
        },
    }


def markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Legacy/V2 shadow reconciliation",
        "",
        f"- Result: {'PASS' if summary['passed'] else 'FAIL'}",
        f"- Differences: {summary['differences']}",
        f"- Approved: {summary['approved']}",
        f"- Unapproved: {summary['unapproved']}",
        "- Secret/raw business values included: no",
        "",
        "| Section | Legacy | V2 | Differences |",
        "|---|---:|---:|---:|",
    ]
    for section in SECTIONS:
        counts = result["sections"][section]
        lines.append(
            f"| {section} | {counts['legacy']} | {counts['v2']} | {counts['differences']} |"
        )
    lines.extend(
        [
            "",
            "Difference records in the JSON contain only key/value hashes and approval metadata.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("legacy", type=Path)
    parser.add_argument("v2", type=Path)
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    legacy = json.loads(args.legacy.read_text())
    v2 = json.loads(args.v2.read_text())
    approvals = json.loads(args.approvals.read_text()) if args.approvals else {}
    result = compare(legacy, v2, approvals)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    args.markdown_output.write_text(markdown(result))
    if not result["summary"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
