# S03-001 — Product experience API and safe account projections

Status: source-owned contracts implemented and mounted in repository and production; external/populated-data gates remain
Owner: S03  
Consumers: Customer Web, Operations Web, Report Studio, Intelligence Web

## Gap

The repository-generated OpenAPI now exposes 128 paths for identity, customers, projects/resources/config freeze,
collection runs, platform accounts/authorizations/profiles/leases/health, interventions, events, revocation,
quarantine, break-glass, Anti-GEO evaluation/admission and the concurrently owned SOP workflow. `pnpm check:api`
passes and S03 applications consume the shared `openapi-fetch<paths>` client for available shared calls.

Round 170's S03 projection change adds no server path; the larger 128-path manifest includes concurrent
other-owner API work preserved in the shared workspace. It makes the remaining public project page, analytics
history and nested Report Detail objects exact browser allow-lists, and changes the direct untrusted analytics,
Report, evidence and Intelligence projectors to accept `unknown` before runtime validation. Compiler key-set
tests, hostile runtime extensions and the frontend architecture guard freeze all 64 projected wrappers. API
client 74/74, the focused three-viewport matrix 36/36 and the complete four-application matrix 495/495 pass.
Release `s03-round170-exact-allow-list` passes isolated-candidate and active-production real-session acceptance
45/45 plus production mock scan 29/29. Focused, full, candidate, production and mock evidence SHA-256 values are
`546eec692ce778cf8f299f5f94036e4bdf73cfaa80d4f7a9bc0df78430439a7a`,
`6c72a3000571187d7deb273d2800492edaef2819e6b937b98811a21900f6c996`,
`5702167dcb8c4fa7357b2578a963aff00f207e02c5a5a184f77b7d4547cdc457`,
`685436f915d0ee7a483c7f194b918edb1a473309ea63e206d014cab9226b9eee` and
`38273c3b7225fcabeb21e5d643a95330d7b1e5517e79dcaa9fb1b3cd72320568`.

The refreshed external audit still has zero platform accounts, profiles, active profile DEKs, adapters, current
authorizations, customer device bindings and terminal tasks; only `admin` is verified as a real role. Unified
completion therefore remains 7/10 with the same six external-authority/real-sample requirements. External and
unified evidence SHA-256 values are
`45490c5a87057919de39dd3d28d86bf98d1d07c850cac13d821c7262e503bdf0` and
`3f74bd21617ed849b8fa27a67077b781a515ecbc37acebe64a8970b3fe780292`. The persistent execution goal
remains paused; it was not marked complete or blocked. No S01-owned Operations execution business source was
modified.

Round 169 changes no API path or server schema. It makes underspecified/raw server response aliases private and
publishes explicit safe projections for analytics anchors/delta/competitors, Intelligence detail/visual diff,
project resources and Report Detail. Structured report objects are now nominal `SafeStructuredRecord` values
that can be created only after the existing recursive DLP boundary succeeds; a plain unknown record cannot enter
the public browser type. All four applications' imported API types have zero compiler-reachable `unknown` string
indexes. API client 62/62, all four component suites, focused three-viewport 36/36 and the exact
four-application matrix 495/495 pass. Deployed release `s03-round169-safe-public-projections` passes
isolated-candidate and active-production real-session acceptance 45/45 plus production mock scan 29/29. The
refreshed external audit remains zero for platform accounts, profiles, active profile DEKs, adapters, active
authorizations, customer device bindings and terminal tasks; only `admin` is verified as a real role. Unified
completion remains 7/10 with the same six external-authority/real-sample gates. External and unified evidence
SHA-256 values are `36b01408b3175885ceb42d3d0c1da7e57db936a182d52f0fcf7392ca70cc5384` and
`ab921818dc6944b249cadd674586fb90f55b05e4977b79e21cc17aa5ed41df28`. No S01-owned Operations
execution business file was modified.

Round 168 changes no API path or server schema. A compiler-backed reachability audit of all 125
`@geo/api-client` exports imported by S03-owned surfaces found no secret-capable field; the only keyword match is
permitted authorization-expiry metadata. It did find that every projected wrapper's optional test client
parameter still exposed the entire 100-path raw client type. All 63 wrappers now accept a fieldless override and
unwrap the generated client only inside their exact request expression. The guard freezes all 63 pairs and
forbids S03 application imports of the raw client, while the unmodified S01 Operations execution consumer remains
the explicit ownership exception. API client 58/58, four application component suites, focused three-viewport
39/39 and the exact four-application matrix 495/495 pass. Deployed release
`s03-round168-projected-client-override` passes isolated-candidate and active-production real-session acceptance
45/45 plus production mock scan 29/29. The refreshed external audit remains zero for platform accounts, profiles,
active profile DEKs, adapters, active authorizations, customer device bindings and terminal tasks; only `admin`
is verified as a real role. Unified completion remains 7/10 with the same six external-authority/real-sample
gates. External and unified evidence SHA-256 values are
`83206aab3e1b815e1e21ecc7b53bbac3e11123f2ce026a900ae153b8d55b1321` and
`ac5ec43fe640c4edcc1bc69922cc7131b8b73b47c8848ce8a807db34ea1613c4`.

Round 167 changes no API path or server schema. It makes the generated analytics overview response private and
derives a browser-safe `Omit<..., 'trace_tokens'>` metric so server trace capabilities are absent—not merely
emptied—from exported Customer types, client projections and React state. Hostile contract mocks retain a Bearer
trace-token canary only as negative input. API client 57/57, Customer components 25/25, focused three-viewport
monitoring 12/12 and the exact four-application matrix 495/495 pass. The deployed release
`s03-round167-safe-analytics-types` passes isolated-candidate and active-production real-session acceptance
45/45 plus production mock scan 29/29. The refreshed external audit remains zero for platform accounts, profiles,
active profile DEKs, adapters, active authorizations, customer device bindings and terminal tasks; only `admin`
is a verified real role. Unified completion remains 7/10 with the same six external-authority/real-sample gates.
External and unified evidence SHA-256 values are
`6d3acdff68602484f06b08db89cff9653f283bec518fe224c6da7b291becd263` and
`e16d7a099009ea28c1d875364d90a6000d3439dd18d19eef674ea945384d5091`. No S01-owned
Operations execution business file was modified.

Round 151's refreshed count-only production audit still finds zero platform accounts, profiles, active profile
DEKs, adapters, current authorizations, customer device bindings and terminal tasks; `admin` remains the only
verified real role. The unified audit therefore remains 7/10 with the same six external-authority/real-sample
requirements open. External evidence SHA-256 is
`2793ede253ec3450bc7cc45cec7e54a5f8cf9a06fb79025621357599d7a9e848`; unified evidence SHA-256 is
`1bd8686a473b1f3d01f9eb19c64b10d4c7389e532bd139575a105304f08b80a3`. No API or generated-client contract
changed.

Round 150 adds no endpoint, hand-written domain type or S01 execution implementation. It strengthens the shared
browser contract so every visible enabled interactive target encountered by the accessibility checks must
measure at least 24×24 CSS pixels, with diagnostics restricted to ordinal/tag/dimensions and capped at 25. The four-app
accessibility matrix passes 12/12 and all 99 visual baselines pass at the three required viewports. Applying the
same assertion to all Operations media-price states found and fixed three 16 px filter checkboxes; that focused
matrix passes 45/45 and its inspected three-viewport visual baseline passes 3/3. Evidence
`tests/s04-evidence/e2e-results-s03-round150-media-targets-final.json` has SHA-256
`58cfeb59bc049fb5e31868770bae1d160e6db548cc363adeb9a64a30b0062345`, and visual evidence
`tests/s04-evidence/e2e-results-s03-round150-media-visual-final.json` has SHA-256
`23d15a5a31f77f01e3f0777fee90ded87f4a8511b83fa730919e60fde2fa3cdb`. All changes remain in S03-owned
experience CSS, tests, visual evidence and contract guards.

The available repository API then revealed target-size cases absent from the bounded mocks: up to 200 reference
links in the real 20,670-row media artifact measured 19 px, and the Intelligence live history selector measured
23 px. Both now expose 24 px targets without changing API reads or domain state. The exact final source passes
the full four-application matrix 495/495 in 578,393 ms with zero unexpected, flaky, skipped, test errors or
attachments. Evidence `tests/s04-evidence/e2e-results-s03-round150-full-real-api-final.json` has SHA-256
`68399129036da42e0c6a97d04407b2b4c87ae7f6d142d5ba62d7c25eaf07f8d1`. No server schema or generated client
changed.

