# Draft: root failure does not stop descendants and child errors re-enter model context

> Publication status: **not submitted**. This is a sanitized draft for user review.
> Candidate destination: `openai/codex` GitHub issues or an evidence comment on the related lifecycle issues listed below.

## Candidate title

Root turn terminal failure leaves descendants running, while child policy failures are replayed as model-visible `agent_message` input

## Summary

In a nested Codex multi-agent run, the root turn reached a terminal `Invalid prompt` failure, but its descendant agents continued for at least 25 minutes 59 seconds. After the root failure:

- the parent sent a follow-up to the same child that had just failed;
- that child reproduced the identical error;
- the parent then spawned another depth-2 child;
- the parent's own turn later logged the same error and did not reach its terminal failed state until much later.

Separately, each failed child result was delivered to the parent as an ordinary model-visible plaintext `agent_message`, twice with identical content. Runtime failure state therefore became conversation input rather than remaining structured control-plane state.

The combination creates a feedback loop: a policy/safeguard failure can pollute the parent context, encourage the model to retry or paraphrase the same task, and leave background descendants active after the user-visible root task has already stopped.

## Environment

```text
Codex CLI: 0.149.0 at target child creation/failure
Model: gpt-5.6-sol
Reasoning effort: max
OS: Linux 5.15.0-125-generic x86_64
Multi-agent mode: enabled
Tree depth observed: 2
Incident date: 2026-08-22, Asia/Shanghai
```

The target depth-2 child had a large long-running rollout, but the lifecycle defect is independent of the project content and can be tested with injected synthetic failures.

## Sanitized agent tree

```text
root
└── integration_review
    ├── temporal_migration_audit   # target child
    └── ddl_integrity_audit        # created after root terminal failure
```

Thread IDs and full timestamps can be supplied privately to maintainers. They are omitted from a public report to avoid correlating private session data.

## Exact observed ordering

```text
02:04:27  root turn reached terminal Invalid prompt failure
02:05:53  depth-2 target child failed
02:05:59  parent received a 459-character plaintext agent_message containing that error
02:06:19  parent sent follow-up to the same failed child
02:06:28  same child failed identically again
02:06:35  parent received the same 459-character message again (identical hash)
02:08:41  parent started another depth-2 child
02:15:09  parent logged its own Invalid prompt
02:30:26  parent turn finally reached terminal failed state
```

Descendants therefore remained active for at least `25m59s` after the root turn's terminal failure, and the lineage accepted a new spawn after that terminal event.

## Why this is distinct from ordinary child failure reporting

A child result summary may legitimately be model-visible when the child completes useful work. A terminal runtime/policy error is different:

- it has structured semantics such as failed thread, failed turn, retryability, and causal root turn;
- it should not be indistinguishable from task prose or a user-authored instruction;
- repeating it verbatim changes the parent's future model context;
- retrying the same failed child/context without an explicit recovery policy is usually futile;
- root failure should define whether descendants are cancelled, detached, or quarantined.

The observed implementation had neither a visible retry circuit breaker nor a lineage-wide terminal boundary.

## Source confirmation in 0.149.1

Affected-release source was inspected at commit `ff29a44391deccde0aba0f8390337d7f3c319ea4`; the relevant files have no 0.149.0→0.149.1 fix, and audit-time `main` (`068c49f075cf287a1fe7d1ee36cf005efac922e7`) retains the same core behavior.

1. `session/turn.rs` handles an ordinary model error by emitting the error and breaking only the current turn. It does not initiate descendant cancellation:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/session/turn.rs#L576-L590
2. `session_prefix.rs` converts `AgentStatus::Errored(error)` into model-visible text and explicitly advises the parent to use collaboration tools to give that agent another task:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/session_prefix.rs#L10-L43
3. `session/mod.rs::forward_child_completion_to_parent` wraps that text in `InterAgentCommunication` with `trigger_turn=false`, then sends it with `parent_turn_id=None` and `root_turn_id=None`:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/session/mod.rs#L1958-L2073
4. `multi_agents_v2/message_tool.rs` carries root/parent turn IDs for a new follow-up, but does not reject a receiver whose status is already `Errored` and does not verify that the owning root turn is still live:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/tools/handlers/multi_agents_v2/message_tool.rs#L55-L135
5. `spawn.rs` already propagates the current `root_turn_id` into child spawn options. The missing piece is enforcement: the handler does not gate the spawn on that root turn's terminal state:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs#L143-L164
6. Explicit cancellation exists as the separate `interrupt_agent` operation, but the ordinary root error path does not call it recursively:
   - https://github.com/openai/codex/blob/ff29a44391deccde0aba0f8390337d7f3c319ea4/codex-rs/core/src/tools/handlers/multi_agents_v2/interrupt_agent.rs#L33-L91

