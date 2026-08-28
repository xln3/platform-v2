from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from io import BytesIO, StringIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from domain.reporting.artifacts import render_xlsx_workbook

_SHEET_ORDER = (
    "README",
    "METRICS",
    "QUERIES",
    "ANSWERS",
    "DECISIONS",
    "EVENTS",
    "EXCLUSIONS",
    "DESIGN_CELLS",
    "HASHES",
)
_FORMULA_PREFIXES = ("=", "+", "-", "@")


class MetricExportIntegrityError(ValueError):
    """The exported rows no longer reconcile with their frozen snapshot."""


def spreadsheet_safe(value: object) -> object:
    """Keep customer-controlled text inert in Excel and CSV consumers.

    The repository renderer uses inline-string OOXML cells already; the leading
    apostrophe is retained as a second, explicit defence for tools that later
    convert a workbook sheet to CSV or formulas.
    """

    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, list | tuple):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _safe_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [{str(key): spreadsheet_safe(value) for key, value in row.items()} for row in rows]


def normalize_export_bundle(
    bundle: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    missing = [name for name in _SHEET_ORDER if name not in bundle and name.lower() not in bundle]
    if missing:
        raise MetricExportIntegrityError(f"metric_export_missing_sheets:{','.join(missing)}")
    return {
        name: _safe_rows(bundle.get(name, bundle.get(name.lower(), ()))) for name in _SHEET_ORDER
    }


def build_metrics_xlsx(bundle: Mapping[str, Sequence[Mapping[str, object]]]) -> bytes:
    sheets = normalize_export_bundle(bundle)
    return render_xlsx_workbook(sheets)


def build_metrics_csv_zip(bundle: Mapping[str, Sequence[Mapping[str, object]]]) -> bytes:
    sheets = normalize_export_bundle(bundle)
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, rows in sheets.items():
            columns = sorted({str(key) for row in rows for key in row}) or ["说明"]
            stream = StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            if rows:
                writer.writerows(rows)
            else:
                writer.writerow({"说明": "无记录"})
            archive.writestr(f"{name}.csv", "\ufeff" + stream.getvalue())
    return output.getvalue()


def artifact_sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def verify_contribution_hashes(
    *,
    contribution_rows: Sequence[Mapping[str, Any]],
    expected_hash: str,
    canonical_hash: Callable[[object], str],
) -> None:
    """Re-read verification hook shared by synchronous and worker exports.

    ``canonical_hash`` is injected so the API layer cannot accidentally fork
    the metric engine's canonical-json version.
    """

    hashes = sorted(str(row.get("contribution_hash") or "") for row in contribution_rows)
    if any(len(value) != 64 for value in hashes) or canonical_hash(hashes) != expected_hash:
        raise MetricExportIntegrityError("metric_export_contribution_hash_mismatch")


__all__ = [
    "MetricExportIntegrityError",
    "artifact_sha256",
    "build_metrics_csv_zip",
    "build_metrics_xlsx",
    "normalize_export_bundle",
    "spreadsheet_safe",
    "verify_contribution_hashes",
]