Round 149 adds no endpoint, runtime data projection or hand-written type. It extends the shared
`prefers-reduced-motion: reduce` rule from smooth scrolling/transitions to animations on elements and both
pseudo-elements. The E2E contract enables the real browser preference and proves that a temporary five-second
animation/transition probe computes to `animation-name: none` and zero durations in all four applications and
all three viewports. The changed conditional branch passes 12/12; evidence
`tests/s04-evidence/e2e-results-s03-round149-reduced-motion.json` has SHA-256
`4664d057db4367ebfb8b3cb94cb7a5f8063745ab966e2acd10593bcf77725382`. Default-motion application/API paths
are unchanged from Round 148's 495/495 matrix.

Round 148 adds no endpoint or hand-written domain type. It closes the shared interaction-accessibility gap with
contrast-safe `:focus-visible` tokens, a distinct dark-surface focus token, Windows forced-color `Highlight`
mapping and bounded top-bar target sizes. Browser tests now verify the actual pseudo-class match, computed 3 px
outline, 24×24 minimum target and forced-color behavior in all four applications at all three required
viewports. The combined accessibility/visual matrix passes 111/111; after an intentional safe update to the
three focused Intelligence textarea baselines, the exact current source passes the complete real-API matrix
495/495 with no unexpected, flaky, skipped, top-level/test errors or runtime attachments. Evidence
`tests/s04-evidence/e2e-results-s03-round148-full-real-api-final-verified.json` has SHA-256
`52e15f08d1b06f84f39e546b9c2ec62cce2aec3069804c80a617e735425fe0e7`. This round changes only shared
experience CSS, E2E accessibility assertions/baselines and their contract guard; it does not modify an S01
execution business file or an API schema.

Round 147 proves the already deployed controlled terminal response ceiling against a genuinely gzip-compressed
HTTP response. The compressed body is below 64 KiB, the fetch-decoded stream is above 64 KiB and contains a
Bearer canary, and the existing `strictFetch` path rejects it as `terminal_response_too_large` before JSON
parsing without echoing the canary. This is test/guard-only: runtime `background.mjs`, manifest 0.1.6 and the
signed production release remain byte-identical, so no release drift was created. The extension now passes
13/13; all 18 frontend tasks, browser-runtime safety 9/9 and Python 223/223 also pass.

Round 146 proves the shared response ceiling against a genuinely gzip-compressed HTTP response without changing
OpenAPI, the generated client contract or any domain implementation. A test-only server sends an identity body
whose encoded length is below 25 MiB but whose Chromium-decoded length is above 25 MiB. The browser completes
that small transfer, measures the decoded bytes and supplies them to the exact production boundary; all four
applications reject it before generated parsing, Query state or business reads with no secret canary retained
and literal-zero console, page and failed-request counts. Focused evidence passes 12/12, the complete shared-shell
matrix passes 48/48 and the exact four-application real-API matrix passes 495/495. Full-matrix evidence
`tests/s04-evidence/e2e-results-s03-round146-full-real-api-final.json` has SHA-256
`0a765e8ffca5ba8267a55f81b47ac7febd5d426b08419c3de6c052c0736f3d90`. The 23:23 production recheck confirms
all four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the current local build. Reconciliation evidence SHA-256 is
`54e3bb48c2d238d704e85c4bc56fa52f9b5ea85501020dd73e75dca80da99b23`.

Round 145 adds a shared browser-decoded JSON ceiling without changing OpenAPI or any domain implementation.
Every JSON response first rejects a malformed or declared-over-limit `Content-Length`, then preflights a clone
branch and counts decoded bytes before generated parsing or projection. Anything above 25 MiB cancels both
branches and returns only the generic unavailable error; a valid response keeps the original browser Response
for the generated parser, avoiding a reconstructed-stream request lifecycle. API client passes 56/56, the
shared-shell matrix passes 36/36, the real 20k+ row artifact passes nine consecutive three-viewport runs and
the exact current four-application real-API matrix passes 483/483 with zero unexpected, flaky, skipped or
top-level errors. Full-matrix evidence
`tests/s04-evidence/e2e-results-s03-round145-full-real-api-final.json` has SHA-256
`f32d1c31f36a74867fa19373d60cc67f4db0bcd1c09341aaaf86bc647a6ddbac`. The 22:54 production recheck confirms
all four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the current local build. Reconciliation evidence SHA-256 is
`46de245938628b121122e58b0b2f046736da9d997823e3c0c0101f410beb1218`.

Round 144 separates the generated 202 accepted-start receipt from authoritative refresh-status projections
without changing the server schema. The accepted receipt must be an exact safe `running` acknowledgement with
null timestamps and no source payload. An authoritative `never` state must be completely empty; `running`,
`done` and `failed` require valid calendar timestamps in nondecreasing order, a nonblank bounded message and
exactly all three source records. A pending source must remain zero-row/note-free and `done` cannot retain one.
Invalid combinations stay unavailable and never become browser success/failure claims. API client passes 55/55,
media components pass 27/27, Operations passes 33/33, the focused three-viewport matrix passes 45/45 and the
exact four-application real-API matrix passes 471/471 with zero unexpected, flaky or skipped results.
Full-matrix evidence `tests/s04-evidence/e2e-results-s03-round144-full-real-api-final.json` has SHA-256
`eb662c9b9f8a9f084d48a11d8bf71c91de26d74c16ec8c1caca945dbef43bdaf`. The 21:50 production recheck confirms
all four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the local Round 144 build. Reconciliation evidence SHA-256 is
`73a72c943a7fa58499df85b271c20600decd37359ed669e03b160e8c6e2d1eea`.

Round 143 closes two more consumer-side terminal-state gaps without changing the generated server schema. A
`done` refresh projection is now valid only when it carries bounded timestamps/message and exactly all three
source records with no source still `pending`. Operations records the pre-refresh safe status revision and
refuses to complete an accepted write from that unchanged terminal record; when no prior terminal baseline
exists, a generated status GET must first observe the new run as `running`. Otherwise it continues polling and
times out rather than claim success. API client passes 54/54, media components pass 27/27, Operations passes
33/33, the focused three-viewport matrix passes 39/39 and the exact four-application real-API matrix passes
465/465 with zero unexpected, flaky or skipped results. Full-matrix evidence
`tests/s04-evidence/e2e-results-s03-round143-full-real-api-final.json` has SHA-256
`2bc14b8a212b8a890d37a64b4dc598e409be264f0c9462e37fb47bc79f2eff1c`. The 21:23 production recheck confirms
all four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the local Round 143 build. Reconciliation evidence SHA-256 is
`86d7b8192f1f03ab4faed8d0e357d597948dd936be75bfadb7358b8a98cf795a`.

The generated refresh contract still exposes neither a job public ID nor a monotonic status revision, while the
accepted response may precede the subprocess-owned status transition. The browser can therefore correlate only
by an observed new `running` projection or a terminal revision distinct from the pre-refresh baseline; it
honestly waits or times out in an uncorrelatable fast-completion case. A server-generated job/revision identifier
returned by both start and status, plus an atomic server-side start lock, remains the contract-owner gap. S03
does not emulate either field or modify the API owner implementation.

Round 142 closes another consumer-side ordering gap without changing the generated server schema. Every initial
or retried refresh-status read now owns a monotonic generation, and an accepted refresh attempt invalidates all
outstanding status reads before its generated-client write starts. A delayed pre-refresh response therefore
cannot overwrite a newer completed result; a failed start from an initially loading surface restores the honest
unavailable state. Media components pass 26/26, Operations passes 32/32, the focused three-viewport matrix
passes 33/33 and the exact four-application real-API matrix passes 459/459 with zero unexpected, flaky or
skipped results. Full-matrix evidence
`tests/s04-evidence/e2e-results-s03-round142-full-real-api-final.json` has SHA-256
`8ba6b7e8f4cf3ce7c1a15b3ee63480b16899cbee61d516c63471ceb89c2bfeec`. The 20:49 production recheck confirms
all four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the local Round 142 build. Reconciliation evidence SHA-256 is
`09c0e29f70612a16c294792ba0937d6b5ff646e0d2d94c0b0701db694300b94c`.

Round 141 closes the previously recorded server default-directory gap after the API owner changed
`api/geo_platform/datasets/router.py::_datasets_dir()` from `parents[2]` to `parents[3]`. With
`GEO_DATASETS_DIR` absent, a repository API now resolves the repository-root `.datasets` and the real generated
browser client consumes the current 12,012,742-byte/20,670-row artifact at all three required viewports with
matching file/header SHA-256 `3ac87a956dd89076983df8f890fe7667bc52d5a66dd991f37473e5fa71c151aa`.
Evidence `tests/s04-evidence/e2e-results-s03-round141-default-dataset-root-real-api.json` passes 3/3 in
36,463 ms and has SHA-256 `80d5494b842a795970b79ebb920fa1258755c84f2089867c62e638c71cb9c4b3`.
S03 did not modify the API implementation.

