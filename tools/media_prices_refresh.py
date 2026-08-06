"""媒体比价数据集刷新流水线。

重拉 prfabu / 投媒网 / 媒体批发网 / 媒体特价网 / 媒介盒子 / 品达发稿
六平台新闻与自媒体目录，
分别合并重建 `.datasets/media-prices.json` 和 `.datasets/media-wemedia.json`
（均带 `.sha256` sidecar），进度与结果落 `.datasets/media-prices.refresh.json`。

由 `POST /api/v2/datasets/media-prices/refresh` 以子进程启动（仓库 .venv 解释器），
也可手动执行：`.venv/bin/python tools/media_prices_refresh.py`。

- prfabu 需要 `.datasets/prfabu_session.txt`（Netscape 格式 PHPSESSID）；会话失效
  （响应 code=201 且 msg 含"登录"）不报错，标记 `stale: session_expired` 并沿用
  `.datasets/raw/prfabu/` 既有分页。会话需人工重新登录后更新该文件（不要自动打码）。
- toumeiw 免登录，被限流（code=301）时标记 `partial` 并保留已拉到的页。
- mtpfw / meititejia 免登录，TLS 证书有问题，verify=False。
- meijiehezi 免登录但字段结构不同（kind='mjhz'），经 `_normalize_rows` 映射成标准字段。
- pinda 复用 `.datasets/pinda_session.txt`，登录后以 pageSize=1000 单路分页抓取；
  单页失败优先沿用 `.datasets/raw/(wemedia/)pinda/` 缓存。
- 单实例闸：`.datasets/media-prices.refresh.lock`（O_EXCL；>45min 视为僵死删除）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / ".datasets"
RAW = DATASETS / "raw"
REFRESH_JSON = DATASETS / "media-prices.refresh.json"
LOCK_FILE = DATASETS / "media-prices.refresh.lock"
SESSION_FILE = DATASETS / "prfabu_session.txt"
PINDA_SESSION_FILE = DATASETS / "pinda_session.txt"
DATASET_JSON = DATASETS / "media-prices.json"
DATASET_SHA256 = DATASETS / "media-prices.sha256"
WEMEDIA_DATASET_JSON = DATASETS / "media-wemedia.json"
WEMEDIA_DATASET_SHA256 = DATASETS / "media-wemedia.sha256"
DEVELOPLOG_COMPARE = ROOT.parent / "developlog" / "research" / "prfabu" / "compare" / "data.js"
WHITELIST_CANDIDATES = [
    DATASETS / "news_source_whitelist.json",
    ROOT.parent / "posting" / "sitechoice" / "white_list.json",
]

LOCK_TTL_SECONDS = 45 * 60
PAGE_LIMIT = 5000
# meijiehezi 服务端强制 100 行/页（无视 limit 参数），17724 行需 ~178 页；
# 其余平台 5000/页在第 4 页即因 fetched>=count 提前结束
MAX_PAGES = 200
PAGE_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30.0
TOUMEIW_FULL_COUNT = 16343
PINDA_PAGE_SIZE = 1000
PINDA_FETCH_WORKERS = 1
_CLIENT_SECRET_INVISIBLE_PATTERN = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
_CLIENT_SECRET_VALUE_PATTERN = re.compile(
    r"(?:bearer\s+|session\s*=|cookie(?:\s|=|:)|token(?:\s|=|:)|"
    r"otp(?:\s|=|:)|password(?:\s|=|:)|"
    r"proxy(?:[_ -]?password)?(?:\s|=|:)|"
    r"profile(?:s|[_ /-]?(?:path|dir|directory))?(?:\s|=|:|\\|/)|"
    r"biometric|dlp-canary|(?:^|[^\w])\d{6}(?:[^\w]|$)|"
    r"(?:^|[^\w])\d{3}[\s.-]\d{3}(?:[^\w]|$)|"
    r"1[3-9]\d{9}|1[3-9](?:[\s().-]?\d){9})",
    re.IGNORECASE | re.ASCII,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
SOURCES: dict[str, dict[str, Any]] = {
    "prfabu": {
        "url": "https://www.prfabu.com/index/media/price.html?type=1",
        "wemedia_url": "https://www.prfabu.com/index/media/price.html?type=2",
        "verify": True,
        "label": "prfabu",
        "kind": "std",
    },
    "toumeiw": {
        "url": "https://toumeiw.cn/web/price/price.html?type=1",
        "wemedia_url": "https://toumeiw.cn/web/price/price.html?type=2",
        "verify": True,
        "label": "投媒网",
        "kind": "std",
    },
    "mtpfw": {
        "url": "https://www.mtpfw.cn/web/price/price.html?type=1",
        "wemedia_url": "https://www.mtpfw.cn/web/price/price.html?type=2",
        "verify": False,
        "label": "媒体批发网",
        "kind": "std",
    },
    "meititejia": {
        "url": "https://www.meititejia.com/web/price/price.html?type=1",
        "wemedia_url": "https://www.meititejia.com/web/price/price.html?type=2",
        "verify": False,
        "label": "媒体特价网",
        "kind": "std",
    },
    "meijiehezi": {
        "url": "https://vip.meijiehezi.com/index/index/media_data.html",
        "wemedia_url": "https://vip.meijiehezi.com/index/index/toutiao_data.html",
        "verify": True,
        "label": "媒介盒子",
        "kind": "mjhz",
    },
    "pinda": {
        "url": "https://fagao.pindarpr.com/home_web/mediadata",
        "wemedia_url": "https://fagao.pindarpr.com/home_web/mediadata",
        "verify": True,
        "label": "品达发稿",
        "kind": "pinda",
    },
}
PLATS = list(SOURCES)
PLAT_NAME = {
    "prfabu": "prfabu媒体管家",
    "toumeiw": "投媒网",
    "mtpfw": "媒体批发网",
    "meititejia": "媒体特价网",
    "meijiehezi": "媒介盒子",
    "pinda": "品达发稿",
}
GEO_KEYS = ["a", "b", "c", "d", "e", "f", "z"]
WEMEDIA_PLATFORMS = [
    "今日头条",
    "百家号",
    "东方头条",
    "搜狐网",
    "微信公众号",
    "懂车帝",
    "新浪号",
    "网易号",
    "一点资讯",
    "UC头条",
    "腾讯号",
    "凤凰号",
    "知乎号",
    "豆瓣",
    "车家号",
    "简书",
    "东方财富号",
    "中金在线号",
    "其他",
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write(path: Path, blob: bytes, *, mode: int | None = None) -> None:
    if mode is not None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, mode)
            handle = os.fdopen(descriptor, "wb")
            descriptor = -1
            with handle:
                handle.write(blob)
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


def _write_refresh(
    state: str,
    message: str,
    sources: dict[str, dict[str, Any]],
    started_at: str,
) -> None:
    payload = {
        "state": state,
        "started_at": started_at,
        "updated_at": _now(),
        "message": message,
        "sources": sources,
    }
    _atomic_write(REFRESH_JSON, json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))


def _acquire_lock() -> bool:
    for _attempt in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
            except OSError:
                age = 0
            if age > LOCK_TTL_SECONDS:
                LOCK_FILE.unlink(missing_ok=True)
                continue
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} started={_now()}\n")
        return True
    return False


def _release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


def _load_phpsessid() -> str | None:
    try:
        for line in SESSION_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") and not line.startswith("#HttpOnly_"):
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7 and parts[5] == "PHPSESSID" and parts[6]:
                return parts[6]
    except OSError:
        pass
    return None


def _save_phpsessid(cookies: httpx.Cookies) -> None:
    sessid = cookies.get("PHPSESSID", domain="www.prfabu.com") or cookies.get("PHPSESSID")
    if not sessid:
        return
    content = (
        "# Netscape HTTP Cookie File\n"
        f"#HttpOnly_www.prfabu.com\tFALSE\t/\tFALSE\t0\tPHPSESSID\t{sessid}\n"
    )
    _atomic_write(SESSION_FILE, content.encode("utf-8"), mode=0o600)


def _load_netscape_cookies(path: Path) -> dict[str, str]:
    cookies: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7 and parts[5] and parts[6]:
                cookies[parts[5]] = parts[6]
    except OSError:
        pass
    return cookies


def _read_raw_pages(plat: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    directory = RAW / plat
    for path in sorted(directory.glob("page_*.json")) + sorted(directory.glob("seg*_page_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            pages.append(payload)
    return pages


def _raw_directory(plat: str, catalog: str) -> Path:
    return RAW / plat if catalog == "news" else RAW / "wemedia" / plat


def _read_catalog_raw_pages(plat: str, catalog: str) -> list[dict[str, Any]]:
    if catalog == "news":
        return _read_raw_pages(plat)
    pages: list[dict[str, Any]] = []
    directory = _raw_directory(plat, catalog)
    segmented = sorted(directory.glob("seg*_page_*.json"))
    paths = segmented or sorted(directory.glob("page_*.json"))
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            pages.append(payload)
    return pages


def _fetch_source(
    plat: str,
    on_progress: Any,
    catalog: str = "news",
) -> tuple[str, list[dict[str, Any]], str]:
    """返回 (status, pages, note)。status ∈ ok|partial|stale|failed。"""
    config = SOURCES[plat]
    session_expired = False
    pages: list[dict[str, Any]] = []
    note = ""
    try:
        with httpx.Client(
            headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=config["verify"]
        ) as client:
            if config.get("kind") == "mjhz" and catalog == "news":
                return _fetch_mjhz(client, on_progress)
            if config.get("kind") == "pinda":
                return _fetch_pinda(client, on_progress, catalog)
            if plat == "prfabu":
                sessid = _load_phpsessid()
                if not sessid:
                    return _fallback(plat, "session_file_missing", catalog)
                client.cookies.set("PHPSESSID", sessid, domain="www.prfabu.com")
            fetched = 0
            for page in range(1, MAX_PAGES + 1):
                label = "新闻" if catalog == "news" else "自媒体"
                on_progress(f"拉取 {plat} {label}第{page}页…")
                response = client.post(
                    config["url" if catalog == "news" else "wemedia_url"],
                    data={"page": page, "limit": PAGE_LIMIT},
                )
                response.raise_for_status()
                payload = response.json()
                code = payload.get("code")
                if code != 0:
                    msg = str(payload.get("msg", ""))
                    if plat == "prfabu" and code == 201 and "登录" in msg:
                        session_expired = True
                        note = "session_expired"
                    elif code == 301 and catalog == "wemedia" and plat != "prfabu":
                        return _fetch_wemedia_segments(client, plat, on_progress)
                    elif code == 301:
                        note = f"rate_limited:{msg[:40]}"
                    else:
                        note = f"code={code}:{msg[:40]}"
                    break
                rows = payload.get("data")
                if not isinstance(rows, list):
                    note = "bad_payload"
                    break
                pages.append(payload)
                fetched += len(rows)
                _atomic_write(
                    _raw_directory(plat, catalog) / f"page_{page}.json",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                )
                count = int(payload.get("count") or 0)
                if fetched >= count or not rows:
                    break
                time.sleep(PAGE_DELAY_SECONDS)
            if plat == "prfabu" and not session_expired:
                _save_phpsessid(client.cookies)
    except Exception as exc:  # 网络/解析异常：有旧数据则 stale 沿用
        return _fallback(plat, f"fetch_failed:{type(exc).__name__}", catalog)
    if session_expired:
        return _fallback(plat, note or "session_expired", catalog)
    if pages and not note:
        return "ok", pages, ""
    if pages:
        return "partial", pages, note
    return _fallback(plat, note or "no_rows", catalog)


def _fetch_wemedia_segments(
    client: httpx.Client,
    plat: str,
    on_progress: Any,
) -> tuple[str, list[dict[str, Any]], str]:
    """匿名接口按账号平台分段，避开无筛选查询的 1万/2.5万条登录门槛。"""
    config = SOURCES[plat]
    seen: dict[Any, dict[str, Any]] = {}
    note = ""
    raw_dir = _raw_directory(plat, "wemedia")
    for segment_index, account_platform in enumerate(WEMEDIA_PLATFORMS):
        for page in range(1, 10):
            on_progress(
                f"拉取 {plat} 自媒体段{segment_index + 1}/{len(WEMEDIA_PLATFORMS)}"
                f"({account_platform}) 第{page}页…"
            )
            response = client.post(
                config["wemedia_url"],
                data={
                    "page": page,
                    "limit": PAGE_LIMIT,
                    "platform": account_platform,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                msg = str(payload.get("msg", ""))
                note = f"code={payload.get('code')}:{msg[:40]}"
                break
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if isinstance(row, dict) and row.get("id") is not None:
                    seen[row["id"]] = row
            _atomic_write(
                raw_dir / f"seg{segment_index:02d}_page_{page}.json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            count = int(payload.get("count") or 0)
            if page * PAGE_LIMIT >= count:
                break
            time.sleep(PAGE_DELAY_SECONDS)
        if note:
            break
    ordered = [seen[key] for key in sorted(seen)]
    pages = [
        {"code": 0, "count": len(ordered), "data": ordered[index : index + PAGE_LIMIT]}
        for index in range(0, len(ordered), PAGE_LIMIT)
    ]
    if ordered and not note:
        return "ok", pages, ""
    if ordered:
        return "partial", pages, note
    return _fallback(plat, note or "no_rows", "wemedia")


def _pinda_page_payload(
    client: httpx.Client,
    page: int,
    catalog: str,
) -> tuple[int, dict[str, Any]]:
    media_type = 249 if catalog == "news" else 250
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.post(
                SOURCES["pinda"]["url"],
                headers={"t-cookie": "1", "x-image-code": "code"},
                data={
                    "page": page,
                    "pageSize": PINDA_PAGE_SIZE,
                    "is_short_video": 0,
                    "mediaType": media_type,
                    "recordType": 2,
                    "mediasub": 0,
                    "official": 2,
                    "zhiFlag": 2,
                    "jingFlag": 2,
                },
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data")
            records = data.get("records") if isinstance(data, dict) else None
            if payload.get("code") != 2 or not isinstance(records, list):
                raise ValueError("pinda_session_expired_or_bad_payload")
            return page, {
                "code": 0,
                "count": int(data.get("total") or 0),
                "pages": int(data.get("pages") or 0),
                "data": records,
            }
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 + attempt)
    assert last_error is not None
    raise last_error


def _valid_pinda_cached_page(
    payload: Any,
    *,
    page: int,
    total_pages: int,
    total_rows: int,
) -> bool:
    """只接受与当前 pageSize 分页完全一致的缓存，避免混用历史分页。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return False
    if payload.get("code") != 0:
        return False
    try:
        cached_pages = int(payload.get("pages") or 0)
        cached_rows = int(payload.get("count") or 0)
    except (TypeError, ValueError):
        return False
    if cached_pages != total_pages or cached_rows != total_rows:
        return False
    expected_rows = (
        PINDA_PAGE_SIZE if page < total_pages else total_rows - PINDA_PAGE_SIZE * (total_pages - 1)
    )
    return expected_rows > 0 and len(payload["data"]) == expected_rows


