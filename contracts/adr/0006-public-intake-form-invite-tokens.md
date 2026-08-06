# ADR-0006: Public intake form via single-use invite tokens

Status: Accepted · 2026-08-03

GEO Platform V2 exposes an anonymous customer intake-form channel (`/api/v2/intake-form/*`)
so a customer can fill the intake questionnaire without a platform account. Access is gated
by per-project invite tokens issued by operators
(`POST /api/v2/projects/{pub}/intake/invites`, `intake:write`).

Decisions:

- **Invite token model.** Tokens are `secrets.token_urlsafe(32)`; the plaintext is returned
  exactly once in the issue response and only its sha256 hash is stored
  (`platform.intake_invite.token_hash`). Each invite carries `expires_at` (default 168h),
  `revoked_at`, `submitted_at`, and an AI call budget (`ai_quota`, default 3) shared by
  `ai-research` and `query-suggestions`; exhaustion returns 429 `quota_exhausted`.
- **Anonymous RLS path.** Token-domain endpoints do not use `get_principal`. The dependency
  opens the narrow transaction-local RLS escape `app.auth_scope='intake_invite'` (same
  precedent as native-session auth lookup, migration s06_0003), resolves the invite by token
  hash, then injects the invite's `tenant_id` into the normal tenant RLS context. All
  subsequent queries in the request are ordinary tenant-scoped queries; there is no
  cross-tenant surface. Failure states return 403 with semantic codes
  (`invite_token_invalid` / `_expired` / `_revoked`) without disclosing token details.
- **Submission gate.** `POST /submit` requires the compliance fields `truth_confirmed=true`
  and a non-empty `filler_name` (422 `submit_incomplete` listing what's missing), is
  idempotent (replay returns the original state), and flips the invite into a read-only
  state: every write endpoint then returns 409 `invite_submitted`.
- **Reuse of the intake module.** Profile/promo/trigger storage, vocab fail-closed
  validation, DLP (`assert_secret_free`) and AI research stay in the `intake` module; the
  public channel reuses them and only adds invite resolution, quota accounting, and
  brand/competitor editing plus read-only SiliconIndex previews (snapshot directory missing
  degrades to `{available:false}` and never errors). All writes keep the AuditLog +
  idempotency conventions, with `actor_pub_id = invite.pub_id` on the anonymous side.

Consequences: the intake data plane stays single-sourced; revoking or expiring an invite
instantly closes the anonymous channel without touching customer data; AI cost per invite is
hard-capped. CORS origins are configuration (`GEO_CORS_ORIGINS`) so the public form origin
can be admitted without code changes.