Round 140 closes two additional consumer-side state gaps without changing a server schema. The completed-refresh
dataset reread now revalidates its initiating identity scope after awaiting the generated client, before it can
clear progress or announce success; a superseded completion therefore cannot overwrite a new identity's refresh
state. Completed status projections with `partial` or ordinary `stale` sources are explicitly downgraded to a
warning that names the affected sources, while an informational completion requires every reported source to be
`ok`. Media components pass 25/25, Operations passes 31/31, the focused three-viewport matrix passes 30/30 and
the exact four-application real-API matrix passes 456/456 with zero unexpected, flaky or skipped results.
Full-matrix evidence `tests/s04-evidence/e2e-results-s03-round140-full-real-api-final.json` has SHA-256
`63931fe8d2308a4a315c244ee398b524786791173d899642ff3f63cd9359051b`. The 20:21 production recheck confirms
all four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the local Round 140 build. Reconciliation evidence SHA-256 is
`0b61d4f011b65355042331851254b959b2b7651d7a7dee56197dd9b69dd5adee`.

Round 139 closes a consumer-side post-completion authorization race without changing the generated server
contract. A `done` refresh status is treated only as permission to attempt the authoritative dataset reread;
Operations announces success only after that read validates under the still-active scope. A forbidden or
unavailable reread clears the formerly authorized snapshot, fails closed into the corresponding local state and
does not persist a stale success claim. Media components pass 24/24, Operations passes 30/30, the focused
three-viewport permission/DLP matrix passes 27/27 and the exact four-application real-API matrix passes 453/453
with zero unexpected, flaky or skipped results. Full-matrix evidence
`tests/s04-evidence/e2e-results-s03-round139-full-real-api-reviewed-final.json` has SHA-256
`8383559f7b3521caa41882343525ce68a04cc20d01f799a6d01fed0be3465748`. The 19:57 production recheck confirms
all four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the local Round 139 build. Reconciliation evidence SHA-256 is
`a6ab0801b4275849be3158c54a950e472be98a9ed0d2f5b34ab7b2e837746d3a`.

Round 138 closes a browser state-semantics gap without adding a server contract. Operations now distinguishes an
unknown refresh status from authoritative “never refreshed”, exposes loading/unavailable/forbidden/ready
separately, offers a GET-only local status retry and stops polling immediately when an accepted refresh loses
read permission. Safe case/site references remain optional generated-client projections and become navigation
targets only when they are credential-free HTTP(S) URLs. Media components pass 23/23, Operations passes 29/29,
the focused three-viewport matrix passes 24/24 and the exact four-application real-API matrix passes 450/450 with
zero unexpected, flaky or skipped results. Full-matrix evidence
`tests/s04-evidence/e2e-results-s03-round138-full-real-api-final.json` has SHA-256
`a2130ce8523b940bb44be0a0d396d5bba044b01ead066eddfbacd3cc1cf5c816`. The 19:25 production recheck confirms all
four app entries return 200, repository and production remain aligned at 100 paths with canonical
`paths + schemas` SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all
media/lifecycle endpoints reach the 401 authorization boundary and the deployed Operations
index/manifest/media JS/CSS exactly match the local Round 138 build. Reconciliation evidence SHA-256 is
`53de2d530313b4074e1c42e766c5d328ac440cbfab471e95a4312f8656b66e01`.

Round 137 closes a frontend consumer-ownership gap without adding or changing a server contract. Operations
binds the media artifact GET, accepted refresh POST and each status-poll generation to the exact initiating
tenant/actor/role header projection. A scope transition invalidates delayed results before they can update
freshness, announce success, schedule another poll or cause a dataset reread under the old identity. Deferred
component tests cover both polling and accepted-write races; the contract matrix passes 21/21, real API media
integration passes 3/3 and the exact four-application matrix passes 447/447 across all required viewports.
Full-matrix evidence
`tests/s04-evidence/e2e-results-s03-round137-full-real-api-final.json` has SHA-256
`61a1cb6558c5924e4ba73bf2a05da4e0afe33a9f93c2b39e7c7b8b5858a46938`. This closes the browser race while
leaving the existing 100-path generated contract unchanged. The 19:00 production recheck confirms all four app
entries return 200, repository and production remain aligned at 100 paths with canonical `paths + schemas`
SHA-256 `601d9cffb2de6f71caa9b345f4f655b0e9f7f810b927e9e7fd50bb31d31396b5`, all media/lifecycle endpoints reach
the 401 authorization boundary and the deployed Operations index/manifest/media JS/CSS exactly match the local
Round 137 build. Reconciliation evidence SHA-256 is
`6c2db86f0084fe64debb1c4a4ea7c76563c181bd5ccfc76f748aa32b06c59ec5`.

Round 136 integrates the generated `/api/v2/datasets/media-prices/refresh` and
`/api/v2/datasets/media-prices/refresh-status` contracts. The UI treats the 202 response only as an accepted
start signal, polls the generated status projection, handles 409 as an already-running job and reloads the
artifact only after authoritative `done`. The projection accepts only the three known source names and bounded
state/count/timestamp/message/note fields; any normalized secret in an allow-listed display field fails closed
before React state. The artifact read now recomputes the response SHA-256 and verifies every row-derived and
aggregate statistic instead of trusting the header or denormalized values.

The exact current-source four-application matrix passes 444/444 in 522,873 ms with zero unexpected, flaky,
skipped or top-level errors. Evidence
`tests/s04-evidence/e2e-results-s03-round136-full-real-api-final.json` has SHA-256
`cfdc9ec9ec2a56d48fa6ea024b89326dc073c1e19915eb521523b310bc87ca5a`. The 18:33 production recheck shows
repository and production identical at 100 paths with canonical `paths + schemas` SHA-256
`6a67d4e2f962faf707ab6fe66ef0f70203ebe504fe15088a93eebddf251aaaff`; both refresh paths reach the
authorization boundary, and the deployed Operations index/manifest/media JS/CSS exactly match the current
forced local build. Reconciliation evidence SHA-256 is
`0fee99242b5ceda908dc82a2e91fb9fe8de10c2e3b6aefa519fb668d4c8cc2de`.

Round 135 closes the browser integration portion of `/api/v2/datasets/media-prices`. Operations now presents it
inside the shared ProductShell without copying S01 execution state. The generated client reconstructs every
returned row through a bounded allow-list, never retains `ids`/unknown fields, drops secret-shaped optional
display text and fails required identity/number/GEO drift closed. URL filters and pagination use the shared
history DLP boundary, and a stable identity-header projection prevents back/forward navigation from downloading
the 11.95 MB artifact again. Contract E2E passes 12/12 and visual E2E 3/3 across all required viewports; a real
generated-client browser call also projects the actual 20,631-row artifact once with matching SHA-256,
`private, no-store` and `nosniff` headers.
After the three deliberately stale Operations baselines were reviewed and reconciled, the exact final-source
four-application matrix passes 438/438 in 512,387 ms across all required viewports with zero unexpected,
skipped or flaky results. Evidence
`tests/s04-evidence/e2e-results-s03-round135-full-real-api-final.json` has SHA-256
`166e83bf2664ba9ae440136392d7ad325b68da293ff3611f53961357f84f1a69`.

At Round 135, one server-side deployment gap remained. When `GEO_DATASETS_DIR` was absent,
`api/geo_platform/datasets/router.py::_datasets_dir()` used
`Path(__file__).resolve().parents[2] / ".datasets"`; for that module this was `platform-v2/api/.datasets`,
although the gap contract required repository-root `platform-v2/.datasets`. An authenticated local call
therefore returned 404 with the real artifact present at the documented path. Configuring
`GEO_DATASETS_DIR=/home/xln/geo-system/platform-v2/.datasets` yielded the then-current
11,953,885-byte/20,631-row 200 response and exact file/header hash
`c96d2dbbb2f05d6776ed358af23db784f40de713b2800aea496838d602c8ed24`. The API owner subsequently corrected
the default root; Round 141 above verifies that resolution without the override.

The 2026-07-27 17:23 read-only production recheck closes the earlier route deployment drift: production and
repository now both expose 98 paths with identical canonical `paths + schemas` SHA-256
`76c5f325fa7797bd850fedfe4ae1cc3611eb55d220f40f8e8415406a5a5acb40`, and an unauthenticated media-price
request returns 401 rather than 404. This proves routing and authentication are deployed, but it does not prove
that the protected production process has a populated artifact. At that time the default-directory defect above
remained assigned to the API owner; Round 141 later closed its repository/default-runtime side. The production
Operations manifest also includes `/platform/operations/media-prices`, and the route JS, shared shell JS and CSS
hashes exactly match the current local production build. Read-only evidence
`tests/s04-evidence/s03-round135-production-media-prices-reconciliation.json` has SHA-256
`c148a4dbe0ef2b7f67871ac149074b982a6c75aed1c7d79a0fd1053fe5825917`.

