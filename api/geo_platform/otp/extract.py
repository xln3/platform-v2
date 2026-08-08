"""OTP 短信解析纯函数层（零 I/O，单测可裸调）。

移植自旧系统（2026-08-07 旧 geosys 退役，OTP 推送链整体迁入 V2）：

- 抽码级联 ``extract_otp_code`` ← ``server/proxyllm/webhook_otp_relay.py``
  （关键词引入 → 紧邻数字串 → 分组数字 → 无关键词时首个独立 6 位串；
  逐行移植，含全部反诈样板/热线/条款断句防护）。
- 推送归一 ``normalize_push`` / 纯文本模板 ``parse_flat_push`` /
  卡槽反解 ``phone_from_slot`` / 平台【品牌】``platform_of`` ←
  ``server/geosys/otp_ingest.py``（docstring 即契约）。
- ``PHONE_RE`` / ``mask_phone`` ← ``server/geosys/phone.py``。

**舍弃项：LLM 抽码不移植**（旧 ``otp_llm.extract_sms_fields`` 优先、正则回落）。
V2 首版 regex-only：旧链 LLM 层带防幻觉校验（码必须数字边界独立出现在正文），
正则级联本就是其兜底且生产命中稳定；LLM 增强留作后续独立任务。
"""

from __future__ import annotations

import re
import urllib.parse

PHONE_RE = re.compile(r"^1[0-9]{10}$")  # 11 位中国大陆手机号（ASCII-only，\d 会吞全角数字）


def mask_phone(phone: str) -> str:
    """PII 掩码：前 3 + ``***`` + 后 4（不足 7 位一律 ``***``）。日志/响应统一口径。"""
    return f"{phone[:3]}***{phone[-4:]}" if phone and len(phone) >= 7 else "***"


# ---- 平台【品牌】识别 ---------------------------------------------------------

_PLATFORM_RE = re.compile(r"【([^】]{1,24})】")  # 【豆包】/【网易新闻】/…


def platform_of(raw: str) -> str:
    """中文 OTP 短信的 【品牌】前缀（多平台审计/排障）；无则 ''。"""
    m = _PLATFORM_RE.search(raw or "")
    return m.group(1).strip() if m else ""


# ---- 卡槽备注反解真实手机号 ----------------------------------------------------

# 卡槽备注/SIM 信息里嵌的真实手机号（旧系统用户 2026-07-15 方案：不信 body 的 phone，
# 改从 SIM 卡槽备注解析——双卡机 ROM 层 SIM 识别不准会把 body phone 写错，但转发器
# 上报的卡槽备注是 SIM 硬件级真值，如 'SIM1_中国联通_+8613121622231'）。取第一个
# 11 位中国手机号，容忍 +86/86 前缀（(?!\d) 锚到号尾，免疫 '86…' 前缀把窗口切错）。
_SLOT_PHONE_RE = re.compile(r"(?:\+?86)?(1[0-9]{10})(?![0-9])")


def phone_from_slot(*vals: str) -> str:
    """从卡槽备注/SIM 信息串里解 11 位真号；找不到 → ''。"""
    for v in vals:
        m = _SLOT_PHONE_RE.search(str(v or ""))
        if m:
            return m.group(1)
    return ""


# ---- 纯文本期望格式（text/plain 旧模板） ---------------------------------------

# 最简 TEXT 模板 "{{DEVICE_NAME}}收到{{SMS}}" 发来的是裸串非 JSON。11 位手机号打头
# （路由键，锚定在开头，JSON body 绝不会误命中），其余原样留 raw。
_FLAT_PHONE_RE = re.compile(r"^(?:\+?86)?(1[0-9]{10})")
_LEAD_VERB_RE = re.compile(r"^(收到|received|recv)[\s:：,，]*", re.IGNORECASE)
# 期望格式在验证码行后追加 "<时刻>;<地点>;<ip>"（行以 。 结束）。仅在这个显式
# ;-分隔尾串里抓，绝不吞码。
_FLAT_META_RE = re.compile(r"。\s*([^。;\n]+;[^。;\n]+(?:;[^。;\n]+)?)\s*$")
_META_KEYS = ("recv_time", "recv_location", "recv_ip")


