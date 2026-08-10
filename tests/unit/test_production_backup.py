"""production_backup.runtime_state 组件单元测试（全 fake：不碰 docker/PG/CH/MinIO）。

只覆盖新增的「durable 运行态文件」组件：逐字节复制 + sha256 入 manifest；
源文件缺失 = 跳过而非失败（OTP 注册表在首次注册前合法不存在）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import production_backup


@pytest.fixture()
def dest_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "snap"
    dest.mkdir()
    return dest


def test_runtime_state_copies_registry_verbatim(
    dest_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = tmp_path / "reg.json"
    payload = [{"phone": "13912345678", "carrier": "联通", "slot": "eSIM",
                "remark": "eSIM_联通_+8613912345678", "ts": 1.0}]
    reg.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GEO_OTP_REGISTRY_PATH", str(reg))

    artifacts: list[dict] = []
    production_backup.backup_runtime_state(dest_dir, artifacts)

    out = dest_dir / "runtime-reg.json"
    assert out.read_bytes() == reg.read_bytes()  # 逐字节一致
    assert artifacts == [{
        "file": "runtime-reg.json",
        "component": "runtime_state",
        "bytes": len(reg.read_bytes()),
        "sha256": hashlib.sha256(reg.read_bytes()).hexdigest(),
        "detail": f"verbatim copy of {reg} (durable operator state)",
    }]


def test_runtime_state_missing_file_is_skip_not_failure(
    dest_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_OTP_REGISTRY_PATH", str(tmp_path / "nope.json"))
    artifacts: list[dict] = []
    production_backup.backup_runtime_state(dest_dir, artifacts)  # 不抛异常
    assert artifacts == []
    assert list(dest_dir.iterdir()) == []


def test_runtime_state_default_path_under_platform_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺省路径与 otp/router.py 的 _DEFAULT_REGISTRY_PATH 同处（runtime/ 下）。"""
    monkeypatch.delenv("GEO_OTP_REGISTRY_PATH", raising=False)
    files = production_backup.runtime_state_files()
    assert [p.name for p in files] == ["otp_registered_numbers.json"]
    assert files[0].parent.name == "runtime"