Round 134 closes the Customer report-question acknowledgement gap without adding a browser contract. The
generated comment receipt is only an exact safe reconciliation target; Customer Web rereads the same generated
report detail and requires complete version/comment projections plus exact comment ID, version, author and body
before success. Stale authority recovers through GET-only retry, and navigation before a delayed receipt issues
no superseded report read. The focused three-viewport gate passes 3/3 and the exact four-application matrix,
including real S01 Operations lifecycle calls, passes 420/420. Concurrent contract work also introduced
`/api/v2/datasets/media-prices`: it is the sole repository-only path against the current 97-path internal
production schema and remains a deployment/integration gap outside this browser reconciliation change.

Round 133 closes the Customer Web member-governance acknowledgement gap without introducing a second account
contract. Generated invite/removal/OIDC bind/revoke receipts are retained only as exact safe reconciliation
targets; the UI rereads both complete authoritative member and binding collections and requires the expected
active member/binding presence or absence before announcing success. A stale projection recovers through a
GET-only local retry, while navigation before a delayed receipt suppresses both the old success claim and any
old-scope authority read. The focused three-viewport gate passes 6/6 and the exact four-application matrix passes
420/420. No S01 execution business file or handwritten response type was added; the six remaining production
gaps still require external authority or real populated samples.

Round 132 closes the Report Studio consumer-side acknowledgement gap without adding a parallel contract. The
generated revision/comment/review/delivery/action/retest receipts and the publish 204 are treated only as accepted
writes. Report Studio now rereads the same report or bounded delivery collection and requires the exact returned
public ID/version plus the expected authoritative state before success. Revision, comment and action stale-first
tests recover through GET-only local retry while proving the accepted POST/PATCH is never replayed. The focused
three-viewport gate passes 6/6 and the exact four-application matrix passes 420/420. No S01/S02 domain route or
handwritten response type was introduced; the six remaining production gaps still require external authority or
real populated samples rather than another browser contract.

Round 93 hardens that single client boundary: JSON routes send `Accept: application/json`, use `no-store`, reject
redirects, omit the Referer and accept only the exact JSON media type before parsing; report artifact routes
negotiate and require the exact format media type before the existing size/hash check. Intelligence detail
responses are additionally bound to the requested investigation root, so an otherwise valid cross-resource
response fails closed. The full fresh-build browser matrix passes 366/366, including the new cross-investigation
negative case at all three viewports.

Round 94 closes browser-side relational gaps left inside individually valid projections. Intelligence visual
diffs must now close over the projected content-version-evidence chain and exact frozen body hashes. Report
section evidence IDs must exist in the current version's `report_evidence_reference` projection; dangling IDs
are removed and every release write stays locked. Customer Web also treats a generated version/artifact
projection whose service total exceeds its returned rows as incomplete, so a 100-row tail cannot be presented or
used as an authoritative current version when the service reports 101. The fresh-build browser matrix passes
372/372 across all three viewports, including no-write/DLP negatives for all three relationships.

Round 95 closes the remaining generated Intelligence Claim/evidence relationship boundary. Claims are unique
`clm_` roots; every retained `ce_` evidence link references one of those projected claims and is unique by both
link ID and `(claim,evidence)` pair; `srca_` source assessments are unique by assessment and assessed-source IDs.
Any relational drop marks the affected projection incomplete and keeps governance writes locked. No unsupported
source-cluster membership rule was added, and the propagation graph still accepts its contract-valid content,
version and entity nodes. API client 37/37, Intelligence component 9/9, focused browser 5/5 and the fresh-build
four-application/three-viewport matrix 372/372 all pass with the new cross-claim, duplicate and DLP negatives.

Round 96 applies the graph's own service/database contract without narrowing its valid node families. Only the
eight server-supported relations are retained, optional evidence IDs must be exact `evd_` IDs, optional weights
must be valid ratios and duplicate `(from,to,relation)` edges are removed in both the shared client and application
boundary. Every removal marks the graph incomplete and locks Intelligence governance writes; invalid relations
are no longer rendered as a fabricated `unknown` edge. Content, content-version and entity endpoints remain valid
alongside Claim/evidence endpoints. The 500-edge browser test preserves an invalid-relation DLP canary and
truthfully exposes 119 safe rows from its bounded first 120. API client 37/37, Intelligence component 9/9,
three-viewport integrity 9/9, performance 3/3 and the exact-source full matrix 372/372 pass.

Round 97 closes the generated score/appeal/verdict governance-history boundary. The client no longer truncates
score history to one row before validating it: all three histories validate up to the newest 200 rows for exact,
unique public IDs and nondecreasing creation time before the application chooses a current view. Appeal state is
also closed over `created_at`, `updated_at` and `resolved_at`: active rows cannot be resolved, and terminal rows
must carry an ordered resolution timestamp. Verdict supersession is null or an exact `vrd_` reference. A
duplicate, reverse-time, invalid-state or over-limit history is explicitly incomplete and locks governance
writes. API client 37/37, Intelligence component 9/9, focused three-viewport browser 24/24 and the exact
four-application matrix 372/372 pass with secret-bearing negative rows removed before browser state.

Round 98 binds the consumer side of every live Intelligence verdict/appeal continuation to the exact current
investigation. An unmounted or superseded case workspace cannot apply a delayed write receipt to the parent
verdict state, and missing validated identity headers cannot be relabelled as a local live success. Case-scoped
workspace keys also prevent local form/receipt state from crossing case identities. A three-viewport delayed
page-two verdict test proves browser back preserves page one's authoritative verdict and leaks no secret-bearing
receipt fields; the complete four-application matrix passes 375/375. This does not add or emulate a server
contract—it closes the generated write contract's browser ownership boundary.

Round 99 closes verdict supersession over the service's existing ordered history contract. A non-null
`supersedes_pub_id` is retained only when it names the immediately preceding projected verdict; a dangling or
nonlinear pointer invalidates that row and the remainder of the chain. A generated-client boundary may therefore
return a safe retained prefix together with `projection.verdicts.invalid=true`; Intelligence Web now reconciles
that boundary metadata before deriving the displayed verdict, so the safe prefix cannot be mistaken for an
authoritative latest decision. The result is `pending`, an explicit incomplete-projection warning and locked
governance writes. API client 38/38, Intelligence component 10/10, focused three-viewport browser 12/12 and the
full four-application matrix 378/378 pass.

Round 100 closes the appeal/verdict relationship that can be proved from the existing service transaction without
adding an absent response contract. An appeal is retained only when at least one projected verdict exists no later
than its creation. A terminal `corrected` appeal additionally consumes one otherwise-unmatched superseding verdict
whose creation lies between the appeal's creation and resolution times. The check is repeated at the shared-client
and application boundaries; an absent or truncated supporting history marks the appeal projection invalid,
displays `pending` and locks governance writes. It does not infer an explicit appeal-to-verdict ID the server does
not expose. API client 39/39, Intelligence component 11/11, focused three-viewport browser 15/15 and the full
four-application matrix 381/381 pass.

Round 101 closes each appeal row over the fields actually written by the existing service transaction. Active
`open`/`reviewing` rows require null `resolution`, resolver, resolution rationale and resolution time. Terminal
rows require an equal state/resolution enum, exact `usr_` submitter and distinct `usr_` resolver, safe nonblank
reason/rationale and create ≤ resolve ≤ update time. Both the shared-client and application projectors enforce the
same rule before governance state is derived; the application then discards the validated actor and reason fields
instead of retaining or rendering them. This is necessary because the generated detail response remains an open
object even though the mounted router selects the transaction columns. API client 40/40, Intelligence component
12/12, focused three-viewport browser 15/15 and the full four-application matrix 381/381 pass with active-resolution,
state mismatch, self-resolution and secret-rationale negatives.

Round 102 closes the resolver-independence relationship across the existing appeal and verdict transactions. Every
verdict must now carry an exact `usr_` reviewer and safe nonblank rationale before it can support an appeal. A
terminal resolver must differ from the immediately preceding verdict reviewer; a corrected appeal must also have
an in-window replacement verdict whose reviewer equals the resolver, whose rationale exactly equals the resolution
rationale and whose supersession names that immediately preceding verdict. Both frontend boundaries consume these
fields only to validate the relationship, then discard them from the rendered target. API client 41/41,
Intelligence component 13/13, focused browser 18/18, targeted integrity/retry regression 27/27 and the exact
four-application matrix 384/384 pass.

Round 103 preserves the service's valid many-Claim-to-one-evidence relationship in the source workspace without
collapsing relation identity. Source cards use the unique `ce_` relationship ID as their React identity while
showing the shared `evd_` source and each Claim-specific stance/rationale. When the separate
`source_independence` projection contains an assessment for that evidence, its cluster and weight govern the
source-independence display; evidence-package creation continues to deduplicate the underlying evidence ID.
Intelligence component 14/14, focused live browser 6/6 and the exact four-application matrix 384/384 pass with
zero browser runtime errors at all three viewports.

