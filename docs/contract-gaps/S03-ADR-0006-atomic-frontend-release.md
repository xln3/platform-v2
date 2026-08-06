# S03-ADR-0006: Isolated and recoverable four-application frontend release

- Status: accepted
- Date: 2026-07-28
- Owner: S03 frontend experience

## Context

Production Nginx serves each React application directly from
`apps/<application>/build/client/`. React Router empties its configured build directory before producing a new
bundle. Building into `build/` therefore exposes an avoidable interval in which production can observe missing
or partially written HTML and assets. Playwright already avoids this path by using `build-e2e/`, but there was
no equivalent isolated production candidate or automated rollback boundary.

The four application routes are independent Nginx locations, so they do not require a shared runtime process.
Changing those locations to a new release-root symlink would cross the S04 deployment boundary and require a
separate coordinated infrastructure decision.

## Decision

1. `GEO_FRONTEND_RELEASE_BUILD=1` selects `build-release/`. It is mutually exclusive with
   `GEO_E2E_BUILD=1`; ordinary builds retain `build/`.
2. `scripts/frontend_release.py prepare` builds each application directly, outside Turbo's production output,
   with an allowlisted environment that contains no `VITE_*`, database, Cookie, token or other ambient secret
   variables.
3. A prepared release records a deterministic fingerprint of all four application inputs, shared frontend
   packages, the OpenAPI contract and generated-client manifest. Each output file is recorded by relative path,
   size and SHA-256. Symlinks, non-regular files, source maps, missing index references and contract identity
   fixtures fail preparation.
4. Activation first revalidates current source and every candidate file. It then uses Linux
   `renameat2(RENAME_EXCHANGE)` to exchange each complete `build/` tree with its verified candidate on the same
   filesystem. At no point is an individual application path absent or partially populated.
5. The four exchanges run while termination signals are deferred. A required external verification command
   follows. Any exchange, manifest or verification failure reverses every completed exchange in reverse order
   and verifies the restored hashes.
6. A successful activation retains the previous four complete trees as the release's rollback candidates.
   Manual rollback performs the same whole-directory exchange and hash verification.
7. A verification failure leaves the restored candidate in a retryable
   `rolled_back_after_failed_activation` state. The same manifest can be inspected and retried after source and
   candidate hashes are revalidated; rebuilding is not required.
8. The manifest records only bounded hashes, counts, timestamps and stable failure codes. At most the latest 32
   failed activation events are retained. Verification command arguments and environment values are never
   persisted.

## Consequences

- A production build can be prepared and inspected without mutating live assets.
- Each application switch is atomic and recoverable. The four independent locations have a bounded
  multi-application transition window of four directory-exchange system calls; there is no missing-file window.
- True one-operation switching across all four locations would require an S04-owned Nginx release-root change.
  That remains outside this ADR and is not silently inferred.
- Activation is Linux-specific and fails closed when `renameat2(RENAME_EXCHANGE)` or same-filesystem placement is
  unavailable.
- A safe retry preserves the bounded prior failure history while replacing the transient top-level failure code
  only after a subsequent activation verifies successfully.
- Production activation must provide the authenticated 45-check browser acceptance command. Source/unit checks
  alone do not qualify a release.

## Verification

- `tests/unit/test_frontend_release.py` injects a failing verifier and proves that all four prior bundles are
  restored byte-for-byte. It then inspects and reactivates that exact restored candidate without rebuilding.
- The same suite proves successful activation plus manual rollback, rejects unsafe bundle contents and verifies
  that the build environment drops secret and `VITE_*` variables.
- `scripts/check_frontend_contracts.py` requires all four React Router configurations to preserve the isolated,
  mutually exclusive release build mode.
