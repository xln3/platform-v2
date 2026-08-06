# S01-003 — Doubao live collection adapter handoff

Status: implemented by review session, pending S04 review  
Owner: S01 (area), implemented out-of-band  
Date: 2026-07-27

## Gap

`collect_with_adapter` in `workflows/activities/collection.py` is a fail-closed stub
(`adapter_not_configured`). No live platform adapter existed for Doubao web collection.

## Provenance

This adapter was implemented by a review session on the user's direct instruction. It is a
new addition inside the S01 ownership area and did not go through the regular ownership
negotiation flow; S04 must review it before the uncommitted wiring below is committed.

## Added files (committed by the review session)

- `workflows/activities/doubao_adapter.py` — Doubao web collection adapter registered as
  `@activity.defn(name="collect_with_adapter")`. Selectors, submit-confirm, wall detection,
  CDP completion-stream capture, SSE assembly and references extraction are ported from the
  live-verified legacy implementation (`server/proxyllm/doubao_client.py`, `capture.py`,
  `sse_parser.py`, `login_state.py`). Happy path + wall classification only; session-heal,
  share export, softban tagging and HAR capture are deliberately not ported.
- `tests/unit/test_doubao_adapter.py` — 6 tests, browser layer fully mocked via session
  dependency injection; no real browser is launched.
- `docs/contract-gaps/S01-003-doubao-live-adapter-handoff.md` — this file.

## Uncommitted changes left for S04 review (do NOT lose)

- `workflows/workers/main.py` — registration gate: reads `GEO_COLLECTION_ADAPTER` (default
  empty = original fail-closed stub untouched); when `doubao`, the `collect_with_adapter`
  registration is swapped to the new adapter implementation.
- `pyproject.toml` — adds `playwright==1.61.0` to dependencies. **Review follow-up:** the
  live smoke showed vanilla Playwright's webdriver fingerprint makes Doubao silently swallow
  the send (risk-control no-op; legacy live evidence 2026-07-15). The adapter now prefers
  `patchright` (legacy production driver, anti-detection patched build) and falls back to
  `playwright` for development only. `patchright==1.59.1` is already installed in the venv
  and should be declared in `pyproject.toml` alongside (or instead of) `playwright`.

Both files carry other sessions' in-flight changes and were intentionally left uncommitted
by the review session.

## v1 boundaries

- `mode='normal'` only; `deep_think` → `ApplicationError(type="unsupported_mode",
non_retryable=True)` (the 5-minute activity budget cannot hold a deep-think stream).
- Single account via env: `GEO_DOUBAO_PROFILE_DIR` (required persistent browser profile;
  missing → `adapter_not_configured` non-retryable). The logged-in profile must be placed
  there manually (legacy OTP login path); Vault-decrypted profile injection is an open S04
  item and is NOT part of v1.
- Optional `GEO_DOUBAO_PROXY_URL` is read from env only, never logged in cleartext
  (masked to `scheme://host:port`), never written into payloads or evidence paths.
- Wall taxonomy (screenshot evidence captured before raising; message carries the evidence
  path, no secrets): login/realname wall → `wall_login_required`; captcha → `wall_captcha`;
  send wall / rate-limit / cloak → `wall_send`; all non-retryable. Incomplete captures
  (truncated stream, empty answer, accepted-but-no-stream) raise retryable
  `answer_capture_incomplete` — honest failure, zero synthesis.
- Success gate: submission accepted (composer cleared) AND `/chat/completion` stream
  `loadingFinished` AND non-empty answer AND no wall features. Result returns
  `quality_state="live_valid"` with `screenshot_ref=file://<evidence>`; references are
  appended to `answer_text` under a `参考来源：` section.
- Heartbeats are pumped every 10s with stage info (workflow budget: 30s heartbeat timeout).

## Rollout steps

1. `.venv/bin/pip install patchright==1.59.1 playwright==1.61.0` then `.venv/bin/playwright
install chromium` (both done on this host; chromium-1228 present). Production runs must
   go through patchright (see the dependency note above).
2. Provision a logged-in Doubao profile into `GEO_DOUBAO_PROFILE_DIR` (reuse the legacy
   OTP login flow), set `GEO_DOUBAO_EVIDENCE_DIR` / `GEO_DOUBAO_PROXY_URL` /
   `GEO_DOUBAO_HEADLESS` as needed.
3. Set `GEO_COLLECTION_ADAPTER=doubao` in the worker environment and restart the V2 worker.
4. Start one collection run and verify: task completes with `quality_state=live_valid`,
   answer persisted, screenshot evidence written under the evidence dir.
   (2026-07-27 live smoke with the Shanghai profile copy + wukong proxy, headed: SMOKE-OK,
   real answer captured end-to-end.)

## 20260727 补记（冒烟后）

- 驱动定案 **patchright**（与旧链生产同款）：vanilla playwright 的 webdriver 指纹会被豆包风控静默吞发送（live 实证）。venv 已装 patchright==1.59.1，pyproject 声明仍留 S04 补。
- live 冒烟通过：上海号 profile 副本+悟空代理，headed，SMOKE-OK len=54 真实回答。冒烟 profile 副本用后已删（会话秘密不留痕），`runtime/` 已加 .gitignore（与 workers/main.py、pyproject.toml 同为未提交，随 S04 一并审查）。
