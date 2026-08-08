"""LLM 抽取结果缓存：runtime/ 下的 JSON 文件缓存（V2 不落 PG——禁新表/禁 migration）。

键 = sha256(domain + "\\n" + 答案全文)——**domain 必在键内**：抽取 prompt 随 domain 变，
异 domain 缓存绝不当命中（旧库律所事故同型隐患；旧库靠 brandrank_extract.domain 列
隔离，V2 用哈希键达到同一语义）。同 domain 同文本 → 命中，重跑零 LLM 成本。

条目形状::

    {"status": "ok"|"failed", "brands": [...], "model": str,
     "error": str|None, "domain": str, "extracted_at": iso8601}

命中口径（照旧库 store/api 语义）：仅 status="ok" 的条目算命中；failed 条目视为未命中
（下次请求重试并覆盖）——failed 条目留存仅为审计线索。

纪律：写=临时文件 + os.replace 原子换名（并发重复请求至多重复抽，绝不产坏文件）；
读=坏 JSON/形状不符 → 未命中（诚实重抽，不猜不补）。
缓存目录默认 ``<platform-v2>/runtime/brandrank-extract``，env
``GEO_BRANDRANK_EXTRACT_CACHE_DIR`` 可覆盖（测试指 tmp_path）。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "runtime" / "brandrank-extract"


def cache_dir() -> Path:
    """缓存目录：env 覆盖优先，缺省 platform-v2/runtime/brandrank-extract。"""
    raw = (os.environ.get("GEO_BRANDRANK_EXTRACT_CACHE_DIR") or "").strip()
    return Path(raw) if raw else _DEFAULT_DIR


def cache_key(domain: str, response_text: str) -> str:
    """sha256(domain + '\\n' + 答案全文)——domain 与答案哈希双因子。"""
    return hashlib.sha256(f"{domain}\n{response_text or ''}".encode()).hexdigest()


def _path(base: Path, key: str) -> Path:
    return base / f"{key}.json"


def load(key: str, *, base: Path | None = None) -> dict[str, Any] | None:
    """读缓存：仅 ok 条目命中返回；未命中/坏文件/failed 条目 → None（调用方重抽）。"""
    path = _path(base or cache_dir(), key)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or entry.get("status") != "ok":
        return None
    brands = entry.get("brands")
    if not isinstance(brands, list) or not all(isinstance(b, str) for b in brands):
        return None                                 # 形状不符 → 诚实重抽
    return entry


def store(key: str, *, brands: list[str], model: str, status: str,
          error: str | None = None, domain: str, base: Path | None = None) -> None:
    """写缓存（ok/failed 都落；failed 供审计，load 不命中它）。原子换名防半截文件。"""
    root = base or cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    entry = {
        "status": status,
        "brands": list(brands),
        "model": model,
        "error": error,
        "domain": domain,
        "extracted_at": datetime.now(UTC).isoformat(),
    }
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
        os.replace(tmp, _path(root, key))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
