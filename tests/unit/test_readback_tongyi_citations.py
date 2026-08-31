from __future__ import annotations

import json
from hashlib import sha256

import pytest

from scripts.readback_tongyi_citations import (
    _citations_from_turn,
    _target_ids_from_har,
    _validated_turn,
)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def test_target_ids_distinguish_session_from_request(tmp_path) -> None:
    har_path = tmp_path / "answer.har.json"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": "https://chat2.qianwen.com/api/v2/chat",
                                "headers": [
                                    {"name": "x-chat-id", "value": "b" * 32},
                                    {
                                        "name": "Referer",
                                        "value": f"https://www.qianwen.com/chat/{'a' * 32}",
                                    },
                                ],
                            }
                        },
                        {
                            "request": {
                                "method": "GET",
                                "url": "https://example.test/asset",
                                "headers": [
                                    {
                                        "name": "referer",
                                        "value": f"https://www.qianwen.com/chat/{'a' * 32}",
                                    }
                                ],
                            }
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert _target_ids_from_har(har_path) == ("a" * 32, "b" * 32)


def test_validated_turn_checks_session_request_and_query_hash() -> None:
    target = {
        "session_id": "a" * 32,
        "request_id": "b" * 32,
        "query_sha256": _digest("原问题"),
    }
    turn = {
        "session_id": "a" * 32,
        "req_id": "b" * 32,
        "request_messages": [{"content": " 原问题 "}],
        "response_messages": [],
    }

    assert _validated_turn({"code": 0, "data": {"list": [turn]}}, target) is turn

    with pytest.raises(ValueError, match="query hash"):
        _validated_turn(
            {
                "code": 0,
                "data": {
                    "list": [
                        {
                            **turn,
                            "request_messages": [{"content": "另一问题"}],
                        }
                    ]
                },
            },
            target,
        )


def test_citations_keep_resolved_cards_and_report_missing_url() -> None:
    turn = {
        "response_messages": [
            {
                "meta_data": {
                    "sources": [
                        {
                            "content": {
                                "list": [
                                    {
                                        "raw_url": "https://one.example/article",
                                        "url": "https://redirect.example/one",
                                        "title": "第一条",
                                        "summary": "摘要一",
                                    },
                                    {"title": "只有标题"},
                                    {
                                        "url": "https://one.example/article",
                                        "title": "重复 URL 的第三张卡",
                                        "summary": "摘要三",
                                    },
                                ]
                            }
                        }
                    ]
                }
            }
        ]
    }

    citations, unresolved = _citations_from_turn(turn)

    assert unresolved == [2]
    assert [row["platform_ordinal"] for row in citations] == [1, 3]
    assert [row["url"] for row in citations] == [
        "https://one.example/article",
        "https://one.example/article",
    ]
    assert citations[0]["cited_text"] == "摘要一"
