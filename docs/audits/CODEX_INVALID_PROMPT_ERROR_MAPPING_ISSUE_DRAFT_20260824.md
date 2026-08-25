# Draft: mid-stream policy failure becomes generic `Invalid prompt` and leaves partial history

> Publication status: **not submitted**. This is a sanitized draft for user review.
> Candidate destination: `openai/codex` GitHub issues, with a possible follow-up pull request limited to client-side classification, observability, and tests.

## Candidate title

Mid-stream policy-coded failure is surfaced as generic `Invalid prompt` / `CodexErrorInfo::Other`, while failed-response items remain in history

## Summary

Codex 0.149.x maps a Responses `response.failed` event with `code="invalid_prompt"` to a message-only generic invalid request. In the inspected sessions, new reasoning/output items had already been emitted and persisted before the terminal policy message, which was then persisted as:

```json
{
  "codexErrorInfo": "other",
  "additionalDetails": null,
  "message": "Invalid prompt: your prompt was flagged as potentially violating our usage policy..."
}
```

The local debug log also contains `unhandled responses event: "error"` immediately before terminal failure. The raw SSE/WS body was not retained, so this report does not claim whether the service emitted only `response.failed`, only `error`, or both. The versioned source mapping below nevertheless explains why the terminal category becomes `Other`.

This presentation makes a mid-generation safeguard intervention look like a pre-flight syntax/input validation failure caused by the user's latest visible prompt. It also omits the upstream request identifier needed for support escalation and retains failed-response items in replayable history.

This draft does **not** request disclosure of classifier internals and does **not** propose bypassing safeguards. It asks the client to preserve the distinction between an initial invalid request and a response that failed after partial generation, together with non-sensitive correlation metadata.

## Environment

```text
Codex CLI at first child failure: 0.149.0
Codex CLI in later root failures/control tests: 0.149.1
Model: gpt-5.6-sol
Reasoning effort in target child: max
OS: Linux 5.15.0-125-generic x86_64
Node.js: v24.6.0
Occurrence dates: 2026-08-22 and 2026-08-24, Asia/Shanghai
```

The same symptom occurred in a process that subsequently logged `client_version="0.149.1"`; upgrading to 0.149.1 did not eliminate it.

## Sanitized observed behavior

Four inspected failed turns shared this ordering:

1. a Responses sampling began normally;
2. at least one new reasoning item was streamed and persisted;
3. the stream delivered an error / `response.failed` condition;
4. Codex surfaced `Invalid prompt: ... potentially violating our usage policy`;
5. the persisted error category was `other`, with no additional details or request ID.

The strongest long-turn case had two successful remote compactions before the error:

```text
219,559 input tokens -> 21,325 input tokens
231,622 input tokens -> 21,174 input tokens
last successful sampling before failure: 68,209 input tokens
model context window recorded by Codex: 258,400
```

The reported `14,594,218 input_tokens` was the cumulative input across 105 successful samplings in one turn, not the size of one request:

```text
48,564,849 cumulative input after
-33,970,631 cumulative input before
=14,594,218 turn input
```

There were no `Error running remote compact task` or `failed to compact` records in the complete failure window. A later turn in the same root thread completed successfully after a larger 164,727-token sampling. This rules out a fixed context-window threshold as the direct explanation.

## Controls

### The visible status sentence is not a stable trigger

A fresh, ephemeral 0.149.1 session with no prior history safely processed the exact harmless sentence that appeared before one failure and returned the requested sentinel:

```text
Input: treat `当前会话已恢复，我继续完成原任务。` as quoted text and return EXACT_OK
Result: EXACT_OK
Exit code: 0
```

The sentence was an already displayed assistant message, not a hidden user/system/developer prompt. No new model-visible natural-language instruction was inserted between that assistant output and the failing continuation.

### A fresh artifact-based handoff works without the old conversation

A separate ephemeral/read-only session read only a 10,049-byte handoff document, verified the frozen source document's SHA-256/line/byte counts without reading its body, identified the three requested collection modalities, and exited successfully. This is a practical mitigation for context pollution, but it does not repair the client error semantics.

## Relevant source behavior in the affected releases

Source was checked on 2026-08-24 at:

```text
rust-v0.149.0: 758ef40f50c1a458425c7cfbf1eb12cbc07af0b0
rust-v0.149.1: ff29a44391deccde0aba0f8390337d7f3c319ea4
main:           068c49f075cf287a1fe7d1ee36cf005efac922e7
```

The relevant error, stream, protocol, turn, and multi-agent files have no 0.149.0→0.149.1 fix. Audit-time `main` retains the same core mapping/partial-stream behavior.

