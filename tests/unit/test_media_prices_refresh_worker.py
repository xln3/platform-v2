from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools import media_prices_refresh_worker as worker

_SUCCESS_SCRIPT = """\
import json, os, pathlib
base = pathlib.Path(os.environ["GEO_DATASETS_DIR"])
(base / "worker-result.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
print("worker completed")
"""


def _write_request(path: Path) -> None:
    path.write_text(
        json.dumps({"version": 1, "requested_at": "2026-08-18 17:00:00"}),
        encoding="utf-8",
    )


def test_worker_claims_and_consumes_durable_request(tmp_path: Path) -> None:
    script = tmp_path / "refresh.py"
    script.write_text(_SUCCESS_SCRIPT, encoding="utf-8")
    _write_request(tmp_path / worker.REQUEST_NAME)

    result = worker.run_once(datasets_dir=tmp_path, refresh_script=script)

    assert result == 0
    assert not (tmp_path / worker.REQUEST_NAME).exists()
    assert not (tmp_path / worker.RUNNING_REQUEST_NAME).exists()
    assert json.loads((tmp_path / "worker-result.json").read_text(encoding="utf-8")) == {
        "state": "done"
    }
    assert "worker completed" in (tmp_path / worker.REFRESH_LOG_NAME).read_text(encoding="utf-8")


def test_worker_resumes_interrupted_claim(tmp_path: Path) -> None:
    script = tmp_path / "refresh.py"
    script.write_text(_SUCCESS_SCRIPT, encoding="utf-8")
    _write_request(tmp_path / worker.RUNNING_REQUEST_NAME)

    assert worker.run_once(datasets_dir=tmp_path, refresh_script=script) == 0
    assert not (tmp_path / worker.RUNNING_REQUEST_NAME).exists()
    assert (tmp_path / "worker-result.json").exists()


def test_worker_retains_claim_when_pipeline_cannot_launch(tmp_path: Path, monkeypatch) -> None:
    _write_request(tmp_path / worker.REQUEST_NAME)

    def fail_to_launch(*args, **kwargs):
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(subprocess, "run", fail_to_launch)

    assert worker.run_once(datasets_dir=tmp_path, refresh_script=tmp_path / "refresh.py") == 1
    assert not (tmp_path / worker.REQUEST_NAME).exists()
    assert (tmp_path / worker.RUNNING_REQUEST_NAME).exists()
    status = json.loads((tmp_path / worker.REFRESH_STATUS_NAME).read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["message"].startswith("refresh_worker_launch_failed")
