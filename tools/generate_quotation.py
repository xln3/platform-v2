#!/usr/bin/env python3
"""生成仅供内部回归的 legacy 报价 DOCX；当前不得作为正式客户报价。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.intake.research import ResearchModelNotAllowed  # noqa: E402
from geo_platform.quotations.catalog import PACKAGE_BY_CODE, SERVICE_CATALOG  # noqa: E402
from geo_platform.quotations.generator import (  # noqa: E402
    QuotationGenerationFailed,
    QuotationLlmDisabled,
)
from geo_platform.quotations.models import (  # noqa: E402
    QuotationArtifactKind,
    QuotationConfiguration,
)
from geo_platform.quotations.service import (  # noqa: E402
    QuotationInputInvalid,
    generate_quotation,
)
from geo_platform.quotations.xlsx import TargetWorkbookInvalid  # noqa: E402

_PRESET_PACKAGES = ("geo_effect_assessment", "minimum_validation")
_ARTIFACT_KINDS: tuple[QuotationArtifactKind, ...] = (
    "complete",
    "quote_table",
    "query_appendix",
)
_ARTIFACT_LABELS: dict[QuotationArtifactKind, str] = {
    "complete": "完整报价单",
    "quote_table": "报价单表格",
    "query_appendix": "查询附件",
}
_NON_FINAL_NOTICE = "非最终模板合规产物（仅供内部回归，禁止作为正式客户报价）"
_CANONICAL_SOURCE = (ROOT.parent / "client-sbaq" / "报价单-盛邦-final(2).docx").resolve()
_CANONICAL_SOURCE_SIGNATURE = ("client-sbaq", "报价单-盛邦-final(2).docx")
_TEMPLATE_ASSETS = (ROOT / "api" / "geo_platform" / "quotations" / "assets").resolve()
_TEMPLATE_ASSETS_SIGNATURE = ("api", "geo_platform", "quotations", "assets")


def _is_protected_template_output(path: Path) -> bool:
    """Canonical source and versioned template assets are never CLI output targets.

    The canonical source may be addressed with the production workspace path,
    a developer checkout path, or a CI fixture path.  Protect its stable
    directory/name identity as well as this checkout's resolved absolute path.
    """
    resolved = path.resolve()
    canonical_signature = tuple(resolved.parts[-2:]) == _CANONICAL_SOURCE_SIGNATURE
    template_asset_signature = (
        resolved.suffix.lower() == ".docx"
        and tuple(resolved.parts[-5:-1]) == _TEMPLATE_ASSETS_SIGNATURE
    )
    return (
        resolved == _CANONICAL_SOURCE
        or canonical_signature
        or template_asset_signature
        or (resolved.suffix.lower() == ".docx" and resolved.is_relative_to(_TEMPLATE_ASSETS))
    )


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成仅供内部回归的 legacy DOCX；非最终模板合规产物，禁止发送客户"
    )
    parser.add_argument("--brand", required=True, help="客户/品牌名称")
    parser.add_argument(
        "--package",
        choices=_PRESET_PACKAGES,
        default="geo_effect_assessment",
        help="套餐；缺省为已开展 GEO 的效果评测",
    )
    parser.add_argument(
        "--artifact-kind",
        choices=_ARTIFACT_KINDS,
        default="complete",
        help=(
            "输出制品；complete=完整报价单（默认），quote_table=仅报价单表格，"
            "query_appendix=仅查询附件（必须提供 --target-words）"
        ),
    )
    parser.add_argument("--website", help="客户官网；包含官网分析时必填")
    parser.add_argument(
        "--official-site-in-citations",
        choices=("yes", "no"),
        help="最小验证中是否已确认引用 URL 命中官网；缺省为条件待定",
    )
    parser.add_argument(
        "--official-site-citation-url",
        help="选择官网已命中时的一条引用证据 URL",
    )
    parser.add_argument(
        "--price",
        action="append",
        default=[],
        metavar="SERVICE_CODE=YUAN",
        help="逐项单价，可重复；例如 ranking_test=20000",
    )
    parser.add_argument(
        "--pending-prices",
        action="store_true",
        help="生成价格待确认的样稿；不能作为正式报价",
    )
    parser.add_argument("--commercial-note", default="", help="商务备注，最多 500 字")
    parser.add_argument("--target-words", type=Path, help="可选目标词 XLSX；上传后生成查询附件")
    parser.add_argument("--output", type=Path, help="输出 DOCX；缺省写入当前目录")
    parser.add_argument("--quote-date", type=date.fromisoformat, help="报价日期 YYYY-MM-DD")
    parser.add_argument("--model", help="GEO_RESEARCH_LLM_MODELS 中允许的模型，仅 XLSX 使用")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的输出文件")
    return parser.parse_args(argv)


def _price_cents(rows: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    known = {service.code for service in SERVICE_CATALOG}
    for row in rows:
        code, separator, raw_value = row.partition("=")
        if not separator or code not in known or code in result:
            raise ValueError(f"价格格式或服务代码无效：{row}")
        try:
            yuan = Decimal(raw_value)
        except InvalidOperation as exc:
            raise ValueError(f"价格不是有效金额：{row}") from exc
        if not yuan.is_finite() or yuan < 0 or yuan.as_tuple().exponent < -2:
            raise ValueError(f"价格须为非负金额且最多两位小数：{row}")
        cents = yuan * 100
        if cents != cents.to_integral_value() or cents > 999_999_999_999:
            raise ValueError(f"价格超出范围：{row}")
        result[code] = int(cents)
    return result


def _configuration(args: argparse.Namespace) -> QuotationConfiguration:
    official_site = (
        None
        if args.package == "minimum_validation" and args.official_site_in_citations is None
        else args.official_site_in_citations != "no"
    )
    package = PACKAGE_BY_CODE[args.package]
    quantities = dict(package.service_quantities)
    if args.package == "minimum_validation" and official_site is False:
        quantities.pop("official_site_audit", None)
    prices = _price_cents(args.price)
    if args.pending_prices and prices:
        raise ValueError("--pending-prices 与 --price 不能同时使用")
    if not args.pending_prices:
        missing = [code for code in quantities if code not in prices]
        extra = [code for code in prices if code not in quantities]
        if missing or extra:
            raise ValueError(
                "价格必须与套餐服务完全一致；"
                f"缺少={','.join(missing) or '无'}；多余={','.join(extra) or '无'}"
            )
    return QuotationConfiguration.model_validate(
        {
            "package_code": args.package,
            "artifact_kind": args.artifact_kind,
            "website_url": args.website or "",
            "official_site_in_citations": official_site,
            "official_site_citation_url": args.official_site_citation_url or "",
            "commercial_note": args.commercial_note,
            "pricing_status": "pending" if args.pending_prices else "priced",
            "service_quotes": [
                {
                    "service_code": code,
                    "quantity": quantity,
                    "unit_price_cents": prices.get(code),
                }
                for code, quantity in quantities.items()
            ],
        }
    )


def main() -> int:
    args = _arguments()
    if args.output is not None and _is_protected_template_output(args.output):
        print("拒绝写入最终模板真源或 quotations/assets 模板资产。", file=sys.stderr)
        return 2
    print(f"警告：{_NON_FINAL_NOTICE}", file=sys.stderr)
    try:
        configuration = _configuration(args)
        workbook_payload = args.target_words.read_bytes() if args.target_words else None
        result = generate_quotation(
            brand_name=args.brand,
            configuration=configuration,
            workbook_payload=workbook_payload,
            settings=get_settings(),
            quote_date=args.quote_date,
            requested_model=args.model,
        )
    except FileNotFoundError:
        print(f"目标词文件不存在：{args.target_words}", file=sys.stderr)
        return 2
    except (QuotationInputInvalid, TargetWorkbookInvalid, ValueError) as exc:
        print(f"输入无效：{exc}", file=sys.stderr)
        return 2
    except QuotationLlmDisabled:
        print("未配置 GEO_RESEARCH_LLM_API_KEY；请移除 XLSX 或配置模型。", file=sys.stderr)
        return 3
    except ResearchModelNotAllowed as exc:
        print(f"模型不在允许清单：{exc}", file=sys.stderr)
        return 2
    except QuotationGenerationFailed as exc:
        print(f"动态内容生成失败：{exc}", file=sys.stderr)
        return 4

    output = (args.output or Path.cwd() / result.metadata.filename).resolve()
    if _is_protected_template_output(output):
        print("拒绝写入最终模板真源或 quotations/assets 模板资产。", file=sys.stderr)
        return 2
    if output.exists() and not args.force:
        print(f"输出文件已存在；如需覆盖请加 --force：{output}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.payload)
    total = (
        f"￥{result.metadata.total_price_cents / 100:,.2f}"
        if result.metadata.total_price_cents is not None
        else "待商务确认"
    )
    maximum_total = (
        f"￥{result.metadata.maximum_total_price_cents / 100:,.2f}"
        if result.metadata.maximum_total_price_cents is not None
        else "待商务确认"
    )
    print(f"已生成内部回归{_ARTIFACT_LABELS[result.metadata.artifact_kind]}：{output}")
    print(_NON_FINAL_NOTICE)
    totals = (
        f"基础总价（不含条件项） {total}"
        if args.package == "minimum_validation" and args.official_site_in_citations is None
        else f"服务费总价 {total}"
    )
    if args.package == "minimum_validation" and args.official_site_in_citations is None:
        totals += f"；条件触发后最高总价 {maximum_total}"
    print(
        f"服务 {result.metadata.service_count} 项；{totals}；"
        f"目标词 {result.metadata.target_query_count} 条；"
        f"查询附件={'是' if result.metadata.query_appendix_included else '否'}；"
        f"SHA-256 {result.metadata.sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