1. `codex-rs/codex-api/src/sse/responses.rs` captures the upstream `x-request-id` in `spawn_response_stream` and stores it on `ResponseStream`:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/codex-api/src/sse/responses.rs#L55-L59
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/codex-api/src/sse/responses.rs#L96-L99
2. The `response.failed` branch maps both `invalid_prompt` and `bio_policy` to the generic `ApiError::InvalidRequest`:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/codex-api/src/sse/responses.rs#L408-L446
3. `api_bridge::map_api_error` converts that to message-only `CodexErr::InvalidRequest`, while `to_codex_protocol_error` has no matching branch and falls through to `CodexErrorInfo::Other`:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/codex-api/src/api_bridge.rs#L35-L53
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/protocol/src/error.rs#L425-L455
4. `CodexErrorInfo` exposes `CyberPolicy` and `MisalignmentPolicyViolation`, but no generic safeguard/prompt-policy category; `StreamErrorEvent` has only a free-form `additional_details` field:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/protocol/src/protocol.rs#L1767-L1804
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/protocol/src/protocol.rs#L3415-L3425
5. The HTTP SSE request ID is available to telemetry/inference trace, but not added to the terminal protocol error. The WebSocket path constructs `ResponseStream { upstream_request_id: None }` even though handshake response headers are already available:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/client.rs#L2117-L2138
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/codex-api/src/endpoint/responses_websocket.rs#L332-L335
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/codex-api/src/endpoint/responses_websocket.rs#L488-L539
6. `map_response_events` immediately forwards `OutputItemDone`; if a later event fails, it records a failed trace and forwards `Err` without retracting the completed item. The downstream turn history therefore can retain items produced by a response that never completed:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/client.rs#L2028-L2068
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/client.rs#L2117-L2138

The local persisted event and the source together show exactly where the upstream code becomes `Other`. Request IDs may still exist in private telemetry, but they are not present in the persisted terminal event available to the user. For WebSocket responses, the structured `ResponseStream` request ID is explicitly absent.

## Expected behavior

When `response.failed` is received, Codex should preserve at least:

- upstream `error.type`, `error.code`, and sanitized `error.param` if present;
- upstream request ID and response ID, where available;
- whether any output/reasoning/tool-call item was emitted before failure;
- whether the failure happened during generation rather than before generation;
- a structured, stable client category distinct from generic `Other`.
- whether any failed-response item was excluded from future model history or deliberately retained as marked partial data.

Suggested user-facing wording for a mid-stream `invalid_prompt` with a policy message:

```text
Generation was interrupted by a safety check after partial output.
This does not necessarily mean your latest visible message was invalid.
Do not automatically retry the same full context. Reference: <request-id>.
```

If the service provides only the ambiguous `invalid_prompt` code, the UI should avoid claiming which classifier fired. `safeguard_intervention` or `prompt_policy_intervention` is safer than inferring `cyber` or `bio` from message text.

## Deterministic client-side reproduction

No unsafe prompt is needed. Add an SSE fixture that emits:

1. `response.created`;
2. one valid `response.output_item.done` reasoning item;
3. `response.failed` with `type="invalid_request_error"`, `code="invalid_prompt"`, and a policy-style message;
4. stream close.

Assert that:

- transient deltas may remain visible in the UI, but the done item is not committed into replayable model history without `response.completed`;
- the terminal category is not `CodexErrorInfo::Other`;
- `partial_output=true` (or equivalent) is serialized;
- upstream request/response correlation data is retained;
- a subsequent full-create/follow-up does not contain the failed response's reasoning item.

Add controls for `[OutputItemDone, response.completed]` to preserve the current successful ordering, plus separate fixtures for `bio_policy`, `cyber_policy`, `misalignment_policy_violation`, pre-stream HTTP 400, WebSocket request-ID propagation, and a normal invalid request unrelated to policy so the categories do not collapse again.

## Contribution sketch

A narrowly scoped client contribution could:

1. replace `ApiError::InvalidRequest { message }` for policy-coded failures with a structured variant carrying `error_type`, `error_code`, `message`, optional `param`, and response ID;
2. propagate HTTP and WebSocket request IDs into the terminal turn error, not only telemetry;
3. separate transient stream presentation from durable conversation commit: buffer `OutputItemDone` for model history until `response.completed`, and discard/quarantine it on terminal failure while retaining a diagnostic trace;
4. add `CodexErrorInfo::SafeguardIntervention` (or an equivalent backward-compatible tagged variant) instead of `Other`, together with `partial_output`;
5. render a neutral mid-generation-interruption message in CLI/TUI/Desktop clients;
6. add SSE/WS parser, core mapping, history atomicity, protocol serialization, persistence, and UI snapshot tests.

Classifier-quality changes themselves are service-side and cannot be fixed by an open-source client PR. The open-source contribution can nevertheless make false positives diagnosable, prevent misleading attribution to the latest visible sentence, and avoid unsafe blind retries.

## Related reports

- https://github.com/openai/codex/issues/7250 — older `invalid_prompt` report during remote compaction; this incident is different because compaction succeeded and failure followed partial generation.
- https://github.com/openai/codex/issues/12011 — similar user observation that Codex was already working and effectively generated the triggering continuation; the current draft adds event ordering, token/compaction controls, persisted error fields, and source mapping.
- Official GPT-5.6 safeguard behavior: https://developers.openai.com/api/docs/guides/latest-model#safeguards

## Privacy and support handoff

The public report should include only the sanitized counters/timeline above. Do not attach full rollout files, encrypted agent payloads, system/developer instructions, private reasoning, repository content, credentials, or local database files. If maintainers request a trace, provide thread/turn IDs and exact timestamps through a private support channel only.
