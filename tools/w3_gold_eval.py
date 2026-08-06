#!/usr/bin/env python3
"""W3 拉踩检测金标准评测（只读，不写任何库）。

读 tests/fixtures/w3_gold_windows.jsonl（人工标注窗），逐窗走真实 LLM 判定
（GEO_AUDIT_LLM_*，缺省复用 GEO_RESEARCH_LLM_*，与 judge_run_disparagement
同口径同 prompt_version="disparage-v1"），做 verbatim 程序校验后与金标签比对，
输出准确率/拉踩召回/混淆矩阵。

达标线（规格 W3 验收建议值）：**overall accuracy ≥ 85%**；拉踩（disparagement=true）
的 precision/recall 一并打印供人工判断。validation_failure（quote 逐字校验未过）
按判错计并单列计数——模型连引用都写不对时判分不可信。

用法：
    .venv/bin/python tools/w3_gold_eval.py [--fixture PATH] [--limit N] [--json]
退出码：0 = 评测完成（达标与否看输出，不强行 fail，便于迭代期跑分留痕）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from geo_platform.config import get_settings

from domain.scoring.disparagement import ATTITUDES, validate_judgment
from workflows.activities.disparagement import (
    JudgeError,
    LlmJudgment,
    _ResponsesApiJudge,
)
from workflows.activities.source_audit import audit_llm_config_from_settings

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "w3_gold_windows.jsonl"


@dataclass(frozen=True)
class GoldEntry:
    entry_id: str
    split: str
    window_text: str
    target_brand: str
    known_brands: tuple[str, ...]
    gold_attitude: str
    gold_disparagement: bool


def load_gold(path: Path) -> list[GoldEntry]:
    entries: list[GoldEntry] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            entries.append(
                GoldEntry(
                    entry_id=str(raw["id"]),
                    split=str(raw["split"]),
                    window_text=str(raw["window_text"]),
                    target_brand=str(raw["target_brand"]),
                    known_brands=tuple(str(item) for item in raw["known_brands"]),
                    gold_attitude=str(raw["gold_attitude"]),
                    gold_disparagement=bool(raw["gold_disparagement"]),
                )
            )
            if entries[-1].gold_attitude not in ATTITUDES:
                raise ValueError(f"第 {line_no} 行 gold_attitude 非法")
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=_DEFAULT_FIXTURE)
    parser.add_argument("--limit", type=int, default=0, help="只评前 N 条（调试）")
    parser.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    args = parser.parse_args()

    entries = load_gold(args.fixture)
    if args.limit > 0:
        entries = entries[: args.limit]

    llm = audit_llm_config_from_settings(get_settings())
    if not llm.api_key:
        print(
            "GEO_AUDIT_LLM_API_KEY（含 GEO_RESEARCH_LLM_* 复用）未配置，无法评测",
            file=sys.stderr,
        )
        return 2
    judge = _ResponsesApiJudge(llm)

    total = 0
    correct = 0
    validation_failures = 0
    llm_errors = 0
    # attitude 混淆矩阵：gold -> predicted -> count
    confusion: dict[str, dict[str, int]] = {a: {b: 0 for b in ATTITUDES} for a in ATTITUDES}
    disp_tp = disp_fp = disp_fn = disp_tn = 0
    misses: list[dict[str, object]] = []

    for entry in entries:
        total += 1
        predicted: LlmJudgment | None = None
        note = ""
        try:
            predicted = judge.judge(
                window_text=entry.window_text,
                target_brand=entry.target_brand,
                known_brands=entry.known_brands,
            )
        except JudgeError as exc:
            llm_errors += 1
            note = f"llm_error: {exc}"
        if predicted is not None:
            failure = validate_judgment(
                predicted,
                window_text=entry.window_text,
                expected_target=entry.target_brand,
                known_brands=entry.known_brands,
            )
            if failure is not None:
                validation_failures += 1
                note = f"validation_failure: {failure}"
                predicted = None
        pred_attitude = predicted.attitude if predicted is not None else None
        pred_disparagement = predicted.disparagement if predicted is not None else None
        hit = (
            pred_attitude == entry.gold_attitude
            and pred_disparagement == entry.gold_disparagement
        )
        if hit:
            correct += 1
        else:
            misses.append(
                {
                    "id": entry.entry_id,
                    "split": entry.split,
                    "gold": [entry.gold_attitude, entry.gold_disparagement],
                    "pred": [pred_attitude, pred_disparagement],
                    "note": note,
                }
            )
        if pred_attitude in ATTITUDES:
            confusion[entry.gold_attitude][pred_attitude] += 1
        if pred_disparagement is not None:
            if entry.gold_disparagement and pred_disparagement:
                disp_tp += 1
            elif entry.gold_disparagement and not pred_disparagement:
                disp_fn += 1
            elif not entry.gold_disparagement and pred_disparagement:
                disp_fp += 1
            else:
                disp_tn += 1

    accuracy = correct / total if total else 0.0
    disp_precision = disp_tp / (disp_tp + disp_fp) if disp_tp + disp_fp else None
    disp_recall = disp_tp / (disp_tp + disp_fn) if disp_tp + disp_fn else None
    report = {
        "model": llm.model,
        "prompt_version": "disparage-v1",
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "target_accuracy": 0.85,
        "meets_target": accuracy >= 0.85,
        "validation_failures": validation_failures,
        "llm_errors": llm_errors,
        "disparagement_precision": (
            round(disp_precision, 4) if disp_precision is not None else None
        ),
        "disparagement_recall": round(disp_recall, 4) if disp_recall is not None else None,
        "disparagement_confusion": {
            "tp": disp_tp,
            "fp": disp_fp,
            "fn": disp_fn,
            "tn": disp_tn,
        },
        "attitude_confusion": confusion,
        "misses": misses,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"model={llm.model} prompt_version=disparage-v1")
        print(f"total={total} correct={correct} accuracy={accuracy:.2%} (达标线 85%)")
        print(f"validation_failures={validation_failures} llm_errors={llm_errors}")
        prec = f"{disp_precision:.2%}" if disp_precision is not None else "n/a"
        rec = f"{disp_recall:.2%}" if disp_recall is not None else "n/a"
        print(f"disparagement precision={prec} recall={rec}")
        print(f"disparagement 混淆: tp={disp_tp} fp={disp_fp} fn={disp_fn} tn={disp_tn}")
        print("attitude 混淆矩阵（gold -> pred）:")
        for gold_a in ATTITUDES:
            row = " ".join(f"{pred_a}={confusion[gold_a][pred_a]}" for pred_a in ATTITUDES)
            print(f"  {gold_a}: {row}")
        if misses:
            print(f"误判 {len(misses)} 条:")
            for miss in misses:
                print(
                    f"  {miss['id']} [{miss['split']}] gold={miss['gold']} "
                    f"pred={miss['pred']} {miss['note']}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