def _flat_meta(body: str) -> dict[str, str]:
    """尽力抓 '<时刻>;<地点>;<ip>' 尾串；无显式 ;-分隔尾串则空（绝不影响 phone/code）。"""
    m = _FLAT_META_RE.search(body or "")
    if not m:
        return {}
    parts = [p.strip() for p in m.group(1).split(";") if p.strip()]
    return {k: v for k, v in zip(_META_KEYS, parts, strict=False)}  # 尾串可只有 2 段


def parse_flat_push(body: str) -> dict[str, object] | None:
    """解析非 JSON 的纯文本推送（期望格式）::

        <11位手机号>[收到]【平台】<短信正文，含验证码>[。<时刻>;<地点>;<ip>]

    手机号（可带 +86）必须打头——它是路由键。其余作为 raw 返回，好让
    ``extract_otp_code`` 与平台/meta 正则都看到真短信而非路由前缀。无打头
    11 位手机号 → None（调用方回落 unrouted 软收）。纯函数。"""
    text = (body or "").strip()
    # SmsForwarder 常以 application/x-www-form-urlencoded 发送→中文正文被 URL 编码。
    # 手机号/验证码是 ASCII 数字不被编码，但编码后的中文 hex 尾字节会混进码旁让抽码
    # 失灵（旧链 live 逮到：…%E7%A0%81123456 → "81123456" 抽不出 6 位）。含 %XX 则整体
    # URL 解码；解码失败/畸形→退回原文（绝不因此丢这条推送）。
    if "%" in text:
        try:
            text = urllib.parse.unquote(text).strip()
        except Exception:  # noqa: BLE001
            pass
    m = _FLAT_PHONE_RE.match(text)
    if not m:
        return None
    phone = m.group(1)
    rest = _LEAD_VERB_RE.sub("", text[m.end():].lstrip())
    return {"phone": phone, "raw": rest,
            "platform": platform_of(rest), "meta": _flat_meta(text), "from": ""}


# ---- 三种推送形态归一 ------------------------------------------------------------

def normalize_push(
    json_data: dict[str, object] | None,
    form: dict[str, str] | None,
    body_text: str,
) -> dict[str, object]:
    """把 SmsForwarder 三种形态（JSON ``{"slot","sms"}`` / 表单 / 纯文本期望格式）
    归一成一条记录。纯函数（无 I/O）。优先级：结构化字段（JSON/表单）胜；纯文本
    解析仅在没拿到结构化 11 位手机号时兜底。"""
    def field(*names: str) -> str:
        for n in names:
            for src in (json_data or {}, form or {}):
                v = src.get(n)
                if v:
                    return str(v)
        return ""

    phone = field("phone", "device", "device_name").strip()
    raw = field("raw", "content", "sms", "text", "msg")
    sender = field("from", "sender")
    code_hint = field("code").strip()
    # T-39 语音验证码通道：显式 code_source='voice'（人工听写推送）——只认这一个
    # 词表值，其余按短信路处理。
    code_source = field("code_source", "source").strip()
    # 显式 platform 字段（body）胜，否则内容【品牌】自动识别。
    platform = field("platform").strip() or platform_of(raw)
    meta: dict[str, str] = {}

    if not PHONE_RE.match(phone) or not (raw or "").strip():
        flat = parse_flat_push(body_text)
        if flat:
            if not PHONE_RE.match(phone):
                phone = str(flat["phone"])
            raw = raw if (raw or "").strip() else str(flat["raw"])
            platform = platform or str(flat["platform"])
            flat_meta = flat["meta"]
            meta = meta or (dict(flat_meta) if isinstance(flat_meta, dict) else {})
            sender = sender or str(flat["from"])
    # SIM 槽/子ID 溯源（双卡机 SIM 错标诊断 + 归属对账）：SmsForwarder JSON 模板可带
    # {{CARD_SLOT}}/{{SIM_INFO}}/{{SUB_ID}}。落 meta→文件记录 + 台账，据此可对账
    # 「路由手机号 vs 实收 SIM 槽」是否一致——ROM 层 SIM 识别不准会把 A 卡短信标成
    # B 卡，这里把转发器自报的槽记下来，服务端就能发现并审计这种错标（而非盲信）。
    meta = dict(meta or {})
    for _mk, _aliases in (("sim_slot", ("slot", "card_slot", "sim_slot", "simslot")),
                          ("sim_info", ("siminfo", "sim_info")),
                          ("sub_id", ("subid", "sub_id", "subscription", "subscription_id"))):
        _v = field(*_aliases)
        if _v:
            meta[_mk] = _v
    return {"phone": phone, "raw": raw, "code_hint": code_hint,
            "from": sender, "platform": platform, "meta": meta, "code_source": code_source}


