"""captcha-assist-v1 门控纯函数单测（workflows.definitions.collection.gate_captcha_pause）。

workflow 侧 ``workflow.patched("captcha-assist-v1")`` 每代只调一次（重放确定性），
判定逻辑全部沉淀在这个纯函数里，故三条关键语义可脱离 Temporal sandbox 直测：
未 patch 的历史重放丢弃 pause、畸形 pause 丢弃、五平台正常标注一律放行
（门只看 patch 不看 slug——2026-08-07 门放开）。
"""

from workflows.activities.collection import CaptchaPause
from workflows.definitions.collection import gate_captcha_pause


def _pause(resume_index: int = 1) -> CaptchaPause:
    return CaptchaPause(resume_index=resume_index, business_key="bk-2")


def test_patched_gate_passes_pause_regardless_of_platform() -> None:
    """patch 门内：正常 pause 标注原样放行（非豆包四平台的标注同口径）。"""
    pause = _pause(1)
    assert gate_captcha_pause(True, pause, items=3, results=3) is pause
    assert gate_captcha_pause(True, _pause(0), items=1, results=1) is not None


def test_unpatched_history_replay_drops_pause() -> None:
    """未打 captcha-assist-v1 补丁的历史重放：pause 一律丢弃，等长结果按
    旧语义全量落库（行为与补丁引入前逐字节一致）。"""
    assert gate_captcha_pause(False, _pause(1), items=3, results=3) is None
    assert gate_captcha_pause(False, None, items=3, results=3) is None


def test_malformed_pause_dropped() -> None:
    """adapter 契约违背（resume_index 越界 / 结果不等长）→ 不当 pause 处理，
    workflow 侧按旧语义全量落库。"""
    assert gate_captcha_pause(True, _pause(3), items=3, results=3) is None   # 越界
    assert gate_captcha_pause(True, _pause(-1), items=3, results=3) is None  # 负下标
    assert gate_captcha_pause(True, _pause(0), items=3, results=2) is None   # 不等长
    assert gate_captcha_pause(True, _pause(0), items=0, results=0) is None   # 空段
    assert gate_captcha_pause(True, None, items=3, results=3) is None        # 无标注
