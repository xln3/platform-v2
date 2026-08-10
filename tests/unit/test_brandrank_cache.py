"""domain.brandrank.cache：抽取结果文件缓存——键含 domain+答案哈希、ok 命中/failed 重试、原子写。"""

import json
from pathlib import Path

from domain.brandrank import cache


def test_key_contains_domain_and_text():
    k1 = cache.cache_key("insurance", "正文A")
    k2 = cache.cache_key("legal", "正文A")  # 同文本异 domain → 异键（域隔离）
    k3 = cache.cache_key("insurance", "正文B")
    assert k1 != k2 and k1 != k3 and k2 != k3
    assert len(k1) == 64  # sha256 hex


def test_store_load_roundtrip(tmp_path: Path):
    key = cache.cache_key("insurance", "正文")
    assert cache.load(key, base=tmp_path) is None  # 未写前未命中
    cache.store(
        key,
        brands=["中意人寿", "中国平安"],
        model="m1",
        status="ok",
        domain="insurance",
        base=tmp_path,
    )
    hit = cache.load(key, base=tmp_path)
    assert hit is not None
    assert hit["brands"] == ["中意人寿", "中国平安"]
    assert hit["model"] == "m1" and hit["domain"] == "insurance"
    assert hit["status"] == "ok" and hit["extracted_at"]


def test_failed_entry_not_hit_but_kept_for_audit(tmp_path: Path):
    """failed 条目落盘（审计线索）但 load 不命中——下次请求重试（照旧库 failed 重跑语义）。"""
    key = cache.cache_key("insurance", "正文")
    cache.store(
        key,
        brands=[],
        model="m1",
        status="failed",
        error="api_error: boom",
        domain="insurance",
        base=tmp_path,
    )
    assert cache.load(key, base=tmp_path) is None
    raw = json.loads((tmp_path / f"{key}.json").read_text(encoding="utf-8"))
    assert raw["status"] == "failed" and "boom" in raw["error"]


def test_corrupt_file_is_miss(tmp_path: Path):
    """坏 JSON/形状不符 → 诚实未命中（重抽），绝不猜。"""
    key = cache.cache_key("insurance", "正文")
    (tmp_path / f"{key}.json").write_text("{bad json", encoding="utf-8")
    assert cache.load(key, base=tmp_path) is None
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"status": "ok", "brands": "not-a-list"}), encoding="utf-8"
    )
    assert cache.load(key, base=tmp_path) is None


def test_env_overrides_cache_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEO_BRANDRANK_EXTRACT_CACHE_DIR", str(tmp_path))
    assert cache.cache_dir() == tmp_path
    key = cache.cache_key("insurance", "正文")
    cache.store(key, brands=["中意人寿"], model="m", status="ok", domain="insurance")
    assert (tmp_path / f"{key}.json").exists()
    assert cache.load(key)["brands"] == ["中意人寿"]


def test_no_tmp_files_left_after_store(tmp_path: Path):
    key = cache.cache_key("insurance", "正文")
    cache.store(key, brands=[], model="m", status="ok", domain="insurance", base=tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [f"{key}.json"]  # 原子换名无残留
