"""run_service._task_matrix 平台×mode 能力过滤（20260810，用户拍板 deepseek 不测
专家模式、只测快速+搜索的思考关/开两种组合）：不支持的平台×mode 对在矩阵构建
时剔除（不产出必败任务），剔除对有日志记录；未知平台 slug 不过滤（dispatcher
诚实报 unsupported adapter）；全被剔空 → collection_matrix_empty。
"""

from __future__ import annotations

import json

import pytest

from api.geo_platform.collection.run_service import _task_matrix
from api.geo_platform.projects.models import MonitoringConfigVersion


def _config(
    models: list[str],
    modes: list[str],
    regions: list[str] | None = None,
    queries: list[str] | None = None,
) -> MonitoringConfigVersion:
    snapshot = {
        "query_groups": [
            {
                "name": "g",
                "items": [
                    {"text": text, "priority": index + 1}
                    for index, text in enumerate(queries or ["问题一"])
                ],
            }
        ],
        "regions": regions or ["北京"],
        "models": models,
        "modes": modes,
    }
    return MonitoringConfigVersion(
        pub_id="cfv_test",
        snapshot_hash="hash",
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
    )


class TestTaskMatrixModeCapabilities:
    def test_unsupported_platform_mode_pairs_dropped(self) -> None:
        config = _config(
            models=["doubao", "deepseek", "tongyi"], modes=["normal", "deep_think"]
        )
        tasks = _task_matrix(config)
        pairs = {(task.model, task.mode) for task in tasks}
        assert ("deepseek", "deep_think") in pairs
        assert ("doubao", "deep_think") in pairs
        assert ("tongyi", "deep_think") not in pairs  # 通义无 deep_think
        assert ("tongyi", "normal") in pairs
        # doubao×2 + deepseek×2 + tongyi×1 = 5 组合 × 1 题 × 1 地域
        assert len(tasks) == 5

    def test_unknown_model_slug_passes_through(self) -> None:
        # 未知 slug 不在能力表 → 不过滤（运行期由 dispatcher 诚实失败）
        tasks = _task_matrix(_config(models=["model-x"], modes=["whatever"]))
        assert [(task.model, task.mode) for task in tasks] == [("model-x", "whatever")]

    def test_all_filtered_raises_matrix_empty(self) -> None:
        with pytest.raises(ValueError, match="collection_matrix_empty"):
            _task_matrix(_config(models=["tongyi"], modes=["deep_think"]))

    def test_business_key_stable_for_kept_pairs(self) -> None:
        # 过滤不改变保留对的 business_key（与未过滤同口径 sha256）
        filtered = _task_matrix(
            _config(models=["deepseek", "yiyan"], modes=["normal", "deep_think"]))
        baseline = _task_matrix(_config(models=["deepseek"], modes=["normal", "deep_think"]))
        filtered_keys = {task.business_key for task in filtered}
        assert all(task.business_key in filtered_keys for task in baseline)
