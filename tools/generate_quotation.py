#!/usr/bin/env python3
"""从品牌名称和目标词 XLSX 一键生成 GEO 报价单。"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.config import get_settings  # noqa: E402
from geo_platform.intake.research import ResearchModelNotAllowed  # noqa: E402
from geo_platform.quotations.generator import (  # noqa: E402
    QuotationGenerationFailed,
    QuotationLlmDisabled,
)
from geo_platform.quotations.service import (  # noqa: E402
    QuotationInputInvalid,
    generate_quotation,
)
from geo_platform.quotations.xlsx import TargetWorkbookInvalid  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 GEO 验证服务报价单 DOCX")
    parser.add_argument("--brand", required=True, help="客户品牌名称")
    parser.add_argument("--target-words", required=True, type=Path, help="优化目标词 XLSX")
    parser.add_argument("--output", type=Path, help="输出 DOCX；缺省写入当前目录")
    parser.add_argument("--quote-date", type=date.fromisoformat, help="报价日期 YYYY-MM-DD")
    parser.add_argument("--model", help="GEO_RESEARCH_LLM_MODELS 中允许的模型")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的输出文件")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        workbook_payload = args.target_words.read_bytes()
        result = generate_quotation(
            brand_name=args.brand,
            workbook_payload=workbook_payload,
            settings=get_settings(),
            quote_date=args.quote_date,
            requested_model=args.model,
        )
    except FileNotFoundError:
        print(f"目标词文件不存在：{args.target_words}", file=sys.stderr)
        return 2
    except (QuotationInputInvalid, TargetWorkbookInvalid) as exc:
        print(f"输入无效：{exc}", file=sys.stderr)
        return 2
    except QuotationLlmDisabled:
        print("未配置 GEO_RESEARCH_LLM_API_KEY，无法生成品牌化 Query 内容。", file=sys.stderr)
        return 3
    except ResearchModelNotAllowed as exc:
        print(f"模型不在允许清单：{exc}", file=sys.stderr)
        return 2
    except QuotationGenerationFailed as exc:
        print(f"动态内容生成失败：{exc}", file=sys.stderr)
        return 4

    output = (args.output or Path.cwd() / result.metadata.filename).resolve()
    if output.exists() and not args.force:
        print(f"输出文件已存在；如需覆盖请加 --force：{output}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.payload)
    print(f"已生成：{output}")
    print(
        f"目标词 {result.metadata.target_query_count} 条；"
        f"附录二 {result.metadata.selected_query_count} 条；"
        f"附录三 {result.metadata.opportunity_count} 条；"
        f"SHA-256 {result.metadata.sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
