#!/usr/bin/env python3
"""Read an existing Qianwen session through the signed web application.

This diagnostic never submits a prompt.  It reuses an isolated copy of an
authenticated profile, redirects one existing sidebar item's in-memory React
session object to a requested chat id, and lets the application issue its own
signed message-list request.  Output deliberately contains structural metadata
only; answer and prompt text are represented by hashes and lengths.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

_SESSION_ID_RE = re.compile(r"^[a-fA-F0-9]{32}$")
_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _find_session_item(page: Page) -> dict[str, str]:
    result = await page.evaluate(
        """
        () => {
          const hex32 = /^[a-fA-F0-9]{32}$/;
          for (const element of document.querySelectorAll('body *')) {
            const values = Object.values(element.dataset || {});
            const sessionId = values.find(value => hex32.test(value || ''));
            if (!sessionId) continue;
            const rect = element.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            return {
              sessionId,
              tag: element.tagName,
              className: String(element.className || '').slice(0, 240),
            };
          }
          return null;
        }
        """
    )
    if not isinstance(result, dict) or not _SESSION_ID_RE.fullmatch(
        str(result.get("sessionId") or "")
    ):
        raise RuntimeError("no visible sidebar session item was found")
    return {key: str(value) for key, value in result.items()}


async def _wait_for_session_item(page: Page, *, timeout_seconds: int = 60) -> dict[str, str]:
    for _ in range(timeout_seconds):
        await page.wait_for_timeout(1_000)
        try:
            return await _find_session_item(page)
        except RuntimeError:
            continue
    raise RuntimeError(
        f"no visible sidebar session item was found after {timeout_seconds} seconds"
    )


async def _redirect_session_item(page: Page, target_session_id: str) -> dict[str, Any]:
    result = await page.evaluate(
        """
        targetSessionId => {
          const hex32 = /^[a-fA-F0-9]{32}$/;
          let element = null;
          let originalSessionId = null;
          for (const candidate of document.querySelectorAll('body *')) {
            const values = Object.values(candidate.dataset || {});
            const sessionId = values.find(value => hex32.test(value || ''));
            if (!sessionId) continue;
            const rect = candidate.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            element = candidate;
            originalSessionId = sessionId;
            break;
          }
          if (!element || !originalSessionId) return {error: 'session_item_not_found'};

          let mutatedSessionIds = 0;
          let mutatedIds = 0;
          const visited = new WeakSet();
          const mutate = (value, depth) => {
            if (!value || typeof value !== 'object' || depth > 8 || visited.has(value)) return;
            visited.add(value);
            for (const key of Object.keys(value)) {
              let child;
              try { child = value[key]; } catch (_) { continue; }
              if (key === 'sessionId' && child === originalSessionId) {
                try { value[key] = targetSessionId; mutatedSessionIds += 1; } catch (_) {}
                continue;
              }
              if (key === 'id' && child === originalSessionId) {
                try { value[key] = targetSessionId; mutatedIds += 1; } catch (_) {}
                continue;
              }
              if (depth < 8 && child && typeof child === 'object') mutate(child, depth + 1);
            }
          };

          let fiber = null;
          for (const key of Object.keys(element)) {
            if (key.startsWith('__reactFiber$')) fiber = element[key];
          }
          let fiberDepth = 0;
          while (fiber && fiberDepth < 18) {
            mutate(fiber.memoizedProps, 0);
            mutate(fiber.pendingProps, 0);
            fiber = fiber.return;
            fiberDepth += 1;
          }
          for (const key of Object.keys(element.dataset || {})) {
            if (element.dataset[key] === originalSessionId) element.dataset[key] = targetSessionId;
          }
          element.click();
          return {
            originalSessionId,
            targetSessionId,
            mutatedSessionIds,
            mutatedIds,
            fiberDepth,
          };
        }
        """,
        target_session_id,
    )
    if not isinstance(result, dict) or result.get("error"):
        raise RuntimeError(str(result))
    return result


def _session_id_from_url(url: str) -> str | None:
    parsed = urlsplit(url)
    values = parse_qs(parsed.query).get("session_id") or []
    return values[0] if values else None


def _summarize_json(value: Any, *, depth: int = 0) -> Any:
    """Describe response shape without emitting conversation content."""

    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): _summarize_json(child, depth=depth + 1)
            for key, child in value.items()
            if key
            in {
                "code",
                "success",
                "message",
                "data",
                "list",
                "messages",
                "session_id",
                "sessionId",
                "have_next",
                "next_token",
            }
        }
    if isinstance(value, list):
        sample = _summarize_json(value[0], depth=depth + 1) if value else None
        return {"length": len(value), "first_shape": sample}
    if isinstance(value, str):
        if _SESSION_ID_RE.fullmatch(value):
            return value
        return {"type": "str", "length": len(value), "sha256": _digest(value)}
    if value is None or isinstance(value, bool | int | float):
        return value
    return type(value).__name__


def _key_shape(value: Any, *, depth: int = 0) -> Any:
    if depth > 7:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): _key_shape(child, depth=depth + 1)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return {
            "length": len(value),
            "first": _key_shape(value[0], depth=depth + 1) if value else None,
        }
    return type(value).__name__


def _url_hits(value: Any, *, path: str = "$", depth: int = 0) -> list[dict[str, str]]:
    if depth > 12:
        return []
    if isinstance(value, dict):
        return [
            hit
            for key, child in value.items()
            for hit in _url_hits(child, path=f"{path}.{key}", depth=depth + 1)
        ]
    if isinstance(value, list):
        return [
            hit
            for index, child in enumerate(value)
            for hit in _url_hits(child, path=f"{path}[{index}]", depth=depth + 1)
        ]
    if not isinstance(value, str):
        return []
    stripped = value.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            decoded = json.loads(stripped)
        except ValueError:
            pass
        else:
            return _url_hits(decoded, path=f"{path}.$json", depth=depth + 1)
    return [
        {"path": path, "url": match.group(0).rstrip(".,;:)]}")}
        for match in _URL_RE.finditer(value)
    ]


def _extract_source_cards(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return []
    data = payload.get("data")
    turns = data.get("list") if isinstance(data, dict) else None
    if not isinstance(turns, list):
        return []
    cards: list[dict[str, Any]] = []
    for turn in turns:
        messages = turn.get("response_messages") if isinstance(turn, dict) else None
        if not isinstance(messages, list):
            continue
        for message in messages:
            meta = message.get("meta_data") if isinstance(message, dict) else None
            groups = meta.get("sources") if isinstance(meta, dict) else None
            if not isinstance(groups, list):
                continue
            for group in groups:
                content = group.get("content") if isinstance(group, dict) else None
                items = content.get("list") if isinstance(content, dict) else None
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    text = next(
                        (
                            item.get(key)
                            for key in ("content", "snippet", "summary", "desc")
                            if isinstance(item.get(key), str) and item[key].strip()
                        ),
                        None,
                    )
                    cards.append(
                        {
                            "keys": sorted(str(key) for key in item),
                            "url": item.get("url"),
                            "raw_url": item.get("raw_url"),
                            "title": item.get("title"),
                            "text_length": len(text) if text else 0,
                            "text_sha256": _digest(text) if text else None,
                        }
                    )
    return cards


def _header_value(headers: Any, name: str) -> str | None:
    if not isinstance(headers, list):
        return None
    for header in headers:
        if (
            isinstance(header, dict)
            and str(header.get("name") or "").lower() == name.lower()
            and isinstance(header.get("value"), str)
        ):
            return str(header["value"])
    return None


def _target_ids_from_har(path: Path) -> tuple[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = ((payload.get("log") or {}).get("entries") or [])
    request_ids: set[str] = set()
    session_counts: dict[str, int] = {}
    for entry in entries:
        request = entry.get("request") if isinstance(entry, dict) else None
        if not isinstance(request, dict):
            continue
        url = str(request.get("url") or "")
        headers = request.get("headers")
        if request.get("method") == "POST" and "/api/v2/chat" in url:
            request_id = _header_value(headers, "x-chat-id")
            if request_id and _SESSION_ID_RE.fullmatch(request_id):
                request_ids.add(request_id)
        referer = _header_value(headers, "referer") or ""
        match = re.search(r"/chat/([A-Fa-f0-9]{32})(?:[/?#]|$)", referer)
        if match:
            session_id = match.group(1).lower()
            session_counts[session_id] = session_counts.get(session_id, 0) + 1
    if len(request_ids) != 1:
        raise ValueError(f"{path}: expected one x-chat-id, found {sorted(request_ids)}")
    if not session_counts:
        raise ValueError(f"{path}: no Qianwen session id was found in Referer headers")
    ranked_sessions = sorted(session_counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked_sessions) > 1 and ranked_sessions[0][1] == ranked_sessions[1][1]:
        raise ValueError(f"{path}: Qianwen session id is ambiguous")
    return ranked_sessions[0][0], next(iter(request_ids)).lower()


def _load_batch_targets(tenant_pub_id: str, config_pub_id: str) -> list[dict[str, str]]:
    from geo_platform.collection.models import CollectionRun, CollectionTask
    from geo_platform.projects.models import MonitoringConfigVersion
    from geo_platform.tenancy.database import WorkerSessionLocal
    from geo_platform.tenancy.repository import TenantRepository
    from sqlalchemy import select

    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        tasks = list(
            session.scalars(
                select(CollectionTask)
                .join(CollectionRun, CollectionRun.id == CollectionTask.run_id)
                .join(
                    MonitoringConfigVersion,
                    MonitoringConfigVersion.id == CollectionRun.config_version_id,
                )
                .where(
                    MonitoringConfigVersion.pub_id == config_pub_id,
                    CollectionTask.state == "completed",
                )
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
            )
        )
        if not tasks:
            raise ValueError("config version has no completed tasks")
        targets: list[dict[str, str]] = []
        for task in tasks:
            evidence = json.loads(task.evidence_json or "[]")
            har_paths = [
                Path(str(item.get("path")))
                for item in evidence
                if isinstance(item, dict)
                and item.get("kind") == "har"
                and item.get("relation_type") == "answer_har"
                and item.get("path")
            ]
            if len(har_paths) != 1 or not har_paths[0].is_file():
                raise ValueError(f"{task.pub_id}: expected exactly one readable HAR")
            session_id, request_id = _target_ids_from_har(har_paths[0])
            matrix = json.loads(task.matrix_json or "{}")
            query = matrix.get("query") if isinstance(matrix, dict) else None
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"{task.pub_id}: collection query is missing")
            targets.append(
                {
                    "task_pub_id": task.pub_id,
                    "session_id": session_id,
                    "request_id": request_id,
                    "query_sha256": _digest(query.strip()),
                    "har_path": str(har_paths[0]),
                }
            )
        if len({target["session_id"] for target in targets}) != len(targets):
            raise ValueError("formal tasks do not map to unique Qianwen sessions")
        if len({target["request_id"] for target in targets}) != len(targets):
            raise ValueError("formal tasks do not map to unique Qianwen requests")
        return targets


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    return cleaned


def _validated_turn(payload: Any, target: dict[str, str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ValueError(f"message list failed with code {(payload or {}).get('code')}")
    data = payload.get("data")
    turns = data.get("list") if isinstance(data, dict) else None
    if not isinstance(turns, list):
        raise ValueError("message list does not contain turns")
    matches = [
        turn
        for turn in turns
        if isinstance(turn, dict)
        and str(turn.get("session_id") or "").lower() == target["session_id"]
        and str(turn.get("req_id") or "").lower() == target["request_id"]
    ]
    if len(matches) != 1:
        raise ValueError(
            "message list did not contain exactly one matching session/request turn"
        )
    turn = matches[0]
    requests = turn.get("request_messages")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        raise ValueError("matched turn does not contain the original query")
    query = requests[0].get("content")
    if not isinstance(query, str) or _digest(query.strip()) != target["query_sha256"]:
        raise ValueError("matched turn query hash differs from the captured task")
    return turn


def _citations_from_turn(turn: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    messages = turn.get("response_messages")
    if not isinstance(messages, list):
        raise ValueError("matched turn does not contain response messages")
    items: list[dict[str, Any]] = []
    for message in messages:
        meta = message.get("meta_data") if isinstance(message, dict) else None
        groups = meta.get("sources") if isinstance(meta, dict) else None
        if not isinstance(groups, list):
            continue
        for group in groups:
            content = group.get("content") if isinstance(group, dict) else None
            source_items = content.get("list") if isinstance(content, dict) else None
            if isinstance(source_items, list):
                items.extend(item for item in source_items if isinstance(item, dict))
    citations: list[dict[str, Any]] = []
    unresolved: list[int] = []
    for ordinal, item in enumerate(items, 1):
        url = _http_url(item.get("raw_url")) or _http_url(item.get("url"))
        if url is None:
            unresolved.append(ordinal)
            continue
        title = item.get("title")
        title = title.strip()[:300] if isinstance(title, str) and title.strip() else None
        summary = item.get("summary")
        summary = (
            summary.strip()[:2_000]
            if isinstance(summary, str) and summary.strip()
            else None
        )
        citations.append(
            {
                "url": url,
                "title": title,
                "cited_text": summary,
                "platform_ordinal": ordinal,
                "ordinal_base": 1,
            }
        )
    return citations, unresolved


def _write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


async def _source_triggers(page: Page) -> list[dict[str, Any]]:
    result = await page.evaluate(
        """
        () => [...document.querySelectorAll('body *')]
          .filter(element => {
            const text = (element.innerText || '').trim();
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 &&
              /^(?:若干|\\d+)(?:个|篇)来源$/.test(text);
          })
          .filter(element => ![...element.children].some(child =>
            /^(?:若干|\\d+)(?:个|篇)来源$/.test((child.innerText || '').trim())))
          .map((element, index) => ({
            index,
            text: (element.innerText || '').trim(),
            tag: element.tagName,
            className: String(element.className || '').slice(0, 300),
            outerHTML: element.outerHTML.slice(0, 1200),
          }))
        """
    )
    return result if isinstance(result, list) else []


async def _click_source_trigger(page: Page) -> bool:
    return bool(
        await page.evaluate(
            """
            () => {
              const matches = [...document.querySelectorAll('body *')]
                .filter(element => {
                  const text = (element.innerText || '').trim();
                  const rect = element.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0 &&
                    /^(?:若干|\\d+)(?:个|篇)来源$/.test(text);
                })
                .filter(element => ![...element.children].some(child =>
                  /^(?:若干|\\d+)(?:个|篇)来源$/.test((child.innerText || '').trim())));
              if (!matches.length) return false;
              matches[matches.length - 1].click();
              return true;
            }
            """
        )
    )


async def _displayed_source_count(page: Page, request_id: str) -> int | None:
    selector = f"#reference-link-anchor-{request_id}"
    for _ in range(40):
        await page.wait_for_timeout(500)
        try:
            text = (await page.locator(selector).inner_text(timeout=250)).strip()
        except Exception:
            continue
        match = re.search(r"(\d+)(?:个|篇)来源", text)
        if match:
            return int(match.group(1))
    return None


async def _visible_source_structure(page: Page) -> dict[str, Any]:
    result = await page.evaluate(
        """
        () => {
          const visible = element => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
              style.display !== 'none';
          };
          const anchors = [...document.querySelectorAll('a[href]')]
            .filter(visible)
            .map(anchor => ({
              href: anchor.href,
              textLength: (anchor.innerText || '').trim().length,
              textHash: crypto.randomUUID ? null : null,
              className: String(anchor.className || '').slice(0, 240),
            }));
          const sourceish = [...document.querySelectorAll('body *')]
            .filter(visible)
            .filter(element => {
              const cls = String(element.className || '').toLowerCase();
              const role = String(element.getAttribute('role') || '').toLowerCase();
              return cls.includes('source') || cls.includes('reference') ||
                cls.includes('citation') || role === 'dialog';
            })
            .filter(element => ![...element.children].some(child => {
              const cls = String(child.className || '').toLowerCase();
              const role = String(child.getAttribute('role') || '').toLowerCase();
              return cls.includes('source') || cls.includes('reference') ||
                cls.includes('citation') || role === 'dialog';
            }))
            .slice(0, 80)
            .map(element => ({
              tag: element.tagName,
              className: String(element.className || '').slice(0, 300),
              role: element.getAttribute('role'),
              text: (element.innerText || '').trim().slice(0, 80),
              textLength: (element.innerText || '').trim().length,
              outerHTML: element.outerHTML.slice(0, 1600),
            }));
          return {anchors, sourceish};
        }
        """
    )
    return result if isinstance(result, dict) else {}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    seen_requests: list[dict[str, str | None]] = []
    seen_responses: list[dict[str, Any]] = []
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(args.profile_dir),
            headless=False,
            proxy={"server": args.proxy},
            locale="zh-CN",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()

            def observe_request(request: Any) -> None:
                path = urlsplit(request.url).path
                if "/api/v1/session/msg/list" in path:
                    seen_requests.append(
                        {
                            "path": path,
                            "session_id": _session_id_from_url(request.url),
                        }
                    )
                elif "/api/v1/app/session/change" in path:
                    session_id = None
                    try:
                        payload = request.post_data_json
                        if isinstance(payload, dict):
                            session_id = payload.get("sessionId")
                    except ValueError:
                        pass
                    seen_requests.append({"path": path, "session_id": session_id})

            async def observe_response(response: Any) -> None:
                path = urlsplit(response.url).path
                if (
                    "/api/v1/session/msg/list" not in path
                    and "/api/v1/app/session/change" not in path
                ):
                    return
                body = await response.body()
                summary: dict[str, Any] = {
                    "path": path,
                    "session_id": _session_id_from_url(response.url),
                    "status": response.status,
                    "body_length": len(body),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                }
                try:
                    decoded = json.loads(body)
                    summary["json_shape"] = _summarize_json(decoded)
                    summary["key_shape"] = _key_shape(decoded)
                    summary["url_hits"] = _url_hits(decoded)[:300]
                    summary["source_cards"] = _extract_source_cards(decoded)
                    if isinstance(decoded, dict) and decoded.get("code") not in (None, 0):
                        summary["error_message"] = decoded.get("msg")
                except (UnicodeDecodeError, ValueError):
                    summary["json_shape"] = None
                seen_responses.append(summary)

            page.on("request", observe_request)
            page.on("response", observe_response)
            await page.goto(
                "https://www.qianwen.com/",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            item = await _wait_for_session_item(page)
            target_session_id = (
                item["sessionId"] if args.session_id == "visible" else args.session_id
            )
            redirect = await _redirect_session_item(page, target_session_id)
            for _ in range(60):
                if any(
                    row.get("path") == "/api/v1/session/msg/list"
                    and row.get("session_id") == target_session_id
                    for row in seen_requests
                ):
                    break
                await page.wait_for_timeout(500)
            target_requested = any(
                row.get("path") == "/api/v1/session/msg/list"
                and row.get("session_id") == target_session_id
                for row in seen_requests
            )
            if not target_requested:
                raise RuntimeError(
                    "signed application request did not target requested session: "
                    f"requests={seen_requests}, responses={seen_responses}"
                )
            await page.wait_for_timeout(10_000)
            triggers = await _source_triggers(page)
            clicked = await _click_source_trigger(page)
            if clicked:
                await page.wait_for_timeout(2_000)
            structure = await _visible_source_structure(page)
            body_text = await page.locator("body").inner_text()
            return {
                "session_id": target_session_id,
                "initial_session_item": item,
                "redirect": redirect,
                "message_requests": seen_requests,
                "message_responses": seen_responses,
                "page_url": page.url,
                "page_text_length": len(body_text),
                "page_text_sha256": _digest(body_text),
                "source_triggers": triggers,
                "source_trigger_clicked": clicked,
                "source_structure": structure,
            }
        finally:
            await context.close()


async def _read_batch_target(
    playwright: Any, args: argparse.Namespace, target: dict[str, str]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "task_pub_id": target["task_pub_id"],
        "session_id": target["session_id"],
        "request_id": target["request_id"],
        "query_sha256": target["query_sha256"],
        "har_path": target["har_path"],
    }
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(args.profile_dir),
        headless=False,
        proxy={"server": args.proxy},
        locale="zh-CN",
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(
            "https://www.qianwen.com/",
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        await _wait_for_session_item(page)
        predicate = lambda response, session_id=target["session_id"]: (  # noqa: E731
            "/api/v1/session/msg/list" in response.url
            and _session_id_from_url(response.url) == session_id
        )
        async with page.expect_response(predicate, timeout=60_000) as pending:
            redirect = await _redirect_session_item(page, target["session_id"])
        response = await pending.value
        body = await asyncio.wait_for(response.body(), timeout=30)
        payload = json.loads(body)
        result.update(
            {
                "response_status": response.status,
                "response_body_sha256": hashlib.sha256(body).hexdigest(),
                "response_key_shape": _key_shape(payload),
                "response_json_shape": _summarize_json(payload),
            }
        )
        turn = _validated_turn(payload, target)
        citations, unresolved = _citations_from_turn(turn)
        displayed_count = await _displayed_source_count(page, target["request_id"])
        raw_source_count = len(citations) + len(unresolved)
        result.update(
            {
                "status": "completed",
                "redirect": redirect,
                "displayed_source_count": displayed_count,
                "raw_source_count": raw_source_count,
                "resolved_source_count": len(citations),
                "unresolved_source_ordinals": unresolved,
                "display_count_matches": (
                    displayed_count is None or displayed_count == raw_source_count
                ),
                "citations": citations,
            }
        )
    except Exception as exc:
        result.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "citations": [],
                "unresolved_source_ordinals": [],
            }
        )
    finally:
        try:
            await asyncio.wait_for(context.close(), timeout=10)
        except (Exception, asyncio.CancelledError):
            browser = context.browser
            if browser is not None:
                try:
                    await asyncio.wait_for(browser.close(), timeout=10)
                except (Exception, asyncio.CancelledError):
                    pass
    return result


def _resume_artifact(args: argparse.Namespace) -> dict[str, Any]:
    if args.artifact_path.is_file():
        artifact = json.loads(args.artifact_path.read_text(encoding="utf-8"))
        expected = {
            "schema_version": "tongyi-history-citations-v1",
            "tenant_pub_id": args.tenant_pub_id,
            "config_version_pub_id": args.config_version_pub_id,
        }
        if any(artifact.get(key) != value for key, value in expected.items()):
            raise ValueError("existing artifact does not match this readback batch")
        if not isinstance(artifact.get("tasks"), list):
            raise ValueError("existing artifact task list is invalid")
        return artifact
    return {
        "schema_version": "tongyi-history-citations-v1",
        "tenant_pub_id": args.tenant_pub_id,
        "config_version_pub_id": args.config_version_pub_id,
        "created_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "tasks": [],
    }


async def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    targets = _load_batch_targets(args.tenant_pub_id, args.config_version_pub_id)
    artifact = _resume_artifact(args)
    results_by_task = {
        row["task_pub_id"]: row
        for row in artifact["tasks"]
        if isinstance(row, dict) and isinstance(row.get("task_pub_id"), str)
    }
    _write_artifact(args.artifact_path, artifact)
    async with async_playwright() as playwright:
        for index, target in enumerate(targets, 1):
            existing = results_by_task.get(target["task_pub_id"])
            if existing is not None and existing.get("status") == "completed":
                event = "tongyi_readback_skipped"
                result = existing
            else:
                event = "tongyi_readback_started"
                print(
                    json.dumps(
                        {
                            "event": event,
                            "index": index,
                            "total": len(targets),
                            "task_pub_id": target["task_pub_id"],
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                result = await _read_batch_target(playwright, args, target)
                results_by_task[target["task_pub_id"]] = result
                artifact["tasks"] = [
                    results_by_task[row["task_pub_id"]]
                    for row in targets
                    if row["task_pub_id"] in results_by_task
                ]
                artifact["updated_at"] = datetime.now(UTC).isoformat()
                _write_artifact(args.artifact_path, artifact)
                event = "tongyi_readback_finished"
            print(
                json.dumps(
                    {
                        "event": event,
                        "index": index,
                        "total": len(targets),
                        "task_pub_id": target["task_pub_id"],
                        "status": result["status"],
                        "resolved_source_count": result.get("resolved_source_count", 0),
                        "unresolved_source_count": len(
                            result.get("unresolved_source_ordinals") or []
                        ),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
    completed = [row for row in artifact["tasks"] if row["status"] == "completed"]
    return {
        "mode": "batch",
        "artifact_path": str(args.artifact_path),
        "tasks": len(artifact["tasks"]),
        "completed": len(completed),
        "failed": len(artifact["tasks"]) - len(completed),
        "citations": sum(len(row.get("citations") or []) for row in completed),
        "unresolved_source_ordinals": sum(
            len(row.get("unresolved_source_ordinals") or []) for row in completed
        ),
        "display_count_mismatches": sum(
            row.get("display_count_matches") is False for row in completed
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--proxy", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--session-id")
    mode.add_argument("--config-version-pub-id")
    parser.add_argument("--tenant-pub-id")
    parser.add_argument("--artifact-path", type=Path)
    args = parser.parse_args()
    if not args.profile_dir.is_dir():
        parser.error("--profile-dir must be an existing isolated profile directory")
    if (
        args.session_id is not None
        and args.session_id != "visible"
        and not _SESSION_ID_RE.fullmatch(args.session_id)
    ):
        parser.error("--session-id must be a 32-character hexadecimal id or 'visible'")
    if args.config_version_pub_id and (not args.tenant_pub_id or not args.artifact_path):
        parser.error(
            "batch mode requires --tenant-pub-id and --artifact-path with "
            "--config-version-pub-id"
        )
    try:
        result = asyncio.run(run_batch(args) if args.config_version_pub_id else run(args))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