Round 104 preserves the service/database propagation-edge identity contract. Multiple edges may share
`from_evidence_pub_id` and `to_evidence_pub_id` when their `relation` differs, so React Flow and its accessible
table alternative now use the stable `(from, relation, to)` triple instead of response-order indexes. Parallel
relations receive deterministic curved paths and separate labels. Below 620 px the graph uses a compact
deterministic layout, no longer forces a 640 px canvas, and the always-present keyboard-focusable four-column
table wraps rather than clipping. Intelligence component 15/15, Design System 41/41, focused live 6/6,
focused static visual 3/3 and the exact final-source four-application matrix 384/384 pass. This was a frontend
projection/visual defect, not a new backend contract gap.

Round 105 preserves the page-history service's multi-content response contract. An investigation history can
contain several `content_pub_id` groups, each with an independent version chain and visual diffs. The browser now
selects a content item explicitly, groups previous/current versions under that public identity and displays a diff
only when its content and both endpoint version IDs equal the selected pair; it no longer treats the final two
rows of the whole response as one chain or borrows the final diff from another page. A labelled selector exposes
multiple safe page titles/sources, and a single-version page disables previous-version navigation and renders no
false similarity. Intelligence component 16/16, focused three-viewport live 6/6 with axe and the exact
four-application matrix 384/384 pass. No backend contract change is requested.

Round 106 makes the parallel investigation reads fail closed at their shared root boundary. Detail,
page-history and visual-diff responses remain independently useful when a transient detail request is
unavailable, but a forbidden or root-mismatched detail now clears successful history/diff projections instead of
allowing an inaccessible/different case's history to remain ready. A three-viewport negative returns a mismatched
detail beside complete 200 history/diff payloads and proves no page title, 93% similarity or secret extension
reaches the browser. Focused 3/3 and the exact four-application matrix 384/384 pass. No API change is requested.

Round 107 handles the reports endpoint's existing tenant-scoped catalog without mistaking a legitimate
cross-project row for contract corruption. Report Studio now performs a bounded generated-client scan, retains
only exact current-project summaries and looks ahead for the next matching report before exposing a URL cursor.
If ten 100-row server pages are exhausted while `has_more` remains true, the UI exposes an incomplete scan and
permits continuation instead of claiming an empty or complete project catalog. A server-owned optional
`project_pub_id` query parameter would remove this bounded client scan for very large tenants; S03 does not add
that parameter or change S02's router. Detail forbidden state and report/version-keyed review/action state are
also closed locally. Report component 5/5, focused desktop 13/13, three-viewport integrity 39/39, live 2/2 and
the exact four-application matrix 393/393 pass.

Round 108 promotes that bounded scan into the shared generated-client boundary and applies it to Customer Web.
There is now one generated-type implementation for project/cursor validation, ten-by-100 tenant scanning,
current-project retention, next-match lookahead and truthful incomplete continuation. Customer Web no longer
requests one tenant row and rejects an otherwise valid directory merely because that row belongs to another
project. Cross-project-only, mixed-project and ten-batch continuation cases pass at all three viewports; API
client 43/43, Customer 25/25, Report 5/5 and the exact four-application matrix 399/399 pass. The optional
server-owned `project_pub_id` query filter remains the only contract gap: it would improve large-tenant
efficiency, but no browser type, router parameter or backend behavior has been invented.

Round 109 closes Customer Evidence Workspace ownership across existing generated contracts. Answer relations are
now invalidated with their answer filter/page URL context, and evidence-package write continuations are bound to
the exact asset page generation and captured `evd_` request body. Browser history or asset pagination cannot
apply a delayed prior-page detail or package receipt to the current page. Focused three-viewport browser 9/9 and
the exact four-application matrix 402/402 pass. This is a frontend continuation-ownership defect and requests no
backend or OpenAPI change.

Round 110 serializes the existing generated tenant-member governance writes. Invite, member revoke and OIDC
bind/revoke now share one synchronous browser mutation lock, and each delayed continuation is bound to its exact
validated tenant/actor/retry generation and initiating membership/user. A bind can no longer race a revoke or
another member write; closing the dialog does not relabel the eventual result, and only the captured member is
updated after the generated client validates the receipt. The hostile three-viewport test passes 3/3, the full
member regression passes 15/15 and the exact four-application matrix passes 405/405. This closes a frontend
continuation/concurrency defect over already-mounted identity paths and requests no backend or OpenAPI change.

Round 111 serializes Customer profile declaration, atomic asset confirmation and change-request submission before
React pending state can render. Each form now owns one synchronous mutation lock and binds its continuation to the
exact validated tenant/project/source generation; context change or unmount invalidates the delayed result. A
hostile three-viewport test emits duplicate submit events in the same browser turn and proves one POST per form,
exact captured bodies, safe delayed completion and no secret-extension retention. The focused suite passes 3/3,
the related regression passes 21/21 and the exact four-application matrix passes 408/408. This closes a frontend
deduplication/continuation defect over existing generated profile, asset and question paths and requests no
backend or OpenAPI change.

Round 112 serializes the mounted Report Studio write surface over existing generated contracts. Revision, comment,
review, publish, delivery, optimization-action and effect-retest operations share workspace-local synchronous
guards bound to exact report/version and validated tenant/actor/role identity. Delayed results cannot cross an
identity or workspace boundary; the two-step action/retest operations also revalidate ownership between POST and
PATCH. Live identity loss fails closed instead of falling through to fixture-local approval, publication or
comment state. The live suite activates each write twice synchronously and still observes one exact request;
focused browser 6/6, complete Report Studio regression 90/90 and the exact four-application matrix 408/408 pass.
This closes frontend concurrency and continuation-ownership defects and requests no backend or OpenAPI change.

Round 113 serializes the mounted Intelligence governance surface over existing generated contracts. Verdict,
appeal, appeal resolution and evidence-package creation are bound to the exact investigation/verdict and
validated tenant/actor/role identity. Dataset registration, evaluation execution, dataset approval and model
admission share one synchronous calibration guard; their TanStack mutation callbacks can no longer apply a
delayed receipt after identity or workspace change. Hostile three-viewport tests submit before pending state can
render and still observe one exact request. Focused browser 24/24, complete Intelligence regression 93/93 and the
exact four-application matrix 408/408 pass. This closes frontend concurrency and continuation-ownership defects
and requests no backend or OpenAPI change.

Round 114 serializes the Customer account-governance lifecycle over its existing generated contracts. Account
registration/authorization, terminal pairing creation and revocation share one synchronous guard bound to the
validated tenant/project/user/role/retry context and request identity. Every ticket owns a unique generation;
identity/context change or unmount invalidates delayed continuations, and a newly authorized scope invalidates
the prior pairing generation. The real lifecycle activates authorization, all four pairing outcomes and
revocation twice synchronously while still observing the original 1 + 1 + 4 + 1 writes. Focused browser 3/3,
related regression 30/30 and the exact four-application matrix 408/408 pass. This closes frontend concurrency and
continuation-ownership defects and requests no backend or OpenAPI change.

Round 115 applies that same identity-aware, unique-generation guard to filtered metric export, evidence-package
creation, report-version questions and customer delivery confirmation. Export and package continuations are
bound to their exact filters or evidence-page projection; question and confirmation share the exact
report/version/delivery scope, and confirmation revalidates it after both the write and authoritative reread.
The mounted live flow activates all four writes twice synchronously while still observing one request each.
Focused browser 3/3, related regression 75/75 and the exact four-application matrix 408/408 pass. This closes
frontend concurrency and continuation-ownership defects over existing generated contracts and requests no
backend or OpenAPI change.

Round 116 applies the shared identity-aware, unique-generation mutation guard to Customer profile truth
confirmation, brand/asset confirmation and configuration-request creation. Every write is bound to the exact
tenant/project/user/role/source context and the validated request tenant/actor/role headers; identity or project
change, unmount and superseding generation discard delayed continuations. The hostile three-viewport test submits
each form twice synchronously, delays every response and still observes one exact generated-client request and
the expected identity headers while secret-bearing response extensions remain absent from browser surfaces.
Focused browser 3/3, related regression 21/21 and the exact four-application matrix 408/408 pass. This closes
frontend concurrency and continuation-identity defects over existing generated contracts and requests no backend
or OpenAPI change.

Round 117 replaces the remaining custom Customer member-governance lock with the same identity-aware,
unique-generation guard. Member invitation, membership revocation, OIDC binding and OIDC revocation share the
exact tenant/project/user/role/source/retry context and validated request identity; identity, role, project,
retry, unmount or generation changes discard delayed continuations. The mounted tenant-admin flow activates all
four writes twice synchronously while still observing one request each with exact member-bound URLs, bodies and
tenant/actor/role headers. A delayed binding also proves the whole member surface remains locked and the result
attaches only to the initiating member. Focused browser 6/6, related regression 18/18 and the exact
four-application matrix 408/408 pass. This closes frontend concurrency and continuation-identity defects over
existing generated identity contracts and requests no backend or OpenAPI change.

