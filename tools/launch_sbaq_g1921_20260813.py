"""Launch the SBAQ Appendix III G19-G21 collection matrix.

Scope is fixed to the quotation's first three Appendix III groups (12 verbatim
queries), deep_think mode, Doubao/DeepSeek/Yiyan, Beijing/Shanghai, and two
independent samples.  Each platform-region leg receives its own frozen config so
the operator can respect the four-browser resource ceiling and avoid two runs
contending for the same resident browser.

Examples (mint a short-lived acceptance session first):

  .venv/bin/python tools/launch_sbaq_g1921_20260813.py --verify
  .venv/bin/python tools/launch_sbaq_g1921_20260813.py --freeze
  .venv/bin/python tools/launch_sbaq_g1921_20260813.py \
    --launch 1 --legs doubao-bj,doubao-sh,deepseek-bj,yiyan-bj
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from launch_sbaq_formal_20260813 import GROUPS_APPENDIX3  # noqa: E402

BASE = "https://127.0.0.1:8443"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
TOKEN_FILE = Path("/tmp/s04-acceptance-token")
CONFIG_STORE = Path("/tmp/sbaq-g1921-20260813-configs.json")
DATE_TAG = "20260813"

GROUPS = GROUPS_APPENDIX3[:3]
LEGS: dict[str, tuple[str, str]] = {
    "doubao-bj": ("doubao", "北京"),
    "doubao-sh": ("doubao", "上海"),
    "deepseek-bj": ("deepseek", "北京"),
    "deepseek-sh": ("deepseek", "上海"),
    "yiyan-bj": ("yiyan", "北京"),
    "yiyan-sh": ("yiyan", "上海"),
}


def _query_groups() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "items": [
                {"text": question, "priority": index} for index, question in enumerate(questions, 1)
            ],
        }
        for name, questions in GROUPS
    ]


def verify() -> None:
    if len(GROUPS) != 3 or any(len(questions) != 4 for _, questions in GROUPS):
        raise SystemExit("G19-G21 scope must contain exactly 3 groups x 4 queries")
    if len({question for _, questions in GROUPS for question in questions}) != 12:
        raise SystemExit("G19-G21 contains duplicate query text")
    print("verify ok: Appendix III G19-G21, 3 groups, 12 unique verbatim queries")


def _client() -> httpx.Client:
    token = TOKEN_FILE.read_text().strip()
    return httpx.Client(
        base_url=BASE,
        verify=False,
        trust_env=False,
        cookies={"__Host-geo_session": token},
        timeout=60,
    )


def freeze_leg(client: httpx.Client, leg: str) -> str:
    model, region = LEGS[leg]
    response = client.post(
        f"/api/v2/projects/{PROJECT}/config/freeze",
        headers={"Idempotency-Key": f"sbaq-g1921-cfg-{leg}-{DATE_TAG}"},
        json={
            "query_groups": _query_groups(),
            "regions": [region],
            "models": [model],
            "modes": ["deep_think"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        },
    )
    response.raise_for_status()
    return str(response.json()["pub_id"])


def launch_leg(client: httpx.Client, leg: str, config_pub_id: str, sample: int) -> str:
    response = client.post(
        "/api/v2/collection/runs",
        headers={"Idempotency-Key": f"sbaq-g1921-run-{leg}-s{sample}-{DATE_TAG}"},
        json={
            "project_pub_id": PROJECT,
            "config_version_pub_id": config_pub_id,
            "requires_intervention": False,
        },
    )
    response.raise_for_status()
    return str(response.json()["workflow_id"]).rsplit("/", 1)[-1]


def _legs(raw: str | None) -> list[str]:
    selected = list(LEGS) if raw is None else [value.strip() for value in raw.split(",")]
    if not selected or len(selected) != len(set(selected)):
        raise SystemExit("--legs must contain unique, comma-separated leg names")
    unknown = sorted(set(selected) - set(LEGS))
    if unknown:
        raise SystemExit(f"unknown legs: {','.join(unknown)}")
    if len(selected) > 4:
        raise SystemExit("refusing to launch more than four browser legs at once")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--launch", type=int, choices=(1, 2))
    parser.add_argument("--legs", help="comma-separated subset; maximum four")
    args = parser.parse_args()

    if args.verify:
        verify()
        if not args.freeze and args.launch is None:
            return
    if not args.freeze and args.launch is None:
        parser.error("choose --verify, --freeze, or --launch")

    selected = _legs(args.legs)
    with _client() as client:
        client.get("/api/v2/identity/session").raise_for_status()
        configs = json.loads(CONFIG_STORE.read_text()) if CONFIG_STORE.exists() else {}
        if args.freeze:
            for leg in selected:
                configs[leg] = freeze_leg(client, leg)
            CONFIG_STORE.write_text(json.dumps(configs, ensure_ascii=False, sort_keys=True))
            print(json.dumps({"configs": configs}, ensure_ascii=False))
        if args.launch is not None:
            missing = sorted(set(selected) - set(configs))
            if missing:
                raise SystemExit(f"freeze configs first: {','.join(missing)}")
            runs = {
                leg: launch_leg(client, leg, str(configs[leg]), args.launch) for leg in selected
            }
            print(
                json.dumps(
                    {"sample": args.launch, "runs": runs},
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