The source therefore explains all three observed client behaviors without needing access to private model reasoning: raw error text becomes model input, that text suggests another task, and the collaboration handlers allow that follow-up while the failed root lineage remains unenforced.

## Expected behavior

### Lineage lifecycle

When a root turn becomes terminal, the runtime should atomically choose and expose one of these contracts:

1. **cascade cancel** — interrupt all live descendants and reject new work in that lineage; or
2. **explicit detach** — keep descendants alive under a new detached job/lineage that is visibly owned and can be enumerated/stopped independently.

Silently continuing descendants under a failed root is not acceptable. At minimum:

- all descendants must remain authoritatively enumerable from the root lineage;
- a new spawn/follow-up must be rejected after an ancestor's terminal failure unless an explicit recovery operation reopens or detaches the lineage;
- cancellation must be idempotent and recursive;
- UI state must not imply “stopped” while descendants can still use tools or modify the workspace.

### Error propagation

Child runtime failures should travel through a structured control-plane event, for example:

```text
SubagentTerminalFailure {
  agent_path,
  thread_id,
  turn_id,
  root_turn_id,
  category,
  retryable,
  partial_output,
  correlation_id
}
```

The parent may receive a short, neutral summary for reasoning, but the original error string should not be injected as ordinary `agent_message` task content. Identical `(thread, context, error category)` failures should open a circuit breaker rather than cause an automatic same-context follow-up.

## Deterministic reproduction without policy-sensitive content

An integration test can use a fake child model/provider and synthetic terminal error:

1. start root `R`;
2. spawn parent `P` from `R`;
3. spawn child `C` from `P`;
4. make `R` terminal-fail while `P` and `C` are active;
5. have `C` return a deterministic synthetic `ApiError`;
6. attempt `followup_task(C)` and `spawn_agent(P -> D)`;
7. inspect lineage state and the parent model input.

Assertions:

- `P` and `C` are interrupted/cancelled, or explicitly detached under a documented contract;
- `P -> D` is rejected after `R` is terminal;
- no raw error string appears as a new ordinary model-visible `agent_message`;
- one structured terminal event is recorded, without duplicate replay;
- a repeated follow-up to the same failed context is rejected with a deterministic control-plane reason;
- an idempotent `interrupt_lineage(root_turn_id)` leaves zero running descendants.

Add a second test in which the user explicitly detaches a child before root termination and assert that only this documented exception survives.

## Contribution sketch

Potential client/runtime changes:

1. retain the existing `root_turn_id`/`parent_turn_id` on completion and failure paths instead of replacing both with `None`;
2. maintain an authoritative root-turn liveness registry and, on root terminal failure/interrupt, traverse the spawn graph to cancel all non-detached descendants;
3. gate `spawn_agent`, `followup_task`, and queued delivery on ancestor-lineage liveness and receiver terminal status;
4. represent terminal child errors as typed lifecycle events, not `agent_message` content; render a bounded neutral summary only when the model truly needs it;
5. remove the unconditional “give it another task” suggestion for non-retryable failures and add a failure fingerprint/circuit breaker for same-thread, same-context retries;
6. make “list descendants” and “interrupt lineage” authoritative, recursive, and idempotent;
7. add race tests for child completion concurrent with root failure, queued follow-ups, nested spawn, repeated interrupt, and explicit detach.

Exact source symbols should be pinned to a specific upstream commit before a PR is attempted. The implementation should preserve backward compatibility for clients that do not yet understand the new structured event.

## Related reports

- https://github.com/openai/codex/issues/19197 — persistent orphaned subagents and incomplete lifecycle controls.
- https://github.com/openai/codex/issues/38237 — missing hard depth/cap enforcement and lineage-wide cleanup.
- https://github.com/openai/codex/issues/32753 — inter-agent task text is not operator-observable, which makes causal auditing harder.
- Official subagent guidance on context pollution and returning concise summaries: https://learn.chatgpt.com/docs/agent-configuration/subagents

This report adds a narrower causal case: descendants accepted follow-up/spawn work after a specific root turn terminal failure, while the same child error was duplicated into the parent model context.

## Privacy and publication choice

Do not publish full rollouts, encrypted task payloads, private repository names/content, system/developer instructions, private reasoning, credentials, or local database files. Before filing, search for a newer exact duplicate and decide whether this evidence belongs in a new issue or as a comment on `#19197`/`#38237`.