Round 118 increments the Report Studio mutation generation at every successful `begin()`, matching the corrected
Customer and Intelligence guards. A completed report ticket can therefore never share a generation with a later
write in the same report/version/identity context. The mounted reviewer/analyst flow still activates comment,
review, publish, delivery, immutable revision, optimization-action and effect-retest writes twice synchronously
while observing one request each. Focused browser 6/6, complete Report Studio regression 90/90 and the exact
four-application matrix 408/408 pass. This closes a frontend ticket-identity invariant over existing generated
report contracts and requests no backend or OpenAPI change.

Round 119 prevents safe API projections cached under one validated experience from being reused after tenant,
project, user, role or source changes. The shared Experience Provider now replaces and clears its complete
TanStack Query client at that boundary, and a component regression reuses an identical query key across two users
to prove no first-user result remains visible. Intelligence calibration also carries the complete projected
experience scope in its dataset, run and admission query keys and its mutation context, rather than keying only
on request-header tenant/role values that are intentionally empty in cookie-owned production sessions. Focused
three-viewport browser 24/24, Design System 42/42, Intelligence 16/16 and the exact four-application matrix 408/408
pass. This closes a frontend cache and mutation-ownership defect over existing generated contracts and requests
no backend or OpenAPI change.

Round 120 applies that same identity scope to the complete React application subtree. Replacing a Query client
alone does not reset component-local forms, dialogs, selections, receipts or mutation flags, so the shared Query
provider is now keyed by the projected tenant/project/user/sorted-role/source tuple. Any scope change unmounts
those states before the next safe experience can render. A component regression holds both a same-key Query value
and a `useState` owner canary, then proves neither first-user value survives the second-user render. All four
shared shells pass 24/24 desktop/tablet/mobile browser checks and the exact four-application matrix passes 408/408.
This closes a frontend local-state ownership defect and requests no backend or OpenAPI change.

Round 121 keys the shared fatal Error Boundary by that same projected experience scope. A render failure owned by
one identity therefore cannot keep a later identity on the prior error surface. The component regression makes
the first user throw, verifies the redacted boundary, changes to a second safe user and requires recovered content
without the old alert. Design System 43/43, all four three-viewport shared shells 24/24 and the exact
four-application matrix 408/408 pass. This closes a frontend cross-identity recovery defect and requests no
backend or OpenAPI change.

Round 122 removes delimiter ambiguity and control-character spoofing from that identity boundary. Safe experience
projection rejects C0, C1, line-separator and bidirectional-control characters, and encodes the complete
tenant/project/user/sorted-role/source scope as a canonical JSON tuple rather than joining attacker-influenced
fields with NUL. Auth applies the same control check to browser hints before any identity request and to the
generated session projection before mounting application state; Intelligence calibration reuses the shared scope
helper. A mounted three-viewport negative proves a NUL-bearing tenant hint is purged with zero identity request
and no collision canary in DOM, URL or storage. Design System 44/44, Auth 8/8, focused browser 21/21 and the exact
four-application matrix 411/411 pass. This closes a frontend identity-key collision/spoofing defect and requests
no backend or OpenAPI change.

Round 123 applies collision-free structured encoding to every remaining S03-owned write boundary. Customer's
eight mutation contexts, Customer/Report/Intelligence validated-header tickets, Report revision/review/outcome
targets and Intelligence calibration governance targets now encode ordered fields as JSON rather than NUL- or
colon-delimited strings; missing identity-header fields cannot start a live ticket. A contract guard rejects any
restored NUL join in those production sources, and a pure regression proves two adversarial delimiter placements
remain distinct without a raw NUL in either key. Design System 45/45, the mounted three-viewport mutation suite
81/81 and the exact four-application matrix 411/411 pass. This closes a frontend write-ticket/context collision
defect and requests no backend or OpenAPI change.

Round 124 closes latest-bootstrap ownership above those contexts. A changed shared loader receives a new
TanStack bootstrap generation instead of attaching to an in-flight predecessor, and the bootstrap client is
cleared on unmount. Auth also generations every loader invocation: after a newer identity call begins, an older
response can only resolve as unavailable and can never overwrite the current in-memory validated headers.
Delayed regressions resolve the newer response first and prove the stale user is not mounted and the stale actor
does not regain ownership. Design System 46/46, Auth 9/9, focused browser 45/45 and the exact
four-application matrix 411/411 pass. This closes a frontend bootstrap/validated-header race over existing
identity contracts and requests no backend or OpenAPI change.

Round 125 closes the corresponding ownership boundary for verified artifact bytes. The shared download primitive
now receives a structured resource identity, invalidates a pending generation synchronously when report, version,
hash, MIME or filename scope changes and serializes activation before React pending state renders. Customer Web
and Report Studio supply exact report/version/hash/MIME resource keys, so an old integrity-valid blob cannot
create an object URL or download after a new artifact owns the surface. Unsafe resource keys fail closed, and
unmount invalidates the continuation. Design System 47/47, focused Customer/Report browser 78/78 and the exact
four-application matrix 411/411 pass. This closes a frontend artifact continuation/duplicate-activation defect
over existing generated report contracts and requests no backend or OpenAPI change.

Round 126 closes pre-effect report presentation ownership. Customer Web and Report Studio bind every terminal
report read to the exact safe experience/page/cursor/retry scope and treat a prior scope as loading immediately
when URL history or retry changes. Current-scope projection failures retain their explicit warnings; old-scope
titles, facts, dialogs and warnings cannot render under the new request. Customer HTML/PDF and Report PDF preview
subtrees are also keyed by exact report/version/format/hash/MIME identity, so the old semantic document or canvas
unmounts before a new resource commits. Focused three-viewport browser 72/72 and the exact four-application matrix
411/411 pass. This closes a frontend presentation/preview ownership defect over existing generated report
contracts and requests no backend or OpenAPI change.

Round 127 applies the exact terminal-result ownership rule to Intelligence Web. Case-list/detail and
page-history/diff results are bound to the complete safe experience/page/cursor/retry scope; a URL, browser-history
or retry transition treats a predecessor as loading synchronously, before effect cleanup. The old case heading,
facts and history therefore cannot render under a new request. Current-scope diagnostics remain available, and
the existing case-owned verdict continuation stays isolated. Intelligence component 16/16, focused
three-viewport browser 9/9 and the exact four-application matrix 411/411 pass. This closes a frontend
presentation-ownership defect over existing generated Intelligence contracts and requests no backend or OpenAPI
change.

Round 128 applies the same exact terminal-result ownership rule to the remaining Customer reads. Monitoring
overview and its auxiliary continuations, profile and asset history, paginated answers, paginated evidence assets
and the selected evidence dialog are bound to complete safe experience/filter/query/page/cursor/retry scopes.
URL, history, filter, query and retry transitions synchronously present loading for a mismatched predecessor, so
old KPI values, governance forms, rows, cursors and dialogs cannot appear under a replacement request before
effect cleanup. Current-scope projection diagnostics and existing answer-relation/evidence-package ownership stay
available. Customer component 25/25, focused three-viewport browser 30/30 and the exact four-application matrix
411/411 pass. This closes a frontend presentation-ownership defect over existing generated Customer contracts
and requests no backend or OpenAPI change.

Round 129 applies exact view ownership to the Anti-GEO calibration governance surface. One structured safe scope
contains identity and all dataset/run/admission page and cursor pairs; the three TanStack queries, dataset/run
dialogs, independent review target, notice/error presentation and mutation ticket are bound to that scope.
Pagination or browser history synchronously closes a prior-page review, invalidates an in-flight old-page write
and suppresses its eventual receipt under the replacement URL. A review target must remain present and eligible
in the current safe projection before its dialog can render. Intelligence component 16/16, focused
three-viewport browser 21/21 and the exact four-application matrix 414/414 pass. This closes a frontend
governance/presentation ownership defect over existing generated Anti-GEO contracts and requests no backend or
OpenAPI change.

Round 130 closes the calibration surface's post-write reconciliation boundary over those same generated
contracts. Dataset registration, evaluation execution, independent approval and model admission preserve the
exact initiating scope after their write receipt, retain the pending lock and await the active dataset/run/
admission query refresh before announcing success or closing the dialog. A refresh failure becomes a local
current-scope failure rather than a false success, and a replacement URL owner suppresses the delayed
continuation. The delayed-projection negative proves an old draft row cannot remain operable under a success
claim. Intelligence component 16/16, focused three-viewport browser 24/24 and the exact four-application matrix
417/417 pass. This is a frontend reconciliation/receipt-ownership correction and requests no backend or OpenAPI
change.

