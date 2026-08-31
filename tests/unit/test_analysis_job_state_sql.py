from __future__ import annotations

from typing import Any

from workflows.activities import analysis_jobs


class _Result:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any]:
        return self.row

    def one(self) -> dict[str, Any]:
        return self.row


class _Session:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.committed = False

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, _parameters: dict[str, Any]) -> _Result:
        self.statements.append(str(statement))
        if len(self.statements) == 1:
            return _Result({"pub_id": "ajb_test", "state": "queued", "attempt_count": 0})
        return _Result({"pub_id": "ajb_test", "state": "running", "attempt_count": 1})

    def commit(self) -> None:
        self.committed = True


def test_mark_analysis_job_casts_reused_state_parameter(monkeypatch: Any) -> None:
    session = _Session()
    monkeypatch.setattr(analysis_jobs, "WorkerSessionLocal", lambda: session)
    monkeypatch.setattr(analysis_jobs, "TenantRepository", lambda *_args: None)

    result = analysis_jobs._mark_analysis_job(
        analysis_jobs.AnalysisJobStateInput(
            tenant_pub_id="tnt_test",
            subject_type="answer",
            subject_pub_id="ans_test",
            analyzer_kind="answer_basic",
            policy_version="answer-basic-v1",
            state="running",
        )
    )

    update_sql = session.statements[1]
    assert "SET state=CAST(:state AS varchar)" in update_sql
    assert "WHEN CAST(:state AS varchar)='running'" in update_sql
    assert result["state"] == "running"
    assert session.committed is True