# ---- 抽码级联（移植自 proxyllm/webhook_otp_relay.extract_otp_code） --------------

# 引入（或少数情况下跟随）验证码的中英文关键词——用来把真码从无关数字串（订单号、
# 金额、热线、日期）里挑出来。登录码/安全码覆盖「本次登录码XXXXXX」式结尾。
_OTP_KEYWORDS = re.compile(
    r"验证码|校验码|动态码|动态密码|动态口令|口令|授权码|登录码|安全码|"
    r"verification\s*code|verification|verify|\bcode\b|\botp\b",
    re.IGNORECASE,
)
# 关键词被这些「不要泄露/绝不会索要」词贴身包住时是反诈样板（"请勿泄露验证码"），
# 不引入码，附近数字串是噪声。只在贴身窗内查，真码坐在关键词和句尾"请勿泄露"
# 之间绝不误伤。
_LEAK_BEFORE = ("索要", "泄露", "告知", "提供", "透露", "外泄",
                "谨防", "不会", "不要", "切勿", "勿向")
_LEAK_AFTER = ("请勿", "勿", "保护", "外泄", "谨防", "切勿")
_EN_BOILERPLATE = re.compile(
    r"(never|don'?t|do\s+not|won'?t|will\s+not|not)\b[^.\n]{0,30}?"
    r"\b(ask|share|give|email|text|request|disclose|provide)", re.IGNORECASE)
# 码在关键词之前时（"778811 是您的验证码"）必须跨这些合法连接词——否则
# 「标签+数字」（"订单尾号480913，验证码…"）会被误当码。
_BEFORE_CONNECTOR = re.compile(r"是|为|的|您的|：|:|\bis\b|\byour\b", re.IGNORECASE)
# 数字与后续关键词之间有子句断点 → 分属两句（"活动编号998877 已生成。您的验证码…"）。
_CLAUSE_BREAK = re.compile(r"[。！？；!?;\n]")

_WINDOW = 24  # 关键词之后扫多少字符找它引入的码
_BEFORE_GAP = 20  # 码与后续关键词之间的最大间隔

# ASCII [0-9] ONLY（默认 UNICODE \d 也匹配全角１２３４５６，登录键盘打不出）。
# finditer 最大连续段 → 长数字串（11 位手机号、14 位订单号）的子串绝不会被误当
# 独立码——这就是「6 位数字边界校验防幻觉」。
_DIGIT_RUN = re.compile(r"[0-9]+")
_MIN_LEN, _MAX_LEN = 4, 8

# 被「打电话给咱」语境贴住的数字串是热线/电话不是码（"客服热线95511"）。
_HOTLINE_RE = re.compile(
    r"热线|致电|拨打|来电|专线|咨询电话|联系电话|hotline|\bcall\b", re.IGNORECASE)

# 转发器附带的 code hint（兜底，须被短信正文数字边界佐证）。
HINT_RE = re.compile(r"^[0-9]{4,8}$")


def standalone(code: str, raw: str) -> bool:
    """``code`` 作为独立数字串（数字边界）真出现在 ``raw`` 里——防把运单号/订单号
    一段当验证码（旧链也用它做 LLM 防幻觉校验，V2 regex-only 下服务 hint 兜底）。"""
    return bool(code) and bool(re.search(r"(?<![0-9])" + re.escape(code) + r"(?![0-9])", raw or ""))


def _runs(text: str) -> list[tuple[int, int, str]]:
    """所有可信度长度的最大 ASCII 数字段，(start, end, digits)。最大段 → 14 位
    订单号里的 6 位子串不会被报出来。"""
    return [(m.start(), m.end(), m.group())
            for m in _DIGIT_RUN.finditer(text)
            if _MIN_LEN <= len(m.group()) <= _MAX_LEN]


def _is_boilerplate_kw(text: str, start: int, end: int) -> bool:
    """True = 这个关键词出现是「请勿泄露验证码」式反诈样板，不引入码。"""
    if any(tok in text[max(0, start - 7):start] for tok in _LEAK_BEFORE):
        return True
    after = text[end:end + 6]
    if any(after.startswith(tok) for tok in _LEAK_AFTER):
        return True
    if _EN_BOILERPLATE.search(text[max(0, start - 32):start]):
        return True
    return False