Round 131 applies authoritative post-write reconciliation to the existing Intelligence verdict and appeal
contracts. Verdict creation, appeal creation and independent appeal resolution keep the initiating identity/case
ticket active after the write response and reread that exact investigation. Success is announced only when the
safe detail projection contains the returned verdict ID, the returned active appeal ID or the addressed terminal
appeal state. A stale/failed projection keeps writes locked and offers a GET-only retry, so an accepted write is
never repeated; navigation invalidates the continuation before any old-case detail probe. The retained React
target contains only safe verdict IDs and enumerated appeal ID/state pairs, not submitted reasons, reviewer
rationales or response extensions. The focused three-viewport matrix passes 9/9 and the exact four-application
matrix passes 420/420. This closes a frontend receipt/reconciliation defect over existing generated contracts and
requests no backend or OpenAPI change.

The production backend at `127.0.0.1:8020` now exposes all 97 repository paths, including all six S02-owned
Anti-GEO dataset/run/admission operations and `/api/v2/operations/lifecycle`; it has no extra path. Repository and
production have identical canonical `paths + components.schemas` SHA-256
`1288a71779b0b279b8ab3c9b97be7f13be67d1036438b10f3f4a5a5bb321f1e6`. The lifecycle route now reaches
authentication (HTTP 401 without a session instead of 404), so the deployment gap is closed without adding a
second API or fixture fallback. The production reverse proxy at `https://127.0.0.1:8443` reports health 200 and
intentionally does not publish OpenAPI. The current generated OpenAPI and TypeScript client hashes are
`2da140787236a2395018b0062d75402531990b49c4a5a697a04235f726cecf26` and
`63d1bd0e89c2d9bed7aa471412a1259df3852abd16306a6995b066002f1656b9`.

Report action update, effect-retest creation, version artifact retrieval and report delivery list/create/recipient
confirmation are mounted in production through the same generated path set. Report Studio creates an explicitly
addressed delivery after publication; Customer Web confirms only a backend-listed recipient delivery and rereads
the list for `confirmed_at` rather than inventing a browser timestamp. Production report rows are empty, so
populated-path qualification remains open without fabricating a customer report, approval or delivery.

Report Studio now writes immutable component revisions through
`POST /api/v2/reports/{report_pub_id}/versions`. The server copies the preceding frozen facts, validates
component-scoped `evidence_pub_ids`, creates a new review version and renders HTML/DOCX/PDF/XLSX without mutating
the prior version. A required `Idempotency-Key` is stored only as SHA-256; concurrent exact retries converge to
one version and one artifact per format, while payload drift returns 409. The generated response schema remains
an open object, so the browser boundary immediately reduces it to a request-matching `rpt_` ID, valid `rpv_` ID,
positive version number and SHA-256 fact hash; unknown response fields cannot enter React state.

All user-authored writes now use React Hook Form + Zod before reaching the generated client: customer report
questions; report section bodies, comments, delivery recipients and bounded effect-retest deltas; and Intelligence
appeals. The same schemas reject secret-shaped values and expose associated accessible errors. The live report
E2E also proves reviewer/analyst separation rather than granting one browser role both approval and authoring
capabilities.

The answer model now includes nullable `run_pub_id` and `config_version_pub_id` provenance in both repository and
production. Customer Web projects and renders only valid `run_`/`cfv_` identifiers, and truthfully shows an
unlinked value when an older row has no lineage. No handwritten compatibility type or fixture fallback was added.

The schema now includes S02 analytics, evidence, exports, reports and intelligence paths. S03 consumes analytics
overview/answers, metric export, evidence asset inventory/package creation, report
list/detail/comment/review/publish/action creation and investigation list/detail/verdict/appeal plus evidence-package
creation through generated paths. Report delivery create/list/confirm also use the generated boundary. Every
open-object detail response is projected through a strict runtime allow-list before entering React state.

S01 has added the narrow customer-safe authorization/health/intervention/event/revocation contract. Customer Web
now consumes it through generated path types and has proved role/DLP behavior against the integrated API.

## Required safe account projection

Customer and Operations product surfaces require an S01-owned projection containing only account mask, platform
label, owner label, custody mode, admission level, granted scopes, expiry label/time, region label, session health,
last verified time, intervention status, event summary and revocation receipt metadata.

Responses must reject or strip unknown properties and must never include Cookie, Authorization, access/refresh
token, OTP, QR payload, proxy password, full phone/email, browser-profile path, storage state, device private key,
HAR secrets or biometric material. Unauthorized roles receive the same forbidden/not-found envelope and cannot
infer account existence.

## Required endpoint families

- customer-terminal handoff for a functional one-time link/QR. The mounted customer pairing create/read contract
  returns only the safe `CustomerPairingView` status and correctly omits `pairing_token`, but it also exposes no
  non-secret `handoff_pub_id`, protected same-origin launch operation or signed terminal-broker acknowledgement.
  Customer Web therefore renders only an explicitly payload-free handoff affordance and cannot truthfully claim
  that its visual is a usable QR. S01 must own a single-use, 5–10 minute, account/action/domain-bound handoff that
  keeps `pairing_token` out of browser JavaScript, DOM, URL, Query cache, storage, telemetry and error reports;
  an opaque non-secret handoff ID or native terminal channel is acceptable. Screenshots must mask/reject the
  machine-readable handoff even after that contract exists.
  Round 166 narrows the public browser identity-header type to the three generated tenant/actor/role keys, removes
  raw generated-schema exports and forbids S03-owned applications from constructing the raw client. The remaining
  token-bearing browser type is in S01-owned Operations execution, which receives the intervention pairing token
  and retains it in component memory for the terminal handoff. S03 does not edit or duplicate that business
  implementation; removing the type from the ordinary Operations bundle requires the broker or terminal-only
  contract described above.
  Round 86 removes the controlled extension's former JSON textarea and implements a locally decoded, bounded
  pairing-QR input whose image is never uploaded or stored; only an allow-listed signed task projection reaches
  extension session storage. Round 87 adds an exact-pinned local jsQR fallback for browsers without
  `BarcodeDetector`, rejects blank/multiple/oversized QR surfaces, clears selected-file and decoded-pixel
  capabilities, and publishes the signed `0.1.1` CRX from a root-owned immutable production release directory.
  Real Chromium proves the token is absent from DOM and storage, including the fallback path, and the release
  mechanics certificate passes. Round 88 corrects the real popup lifecycle: after the target platform closes the
  popup, reopening now strictly revalidates the exact stored projection, restores only action/domain/challenge and
  lets a keyboard user submit completion. Initial/restored states pass WCAG-AA and the signed `0.1.2` CRX is
  published from the immutable release pointer; its clean-build-independent release certificate passes 12/12.
  Round 89 closes the terminal-consumer refusal/expiry UX gap without adding an API: the generated terminal result
  already permits `rejected`, so the signed `0.1.3` extension now offers completion and rejection for an exact
  restored task, prevents concurrent result submission and reports a safely recognized local expiry after
  removing session storage. Real Chromium proves the rejected task becomes authoritative backend
  `state/platform_result=rejected`; a separate completed task remains `awaiting_platform_probe`, so the extension
  still cannot self-attest live platform verification. The immutable production 0.1.3 CRX and its 12/12 signature
  certificate are published.
  Round 90 closes the remaining terminal-result consumer boundary. The signed `0.1.4` extension exposes
  completion, native-verification failure and customer rejection, then accepts a write receipt only when the
  exact generated `TerminalResultView` is bound to the submitted task/result with a strict timestamp. Cross-task,
  result-mismatched, impossible-time and secret-extended receipts fail closed without deleting the resumable task.
  Real Chromium proves separate authoritative `rejected` and `failed` results while completion still stops at
  `awaiting_platform_probe`; the immutable production CRX and 12/12 release certificate are published.
  Round 91 makes the signed `0.1.5` task consumer match the generated `TerminalTaskView`: the six-field root is
  exact, outer and signed expiries must be strict ISO values for the same instant within five minutes, and
  terminal requests explicitly disable credentials, redirects, referrers and HTTP caching. Real Chromium proves
  the actual backend's equivalent `Z`/`+00:00` serialization passes; ambiguous dates, expiry drift and
  secret-extended roots fail before session storage. The immutable production CRX and 12/12 certificate are live.
  Round 92 closes response media-type confusion in the signed `0.1.6` consumer. Terminal requests now explicitly
  negotiate JSON, and a successful task/result body enters exact projection only when its declared media type is
  `application/json`; missing, HTML and vendor/problem JSON types fail before parsing even if their bodies happen
  to be JSON-shaped. Real Chromium observes all six bind/result POSTs with JSON negotiation and no Cookie or
  Referer, while unit negatives freeze the MIME, size and safe HTTP-error boundaries. The immutable production CRX
  and 12/12 signature certificate are live.
  This closes the terminal-consumer and artifact-publication portions only:
  no customer-safe API/native broker currently delivers that QR to an authorized customer terminal without first
  exposing the token to an ordinary browser, and no authorized installation or native-platform challenge canary
  is available. The issuer/authorized-installation/real-platform qualification portion of this gap remains open.