def _read_valid_pinda_cache(catalog: str) -> list[dict[str, Any]]:
    raw_dir = _raw_directory("pinda", catalog)
    try:
        first = json.loads((raw_dir / "page_1.json").read_text(encoding="utf-8"))
        total_pages = int(first.get("pages") or 0)
        total_rows = int(first.get("count") or 0)
    except (AttributeError, OSError, TypeError, ValueError):
        return []
    if total_pages < 1 or total_pages > 2_000 or total_rows < 1 or total_rows > 2_000_000:
        return []
    pages: list[dict[str, Any]] = []
    for page_number in range(1, total_pages + 1):
        try:
            payload = json.loads((raw_dir / f"page_{page_number}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not _valid_pinda_cached_page(
            payload,
            page=page_number,
            total_pages=total_pages,
            total_rows=total_rows,
        ):
            return []
        pages.append(payload)
    return pages


def _fetch_pinda(
    client: httpx.Client,
    on_progress: Any,
    catalog: str,
) -> tuple[str, list[dict[str, Any]], str]:
    """品达目录：pageSize=1000 单路分页，每页独立缓存并在瞬时失败时复用旧页。"""
    cookies = _load_netscape_cookies(PINDA_SESSION_FILE)
    if not cookies:
        return _fallback("pinda", "session_file_missing", catalog)
    for name, value in cookies.items():
        client.cookies.set(name, value, domain="fagao.pindarpr.com")

    label = "新闻" if catalog == "news" else "自媒体"
    try:
        _, first = _pinda_page_payload(client, 1, catalog)
    except Exception as exc:
        return _fallback("pinda", f"fetch_failed:{type(exc).__name__}", catalog)
    total_pages = int(first.get("pages") or 1)
    total_rows = int(first.get("count") or 0)
    if total_pages < 1 or total_pages > 2_000:
        return _fallback("pinda", "bad_page_count", catalog)
    if total_rows < 1 or total_rows > 2_000_000:
        return _fallback("pinda", "bad_row_count", catalog)
    if not _valid_pinda_cached_page(
        first,
        page=1,
        total_pages=total_pages,
        total_rows=total_rows,
    ):
        return _fallback("pinda", "bad_first_page", catalog)

    raw_dir = _raw_directory("pinda", catalog)
    _atomic_write(
        raw_dir / "page_1.json",
        json.dumps(first, ensure_ascii=False).encode("utf-8"),
    )
    pages_by_number: dict[int, dict[str, Any]] = {1: first}
    failed_pages: list[int] = []
    on_progress(f"拉取 pinda {label}：共{total_pages}页，单路请求…")
    with ThreadPoolExecutor(max_workers=PINDA_FETCH_WORKERS) as executor:
        futures = {
            executor.submit(_pinda_page_payload, client, page, catalog): page
            for page in range(2, total_pages + 1)
        }
        completed = 1
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                _, payload = future.result()
            except Exception:
                cache_path = raw_dir / f"page_{page_number}.json"
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    failed_pages.append(page_number)
                else:
                    if _valid_pinda_cached_page(
                        cached,
                        page=page_number,
                        total_pages=total_pages,
                        total_rows=total_rows,
                    ):
                        pages_by_number[page_number] = cached
                    else:
                        failed_pages.append(page_number)
            else:
                if _valid_pinda_cached_page(
                    payload,
                    page=page_number,
                    total_pages=total_pages,
                    total_rows=total_rows,
                ):
                    pages_by_number[page_number] = payload
                    _atomic_write(
                        raw_dir / f"page_{page_number}.json",
                        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    )
                else:
                    failed_pages.append(page_number)
            completed += 1
            if completed % 5 == 0 or completed == total_pages:
                on_progress(f"拉取 pinda {label}：{completed}/{total_pages}页")

    pages = [pages_by_number[number] for number in sorted(pages_by_number)]
    if not pages:
        return _fallback("pinda", "no_rows", catalog)
    for path in raw_dir.glob("page_*.json"):
        match = re.fullmatch(r"page_(\d+)\.json", path.name)
        if match and int(match.group(1)) > total_pages:
            path.unlink(missing_ok=True)
    if failed_pages:
        return "partial", pages, f"missing_pages:{len(failed_pages)}"
    return "ok", pages, ""


def _fallback(
    plat: str,
    note: str,
    catalog: str = "news",
) -> tuple[str, list[dict[str, Any]], str]:
    existing = (
        _read_valid_pinda_cache(catalog)
        if plat == "pinda"
        else _read_catalog_raw_pages(plat, catalog)
    )
    if existing:
        return "stale", existing, note
    return "failed", [], note


# 媒介盒子（meijiehezi）匿名分页 20 页封顶（code=203），须按过滤器分段抓全：
# 省份(31) + 综合全国按 jgfl 价格带(100/200/201) + 0~50 元带按 fgsd/link_type/resource_type 细分。
# 分段两两正交（merge 按媒体名去重兜底）；唯一已知盲区=综合全国×0~50元×1小时×可带网址 内
# 无类目行（量级 ~百以内 / 1.77 万）。
MJHZ_AREAS = [
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "甘肃",
    "四川",
    "贵州",
    "海南",
    "云南",
    "青海",
    "陕西",
    "新疆",
    "西藏",
    "宁夏",
    "内蒙古",
    "广西",
]
MJHZ_RT_IDS = [30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 43, 44, 45, 63, 64, 65, 66, 67]
MJHZ_PAGE_SIZE = 100
MJHZ_PAGE_CAP = 20


def _mjhz_segments() -> list[dict[str, Any]]:
    base: dict[str, Any] = {"area": "综合全国"}
    segments: list[dict[str, Any]] = [{"area": area} for area in MJHZ_AREAS]
    segments += [
        {**base, "price": "100"},
        {**base, "price": "200"},
        {**base, "price": "201"},
        {**base, "price": "50", "publication_time": "2"},
        {**base, "price": "50", "publication_time": "12"},
        {**base, "price": "50", "publication_time": "48"},
        {**base, "price": "50", "publication_time": "24", "link_type": "0"},
        {**base, "price": "50", "publication_time": "24", "link_type": "2"},
        {**base, "price": "50", "publication_time": "1", "link_type": "0"},
    ]
    segments += [
        {**base, "price": "50", "publication_time": "1", "link_type": "2", "resource_type": rt}
        for rt in MJHZ_RT_IDS
    ]
    return segments


def _fetch_mjhz(client: httpx.Client, on_progress: Any) -> tuple[str, list[dict[str, Any]], str]:
    """按分段拉全媒介盒子；返回 (status, pages, note)。限流即停（partial 保留已抓）。"""
    config = SOURCES["meijiehezi"]
    seen: dict[Any, dict[str, Any]] = {}
    note = ""
    segments = _mjhz_segments()
    for index, segment in enumerate(segments):
        if note:
            break
        label = "/".join(str(v) for v in segment.values())
        for page in range(1, MJHZ_PAGE_CAP + 2):
            on_progress(f"拉取 meijiehezi 段{index + 1}/{len(segments)}({label}) 第{page}页…")
            response = client.post(
                config["url"],
                data={"page": page, "limit": MJHZ_PAGE_SIZE, **segment},
            )
            response.raise_for_status()
            payload = response.json()
            code = payload.get("code")
            if code == 203:  # 分页封顶：超过段预算页数说明该段未抓全
                if page > MJHZ_PAGE_CAP:
                    note = f"capped:{label}"
                break
            if code != 0:
                msg = str(payload.get("msg", ""))
                note = f"rate_limited:{msg[:40]}" if code == 301 else f"code={code}:{msg[:40]}"
                break
            rows = payload.get("data")
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if isinstance(row, dict) and row.get("id") is not None:
                    seen[row["id"]] = row
            _atomic_write(
                RAW / "meijiehezi" / f"seg{index:02d}_page_{page}.json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            count = int(payload.get("count") or 0)
            if page * MJHZ_PAGE_SIZE >= count:
                break
            time.sleep(PAGE_DELAY_SECONDS)
    ordered = [seen[key] for key in sorted(seen)]
    pages = [
        {"code": 0, "count": len(ordered), "data": ordered[i : i + 500]}
        for i in range(0, len(ordered), 500)
    ]
    if ordered and not note:
        return "ok", pages, ""
    if ordered:
        return "partial", pages, note
    return _fallback("meijiehezi", note or "no_rows")


def _fnum(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _browser_safe_required_label(value: Any, maximum_length: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > maximum_length:
        return None
    normalized = _CLIENT_SECRET_INVISIBLE_PATTERN.sub(
        "",
        unicodedata.normalize("NFKC", value),
    )
    for _attempt in range(3):
        decoded = unquote(normalized)
        if decoded == normalized:
            break
        normalized = _CLIENT_SECRET_INVISIBLE_PATTERN.sub(
            "",
            unicodedata.normalize("NFKC", decoded),
        )
    return None if _CLIENT_SECRET_VALUE_PATTERN.search(normalized) else value


def _geo_tags(row: dict[str, Any]) -> list[str]:
    if not row.get("geo_rank"):
        return []
    raw = str(row.get("geo_rank_platform") or "")
    return [p for p in raw.split(",") if p.strip() in GEO_KEYS]


def _load_whitelist() -> tuple[set[str], list[str]] | None:
    """加载「互联网新闻信息稿源单位名单」（网信办 2025-06，parse_whitelist.py 产物）。

    返回 (集合, 按长度降序列表)；文件缺失/损坏返回 None（不阻断刷新，仅不打标）。
    """
    for candidate in WHITELIST_CANDIDATES:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            names = payload.get("names")
            if isinstance(names, list) and names:
                wl_set = {str(n) for n in names}
                return wl_set, sorted(wl_set, key=len, reverse=True)
        except (OSError, ValueError):
            continue
    return None


def _whitelist_match(name: str, wl_set: set[str], wl_sorted: list[str]) -> bool:
    """精确相等，或数据集名以名单名开头（名单名≥3字，视为同主体频道/客户端）。

    防护：名单名以「报」结尾时，剩余部分以「道/讯」开头的不算（排除"XX报道/XX报讯网"
    这类仿冒命名）。
    """
    stripped = re.sub(r"[（(].*?[）)]", "", name).strip()
    if stripped in wl_set:
        return True
    for w in wl_sorted:
        if len(w) < 3 or not stripped.startswith(w):
            continue
        if w.endswith("报") and stripped[len(w) : len(w) + 1] in ("道", "讯"):
            continue
        return True
    return False


def _load_rows(
    plat: str,
    pages: list[dict[str, Any]],
    catalog: str = "news",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in pages:
        data = payload.get("data")
        if isinstance(data, list):
            rows += data
    if catalog != "news":
        return rows
    extra_dir = RAW / "extra"
    extra_names = {"toumeiw": "geo_media_toumeiw_1113.json", "mtpfw": "geo_media_mtpfw_721.json"}
    name = extra_names.get(plat)
    if name:
        for candidate in (
            extra_dir / name,
            ROOT.parent / "developlog" / "research" / "prfabu" / "data" / name,
        ):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            data = payload.get("data")
            if isinstance(data, list):
                rows += data
            break
    return rows


def _normalize_mjhz_row(row: dict[str, Any]) -> dict[str, Any]:
    """媒介盒子（meijiehezi）字段 → 标准（prfabu 系）字段名。

    rc_v0 是真实价（v0_cost_2023 为"查看报价"占位）；publish_rate 为 "80%" 字符串；
    news_resource 为 0/1；无 geo_rank/portal_media/include_condition 等字段（缺省）。
    """
    out = dict(row)
    out["custom_cost"] = row.get("rc_v0")
    out["channel_type_str"] = row.get("resource_type_name")
    out["province"] = row.get("area")
    rate = _fnum(str(row.get("publish_rate") or "").replace("%", ""))
    if rate is not None:
        out["publish_rate"] = rate
    news_resource = row.get("news_resource")
    if news_resource in (0, 1):
        out["news_resource_str"] = "百度新闻源" if news_resource == 1 else "非新闻源"
    return out


def _normalize_pinda_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    # 自动发帖下单需要把价格快照绑定到供应商资源；仅保留不敏感的媒体公开 ID。
    out["id"] = row.get("id")
    out["media_name"] = row.get("name")
    out["custom_cost"] = row.get("pay_price") or row.get("price_n")
    out["channel_type_str"] = row.get("resourceType") or row.get("classify")
    out["province"] = row.get("area") or row.get("zone")
    speed = row.get("speed")
    out["publication_time_str"] = f"{speed}小时" if speed not in (None, "") else ""
    out["publish_rate"] = _fnum(row.get("publishRate"))
    out["news_resource_str"] = row.get("newsSource")
    out["pc_weight"] = _fnum(row.get("pcWeight"))
    out["m_weight"] = _fnum(row.get("mobileWeight"))
    out["remark"] = row.get("brief") or row.get("remark")
    out["case_link"] = row.get("exampleUrl")
    return out


def _normalize_rows(plat: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if SOURCES[plat].get("kind") == "mjhz":
        return [_normalize_mjhz_row(row) for row in rows]
    if SOURCES[plat].get("kind") == "pinda":
        return [_normalize_pinda_row(row) for row in rows]
    return rows


_FANS_LABELS = {
    1: "0-1000",
    5: "1001-5000",
    10: "5001-1万",
    50: "1万-5万",
    100: "5万-10万",
    1000: "10万-100万",
    1001: "100万以上",
}
_READ_LABELS = {
    1: "0-1000",
    5: "1001-5000",
    10: "5001-1万",
    50: "1万-5万",
    100: "5万-10万",
    101: "10万以上",
}
_ACCOUNT_AUTH_LABELS = {
    0: "未认证",
    1: "黄V认证",
    2: "蓝V认证",
    3: "红V认证",
}


def _normalize_wemedia_rows(
    plat: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if SOURCES[plat].get("kind") == "pinda":
        normalized: list[dict[str, Any]] = []
        for row in rows:
            out = dict(row)
            out["wemedia_name"] = row.get("name")
            out["custom_cost"] = row.get("pay_price") or row.get("price_n")
            out["platform_str"] = row.get("platform")
            out["industry_str"] = row.get("classify") or row.get("resourceType")
            out["account_auth_str"] = row.get("varifyinfo")
            fans = row.get("realfans") or row.get("fanCount")
            reads = row.get("totalReadingVolume") or row.get("readCount")
            out["fans_num_str"] = str(fans) if fans not in (None, "", 0, "0") else ""
            out["read_num_str"] = str(reads) if reads not in (None, "", 0, "0") else ""
            out["remark"] = row.get("brief") or row.get("remark")
            out["case_link"] = row.get("exampleUrl")
            normalized.append(out)
        return normalized
    if SOURCES[plat].get("kind") != "mjhz":
        return rows
    normalized: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        out["wemedia_name"] = row.get("toutiao_name")
        out["custom_cost"] = row.get("v0_cost_2023")
        out["platform_str"] = row.get("platform")
        out["industry_str"] = row.get("industry")
        out["province_str"] = row.get("province")
        fans = row.get("fans_num")
        reads = row.get("read_num")
        auth = row.get("account_auth")
        out["fans_num_str"] = _FANS_LABELS.get(fans, str(fans) if fans not in (None, "") else "")
        out["read_num_str"] = _READ_LABELS.get(reads, str(reads) if reads not in (None, "") else "")
        out["account_auth_str"] = _ACCOUNT_AUTH_LABELS.get(
            auth, str(auth) if auth not in (None, "") else ""
        )
        normalized.append(out)
    return normalized


def _merge(
    source_pages: dict[str, list[dict[str, Any]]],
    source_status: dict[str, str],
) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    geo_counts: dict[str, int] = {}
    for plat in PLATS:
        rows = _normalize_rows(plat, _load_rows(plat, source_pages.get(plat, [])))
        counts[plat] = len(rows)
        geo_hits = 0
        for row in rows:
            name = _browser_safe_required_label(
                str(row.get("media_name") or "").strip(),
                500,
            )
            if not name:
                continue
            rec = merged.setdefault(
                name,
                {"name": name, "prices": {}, "ids": {}, "geo": set(), "geo_src": {}, "meta": {}},
            )
            price = _fnum(row.get("custom_cost")) or _fnum(row.get("v0_cost"))
            if price:
                if plat not in rec["prices"] or price < rec["prices"][plat]:
                    rec["prices"][plat] = price
            if row.get("id"):
                rec["ids"][plat] = row["id"]
            tags = _geo_tags(row)
            if tags:
                geo_hits += 1
                rec["geo"].update(tags)
                rec["geo_src"][plat] = tags
            ai_rate = _fnum(row.get("ai_inclusion_rate"))
            meta = rec["meta"]
            for k_src, k_dst in [
                ("portal_media_str", "portal"),
                ("channel_type_str", "channel"),
                ("include_condition_str", "include"),
                ("news_resource_str", "news_src"),
                ("publication_time_str", "speed"),
                ("pc_weight", "pc_w"),
                ("m_weight", "m_w"),
                ("publish_rate", "pub_rate"),
                ("province", "province"),
                ("remark", "remark"),
                ("case_link", "case"),
                ("entrance_link", "site"),
                ("weekend_publish_str", "weekend"),
            ]:
                value = row.get(k_src)
                if value in (None, "", 0) and k_dst not in ("pc_w", "m_w"):
                    continue
                if plat == "prfabu" or k_dst not in meta or not meta[k_dst]:
                    if k_dst in ("remark",) and meta.get(k_dst) and plat != "prfabu":
                        continue
                    meta[k_dst] = value
            if ai_rate is not None:
                meta["ai_rate"] = max(ai_rate, meta.get("ai_rate") or 0)
        geo_counts[plat] = geo_hits

    rows_out: list[dict[str, Any]] = []
    whitelist = _load_whitelist()
    for name, rec in merged.items():
        prices = rec["prices"]
        values = list(prices.values())
        best = min(values) if values else None
        best_plat = min(prices, key=prices.get) if prices else None
        worst = max(values) if values else None
        meta = rec["meta"]
        row: dict[str, Any] = {
            "name": name,
            "prices": prices,
            "best": best,
            "best_plat": best_plat,
            "spread": round(worst / best, 1) if best and worst and len(values) > 1 else None,
            "n_src": len(values),
            "geo": sorted(rec["geo"]),
            "geo_n": len(rec["geo_src"]),
            "ids": rec["ids"],
        }
        row.update({k: v for k, v in meta.items() if v not in (None, "")})
        if whitelist and _whitelist_match(name, whitelist[0], whitelist[1]):
            row["whitelist"] = True
        rows_out.append(row)

    rows_out.sort(key=lambda r: (r["best"] is None, r["best"] or 0))
    stats = {
        "counts": counts,
        "geo_counts": geo_counts,
        "unique_media": len(rows_out),
        "matched_2plus": sum(1 for r in rows_out if r["n_src"] >= 2),
        # 契约口径：matched_3 = 全平台（len(PLATS)）重合行数（api-client 投影按此交叉校验）
        "matched_3": sum(1 for r in rows_out if r["n_src"] == len(PLATS)),
        "geo_union": sum(1 for r in rows_out if r["geo"]),
        "geo_multi_src": sum(1 for r in rows_out if r["geo_n"] >= 2),
        "whitelist": sum(1 for r in rows_out if r.get("whitelist")),
    }
    partial = {
        plat: source_status.get(plat) == "partial"
        or (plat == "toumeiw" and counts["toumeiw"] < TOUMEIW_FULL_COUNT)
        for plat in PLATS
    }
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": PLAT_NAME,
        "partial": partial,
        "stats": stats,
        "rows": rows_out,
    }


def _merge_wemedia(
    source_pages: dict[str, list[dict[str, Any]]],
    source_status: dict[str, str],
) -> dict[str, Any]:
    """按「账号平台 + 账号名」合并六家自媒体报价。"""
    merged: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    geo_counts: dict[str, int] = {}
    for plat in PLATS:
        rows = _normalize_wemedia_rows(
            plat,
            _load_rows(plat, source_pages.get(plat, []), "wemedia"),
        )
        counts[plat] = len(rows)
        geo_hits = 0
        for row in rows:
            name = _browser_safe_required_label(
                str(row.get("wemedia_name") or "").strip(),
                500,
            )
            account_platform = _browser_safe_required_label(
                str(row.get("platform_str") or row.get("platform") or "").strip(),
                160,
            )
            if not name or not account_platform:
                continue
            merge_key = f"{account_platform}\0{name}"
            rec = merged.setdefault(
                merge_key,
                {
                    "name": name,
                    "platform": account_platform,
                    "prices": {},
                    "ids": {},
                    "geo": set(),
                    "geo_src": {},
                    "meta": {},
                },
            )
            price = _fnum(row.get("custom_cost")) or _fnum(row.get("v0_cost"))
            if price and (plat not in rec["prices"] or price < rec["prices"][plat]):
                rec["prices"][plat] = price
            if row.get("id"):
                rec["ids"][plat] = row["id"]
            tags = _geo_tags(row)
            if tags:
                geo_hits += 1
                rec["geo"].update(tags)
                rec["geo_src"][plat] = tags
            meta = rec["meta"]
            for k_src, k_dst in [
                ("industry_str", "industry"),
                ("account_auth_str", "account_auth"),
                ("fans_num_str", "fans"),
                ("read_num_str", "reads"),
                ("remark", "remark"),
                ("case_link", "case"),
                ("entrance_link", "site"),
            ]:
                value = row.get(k_src)
                if value in (None, "", 0):
                    continue
                if plat == "prfabu" or k_dst not in meta or not meta[k_dst]:
                    meta[k_dst] = value
        geo_counts[plat] = geo_hits

    rows_out: list[dict[str, Any]] = []
    for rec in merged.values():
        prices = rec["prices"]
        values = list(prices.values())
        best = min(values) if values else None
        best_plat = min(prices, key=prices.get) if prices else None
        worst = max(values) if values else None
        row: dict[str, Any] = {
            "name": rec["name"],
            "platform": rec["platform"],
            "prices": prices,
            "best": best,
            "best_plat": best_plat,
            "spread": round(worst / best, 1) if best and worst and len(values) > 1 else None,
            "n_src": len(values),
            "geo": sorted(rec["geo"]),
            "geo_n": len(rec["geo_src"]),
            "ids": rec["ids"],
        }
        meta = rec["meta"]
        if meta.get("remark"):
            meta["remark"] = str(meta["remark"])[:300]
        if meta.get("site") == meta.get("case"):
            meta.pop("site", None)
        row.update({k: v for k, v in meta.items() if v not in (None, "")})
        rows_out.append(row)

    rows_out.sort(key=lambda row: (row["best"] is None, row["best"] or 0))
    stats = {
        "counts": counts,
        "geo_counts": geo_counts,
        "unique_media": len(rows_out),
        "matched_2plus": sum(1 for row in rows_out if row["n_src"] >= 2),
        "matched_3": sum(1 for row in rows_out if row["n_src"] == len(PLATS)),
        "geo_union": sum(1 for row in rows_out if row["geo"]),
        "geo_multi_src": sum(1 for row in rows_out if row["geo_n"] >= 2),
    }
    partial = {
        plat: source_status.get(plat) == "partial"
        or (plat == "toumeiw" and counts["toumeiw"] < 43_000)
        for plat in PLATS
    }
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": PLAT_NAME,
        "partial": partial,
        "stats": stats,
        "rows": rows_out,
    }


def _summary_message(sources: dict[str, dict[str, Any]]) -> str:
    def suffix(status: Any, note: Any) -> str:
        if status == "partial":
            return "(部分采集)"
        if status == "stale":
            return "(会话失效沿用旧数据)" if note == "session_expired" else "(沿用旧数据)"
        if status == "failed":
            return "(失败)"
        return ""

    parts = []
    for plat in PLATS:
        info = sources[plat]
        label = SOURCES[plat]["label"]
        wemedia_rows = info.get("wemedia_rows", 0)
        parts.append(
            f"{label} 新闻{info['rows']}{suffix(info['status'], info['note'])}"
            f"/自媒体{wemedia_rows}"
            f"{suffix(info.get('wemedia_status'), info.get('wemedia_note'))}"
        )
    return " · ".join(parts)


def main() -> int:
    DATASETS.mkdir(parents=True, exist_ok=True)
    for plat in PLATS:
        (RAW / plat).mkdir(parents=True, exist_ok=True)
        (RAW / "wemedia" / plat).mkdir(parents=True, exist_ok=True)
    if not _acquire_lock():
        print("另一个刷新正在进行（lock 存在且未过期），退出。", file=sys.stderr)
        return 2
    started_at = _now()
    sources: dict[str, dict[str, Any]] = {
        plat: {
            "status": "pending",
            "rows": 0,
            "note": "",
            "wemedia_status": "pending",
            "wemedia_rows": 0,
            "wemedia_note": "",
        }
        for plat in PLATS
    }

    def progress(message: str) -> None:
        _write_refresh("running", message, sources, started_at)
        print(message, flush=True)

    try:
        _write_refresh("running", "启动刷新…", sources, started_at)
        source_pages: dict[str, list[dict[str, Any]]] = {}
        source_status: dict[str, str] = {}
        for plat in PLATS:
            status, pages, note = _fetch_source(plat, progress, "news")
            source_pages[plat] = pages
            source_status[plat] = status
            rows_count = sum(len(p.get("data") or []) for p in pages)
            sources[plat].update({"status": status, "rows": rows_count, "note": note})
            progress(f"{plat} 拉取结束：{status} {rows_count} 行 {note}".strip())
        wemedia_pages: dict[str, list[dict[str, Any]]] = {}
        wemedia_status: dict[str, str] = {}
        for plat in PLATS:
            status, pages, note = _fetch_source(plat, progress, "wemedia")
            wemedia_pages[plat] = pages
            wemedia_status[plat] = status
            rows_count = sum(len(p.get("data") or []) for p in pages)
            sources[plat].update(
                {
                    "wemedia_status": status,
                    "wemedia_rows": rows_count,
                    "wemedia_note": note,
                }
            )
            progress(f"{plat} 自媒体拉取结束：{status} {rows_count} 行 {note}".strip())
        progress("合并重建新闻与自媒体数据集…")
        payload = _merge(source_pages, source_status)
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        _atomic_write(DATASET_JSON, blob)
        digest = hashlib.sha256(blob).hexdigest()
        _atomic_write(DATASET_SHA256, f"{digest}  media-prices.json\n".encode())
        wemedia_payload = _merge_wemedia(wemedia_pages, wemedia_status)
        wemedia_blob = json.dumps(
            wemedia_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _atomic_write(WEMEDIA_DATASET_JSON, wemedia_blob)
        wemedia_digest = hashlib.sha256(wemedia_blob).hexdigest()
        _atomic_write(
            WEMEDIA_DATASET_SHA256,
            f"{wemedia_digest}  media-wemedia.json\n".encode(),
        )
        try:  # 可选：同步刷新 developlog 静态页数据，失败不阻断
            if DEVELOPLOG_COMPARE.parent.is_dir():
                DEVELOPLOG_COMPARE.write_text(
                    "window.PRICE_DATA = " + blob.decode("utf-8") + ";\n", encoding="utf-8"
                )
        except OSError as exc:
            print(f"data.js 同步失败（不阻断）: {exc}", file=sys.stderr)
        message = _summary_message(sources)
        _write_refresh("done", message, sources, started_at)
        print(
            "done:",
            message,
            f"| news_rows={payload['stats']['unique_media']} sha256={digest}",
            f"| wemedia_rows={wemedia_payload['stats']['unique_media']} sha256={wemedia_digest}",
        )
        return 0
    except Exception as exc:
        _write_refresh("failed", f"{type(exc).__name__}: {exc}"[:200], sources, started_at)
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    finally:
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())