def _intro_keywords(text: str) -> list[tuple[int, int]]:
    """所有**引入码**的关键词 (start, end)（样板已排除）。"""
    return [(m.start(), m.end()) for m in _OTP_KEYWORDS.finditer(text)
            if not _is_boilerplate_kw(text, m.start(), m.end())]


def _candidates(text: str) -> list[tuple[int, int, str]]:
    """候选码 (gap_to_keyword, position, digits)：每个与引入关键词相邻的最大数字段
    ——关键词后 ``_WINDOW`` 内第一段，或跨合法连接词在关键词之前的那段。"""
    runs = _runs(text)
    intro = _intro_keywords(text)

    def _hotline(rs: int) -> bool:
        return bool(_HOTLINE_RE.search(text[max(0, rs - 8):rs]))

    out: list[tuple[int, int, str]] = []
    for kstart, kend in intro:
        # after：关键词后窗内第一段数字（且与关键词之间无其他数字）。
        for rs, _re, dig in runs:
            if kend <= rs <= kend + _WINDOW and not any(ch.isdigit() for ch in text[kend:rs]):
                if not _hotline(rs):
                    out.append((rs - kend, rs, dig))
                break
        # before："778811 是您的验证码" —— 码在关键词前，跨短的、无数字的、
        # 子句内部、带引入连接词（是/为/的/is/your/:）的间隔。
        for rs, re_, dig in runs:
            gap = kstart - re_
            if (0 <= gap <= _BEFORE_GAP
                    and not any(ch.isdigit() for ch in text[re_:kstart])
                    and _BEFORE_CONNECTOR.search(text[re_:kstart])
                    and not _CLAUSE_BREAK.search(text[re_:kstart])):
                out.append((gap, rs, dig))
    return out


def _grouped_candidate(text: str, length: int) -> str | None:
    """处理关键词后空格/短横分组码（"验证码 12 34 56" → 123456）。仅当短窗内真有
    数字-分隔-数字模式时才触发，绝不远扫无关短串（如句尾 客服热线95511）。"""
    for _kstart, kend in _intro_keywords(text):
        window = text[kend:kend + 18]
        if not re.search(r"[0-9][ \t\-]+[0-9]", window):  # 真分组才触发
            continue
        collapsed = re.sub(r"(?<=[0-9])[ \t\-]+(?=[0-9])", "", window)
        m = (re.search(rf"(?<![0-9])([0-9]{{{length},{_MAX_LEN}}})(?![0-9])", collapsed)
             or re.search(rf"(?<![0-9])([0-9]{{{_MIN_LEN},{length}}})(?![0-9])", collapsed))
        if m:
            return m.group(1)
    return None


def extract_otp_code(raw: str | None, *, length: int = 6) -> str | None:
    """从 OTP 短信正文抽验证码。纯函数（无 I/O）。ASCII-only。

    判定顺序：
      1. 与引入关键词相邻的数字段（样板关键词已排除）。优先 ``length`` 位
         （豆包/多数国内平台为 6 位）；否则取离关键词最近的（4 位码贴着
         「验证码为」胜过 13 字符外的 5 位热线）。平手 → 最后提到的赢
         （"旧验证码…新验证码…" → 新的）。
      2. 关键词旁的空格/短横分组段（"验证码 12 34 56"）。
      3. 仅当全文**没有任何**码关键词时：第一个独立 ``length`` 位数字段
         （"豆包登录 246813"）。有关键词但都没引入相邻码 → 返回 None——那是
         「请勿泄露验证码」式通知/订单提醒，不是发码。
    """
    text = raw or ""
    if not text:
        return None

    cands = _candidates(text)  # pass 1
    if cands:
        exact = [c for c in cands if len(c[2]) == length]
        if exact:
            # 同长度（6 位）候选里最后提到的赢（"旧验证码…新验证码…"）。
            return max(exact, key=lambda c: c[1])[2]
        # 无 6 位：取离关键词最近的；平手 → 最后提到的。
        return min(cands, key=lambda c: (c[0], -c[1]))[2]

    grouped = _grouped_candidate(text, length)  # pass 2
    if grouped:
        return grouped

    if not _OTP_KEYWORDS.search(text):  # pass 3（无关键词）
        for _rs, _re, dig in _runs(text):
            if len(dig) == length:
                return dig
    return None