- customer-role/project-scoped member administration and role update (tenant-admin list/create/revoke, OIDC
  binding lifecycle, customer browser bootstrap and customer-safe account lifecycle are integrated);
- analytics daily trend/model/region-mode/question-row reads beyond the integrated KPI overview, previous-period
  delta and competitor comparison (implemented and mounted in production; external-data qualification remains
  separate);
- answer-to-citation/evidence relations, answer/source screenshots, anchors and history diffs (implemented and
  deployed; populated customer-workspace production qualification remains open);
- report evidence-binding relations (implemented and deployed; populated production qualification remains open);
- report/version creation or freeze, section/component revision writes and component-targeted evidence-binding
  mutation (implemented through immutable revision creation; validated live analyst sessions use the generated
  client and never report a local edit as persisted);
- intelligence page snapshot/visual diff history (implemented and deployed; populated production qualification
  remains open);
- a customer registration/authorization update that can assign a responsible person distinct from the
  authenticated actor (implemented and production-certified).

## Current integration boundary

- Real and generated: identity/project bootstrap, health, S01 Operations execution/account lifecycle, Customer
  account registration/authorization/pairing/events/revocation, tenant-admin member list/create/revoke/OIDC
  binding lifecycle, and Customer
  project `change-requests`. Live
  account responses pass through an allow-list projection before React state; pairing transitions remain owned by
  the controlled terminal, signed terminal results map explicitly to waiting/refused/timed-out/failed/completed,
  and revocation remains pending until both a safe `rev_` receipt ID and backend deletion-verification timestamp
  exist. Customer Web can then refresh and render the shared completion receipt. The registration owner control
  is read-only and uses only the validated experience's safe user label; it never relabels an `admin` or another
  admitted role as a customer. The customer-safe receipt does not currently project its initiating actor, so the
  UI explicitly says the initiator is not exposed instead of inferring it from the current viewer. Change requests
  are selected only after a validated live identity; contract-fixture sessions
  keep their writes explicitly local. Validated request headers are memory-only, never persisted into Query cache,
  URL or telemetry, and the browser never receives a service token.
- Real, generated and production-qualified: versioned customer declarations and atomic brand + product/service +
  competitor + prohibited-claim confirmations. The composite write requires a truth assertion and idempotency key,
  writes its version and brand/competitor projections in one database transaction, and exposes cursor-paged history
  without returning the declaring subject. S04 production evidence records 21/21 authenticated browser checks,
  profile/assets at all three viewports, 2/2 focused API integration and zero console, page, request or HTTP
  failures without emitting secrets.
- Real, generated and production-qualified: customer-safe responsible-member selection. The list is tenant-scoped, excludes disabled,
  revoked and service identities, returns only opaque user IDs plus non-PII labels, and maps missing/cross-tenant
  targets to the same 404. Registration and authorization can assign that member independently from the
  authenticated account owner; Customer Web no longer accepts and silently discards a free-form responsible
  person on live writes. The production account workspace passes all three viewports as part of the 24/24
  real-session browser matrix.
- Real, generated and production-qualified for the current admin production session: Customer analytics overview, previous-period delta, competitor comparison,
  daily/model/region-mode/question breakdowns, and answers (including
  URL/history-bound cursor pagination), metric export/evidence inventory/evidence package/reports and version-bound
  report questions. The expanded real-session production browser matrix passes 33/33, including the Monitoring
  workspace at desktop/tablet/mobile with zero console/page/request/HTTP errors; production fact counts and
  generated-contract hashes are recorded in `tests/s04-evidence/production-analytics-breakdowns.json`.
  The current qualified runtime contains 146 answers/analyses. A real customer-role production identity is still
  unavailable, so this evidence does not claim customer-role populated acceptance.
  Report Studio list/detail and comment/review/publish/action creation;
  Intelligence list/detail Claim/evidence/source/graph/score-explanation projection plus
  verdict/appeal/appeal-resolution/evidence package.
- Real, generated and deployed: answer-scoped citation, linked evidence, OCR anchor and evidence-history
  projections. URL queries/fragments, object keys, diff bodies and unknown bbox fields are excluded before the
  browser; hostile relation payload E2E passes at all three viewports. The qualified production runtime now
  contains answer cards, while populated customer-role acceptance remains open because only `admin` is verified.
- Real, generated and deployed: typed frozen report facts/components/artifacts/reviews/comments/events/actions/
  effect-retests, fact-to-evidence bindings with anchor counts and hashes, verified artifact proxy, and
  action/retest state writes. Report publication, delivery creation and customer confirmation are separate
  generated operations. The browser delivery projection retains only matching safe IDs and timestamps; it drops
  recipient/comment/extension fields and renders confirmation only after a backend reread. Object keys and source
  URLs are excluded from the binding projection. Real PostgreSQL/MinIO integration and desktop/tablet/mobile live
  E2E pass. The production delivery mechanism certificate passes recipient isolation, authoritative replay,
  monotonic confirmation and event attribution with its synthetic fixture removed. Populated production report
  acceptance is not claimed because a read-only production audit at 2026-07-25 14:40 still found zero report
  rows and the certificate explicitly does not prove a real customer identity.
- Real, generated and deployed: immutable report component authoring. Analysts can edit a component body and its
  `evd_` bindings in Report Studio; reviewers see the write control disabled. Server-side DLP, tenant-scoped
  evidence validation, hash-only idempotency, report-row serialization and per-format artifact locks prevent
  partial or duplicate versions. Real PostgreSQL/MinIO concurrent replay and desktop/tablet/mobile live-contract
  E2E pass. Production remains an empty-data schema/runtime certificate, not a fabricated report acceptance.
- Real, generated and deployed: Intelligence immutable page history plus adjacent evidence-version text/visual
  diff metadata. Browser projections expose only safe IDs, source host, title, time, hashes, similarity and visual
  availability; object keys and diff bodies never enter React state. Populated production qualification remains
  open because production contains no investigations or content versions.
- Real, generated and deployed: governed Anti-GEO calibration dataset registration/listing, independent dataset
  approval, evaluation execution/listing and model admission/listing.
  The browser sends only evidence IDs/hashes, case and propagation-cluster digests, prediction probabilities,
  threshold-consistent labels, the six enumerated explanation-presence fields and an optional training-cluster
  digest manifest. Cached responses retain only dataset/evaluation/admission summaries; raw labels, predictions
  and training-cluster lists are never returned. Analyst/reviewer role separation, DLP, three-viewport interaction
  and WCAG-AA checks pass against the generated contract. Production now exposes all 97 repository paths, so the
  route-deployment portion is closed. The 2026-07-27 13:47 count-only audit still records zero evaluation
  datasets, runs and model admissions; no approved authorized dataset or independent analyst/reviewer production
  journey exists. Populated calibration and live model-admission qualification therefore remain explicitly
  unclaimed. Round 130's exact-source matrix passes 417/417 across all four applications and three target
  viewports; this proves the mounted browser contract and fail-closed empty state, not real-platform capability or
  calibration quality.
- Fail closed in validated live sessions: customer-role member administration. Contract-fixture sessions remain explicitly
  labelled and are never used as live fallback.
- The S01 Operations execution module now uses the shared generated OpenAPI client for every lifecycle
  read/control call. Its former localStorage actor boundary, development-port default and hand-written request
  paths were removed; real-cookie production deep-link checks pass at all three viewports.
- The Operations composition boundary is now mounted through one generated,
  `GET /api/v2/operations/lifecycle` read model. S03's Overview, Session Health, Intervention and Event pages
  consume that bounded projection through the shared client and the pure `OperationsLifecycleSnapshot`
  allow-list projector; they do not import execution state, issue parallel account/intervention/event requests or
  maintain a second account model. Unknown or secret-shaped account, intervention, event and receipt fields are
  dropped before shared security components render them. The S01-owned `ExecutionControlPlane` remains the sole
  execution business surface and was not copied or changed by S03. Production 30/30 at all three viewports proves
  the lifecycle read model, role boundary, responsive presentation and zero browser runtime errors. This former
  composition/deployment gap is closed; empty real accounts and absent authorized customer/operator journeys
  remain external qualification gates.

All list filters use URL-bound cursor pagination; every state distinguishes loading/empty/real-zero/insufficient/
failed/delayed/forbidden/ready. Writes use `Idempotency-Key`.

## Canvas ADR

S03 selects **Konva / react-konva** for screenshot anchor annotation because the required interaction is a bounded
canvas overlay (rectangles, handles, zoom and coordinates), not a general design editor. Its smaller conceptual
surface and direct scene graph fit evidence annotation. Fabric.js is not selected. S03 originally recorded the
complete proposal in `docs/contract-gaps/S03-ADR-0005-konva-evidence-annotation.md`; S04 promoted it without
semantic drift to the accepted `contracts/adr/0005-konva-evidence-annotation.md`.
