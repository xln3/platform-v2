"""W3 金标准 fixture 自检：条数/标签合法/窗口非空/正反边界构成达标。

规格 W3 验收：标注小样本 ≥30 窗，正（拉踩=true）/反/边界各 ≥8 条，
每条须写标注依据（note）。本测试只读 fixture，不调 LLM。
"""

from __future__ import annotations

import json
from pathlib import Path

from domain.scoring.disparagement import ATTITUDES

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "w3_gold_windows.jsonl"
_SPLITS = ("positive", "negative", "boundary")


def _entries() -> list[dict[str, object]]:
    with _FIXTURE.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_gold_fixture_size_and_composition() -> None:
    entries = _entries()
    assert len(entries) >= 30, f"金标准须 ≥30 窗，当前 {len(entries)}"
    by_split = {split: 0 for split in _SPLITS}
    for entry in entries:
        by_split[str(entry["split"])] += 1
    for split in _SPLITS:
        assert by_split[split] >= 8, f"{split} 组须 ≥8 条，当前 {by_split[split]}"


def test_gold_fixture_labels_valid() -> None:
    ids: set[str] = set()
    for entry in _entries():
        entry_id = str(entry["id"])
        assert entry_id not in ids, f"id 重复: {entry_id}"
        ids.add(entry_id)
        assert str(entry["split"]) in _SPLITS
        assert str(entry["gold_attitude"]) in ATTITUDES
        assert isinstance(entry["gold_disparagement"], bool)
        assert str(entry["window_text"]).strip(), "窗口文本非空"
        assert str(entry["target_brand"]).strip()
        known = entry["known_brands"]
        assert isinstance(known, list) and all(str(b).strip() for b in known)
        assert str(entry["target_brand"]) in [str(b) for b in known]
        assert str(entry["note"]).strip(), "每条须写标注依据"
        # 拉踩=true 的金标签必须 attitude=negative（与程序校验同规则）
        if entry["gold_disparagement"]:
            assert entry["gold_attitude"] == "negative", entry_id


def test_gold_fixture_positive_split_all_disparagement() -> None:
    # positive 组=拉踩正例；negative 组=明确非拉踩；boundary 组标签各异（难点样本）
    for entry in _entries():
        if entry["split"] == "positive":
            assert entry["gold_disparagement"] is True, entry["id"]
        if entry["split"] == "negative":
            assert entry["gold_disparagement"] is False, entry["id"]
