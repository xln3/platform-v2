"""INV-1 合格性接线单测（2026-08-08，不打真 DB）。

覆盖：
- ``resolve_measurement_eligibility``：新路径（五元键存在）由
  measurement_eligible 真实计算并盖 eligible 标记；旧路径（无五元键）继承
  现状（缺省 true、dimensions 零增补——历史 metric dimensions_hash 不漂移）。
- fanout provenance 盖章（``_analysis_dimensions`` /
  ``_measurement_geo_provenance``）：结构四元 + geo 二元（env 声明出口省码；
  未声明 fail-loud=unverified；畸形映射 fail-closed 抛错）。
- metric_daily advisory 锁键=唯一约束列稳定拼接。
"""

from __future__ import annotations

import pytest
from geo_platform.analytics.service import _metric_daily_lock_key
from temporalio.exceptions import ApplicationError

from domain.scoring.eligibility import (
    ELIGIBILITY_PROVENANCE_KEYS,
    resolve_measurement_eligibility,
)
from workflows.activities.collection import (
    ENV_MEASUREMENT_EXIT_GB_MAP,
    CollectionTaskInput,
    _analysis_dimensions,
    _measurement_geo_provenance,
)

_VALID_PROVENANCE = {
    "captcha_mode": "not_challenged",
    "geo_source": "observed_gb_code",
    "account_source": "self_pool",
    "rate_policy": "pool_burn",
    "degraded_flag": "0",
    "observed_gb_code": "310000",
}


class TestResolveMeasurementEligibility:
    def test_new_path_all_limbs_pass(self) -> None:
        eligible, degraded, stamped = resolve_measurement_eligibility(
            {"model": "doubao", **_VALID_PROVENANCE}
        )
        assert eligible is True
        assert degraded is False
        assert stamped["eligible"] == "true"
        assert stamped["model"] == "doubao"
        assert stamped["observed_gb_code"] == "310000"

    @pytest.mark.parametrize(
        ("key", "bad_value"),
        [
            ("captcha_mode", "wall_captcha"),
            ("geo_source", "unverified"),
            ("account_source", "unknown"),
            ("rate_policy", "burst"),
            ("degraded_flag", "1"),
            ("observed_gb_code", ""),
        ],
    )
    def test_new_path_each_failing_limb_marks_ineligible(
        self, key: str, bad_value: str
    ) -> None:
        eligible, _degraded, stamped = resolve_measurement_eligibility(
            {**_VALID_PROVENANCE, key: bad_value}
        )
        assert eligible is False
        assert stamped["eligible"] == "false"

    def test_new_path_degraded_flag_garbage_fails_closed(self) -> None:
        eligible, degraded, _stamped = resolve_measurement_eligibility(
            {**_VALID_PROVENANCE, "degraded_flag": "not-a-number"}
        )
        assert eligible is False  # measurement_eligible 之外：degraded 判读也 fail-closed
        assert degraded is True

    def test_legacy_path_defaults_eligible_and_never_stamps(self) -> None:
        dimensions = {"model": "test", "region": "all", "mode": "normal"}
        eligible, degraded, stamped = resolve_measurement_eligibility(dimensions)
        assert eligible is True
        assert degraded is False
        assert stamped == dimensions  # 不补 eligible 键：历史 dimensions_hash 零漂移
        assert not any(key in stamped for key in ELIGIBILITY_PROVENANCE_KEYS)

    def test_legacy_path_honors_explicit_flags(self) -> None:
        eligible, degraded, _stamped = resolve_measurement_eligibility(
            {"eligible": "false", "degraded": "true"}
        )
        assert eligible is False
        assert degraded is True


class TestFanoutProvenanceStamping:
    def test_dimensions_carry_five_tuple_with_declared_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_MEASUREMENT_EXIT_GB_MAP, "doubao:310000,tongyi:110000")
        dimensions = _analysis_dimensions(
            CollectionTaskInput("bk", "q", "doubao", "上海", "normal", "doubao"),
            run_pub_id="run_x",
            config_version_pub_id="mcv_y",
        )
        assert dimensions["captcha_mode"] == "not_challenged"
        assert dimensions["degraded_flag"] == "0"
        assert dimensions["account_source"] == "self_pool"
        assert dimensions["rate_policy"] == "pool_burn"
        assert dimensions["geo_source"] == "observed_gb_code"
        assert dimensions["observed_gb_code"] == "310000"
        # 既有维度键不丢
        assert dimensions["query_text"] == "q"
        assert dimensions["model"] == "doubao"
        assert dimensions["run_pub_id"] == "run_x"
        assert dimensions["config_version_pub_id"] == "mcv_y"

    def test_undeclared_platform_exit_is_unverified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_MEASUREMENT_EXIT_GB_MAP, "doubao:310000")
        assert _measurement_geo_provenance("tongyi") == {
            "geo_source": "unverified",
            "observed_gb_code": "",
        }

    def test_missing_env_is_unverified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_MEASUREMENT_EXIT_GB_MAP, raising=False)
        assert _measurement_geo_provenance("doubao") == {
            "geo_source": "unverified",
            "observed_gb_code": "",
        }

    def test_malformed_map_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_MEASUREMENT_EXIT_GB_MAP, "doubao:shanghai")
        with pytest.raises(ApplicationError, match="measurement_exit_gb_map_invalid"):
            _measurement_geo_provenance("doubao")


def test_metric_daily_lock_key_is_unique_constraint_concatenation() -> None:
    import datetime

    key = _metric_daily_lock_key(
        tenant_pub_id="tnt_a",
        project_pub_id="prj_b",
        metric_date=datetime.date(2026, 8, 8),
        metric_name="mention_rate",
        dimensions_hash="ab" * 32,
        metric_version="metrics-v2",
        scorer_version="scorer-v2",
    )
    assert key == f"tnt_a|prj_b|2026-08-08|mention_rate|{'ab' * 32}|metrics-v2|scorer-v2"
