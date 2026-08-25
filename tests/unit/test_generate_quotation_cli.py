"""报价单 CLI 的结构化制品类型契约。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from tools.generate_quotation import _arguments, _configuration, _is_protected_template_output, main


def _priced_arguments(*extra: str) -> argparse.Namespace:
    return _arguments(
        [
            "--brand",
            "盛邦安全",
            "--website",
            "https://www.webray.com.cn",
            "--price",
            "ranking_test=20000",
            "--price",
            "outbound_disparagement_audit=8000",
            "--price",
            "inbound_disparagement_audit=12000",
            "--price",
            "official_site_audit=10000",
            *extra,
        ]
    )


@pytest.mark.parametrize(
    ("arguments", "expected_kind"),
    [
        ((), "complete"),
        (("--artifact-kind", "quote_table"), "quote_table"),
        (("--artifact-kind", "query_appendix"), "query_appendix"),
    ],
)
def test_cli_artifact_kind_defaults_and_reaches_configuration(
    arguments: tuple[str, ...], expected_kind: str
) -> None:
    configuration = _configuration(_priced_arguments(*arguments))
    assert configuration.artifact_kind == expected_kind


def test_cli_help_explains_all_artifacts(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _arguments(["--help"])
    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--artifact-kind {complete,quote_table,query_appendix}" in output
    compact_output = "".join(output.split())
    assert "complete=完整报价单（默认）" in compact_output
    assert "quote_table=仅报价单表格" in compact_output
    assert "query_appendix=仅查询附件" in compact_output
    assert "上传后生成查询附件" in compact_output
    assert "非最终模板合规产物" in compact_output


def test_cli_refuses_canonical_source_and_versioned_template_asset_outputs() -> None:
    assert _is_protected_template_output(
        Path("/home/xln/geo-system/client-sbaq/报价单-盛邦-final(2).docx")
    )
    assert _is_protected_template_output(
        Path(
            "/home/xln/geo-system/platform-v2/api/geo_platform/quotations/assets/"
            "quotation-template-v1.docx"
        )
    )
    assert _is_protected_template_output(
        Path(
            "/opt/runner/another-checkout/api/geo_platform/quotations/assets/"
            "quotation-template-v2.docx"
        )
    )
    assert not _is_protected_template_output(Path("/tmp/internal-quotation.docx"))


def test_cli_rejects_canonical_output_before_generation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_quotation.py",
            "--brand",
            "盛邦安全",
            "--output",
            "/home/xln/geo-system/client-sbaq/报价单-盛邦-final(2).docx",
        ],
    )
    assert main() == 2
    assert "拒绝写入最终模板真源" in capsys.readouterr().err
