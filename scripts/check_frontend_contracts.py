import json
import re
from pathlib import Path

root = Path(__file__).parents[1]
apps = {
    "customer-web": "/platform/customer",
    "operations-web": "/platform/operations",
    "report-studio": "/platform/reports",
    "intelligence-web": "/platform/intelligence",
    "intake-form": "/platform/intake-form",
}
page_coverage = {
    "customer": {
        "app": "customer-web",
        "sections": {
            "home",
            "profile",
            "intake",
            "assets",
            "questions",
            "services",
            "service-1",
            "service-2",
            "service-3",
            "service-4",
            "service-5",
            "evidence",
            "reports",
            "members",
            "accounts",
        },
        "compatibility_sections": {
            "answers",
            "monitoring",
            "competition",
            "sources",
            "reputation",
            "opportunities",
        },
        "spec": "customer-visual.spec.ts",
    },
    # S01 owns execution and its visual evidence. S03 owns every other Operations workspace.
    "operations": {
        "app": "operations-web",
        "sections": {"overview", "sessions", "interventions", "events"},
        "external_sections": {
            "execution",
            "media-prices",
            "posting",
            "quotation-generator",
            "sop",
            "onboarding",
            "post-analysis",
            "service-visibility",
            "service-outbound-risk",
            "service-inbound-risk",
            "service-site-audit",
            "service-pilot",
            "formal-reports",
            # 服务1地域/账号审计链的运维入口。
            "accounts",
            "browsers",
            # 报告交付：独立 report-studio app（/platform/reports/）的整页跳转入口。
            "reports-delivery",
        },
        "spec": "operations-visual.spec.ts",
    },
    "reports": {
        "app": "report-studio",
        "sections": {
            "window",
            "trace",
            "editor",
            "diff",
            "evidence",
            "preview",
            "review",
            "outcomes",
        },
        "spec": "reports-visual.spec.ts",
    },
    "intelligence": {
        "app": "intelligence-web",
        "sections": {
            "cases",
            "claims",
            "sources",
            "source-insight",
            "graph",
            "history",
            "calibration",
            "verdict",
            "package",
        },
        "spec": "intelligence-visual.spec.ts",
    },
    "intake-form": {
        "app": "intake-form",
        "sections": {"brand", "research", "profile", "questions", "submit"},
        "spec": "intake-form-visual.spec.ts",
    },
}
errors: list[str] = []
auth_source = (root / "packages/auth/src/index.ts").read_text(encoding="utf-8")
auth_tests = (root / "packages/auth/src/index.test.ts").read_text(encoding="utf-8")

base_tsconfig = json.loads((root / "tsconfig.base.json").read_text(encoding="utf-8"))
compiler = base_tsconfig.get("compilerOptions", {})
for option in ("strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes"):
    if compiler.get(option) is not True:
        errors.append(f"tsconfig.base.json must keep compilerOptions.{option}=true")

for fragment in (
    "function readBrowserHints(",
    "scrubClientStorage(localStorage, sessionHintKeySet)",
    "scrubClientStorage(sessionStorage)",
    "localStorageScrub.removedRequiredHint",
    "containsUnsafeClientControlCharacter(",
    "let experienceLoadGeneration = 0",
    "const loadGeneration = ++experienceLoadGeneration",
    "if (loadGeneration !== experienceLoadGeneration) return { kind: 'unavailable' }",
):
    if fragment not in auth_source:
        errors.append(f"@geo/auth is missing browser-storage DLP scrubbing: {fragment}")
for fragment in (
    "scrubs normalized secret keys and values from both browser stores before identity use",
    "opaque-fullwidth-key-canary",
    "opaque-zero-width-key-canary",
    "geo.preference.panel",
    "clears an oversized browser store and fails closed without probing identity",
    "tnt_safe\\u0000actor_collision",
    "usr_safe\\u202e",
    "lets only the newest concurrent bootstrap own validated request headers",
    "subject-first",
    "subject-second",
    "await expect(first).resolves.toEqual({ kind: 'unavailable' })",
):
    if fragment not in auth_tests:
        errors.append(f"@geo/auth browser-storage DLP tests are missing coverage: {fragment}")

for app, basename in apps.items():
    app_root = root / "apps" / app
    package = json.loads((app_root / "package.json").read_text(encoding="utf-8"))
    dependencies = package.get("dependencies", {})
    if not str(dependencies.get("react", "")).startswith("19."):
        errors.append(f"{app}: React 19 is required")
    if "react-router" not in dependencies or "@react-router/dev" not in package.get(
        "devDependencies", {}
    ):
        errors.append(f"{app}: React Router Framework Mode dependencies are required")
    if package.get("scripts", {}).get("build") != "react-router build":
        errors.append(f"{app}: production build must use react-router build")

    config = (app_root / "react-router.config.ts").read_text(encoding="utf-8")
    if not re.search(r"\bssr\s*:\s*false\b", config):
        errors.append(f"{app}: react-router.config.ts must keep ssr:false")
    if not re.search(rf"\bbasename\s*:\s*['\"]{re.escape(basename)}/?['\"]", config):
        errors.append(f"{app}: expected basename {basename}")
    for release_fragment in (
        "GEO_FRONTEND_RELEASE_BUILD",
        "'build-release'",
        "GEO_E2E_BUILD and GEO_FRONTEND_RELEASE_BUILD are mutually exclusive",
    ):
        if release_fragment not in config:
            errors.append(
                f"{app}: isolated production release output is missing {release_fragment}"
            )
    vite_config = (app_root / "vite.config.ts").read_text(encoding="utf-8")
    if not re.search(rf"\bbase\s*:\s*['\"]{re.escape(basename)}/['\"]", vite_config):
        errors.append(f"{app}: Vite base must be {basename}/ for isolated production assets")

    for source in (app_root / "app").rglob("*"):
        if source.suffix not in {".ts", ".tsx"} or ".test." in source.name:
            continue
        relative = source.relative_to(root)
        # S01 owns the complete execution feature, including its temporary handwritten API boundary.
        # The login feature is the pre-session credential boundary: no identity headers or
        # session context exist yet, so it posts to the identity endpoint directly.
        # The onboarding wizard follows the execution feature's operations-side client pattern.
        # The four service workspaces reuse that raw client pattern plus raw fetches for the
        # analytics endpoints that have no generated wrapper yet. Account governance is the
        # operations-only machine-resource control plane and follows the same boundary until
        # those collection endpoints gain projected @geo/api-client wrappers.
        if app == "operations-web" and (
            source.is_relative_to(app_root / "app" / "features" / "execution")
            or source.is_relative_to(app_root / "app" / "features" / "login")
            or source.is_relative_to(app_root / "app" / "features" / "onboarding")
            or source.is_relative_to(app_root / "app" / "features" / "services")
            or source.is_relative_to(app_root / "app" / "features" / "account-governance")
        ):
            continue
        # report-studio 的扩展 fact 面板直连同源扩展组端点：扩展键（w3_disparagement/
        # w2_site_audit/before_after）过不了 api-client 主数组的 fail-closed 词表投影，
        # 与 operations-web services 工作区同属"无生成 wrapper 的分析端点"原始客户端模式。
        if app == "report-studio" and source == app_root / "app" / "fact-suggestions.tsx":
            continue
        text = source.read_text(encoding="utf-8")
        if re.search(r"\bfetch\s*\(", text):
            errors.append(f"{relative}: direct fetch is forbidden; use @geo/api-client")
        if "/api/v2/" in text:
            errors.append(f"{relative}: API path literals are forbidden; use generated paths")
        if (
            "URL.createObjectURL" in text
            or re.search(r"createElement\(\s*['\"]a['\"]\s*\)", text)
            or re.search(r"\.download\s*=", text)
        ):
            errors.append(
                f"{relative}: raw browser downloads are forbidden; use the design-system "
                "generated-file or verified-Blob boundary"
            )
        if re.search(r"window\.history\.(?:pushState|replaceState)\s*\(", text):
            errors.append(
                f"{relative}: direct browser-history mutation is forbidden; use the "
                "design-system URL boundary or React Router"
            )
        if re.search(r"\bfrom\s+['\"]zustand['\"]", text):
            errors.append(
                f"{relative}: Zustand is not currently justified; add an ADR before introducing it"
            )
        for forbidden_persistent_runtime in (
            "navigator.serviceWorker.register(",
            "navigator.storage.getDirectory(",
            "navigator.storageBuckets",
            "webkitRequestFileSystem(",
            "openDatabase(",
            "document.cookie",
            "cookieStore",
        ):
            if forbidden_persistent_runtime in text:
                errors.append(
                    f"{relative}: persistent browser runtime is forbidden; "
                    f"found {forbidden_persistent_runtime}"
                )

for package_name in (
    "api-client",
    "auth",
    "charts",
    "design-system",
    "domain-types",
    "evidence-viewer",
    "workflow-ui",
):
    for source in (root / "packages" / package_name / "src").rglob("*"):
        if source.suffix not in {".ts", ".tsx"} or ".test." in source.name:
            continue
        text = source.read_text(encoding="utf-8")
        for forbidden_persistent_runtime in (
            "navigator.serviceWorker.register(",
            "navigator.storage.getDirectory(",
            "navigator.storageBuckets",
            "webkitRequestFileSystem(",
            "openDatabase(",
            "document.cookie",
            "cookieStore",
        ):
            if forbidden_persistent_runtime in text:
                errors.append(
                    f"{source.relative_to(root)}: persistent browser runtime is forbidden; "
                    f"found {forbidden_persistent_runtime}"
                )

required_dependencies = {
    "packages/design-system/package.json": {"@tanstack/react-query"},
    "apps/customer-web/package.json": {
        "@tanstack/react-table",
        "react-hook-form",
        "@hookform/resolvers",
        "zod",
    },
    "packages/charts/package.json": {"echarts"},
    "apps/intelligence-web/package.json": {
        "@tanstack/react-query",
        "@xyflow/react",
        "react-hook-form",
        "@hookform/resolvers",
        "zod",
    },
    "apps/report-studio/package.json": {
        "konva",
        "react-konva",
        "pdfjs-dist",
        "react-hook-form",
        "@hookform/resolvers",
        "zod",
    },
}
for package_path, required in required_dependencies.items():
    package = json.loads((root / package_path).read_text(encoding="utf-8"))
    declared = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    missing = sorted(required - declared)
    if missing:
        errors.append(f"{package_path} is missing frozen stack dependencies: {missing}")

terminal_root = root / "packages/customer-terminal-extension"
terminal_package = json.loads((terminal_root / "package.json").read_text(encoding="utf-8"))
terminal_manifest = json.loads((terminal_root / "manifest.json").read_text(encoding="utf-8"))
terminal_protocol = (terminal_root / "src/protocol.mjs").read_text(encoding="utf-8")
terminal_protocol_tests = (terminal_root / "src/protocol.test.mjs").read_text(encoding="utf-8")
terminal_background = (terminal_root / "src/background.mjs").read_text(encoding="utf-8")
terminal_background_tests = (terminal_root / "src/background.test.mjs").read_text(encoding="utf-8")
terminal_qr_decoder = (terminal_root / "src/qr-decoder.mjs").read_text(encoding="utf-8")
terminal_qr_tests = (terminal_root / "src/qr-decoder.test.mjs").read_text(encoding="utf-8")
terminal_popup = (terminal_root / "src/popup.mjs").read_text(encoding="utf-8")
terminal_popup_html = (terminal_root / "popup.html").read_text(encoding="utf-8")
terminal_popup_css = (terminal_root / "popup.css").read_text(encoding="utf-8")
terminal_build = (terminal_root / "build.mjs").read_text(encoding="utf-8")
terminal_runtime = (root / "tools/customer_terminal_extension_runtime.mjs").read_text(
    encoding="utf-8"
)
terminal_release_certifier = (root / "tools/certify_customer_terminal_release.py").read_text(
    encoding="utf-8"
)
artifact_guard = (root / "scripts/check_e2e_artifacts.py").read_text(encoding="utf-8")
terminal_nginx = (root / "deploy/production/geo-platform-v2-port-edges.conf").read_text(
    encoding="utf-8"
)
production_browser_acceptance = (root / "tools/production_browser_acceptance.mjs").read_text(
    encoding="utf-8"
)
frontend_release = (root / "scripts/frontend_release.py").read_text(encoding="utf-8")
frontend_release_tests = (root / "tests/unit/test_frontend_release.py").read_text(encoding="utf-8")
frontend_nginx = terminal_nginx + (root / "deploy/production/nginx-v2-locations.conf").read_text(
    encoding="utf-8"
)
for app in (
    "customer-web",
    "operations-web",
    "report-studio",
    "intelligence-web",
    "intake-form",
):
    immutable_alias = f"/opt/geo-platform-v2/current/apps/{app}/build/client/"
    if immutable_alias not in frontend_nginx:
        errors.append(f"Production frontend alias is not release-bound: {app}")
if "/home/xln/geo-system/platform-v2/apps/" in frontend_nginx:
    errors.append("Production frontend aliases must not read from the mutable development tree")
for fragment in (
    "GEO_FRONTEND_RELEASE_BUILD",
    "renameat2",
    "RENAME_EXCHANGE",
    "sanitized_build_environment",
    "verification_command_required",
    "rolled_back_after_failed_activation",
    "certify_active_release",
    "materialize_release",
    "rollback_trees_match_previous_release",
    "served_root",
):
    if fragment not in frontend_release:
        errors.append(f"Atomic frontend release boundary is missing {fragment}")
for fragment in (
    "test_failed_verification_atomically_restores_every_active_bundle",
    "test_failed_activation_can_be_inspected_and_retried_without_rebuilding",
    "test_verified_activation_and_manual_rollback_exchange_whole_trees",
    "test_build_environment_drops_all_secret_and_vite_values",
    "test_materialize_fresh_immutable_root_restores_candidates_on_failure",
):
    if fragment not in frontend_release_tests:
        errors.append(f"Atomic frontend release regression is missing {fragment}")
for fragment in (
    "S03_FRONTEND_CANDIDATE_ROOT",
    "isolated_frontend_candidate",
    "production_assets_mutated: false",
    "candidate_root_outside_frontend_releases",
):
    if fragment not in production_browser_acceptance:
        errors.append(f"Candidate browser qualification boundary is missing {fragment}")
if terminal_package.get("dependencies", {}).get("jsqr") != "1.4.0":
    errors.append("Customer terminal must pin the audited offline jsQR 1.4.0 fallback")
if terminal_package.get("devDependencies", {}).get("esbuild") != "0.28.1":
    errors.append("Customer terminal must pin its browser bundle builder")
if terminal_manifest.get("version") != "0.1.6":
    errors.append("Recoverable customer terminal source must remain an upgradeable 0.1.6 release")
if terminal_package.get("version") != terminal_manifest.get("version"):
    errors.append("Customer terminal package and manifest versions must match")
if terminal_manifest.get("permissions") != ["storage", "tabs"]:
    errors.append("Customer terminal permissions must remain exactly storage and tabs")
if terminal_manifest.get("optional_host_permissions") != ["https://*/*"]:
    errors.append("Customer terminal may request only runtime HTTPS host access")
for forbidden_key in (
    "content_scripts",
    "externally_connectable",
    "web_accessible_resources",
):
    if forbidden_key in terminal_manifest:
        errors.append(f"Customer terminal manifest must not declare {forbidden_key}")
for fragment in (
    "import jsQR from 'jsqr'",
    "MAX_DIMENSION = 2048",
    "MAX_PIXELS = 16_000_000",
    "MAX_RAW_BYTES = 4096",
    "async function nativeValue(",
    "function fallbackValue(",
    "const second = decoder(",
    "if (second) throw new Error('pairing_qr_invalid')",
    "imageData?.data.fill(0)",
    "canvas.width = 1",
    "canvas.height = 1",
):
    if fragment not in terminal_qr_decoder:
        errors.append(f"Customer terminal offline QR boundary is missing: {fragment}")
for fragment in (
    "uses the native single-QR fast path without allocating fallback pixels",
    "falls back locally, rejects a second QR and clears decoded pixels",
    "fails closed for absent, oversized and unsupported QR surfaces",
    "rejects multiple or oversized native QR results without a fallback",
):
    if fragment not in terminal_qr_tests:
        errors.append(f"Customer terminal QR tests are missing coverage: {fragment}")
for fragment in (
    "export function validateTaskProjection(",
    "export function validateStoredTerminalTask(",
    "hasExactKeys(task, STORED_TASK_KEYS)",
    "payload: validateTaskProjection(task.payload, now)",
    "const TERMINAL_RESULTS = new Set(['challenge_completed', 'failed', 'expired', 'rejected'])",
    "terminalResultPayload(task, evidenceHash, result = 'challenge_completed')",
    "export function validateTerminalResultView(",
    "hasExactKeys(value, TERMINAL_RESULT_VIEW_KEYS)",
    "value.platform_result !== expectedResult",
    "hasExactKeys(task, TERMINAL_TASK_VIEW_KEYS)",
    "strictIsoTimestamp(payload.expires_at)",
    "Date.parse(rootExpiresAt) !== Date.parse(payloadExpiresAt)",
):
    if fragment not in terminal_protocol:
        errors.append(f"Customer terminal resumable-task boundary is missing: {fragment}")
if "restores only an exact unexpired terminal task projection" not in terminal_protocol_tests:
    errors.append("Customer terminal protocol tests do not cover safe task restoration")
for fragment in (
    "new Date(now + 120_000).toUTCString()",
    "terminal_task_scope_invalid",
    "cookie: 'must-not-survive'",
):
    if fragment not in terminal_protocol_tests:
        errors.append(f"Customer terminal strict task-envelope tests are missing: {fragment}")
for fragment in (
    "async function resume()",
    "return { state: stored.state, payload: stored.task?.payload ?? null }",
    "message?.type === 'resume'",
    "message?.type === 'reject'",
    "message?.type === 'fail'",
    "submitResult('failed')",
    "submitResult('rejected')",
    "validateTerminalResultView(response, task.task_pub_id, result)",
    "return { state: 'submitted' }",
    "headers.set('Accept', 'application/json')",
    "mediaType !== 'application/json'",
    "cache: 'no-store'",
    "referrerPolicy: 'no-referrer'",
):
    if fragment not in terminal_background:
        errors.append(f"Customer terminal background restoration is missing: {fragment}")
for fragment in (
    "accepts only bounded application/json responses with hardened request options",
    "rejects JSON-shaped bodies declared as a non-contract media type",
    "'application/problem+json'",
    "request.options.credentials, 'omit'",
    "request.options.redirect, 'error'",
    "bounds a genuinely gzip-compressed response by decoded bytes before parsing",
    "const compressed = gzipSync(decoded)",
    "'Content-Encoding': 'gzip'",
    "terminal-gzip-boundary-canary",
    "error.message, 'terminal_response_too_large'",
):
    if fragment not in terminal_background_tests:
        errors.append(f"Customer terminal HTTP boundary tests are missing coverage: {fragment}")
for fragment in (
    "detectSingleQrValue(bitmap)",
    "file.size > 2 * 1024 * 1024",
    "image/png",
    "image/jpeg",
    "image/webp",
    "qrInput.value = ''",
    "validateTaskProjection(rawPayload)",
    "async function restoreActiveTask()",
    "chrome.runtime.sendMessage({ type: 'resume' })",
    "showActiveTask(response.value.payload)",
    "await submitTaskResult('reject')",
    "await submitTaskResult('fail')",
    "response.value?.state === 'expired'",
    "终端结果回执未通过任务绑定校验",
):
    if fragment not in terminal_popup:
        errors.append(f"Customer terminal popup DLP boundary is missing: {fragment}")
for forbidden_fragment in (
    "bundleInput",
    "JSON.parse(bundleInput",
    "localStorage",
    "sessionStorage",
):
    if forbidden_fragment in terminal_popup:
        errors.append(f"Customer terminal popup retains a forbidden surface: {forbidden_fragment}")
for forbidden_fragment in ("<textarea", 'id="bundle"', 'name="pairing_token"'):
    if forbidden_fragment in terminal_popup_html:
        errors.append(
            f"Customer terminal popup HTML retains a capability input: {forbidden_fragment}"
        )
for fragment in (
    'role="status"',
    'aria-atomic="true"',
    'id="fail"',
    'id="reject"',
):
    if fragment not in terminal_popup_html:
        errors.append(f"Customer terminal popup accessibility semantics are missing: {fragment}")
for fragment in (
    "input:focus-visible",
    "button:focus-visible",
    "@media (forced-colors: active)",
    "width: min(360px, 100vw)",
):
    if fragment not in terminal_popup_css:
        errors.append(f"Customer terminal popup responsive accessibility is missing: {fragment}")
for fragment in (
    "cp('node_modules/jsqr/LICENSE', 'dist/LICENSE-jsQR.txt')",
    "bundle: true",
    "target: ['chrome120']",
    "popupBundle.byteLength > 400 * 1024",
    "manifest.content_scripts",
    "manifest.externally_connectable",
    "manifest.web_accessible_resources",
):
    if fragment not in terminal_build:
        errors.append(f"Customer terminal build policy is missing: {fragment}")
for fragment in (
    "QRCodeEncoder_MODE_BYTE",
    "Object.defineProperty(globalThis, 'BarcodeDetector'",
    "value: undefined",
    "input.files?.length ?? 0",
    "local_jsqr_fallback_decoded_real_qr: true",
    "empty_qr_failed_closed: emptyQrFailedClosed",
    "multiple_qr_failed_closed: multipleQrFailedClosed",
    "extensionManifest.version === '0.1.6'",
    "hardened_terminal_json_requests: hardenedTerminalRequests",
    "terminal_request_count: terminalRequests.length",
    "await popup.close()",
    "popup_reopen_restores_safe_task: resumedPopupSafe",
    "signed_customer_rejection_submitted: rejectedView.state === 'rejected'",
    "signed_native_failure_submitted: failedView.state === 'failed'",
    "expired_task_announced_and_removed: expiredTaskRecoverySafe",
    "resumed_popup_wcag_aa: resumedA11yPassed",
    "resumed_popup_keyboard_focus_visible: resumedKeyboardFocusVisible",
    "customer-terminal-resumed-task.png",
):
    if fragment not in terminal_runtime:
        errors.append(f"Customer terminal real-browser fallback evidence is missing: {fragment}")
for fragment in (
    'SOURCE_MANIFEST = ROOT / "packages/customer-terminal-extension/manifest.json"',
    'manifest: dict[str, Any] = json.loads(get("manifest.json"))',
    '"source_manifest_matches_production": manifest == source_manifest',
    '"signing_private_key_not_in_crx": b"PRIVATE KEY" not in remote_crx',
):
    if fragment not in terminal_release_certifier:
        errors.append(f"Customer terminal remote release certificate is missing: {fragment}")
if "packages/customer-terminal-extension/dist" in terminal_release_certifier:
    errors.append("Customer terminal release certification must survive a clean development build")
for fragment in (
    'EVIDENCE / "customer-terminal-extension-release.json"',
    'EVIDENCE / "customer-terminal-extension-runtime.json"',
    'TESTS / "visual-evidence/s03/customer-terminal-resumed-task.png"',
):
    if fragment not in artifact_guard:
        errors.append(f"Customer terminal bounded evidence guard is missing: {fragment}")
if "alias /var/lib/geo-platform-v2/customer-terminal-extension/current/;" not in terminal_nginx:
    errors.append("Production customer terminal must use an immutable signed-release pointer")
if "packages/customer-terminal-extension/dist/" in terminal_nginx:
    errors.append("Production customer terminal must not serve the development build directory")

implementation_invariants = {
    "packages/design-system/src/index.tsx": (
        "QueryClientProvider",
        "new QueryClient(",
        "const queryIdentityScope = createSafeExperienceScopeKey(safeValue)",
        "export function createSafeExperienceScopeKey(",
        "export const createStructuredClientScopeKey =",
        "JSON.stringify([",
        "[queryIdentityScope]",
        "useEffect(() => () => queryClient.clear(), [queryClient])",
        "key={queryIdentityScope}",
        "<ProductErrorBoundary key={queryIdentityScope}",
        "const loadGeneration = useRef(0)",
        "queryKey: ['validated-experience', currentLoadGeneration, loadAttempt]",
        "useEffect(() => () => bootstrapQueryClient.clear(), [bootstrapQueryClient])",
        ".fetchQuery({",
        "setLoadAttempt",
        "projectSafeExperienceContext(value.value)",
        "export function projectSafeAccountSummary(",
        "export function AccountSummary(",
        "export function AuthorizationScope(",
        "export function CustodyMode(",
        "export function SessionHealth(",
        "export function AdmissionLevel(",
        "export function InterventionStatus(",
        "export function RevocationReceipt(",
    ),
    "apps/customer-web/app/shell.tsx": (
        "useReactTable(",
        "useForm<",
        "zodResolver(",
        "reportQuestionSchema",
        "<GeoBarChart",
        "function useLocalRetry()",
        'state="failed" onRetry={retry}',
        "liveAssetState",
        'state="failed" onRetry={retryAssets}',
        'label="证据中心资产"',
        "customerEvidenceProjectionLimits",
        "projectCustomerAnswerPage(",
        "projectEvidenceAssetPage(",
        "projectAnswerRelations(",
        "relationRequest.current",
        "const answerReadScope = createStructuredClientScopeKey([",
        "answerResultScope !== answerReadScope",
        "setAnswerResultScope(answerReadScope)",
        "const assetReadScope = createStructuredClientScopeKey([",
        "assetResultScope !== assetReadScope",
        "setAssetResultScope(assetReadScope)",
        "selectedScope === answerPresentationScope",
        "liveAssetProjection.total > liveAssetProjection.shown",
        "customerMonitoringProjectionLimits",
        "projectAnalyticsOverviewResult(",
        "projectAnalyticsBreakdownResult(",
        "deltaResult.data.projection",
        "setDeltaState(",
        "setCompetitorState(",
        "setLiveBreakdowns({ day: [], model: [], regionMode: [], question: [] })",
        "const monitoringReadScope = createStructuredClientScopeKey([",
        "setLiveResultScope(monitoringReadScope)",
        "liveResultScope !== monitoringReadScope",
        "effectiveLiveState === 'loading'",
        "if (cancelled) return",
        "customerGovernanceHistoryLimit",
        "const profileReadScope = createStructuredClientScopeKey([",
        "liveResultScope !== profileReadScope",
        "setLiveResultScope(profileReadScope)",
        "const assetHistoryReadScope = createStructuredClientScopeKey([",
        "liveResultScope !== assetHistoryReadScope",
        "setLiveResultScope(assetHistoryReadScope)",
        "projectClientProfilePage(",
        "projectAssetConfirmationPage(",
        "projectedLatest.invalid",
    ),
    "packages/charts/src/index.tsx": (
        "import('echarts/core')",
        "geo-chart-table",
    ),
    "apps/intelligence-web/app/shell.tsx": (
        "<ReactFlow",
        "传播图节点与关系",
        "<table",
        "useForm<",
        "zodResolver(",
        "appealReasonSchema",
        "GovernedCalibrationWorkspace",
        "function useLocalRetry()",
        'state="failed" onRetry={retry}',
        "let cancelled = false",
        "if (superseded()) return",
    ),
    "apps/intelligence-web/app/calibration-workspace.tsx": (
        "useQuery(",
        "useMutation(",
        "useForm<",
        "zodResolver(",
        "registerEvaluationDataset(",
        "runEvaluationDataset(",
        "approveEvaluationDataset(",
        "admitEvaluatedModel(",
        "training_propagation_cluster_digests",
        "模型校准与准入",
        "datasetsQuery.refetch()",
        "runsQuery.refetch()",
        "admissionsQuery.refetch()",
        "真实 API · 部分不可用",
    ),
    "apps/operations-web/app/lifecycle-snapshot.tsx": (
        "export type OperationsLifecycleSnapshot",
        "projectSafeOperationsLifecycleSnapshot",
        "projectSafeAccountSummary(",
        "OperationsLifecycleWorkspace",
        "fixtureOperationsLifecycleSnapshot",
        "<AccountSummary",
        "<InterventionStatus",
        "<RevocationReceipt",
    ),
    "apps/operations-web/app/shell.tsx": (
        "BusinessOverviewContainer",
        "OperationsLifecycleWorkspace",
        "fixtureOperationsLifecycleSnapshot",
        "if (section === 'overview')",
        "loadLifecycle(headers)",
        "getValidatedIdentityHeaders()",
        "setLiveSnapshot(result.data)",
        "experience?.source !== 'live'",
    ),
    "apps/operations-web/app/business-overview.tsx": (
        "getOperationsBusinessOverview",
        "updateClientUrlParameters(",
        "controller.abort()",
        "createFixtureOperationsBusinessOverview",
        "每页最多 4 个项目",
        "系统目前未保存可查询的报价历史、已签合同、开票应收与回款台账",
    ),
    "apps/report-studio/app/shell.tsx": (
        "import('pdfjs-dist')",
        "pdf.worker.min.mjs",
        "<Stage",
        "useForm<",
        "zodResolver(",
        "reportSectionSchema",
        "reportRevisionSchema",
        "reportCommentSchema",
        "reportDeliverySchema",
        "reportRetestSchema",
        "reportProjectionLimits",
        "ReportProjectionNotice",
        "invalidProjection",
        "hasIncompleteReportProjection",
        "function useLocalRetry()",
        'state="failed" onRetry={retry}',
    ),
}
for source_path, required_fragments in implementation_invariants.items():
    source = (root / source_path).read_text(encoding="utf-8")
    for fragment in required_fragments:
        if fragment not in source:
            errors.append(f"{source_path} is missing frozen implementation invariant: {fragment}")

for app in ("customer-web", "report-studio", "intelligence-web"):
    shell_path = root / "apps" / app / "app" / "shell.tsx"
    if "location.reload()" in shell_path.read_text(encoding="utf-8"):
        errors.append(
            f"{shell_path.relative_to(root)} must retry failed live regions locally, "
            "not reload the whole document"
        )

if "location.reload()" in (root / "packages/design-system/src/index.tsx").read_text(
    encoding="utf-8"
):
    errors.append(
        "packages/design-system/src/index.tsx must retry validated experience bootstrap "
        "locally, not reload the whole document"
    )

local_retry_specs = {
    "tests/e2e/customer-local-retry.spec.ts": (
        "customer-dashboard-transient",
        "successfulDashboardRequests",
    ),
    "tests/e2e/reports-local-retry.spec.ts": (
        "report-catalog-transient",
        "successfulReportRequests",
    ),
    "tests/e2e/intelligence-local-retry.spec.ts": (
        "intelligence-catalog-transient",
        "successfulInvestigationRequests",
    ),
}
for spec_path, (synthetic_rule_id, success_counter) in local_retry_specs.items():
    source = (root / spec_path).read_text(encoding="utf-8")
    for fragment in (
        "transient read failure",
        "重试此区域",
        "__geoLocalRetrySentinel",
        f"syntheticHttpResponseCount(page, '{synthetic_rule_id}')",
        f"expect({success_counter}).toBe(1)",
    ):
        if fragment not in source:
            errors.append(f"{spec_path} is missing local retry coverage: {fragment}")

intelligence_retry_e2e = (root / "tests/e2e/intelligence-local-retry.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "browser back discards a slower superseded investigation response",
    "delayedPageRequested",
    "secondPageDetailRequests",
    "数据正在安全获取，请稍候。",
    "getByText('当前第一页案件', { exact: true })).toHaveCount(0)",
    "expect(secondPageDetailRequests).toBe(0)",
    "不应覆盖的第二页 Claim",
    "case navigation discards a slower superseded verdict receipt",
    "delayedWriteRequested",
    "delayedWriteResolved",
    "inv_write_scope_page_02/verdicts",
    "usr_write_scope_reviewer",
    "superseded-write-receipt-canary",
    "page.getByText('rejected', { exact: true })",
    "expectAccessible(page)",
):
    if fragment not in intelligence_retry_e2e:
        errors.append(
            "tests/e2e/intelligence-local-retry.spec.ts is missing stale-response "
            f"isolation coverage: {fragment}"
        )

customer_retry_e2e = (root / "tests/e2e/customer-local-retry.spec.ts").read_text(encoding="utf-8")
for fragment in (
    "customer-evidence-transient",
    "successfulEvidenceRequests",
    "evd_customer_retry_safe",
    "SESSION=local-retry-evidence-canary",
    "Bearer local-retry-page-canary",
    "证据中心",
    "page.getByText('evd_customer_retry_safe')",
    "page.locator('body').innerText()",
):
    if fragment not in customer_retry_e2e:
        errors.append(
            "tests/e2e/customer-local-retry.spec.ts is missing fail-closed evidence center "
            f"retry coverage: {fragment}"
        )

customer_monitoring_integrity_e2e = (
    root / "tests/e2e/customer-monitoring-integrity.spec.ts"
).read_text(encoding="utf-8")
for fragment in (
    "oversized atomic dashboard collections fail closed without exposing rejected rows",
    "operational fields fail the atomic customer dashboard snapshot closed",
    "a malformed nested dimension fails atomically instead of claiming an empty window",
    "filter changes discard an older customer dashboard snapshot response",
    "metrics: Array.from({ length: 41 }",
    "expectAccessible(page)",
    "atomic-dashboard-oversize-canary",
    "wf_customer_dashboard_forbidden",
    "customer-dashboard-token-canary",
    "oldRequestCount",
    "currentRequestCount",
    "getByText('95.0%', { exact: true }).first()).toBeVisible()",
    "getByText('10.0%', { exact: true })).toHaveCount(0)",
):
    if fragment not in customer_monitoring_integrity_e2e:
        errors.append(
            "tests/e2e/customer-monitoring-integrity.spec.ts is missing atomic "
            "bounded/race/DLP "
            f"coverage: {fragment}"
        )

customer_governance_history_e2e = (
    root / "tests/e2e/customer-governance-history-integrity.spec.ts"
).read_text(encoding="utf-8")
for fragment in (
    "profile and asset history stay project-bound, bounded and cursor-safe",
    "project catalog rows stay kind-bound and secret extensions never cross browser surfaces",
    "browser history discards slower superseded profile and asset pages",
    "客户声明历史：服务返回 3 条，浏览器安全视图展示 1 条",
    "客户资产确认历史：服务返回 3 条，浏览器安全视图展示 1 条",
    "问题与目标目录包含跨项目、种类错配",
    "cross-project-profile-e2e-canary",
    "nested-brand-catalog-canary",
    "goal-payload-canary",
    "stale-asset-history-canary",
    "getByLabel('企业全称')).toHaveCount(0)",
    "getByText(/v5 · 安全确认品牌 5/)).toHaveCount(0)",
    "expectAccessible(page)",
):
    if fragment not in customer_governance_history_e2e:
        errors.append(
            "tests/e2e/customer-governance-history-integrity.spec.ts is missing "
            f"project/cursor/race/DLP coverage: {fragment}"
        )

customer_account_integrity_e2e = (root / "tests/e2e/customer-account-integrity.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "oversized account lifecycle collections stay bounded, account-bound and secret-free",
    "a same-account but input-mismatched pairing receipt fails locally without leakage",
    "revocation and newer refreshes discard slower pairing and event responses",
    "客户账号候选：服务返回 3 条，浏览器安全视图展示 1 条",
    "account-phone-leak-canary",
    "account13800138000***",
    "配对状态候选：服务返回 52 条，浏览器安全视图展示 1 条",
    "stale-event-response-canary",
    "stale-pairing-response-canary",
    "pairing-input-mismatch-canary",
    "expectAccessible(page)",
):
    if fragment not in customer_account_integrity_e2e:
        errors.append(
            "tests/e2e/customer-account-integrity.spec.ts is missing "
            f"bounded/account-binding/race/DLP coverage: {fragment}"
        )

customer_account_e2e = (root / "tests/e2e/customer-account.spec.ts").read_text(encoding="utf-8")
for fragment in (
    "customer@example.test",
    "只填写带 *、尾号或其他明确隐藏标记的账号掩码",
    "toBeDisabled()",
    "validated customer lifecycle writes stay single under synchronous duplicate activation",
    "expect(writes).toHaveLength(7)",
):
    if fragment not in customer_account_e2e:
        errors.append(
            "tests/e2e/customer-account.spec.ts is missing account-mask negative coverage: "
            f"{fragment}"
        )
if customer_account_e2e.count("addEventListener('click'") < 3:
    errors.append(
        "tests/e2e/customer-account.spec.ts must synchronously activate authorization, pairing "
        "and revocation writes twice before asserting one generated-client request"
    )

design_system_source = (root / "packages/design-system/src/index.tsx").read_text(encoding="utf-8")
design_system_styles = (root / "packages/design-system/src/styles.css").read_text(encoding="utf-8")
design_system_primitives_tests = (
    root / "packages/design-system/src/primitives.test.tsx"
).read_text(encoding="utf-8")
design_system_security_tests = (root / "packages/design-system/src/security.test.ts").read_text(
    encoding="utf-8"
)
design_system_error_tests = (root / "packages/design-system/src/error-boundary.test.tsx").read_text(
    encoding="utf-8"
)
shared_accessibility_e2e = (root / "tests/e2e/accessibility.ts").read_text(encoding="utf-8")
for fragment in (
    "--focus-ring: #176b51",
    "--focus-ring-on-dark: #daf073",
    "[tabindex]:not([tabindex='-1'])",
    "outline: 3px solid var(--focus-ring)",
    "@media (forced-colors: active)",
    "outline-color: Highlight",
    "min-height: 44px",
    ".check-field input[type='checkbox']",
    "flex: 0 0 24px",
    ".checkbox-line input[type='checkbox']",
    ".link-button",
    "min-height: 24px",
    ".react-flow__attribution a",
    "padding-inline: 4px",
    "label.field {\n  display: grid;\n  min-width: 0;\n  max-width: 100%;",
    "label.field select {\n  width: 100%;\n  min-width: 0;\n"
    "  max-width: 100%;\n  min-height: 24px;",
    "@media (prefers-reduced-motion: reduce)",
    "animation: none !important",
    "*::before",
    "*::after",
):
    if fragment not in design_system_styles:
        errors.append(
            f"Design system is missing shared visible-focus/forced-color coverage: {fragment}"
        )
for fragment in (
    "expectSharedInteractionAccessibility",
    "Every visible enabled interactive target must be at least 24 by 24 CSS pixels",
    'input:not([disabled]):not([type="hidden"])',
    "element.classList.contains('skip-link') && !element.matches(':focus')",
    "bounds.width >= 24 && bounds.height >= 24",
    ".slice(0, 25)",
    "Diagnostics intentionally exclude labels, values and identifiers",
    "line-height: 1.5 !important",
    "letter-spacing: 0.12em !important",
    "word-spacing: 0.16em !important",
    "margin-block-end: 2em !important",
    "WCAG text-spacing overrides must not create root-page horizontal overflow",
    "WCAG text-spacing overrides must not clip interactive labels",
    "expectMobileNarrowReflow",
    "viewport.width > 390",
    "page.setViewportSize({ width: 320, height: viewport.height })",
    "finally {\n      await page.setViewportSize(viewport);",
    "WCAG 1.4.10 narrow reflow must not create root-page horizontal overflow",
    "WCAG 1.4.10 narrow reflow must not clip interactive labels",
    "overflowingTargets",
    "element.scrollWidth > element.clientWidth + 1",
    "element.matches(':focus-visible')",
    "outlineWidth: Number.parseFloat(style.outlineWidth)",
    "toBeGreaterThanOrEqual(24)",
    "page.emulateMedia({ forcedColors: 'active' })",
    "page.emulateMedia({ reducedMotion: 'reduce' })",
    "geo-a11y-motion-probe 5s linear infinite",
    "animationName: 'none'",
    "transitionDuration: '0s'",
):
    if fragment not in shared_accessibility_e2e:
        errors.append(f"Shared accessibility E2E is missing interaction coverage: {fragment}")
for fragment in (
    "drops every Query cache and local state when the safe experience scope changes",
    "shared-identity-cache-canary",
    "const [localOwner] = useState(context.userPubId)",
    "expect(await screen.findByText('usr_second:usr_second')).toBeTruthy()",
    "expect(screen.queryByText(/usr_first/)).toBeNull()",
):
    if fragment not in design_system_primitives_tests:
        errors.append(
            f"Design-system identity cache isolation tests are missing coverage: {fragment}"
        )
for fragment in (
    "drops a failed error boundary when the safe experience scope changes",
    "identity-scoped render failure",
    "expect(screen.getByText('usr_recovered')).toBeTruthy()",
    "expect(screen.queryByRole('alert')).toBeNull()",
):
    if fragment not in design_system_primitives_tests:
        errors.append(
            f"Design-system identity error-boundary recovery tests are missing coverage: {fragment}"
        )
for fragment in (
    "starts a new bootstrap generation when the loader changes and discards the older response",
    "usr_second_bootstrap",
    "usr_stale_bootstrap",
    "expect(secondLoad).toHaveBeenCalledOnce()",
):
    if fragment not in design_system_primitives_tests:
        errors.append(f"Design-system bootstrap generation tests are missing coverage: {fragment}")
for fragment in (
    "rejects control and bidi identity text and encodes the remaining scope structurally",
    "tnt_safe\\u0000prj_collision",
    "usr_safe\\u2066",
    "const scope = createSafeExperienceScopeKey(projected)",
    "expect(JSON.parse(scope)).toEqual(",
):
    if fragment not in design_system_security_tests:
        errors.append(
            f"Design-system structured identity scope tests are missing coverage: {fragment}"
        )
for fragment in (
    "keeps hostile delimiter placement structurally distinct without retaining a raw NUL",
    "createStructuredClientScopeKey(['tenant\\u0000actor', 'role'])",
    "createStructuredClientScopeKey(['tenant', 'actor\\u0000role'])",
    "expect(left).not.toBe(right)",
):
    if fragment not in design_system_security_tests:
        errors.append(
            f"Design-system structured client scope tests are missing coverage: {fragment}"
        )
api_client_source = (root / "packages/api-client/src/index.ts").read_text(encoding="utf-8")
browser_security_source = (root / "packages/api-client/src/browser-security.ts").read_text(
    encoding="utf-8"
)
shared_hostile_url_e2e = (root / "tests/e2e/shared-hostile-url.ts").read_text(encoding="utf-8")
shared_shell_actions_e2e = (root / "tests/e2e/shared-shell-actions.ts").read_text(encoding="utf-8")
shared_oversized_json_e2e = (root / "tests/e2e/shared-oversized-json.ts").read_text(
    encoding="utf-8"
)
customer_shell_source = (root / "apps/customer-web/app/shell.tsx").read_text(encoding="utf-8")
if "trace_tokens" in customer_shell_source:
    errors.append(
        "apps/customer-web/app/shell.tsx must not retain the server-only analytics "
        "trace_tokens capability in browser types or state"
    )
customer_account_mutation_guard = (
    root / "apps/customer-web/app/account-mutation-guard.ts"
).read_text(encoding="utf-8")
client_entries = {
    application: (root / f"apps/{application}/app/entry.client.tsx").read_text(encoding="utf-8")
    for application in (
        "customer-web",
        "operations-web",
        "report-studio",
        "intelligence-web",
        "intake-form",
    )
}
if "|1[3-9]\\d{9}|" not in browser_security_source:
    errors.append(
        "packages/api-client/src/browser-security.ts must reject complete phone numbers "
        "embedded inside otherwise ordinary client strings"
    )
for fragment in (
    "normalizeClientSecretCandidate",
    "normalize('NFKC')",
    "clientSecretInvisiblePattern",
    "decodeURIComponent(normalized)",
    "profile(?:s|[_ /-]?(?:path|dir|directory))?",
    "1[3-9](?:[\\s().-]?\\d){9}",
    "export const containsClientSecret",
    "export const containsClientSecretKey",
    "export const containsUnsafeClientControlCharacter",
):
    if fragment not in browser_security_source:
        errors.append(
            "packages/api-client/src/browser-security.ts is missing canonical client DLP "
            f"coverage: {fragment}"
        )
for source_name, source, module_path in (
    (
        "packages/design-system/src/index.tsx",
        design_system_source,
        "@geo/api-client/browser-security",
    ),
    ("packages/api-client/src/index.ts", api_client_source, "./browser-security"),
):
    for fragment in (
        "containsClientSecret,",
        "containsClientSecretKey,",
        "containsUnsafeClientControlCharacter,",
        f"from '{module_path}'",
    ):
        if fragment not in source:
            errors.append(f"{source_name} is not wired to canonical client DLP: {fragment}")
for fragment in (
    "!containsClientSecretKey(key)",
    "containsClientSecretKey(decodedParameter)",
    "containsClientSecretKey(header)",
):
    if fragment not in design_system_source:
        errors.append(f"Design system is missing normalized secret-key DLP usage: {fragment}")
for fragment in ("containsBrowserSecretKey", "containsBrowserSecretKey(key)"):
    if fragment not in api_client_source:
        errors.append(f"@geo/api-client is missing normalized secret-key DLP usage: {fragment}")
for fragment in (
    "normalizes encoded, full-width and zero-width secrets plus cross-platform profile paths",
    "Bearer%2520encoded-session-canary",
    "824-911",
    "browser-profiles",
):
    if fragment not in design_system_security_tests:
        errors.append(f"Design-system normalized DLP tests are missing coverage: {fragment}")
for fragment in (
    "normalizes full-width, zero-width and encoded secret property names",
    "ａｃｃｅｓｓ＿ｔｏｋｅｎ",
    "profile%255Fpath",
    "encoded-key-canary",
):
    if fragment not in design_system_security_tests:
        errors.append(f"Design-system normalized secret-key tests are missing coverage: {fragment}")
for fragment in (
    "１３８００１３８０００",
    "Bearer%2520encoded-session-canary",
    "profile_dir",
    "User Data",
):
    if fragment not in shared_shell_actions_e2e:
        errors.append(f"Shared shell normalized DLP E2E is missing coverage: {fragment}")
for fragment in (
    "normalizedSecretKeyParameters",
    "fullwidth-key-url-canary",
    "zero-width-key-url-canary",
    "encoded-key-url-canary",
):
    if fragment not in shared_hostile_url_e2e:
        errors.append(f"Shared hostile-URL secret-key E2E is missing coverage: {fragment}")
for fragment in (
    "clientUrlLimits",
    "clientHistoryStateLimits",
    "projectSafeClientHistoryState",
    "containsClientSecretKey(key)",
    "containsNumericClientSecret(value)",
    "historyProjection.value",
    "export function installClientNavigationSecurity",
    "export function installClientWindowNameSecurity",
    "export function installClientBrowserSecurity",
    "export function scrubClientStorage",
    "storagePrototype.setItem = secureSetItem",
    "containsClientSecretKey(key)",
    "containsClientSecret(value)",
    "decodeClientUrlValue",
    "url.username = ''",
    "decodedPathSegments",
    "containsClientSecretKey(decodedFragment)",
    "window.history.pushState = securePushState",
    "window.history.replaceState = secureReplaceState",
    "projectSafeClientHistoryState(data).value",
    "addEventListener('popstate', sanitize, { capture: true })",
    "removeEventListener('popstate', sanitize, { capture: true })",
    "const uninstallWindowName = installClientWindowNameSecurity()",
    "window.name = ''",
    "Object.defineProperty(window, 'name'",
    "get: readEmptyWindowName",
    "set: discardWindowNameWrite",
    "uninstallWindowName()",
    "rawFragment",
    "url.toString().length > clientUrlLimits.totalLength",
    "function navigateClientSection",
    "function updateClientUrlParameters",
    "projectSafeClientHistoryState({}).value",
):
    if fragment not in design_system_source:
        errors.append(f"Design system is missing the bounded full-URL boundary: {fragment}")
for fragment in (
    "removes multi-encoded secret fragments while retaining a bounded public anchor",
    "removes standalone secret names from path and fragment without echoing them",
    "access%255Ftoken#profile%255Fpath",
    "installs the navigation boundary before router popstate consumers",
    "projects pushState and replaceState before the browser retains either entry",
    "scrubs existing storage and rejects secret writes before either browser store retains them",
    "clears and seals the cross-navigation window name surface",
    "Bearer bootstrap-window-name-canary",
    "Cookie=session-window-name-canary OTP 824911",
    "safe-name-after-uninstall",
    "reports required-hint removal and clears an oversized storage projection",
    "retains the exact public profile section while rejecting profile path names",
    "history-install-canary",
    "bounds parameter names, values, count and total URL length before browser "
    "history retains them",
    "projects browser history state without retaining secret keys, values or cycles",
    "opaque-history-canary",
    "cyclic.self = cyclic",
    "navigates between allow-listed sections only after applying the shared URL boundary",
    "updates bounded public URL filters while deleting secret-shaped values before history",
):
    if fragment not in design_system_security_tests:
        errors.append(f"Design-system URL security tests are missing coverage: {fragment}")
for fragment in (
    "fragment-url-canary",
    "long_safe",
    "new URL(page.url()).hash",
    "history.pushState(",
    "history-url-canary",
    "history-state-key-canary",
    "immediateHistorySurfaces",
    "immediateStorageSurfaces",
    "bootstrap-window-name-canary",
    "window-name-write-canary",
    "windowName: window.name",
    "post-bootstrap-storage-key-canary",
    "history.state",
    "await page.goBack()",
    "applicationPath",
    "access%255Ftoken",
    "profile%255Fpath",
):
    if fragment not in shared_hostile_url_e2e:
        errors.append(f"Shared hostile-URL E2E is missing fragment/length coverage: {fragment}")
for fragment in (
    "verifyOversizedJsonBoundary",
    "rejects oversized decoded JSON before parsing or business reads",
    "'Content-Length': String(25 * 1024 * 1024 + 1)",
    "Bearer oversized-json-browser-canary",
    "/secret/browser/profile/oversized-json-canary",
    "__geoOversizedIdentityReads",
    "await expectAccessible(page)",
    "rejects genuinely gzip-compressed JSON by decoded bytes before business reads",
    "X-Geo-E2E-Decoded-Json-Boundary",
    "__geoOversizedGzipResponseFacts",
    "contentEncoding: 'gzip'",
    "encodedBelowBoundary: true",
    "decodedAboveBoundary: true",
    "const decodedBody = await response.arrayBuffer()",
    "declaredDecodedLength === decodedBody.byteLength",
    "decodedLengthMatches: true",
    "oversized-gzip-browser-canary",
    "oversized-gzip-canary",
):
    if fragment not in shared_oversized_json_e2e:
        errors.append(f"Shared oversized-JSON E2E is missing boundary coverage: {fragment}")
e2e_static_server = (root / "scripts/e2e_static_servers.mjs").read_text(encoding="utf-8")
for fragment in (
    "import { gzipSync } from 'node:zlib'",
    "const jsonResponseLimitBytes = 25 * 1024 * 1024",
    "request.headers[oversizedGzipJsonHeader] === appRole",
    "'Content-Encoding': 'gzip'",
    "'X-Geo-E2E-Decoded-Length'",
    "compressed.byteLength >= jsonResponseLimitBytes",
    "Bearer oversized-gzip-browser-canary",
    "/secret/browser/profile/oversized-gzip-canary",
):
    if fragment not in e2e_static_server:
        errors.append(f"E2E static server is missing decoded gzip boundary coverage: {fragment}")
if "navigateClientSection(section, customerNavIds)" not in customer_shell_source:
    errors.append("Customer cross-workspace navigation must use the shared URL boundary")
for application, entry_source in client_entries.items():
    for fragment in (
        "installClientBrowserSecurity(",
        "hydrateRoot(",
        "<HydratedRouter />",
        "safeReactRootErrorHandlers",
    ):
        if fragment not in entry_source:
            errors.append(
                f"{application} client entry is missing early browser/error security: {fragment}"
            )
    if entry_source.find("installClientBrowserSecurity(") > entry_source.find("<HydratedRouter />"):
        errors.append(
            f"{application} must install URL security before HydratedRouter is constructed"
        )
    if entry_source.count("safeReactRootErrorHandlers") != 2:
        errors.append(
            f"{application} must import and install exactly one shared React root error boundary"
        )
    if entry_source.find("safeReactRootErrorHandlers,") < entry_source.find("<HydratedRouter />"):
        errors.append(
            f"{application} must pass the shared React root error handlers to hydrateRoot"
        )
    for forbidden_fragment in ("console.", "reportError(", "sendBeacon("):
        if forbidden_fragment in entry_source:
            errors.append(
                f"{application} client entry bypasses safe React diagnostics: {forbidden_fragment}"
            )
for fragment in (
    "safeClientDiagnosticEventName = 'geo:safe-client-diagnostic'",
    "export type SafeClientErrorDiagnostic",
    "projectSafeClientErrorDiagnostic(",
    "safeClientErrorNames.has(error.name)",
    ".reduce((count, line) => count + (line.trim().length > 0 ? 1 : 0), 0)",
    "Math.min(",
    "componentFrames",
    "hasCause: error instanceof Error && error.cause !== undefined",
    "Object.freeze({",
    "window.dispatchEvent(new CustomEvent(safeClientDiagnosticEventName",
    "A diagnostic sink may not turn an already-handled product failure into a second failure",
    "export const safeReactRootErrorHandlers",
    "onCaughtError:",
    "onUncaughtError:",
    "onRecoverableError:",
    "export function installClientDiagnosticSecurity()",
    "const relayedSafeErrors = new WeakSet<Error>()",
    "reportClientError('window_error', event.error)",
    "reportClientError('unhandled_rejection', event.reason)",
    "event.stopImmediatePropagation()",
    "event.preventDefault()",
    "relaySafeRuntimeError('GEO_SAFE_WINDOW_ERROR', false)",
    "relaySafeRuntimeError('GEO_SAFE_UNHANDLED_REJECTION', true)",
    "window.addEventListener('error', onWindowError)",
    "window.addEventListener('unhandledrejection', onUnhandledRejection)",
    "reportClientError('react_error_boundary'",
    "reportClientError('experience_bootstrap_error'",
    "错误通道只接收类型与栈帧计数",
):
    if fragment not in design_system_source:
        errors.append(f"Design system safe React error channel is missing: {fragment}")
safe_react_error_channel = design_system_source.split(
    "export const safeClientDiagnosticEventName", 1
)[-1].split("export class ProductErrorBoundary", 1)[0]
for forbidden_fragment in (
    "error.message",
    "error.stack",
    "String(error)",
    "event.message",
    "event.reason.message",
    "String(event.reason)",
    "console.",
    "reportError(",
    "sendBeacon(",
    "localStorage",
    "sessionStorage",
):
    if forbidden_fragment in safe_react_error_channel:
        errors.append(
            "Safe React error channel must not inspect or persist raw diagnostics: "
            f"{forbidden_fragment}"
        )
for fragment in (
    "replaces every React 19 root error default with a count-only ephemeral diagnostic",
    "safeReactRootErrorHandlers.onCaughtError",
    "safeReactRootErrorHandlers.onUncaughtError",
    "safeReactRootErrorHandlers.onRecoverableError",
    "Cookie=session-react-root-canary",
    "profile_path: '/secret/browser/profile/react-root-canary'",
    "expect(diagnostics.every(Object.isFrozen)).toBe(true)",
    "expect(consoleError).not.toHaveBeenCalled()",
    "expect(window.location.href).toBe(originalUrl)",
    "expect(JSON.stringify(localStorage)).toBe(originalLocalStorage)",
    "expect(JSON.stringify(sessionStorage)).toBe(originalSessionStorage)",
):
    if fragment not in design_system_error_tests:
        errors.append(f"Design-system safe React error tests are missing coverage: {fragment}")
for fragment in (
    "projects global errors and unhandled rejections before browser default reporting",
    "Cookie=session-global-error-canary OTP 824911",
    "proxy_password=global-error-canary",
    "13800138000 token=global-rejection-canary",
    "expect(relayTimerCount).toBe(2)",
    "expect(errorEvent.defaultPrevented).toBe(true)",
    "expect(rejectionEvent.defaultPrevented).toBe(true)",
):
    if fragment not in design_system_security_tests:
        errors.append(
            f"Design-system global error diagnostic tests are missing coverage: {fragment}"
        )
for fragment in (
    "kind: 'experience_bootstrap_error'",
    "componentFrames: 0",
    "hasCause: false",
):
    if fragment not in design_system_primitives_tests:
        errors.append(f"Experience bootstrap safe diagnostic tests are missing: {fragment}")
for fragment in (
    "type SafeNavItem",
    "projectSafeInternalNavigationHref",
    "function projectSafeProductNavigation",
    "safeNav.filter((item) => !item.href && !item.disabledExternal)",
    'title="导航地址未通过安全校验"',
):
    if fragment not in design_system_source:
        errors.append(f"Design system is missing safe product navigation projection: {fragment}")
for fragment in (
    "retains only unique safe labels and bounded internal platform links",
    "keeps internal href navigation outside section state and renders unsafe destinations disabled",
    "projects health status again in the shared shell and ignores superseded probes",
    "health-probe-canary",
):
    if (
        fragment not in design_system_security_tests
        and fragment not in design_system_primitives_tests
    ):
        errors.append(f"Design-system navigation security tests are missing coverage: {fragment}")
operations_shared_shell_e2e = (root / "tests/e2e/operations-shared-shell.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in ("internalLink:", "href: '/platform/operations/execution'"):
    if fragment not in operations_shared_shell_e2e:
        errors.append(f"Operations shared-shell E2E is missing safe deep-link coverage: {fragment}")
shared_shell_specs = {
    product: (root / f"tests/e2e/{product}-shared-shell.spec.ts").read_text(encoding="utf-8")
    for product in ("customer", "operations", "reports", "intelligence")
}
for product, shared_shell_spec in shared_shell_specs.items():
    for fragment in (
        "import { verifyOversizedJsonBoundary } from './shared-oversized-json'",
        "verifyOversizedJsonBoundary({",
    ):
        if fragment not in shared_shell_spec:
            errors.append(
                f"{product} shared-shell E2E is missing oversized JSON boundary coverage: "
                f"{fragment}"
            )
media_prices_source = (
    root / "apps/operations-web/app/features/media-prices/MediaPrices.tsx"
).read_text(encoding="utf-8")
media_prices_route = (root / "apps/operations-web/app/features/media-prices/route.tsx").read_text(
    encoding="utf-8"
)
media_prices_css = (
    root / "apps/operations-web/app/features/media-prices/media-prices.css"
).read_text(encoding="utf-8")
media_prices_tests = (
    root / "apps/operations-web/app/features/media-prices/MediaPrices.test.tsx"
).read_text(encoding="utf-8")
media_prices_e2e = (root / "tests/e2e/operations-media-prices.spec.ts").read_text(encoding="utf-8")
media_prices_real_e2e = (root / "tests/e2e/operations-media-prices-real.spec.ts").read_text(
    encoding="utf-8"
)
media_prices_visual_e2e = (root / "tests/e2e/operations-visual.spec.ts").read_text(encoding="utf-8")
for fragment in (
    "sourceCount !== priceEntries.length",
    "stats.unique_media !== rows.length",
    "stats.matched_2plus !== matchedTwoPlus",
    "globalThis.crypto.subtle.digest('SHA-256', result.data)",
    "if (sha256 !== shaHeader)",
):
    if fragment not in api_client_source:
        errors.append(f"Media-price generated-client integrity boundary is missing: {fragment}")
for fragment in (
    "readMediaPricesUrlState",
    "writeMediaPricesUrlState",
    "updateClientUrlParameters(updates, [], replace)",
    "window.addEventListener('popstate', restoreUrlState)",
    "getMediaPricesDataset(requestHeaders)",
    "activeRequestHeadersRef.current !== headers",
    "refreshStatusReadGenerationRef.current !== readGeneration",
    "refreshStatusReadGenerationRef.current += 1",
    "mediaPricesRefreshRevision(status) !== refreshTerminalBaselineRef.current",
    "observedCurrentRunStatusRef.current",
    "刷新已接受，正在等待新的终态记录",
    "pollingGenerationRef.current === generation",
    "refreshSubmissionScopeRef.current !== headers",
    "const reloaded = await reloadDataset(headers)",
    "const reloaded = await reloadDataset(headers);\n"
    "          if (activeRequestHeadersRef.current !== headers) return;",
    "presentation.staleOther.join('、')",
    "presentation.partial.join('、')",
    "setDataset(null)",
    "setState(result.kind)",
    "setRefreshStatusReadState(result.kind)",
    "刷新状态读取失败，当前状态未知",
    "权限不足：无法查看或启动数据刷新",
    'StatePanel state="real-zero"',
    'StatePanel state="empty"',
):
    if fragment not in media_prices_source:
        errors.append(f"Operations media-prices URL/state boundary is missing: {fragment}")
for fragment in (
    "mode: 'authoritative-status' | 'accepted-start'",
    "const sourceSetIsComplete =",
    "state !== 'running'",
    "startedAt > updatedAt",
    "state === 'done' &&",
    "sources[platform]?.status === 'pending'",
    "(status === 'pending' && (source.rows !== 0 || note !== ''))",
    "projectMediaPricesRefreshEnvelope(result.data, 'accepted-start')",
):
    if fragment not in api_client_source:
        errors.append(f"Media-price refresh terminal truth boundary is missing: {fragment}")
if re.search(r"window\.history\.(?:pushState|replaceState)\s*\(", media_prices_source):
    errors.append("Operations media-prices must mutate URL state only through the shared boundary")
for fragment in (
    "<ProductShell",
    'currentNavId="media-prices"',
    "liveOperationsRouteNav",
    "operationsRouteNav",
):
    if fragment not in media_prices_route:
        errors.append(f"Operations media-prices route lost the shared product shell: {fragment}")
for fragment in (
    ".media-prices-filters input[type='checkbox']",
    "flex: 0 0 24px",
    "width: 24px",
    "height: 24px",
    "min-height: 24px",
    ".cell-links a {\n  display: inline-flex;\n  align-items: center;\n  min-height: 24px;",
    ".media-prices .geo-a",
    ".media-prices .geo-z",
    "grid-template-columns: repeat(4, minmax(0, 1fr))",
    "grid-template-columns: repeat(3, minmax(0, 1fr))",
    ".metric-row .metric-refresh",
):
    if fragment not in media_prices_css:
        errors.append(f"Operations media-prices responsive/WCAG styles are missing: {fragment}")
for fragment in (
    "round-trips bounded URL filters and refuses secret-shaped query values",
    "responds to browser history without another API read",
    "retries only the failed dataset region and accepts an authoritative real zero",
    "discards an older identity poll before it can update freshness or reread the dataset",
    "discards an accepted refresh response after the initiating identity changes",
    "does not mislabel an unavailable refresh-status read as never refreshed and retries locally",
    "does not let a delayed initial status overwrite a refresh completed afterward",
    "keeps polling when the first terminal status is unchanged from before the refresh",
    "stops an accepted refresh immediately when status polling becomes forbidden",
    "fails closed instead of retaining an old dataset when the completed refresh reload "
    "is forbidden",
    "does not let an older completed-refresh reload clear the new identity refresh state",
):
    if fragment not in media_prices_tests:
        errors.append(f"Operations media-prices component tests are missing: {fragment}")
for fragment in (
    "uses the shared shell, URL history, pagination and safe CSV export",
    "distinguishes missing, forbidden, failed retry and authoritative real zero",
    "rejects secret-bearing API projections without rendering or persisting canaries",
    "fails closed when artifact digest or statistics contradict projected rows",
    "starts, polls and reloads an authoritative completed refresh exactly once",
    "media refresh status permission loss stays distinct and stops polling immediately",
    "a delayed initial refresh status cannot overwrite a newer completed refresh",
    "a contradictory completed refresh remains unavailable and never claims success",
    "an unchanged pre-refresh terminal record cannot complete a newly accepted refresh",
    "a contradictory authoritative failure remains unavailable and never claims failure",
    "an authoritative status-shaped 202 body is not accepted as a start receipt",
    "completed refresh fails closed when the new dataset read loses permission",
    "completed refresh warns when a source is partial or uses stale data",
    "installSyntheticHttpResponses",
    "await expectAccessible(page)",
):
    if fragment not in media_prices_e2e:
        errors.append(f"Operations media-prices E2E is missing: {fragment}")
for fragment in (
    "consumes the real 20k-row API artifact through the generated boundary",
    "x-dataset-sha256",
    "private, no-store",
    "toHaveCount(100)",
    "Date.now() - startedAt",
    "__geoRealMediaDatasetReads",
    "await artifactResponse.dispose()",
):
    if fragment not in media_prices_real_e2e:
        errors.append(f"Operations media-prices real-API E2E is missing: {fragment}")
if "operations-media-prices.png" not in media_prices_visual_e2e:
    errors.append("Operations media-prices has no frozen three-viewport visual regression")
for viewport in ("desktop", "tablet", "mobile"):
    media_prices_snapshot = (
        root
        / "tests/e2e/operations-visual.spec.ts-snapshots"
        / f"operations-media-prices-operations-{viewport}-linux.png"
    )
    if not media_prices_snapshot.is_file():
        errors.append(
            f"Operations media-prices visual baseline is missing: "
            f"{media_prices_snapshot.relative_to(root)}"
        )
for fragment in ("metricStatePresentation", "state.label", "metric-badges"):
    if fragment not in design_system_source:
        errors.append(
            "packages/design-system/src/index.tsx is missing semantic metric-state "
            f"presentation: {fragment}"
        )
for fragment in (
    "projectAnalyticsMetricState",
    "analyticsMetricDataState",
    "analyticsRateChartState",
    "state: metricDataState('mention_rate')",
    "pageProjection?.has_more === true",
):
    if fragment not in customer_shell_source:
        errors.append(
            "apps/customer-web/app/shell.tsx is missing honest metric or pagination projection: "
            f"{fragment}"
        )
if customer_shell_source.count("pageProjection?.has_more === true") < 2:
    errors.append(
        "apps/customer-web/app/shell.tsx must fail closed on both answer and evidence "
        "page metadata projections"
    )
if customer_shell_source.count("const pageProjectionIsValid =") < 2:
    errors.append(
        "apps/customer-web/app/shell.tsx must reject inconsistent answer and evidence "
        "page metadata before claiming completeness"
    )

customer_product_e2e = (root / "tests/e2e/customer-product-live.spec.ts").read_text(
    encoding="utf-8"
)
customer_member_integrity_e2e = (root / "tests/e2e/customer-member-integrity.spec.ts").read_text(
    encoding="utf-8"
)
customer_project_write_integrity_e2e = (
    root / "tests/e2e/customer-project-write-integrity.spec.ts"
).read_text(encoding="utf-8")
for fragment in (
    "inconsistentPageMeta",
    "has_more: inconsistentPageMeta ? false : !secondPage",
    "getByRole('button', { name: '下一页' })).toBeDisabled()",
    "oversized or unsafe identity governance lists stay explicit and governance-write locked",
    "a same-subject but input-mismatched member receipt fails locally without leakage",
    "member-input-mismatch-canary",
    "成员合同安全投影：服务返回 102 条，浏览器安全视图展示 98 条",
    "OIDC 绑定安全投影：服务返回 102 条，浏览器安全视图展示 98 条",
    "expect(governanceWrites).toBe(0)",
    "validated customer reads mounted data and serializes every write without secret leakage",
    "synchronouslyActivateTwice",
    "expect(exportBodies).toHaveLength(0)",
    "expect(packageBodies).toHaveLength(1)",
    "expect(reportQuestionBodies).toEqual([",
    "expect(deliveryConfirmBodies).toEqual([",
):
    if fragment not in customer_product_e2e:
        errors.append(
            "tests/e2e/customer-product-live.spec.ts is missing inconsistent page-meta "
            f"coverage: {fragment}"
        )
if customer_product_e2e.count("synchronouslyActivateTwice(") < 4:
    errors.append(
        "tests/e2e/customer-product-live.spec.ts must synchronously activate metric export, "
        "evidence package, report question and delivery confirmation twice"
    )

customer_role_e2e = (root / "tests/e2e/customer-role-isolation.spec.ts").read_text(encoding="utf-8")
for fragment in (
    "cross-tenant or duplicate project bootstrap fails closed before business reads",
    "bootstrap-browser-permission-canary",
    "bootstrap-cross-tenant-canary",
    "bootstrap-duplicate-canary",
    "control-character session hints are purged before any identity request",
    r"tnt_safe\u0000actor_collision",
    "expect(identityRequests).toBe(0)",
    "expect(surfaces.storage).not.toContain('geo.session.')",
    "expect(businessReads).toBe(0)",
    "expectAccessible(page)",
):
    if fragment not in customer_role_e2e:
        errors.append(
            "tests/e2e/customer-role-isolation.spec.ts is missing identity bootstrap "
            f"integrity/DLP coverage: {fragment}"
        )

customer_evidence_integrity_e2e = (
    root / "tests/e2e/customer-evidence-integrity.spec.ts"
).read_text(encoding="utf-8")
for fragment in (
    "oversized answer evidence stays bounded",
    "Array.from({ length: 201 }",
    "本页回答：服务返回 3 条，浏览器安全视图展示 1 条",
    "回答引用：服务返回 201 条，浏览器安全视图展示 199 条",
    "单项证据锚点：服务返回 201 条，浏览器安全视图展示 200 条",
    "toBeDisabled()",
    "expectAccessible(page)",
    "expect(packageWrites).toEqual([])",
    "closing one answer discards its slower relation response",
    "firstRelationGate",
    "A 过期来源",
    "B 当前来源",
    "answer and asset pagination discard delayed detail and package receipts from the prior page",
    "secondAnswerGate",
    "secondAssetGate",
    "getByRole('heading', { name: '第一页较慢回答' })).toHaveCount(0)",
    "getByRole('cell', { name: 'evd_evidence_page_01' })).toHaveCount(0)",
    "expect(packageBodies[0]?.evidence_pub_ids).toEqual(['evd_evidence_page_01'])",
    "stale-evidence-package-receipt-canary",
):
    if fragment not in customer_evidence_integrity_e2e:
        errors.append(
            "tests/e2e/customer-evidence-integrity.spec.ts is missing bounded answer/evidence "
            f"integrity coverage: {fragment}"
        )
for fragment in (
    "packageRequest.current += 1",
    "packageRequest.current !== requestId",
    "setSelected(null)",
    "setLiveRelationState('idle')",
):
    if fragment not in customer_shell_source:
        errors.append(
            f"Customer evidence workspace is missing resource-generation race handling: {fragment}"
        )

operations_snapshot = (root / "apps/operations-web/app/lifecycle-snapshot.tsx").read_text(
    encoding="utf-8"
)
for forbidden in (
    "@geo/api-client",
    "useQuery(",
    "useState(",
    "localStorage",
    "sessionStorage",
    "/api/v2/",
):
    if forbidden in operations_snapshot:
        errors.append(
            "apps/operations-web/app/lifecycle-snapshot.tsx must remain a pure, "
            f"single-snapshot S01 consumer; found {forbidden}"
        )

root_package = json.loads((root / "package.json").read_text(encoding="utf-8"))
root_dev_dependencies = root_package.get("devDependencies", {})
for dependency in ("@playwright/test", "@testing-library/react"):
    if dependency not in root_dev_dependencies:
        errors.append(f"package.json must retain frontend test dependency {dependency}")
for package_path in sorted((root / "apps").glob("*/package.json")) + sorted(
    (root / "packages").glob("*/package.json")
):
    package = json.loads(package_path.read_text(encoding="utf-8"))
    test_task = package.get("scripts", {}).get("test", "")
    uses_node_test_runner = test_task.startswith("node --test ")
    if (
        test_task
        and not uses_node_test_runner
        and "vitest" not in package.get("devDependencies", {})
    ):
        errors.append(
            f"{package_path.relative_to(root)} has a test task but declares neither "
            "the Node test runner nor Vitest"
        )

api_client = (root / "packages/api-client/src/index.ts").read_text(encoding="utf-8")
if "import type { paths } from './schema.generated'" not in api_client:
    errors.append("@geo/api-client must derive public types from schema.generated.ts")
if "createClient<paths>" not in api_client:
    errors.append("@geo/api-client must instantiate openapi-fetch with generated paths")
for fragment in (
    "type GeneratedIdentitySessionHeaders = NonNullable<",
    "'X-Tenant-Id' | 'X-Actor-Id' | 'X-Actor-Role'",
    "type ProjectedApiClientOverride = object",
    "const projectedApiClient = (client: ProjectedApiClientOverride): GeoApiClient",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client browser identity type is missing {fragment}")
if api_client.count("client: ProjectedApiClientOverride = apiClient") != 147:
    errors.append(
        "@geo/api-client must keep all 147 projected wrapper overrides free of the raw "
        "generated client type"
    )
projected_client_unwraps = len(
    re.findall(
        r"projectedApiClient\(client\)\.(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)",
        api_client,
    )
) + api_client.count("const api = projectedApiClient(client);")
if projected_client_unwraps != 147:
    errors.append(
        "@geo/api-client must unwrap every projected wrapper override only inside its "
        "generated request implementation"
    )
if "client: GeoApiClient = apiClient" in api_client or re.search(
    r"\bclient\.(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)",
    api_client,
):
    errors.append(
        "@geo/api-client projected wrappers must not expose or directly use the raw "
        "generated client type"
    )
for forbidden_fragment in (
    "export type { paths }",
    "export * from './schema.generated'",
    "export type AnalyticsOverviewResponse =",
    "export type AnalyticsAnswerPage =",
    "export type AnalyticsAnswerRelations =",
    "export type AnalyticsDeltaResponse =",
    "export type AnalyticsCompetitorResponse =",
    "export type InvestigationDetail =",
    "export type InvestigationVisualDiffs =",
    "export type ReportDetail =",
    "export type ReportPage =",
    "export type ReportSummary =",
    "export type ReportReviewCreateResponse =",
    "export type ReportCommentCreateResponse =",
    "export type ReportDeliveryView =",
    "export type ReportDeliveryCreateResponse =",
    "export type ReportDeliveryConfirmResponse =",
    "export type ReportActionCreateResponse =",
    "export type ReportEffectRetestCreateResponse =",
    "export type EvidenceAssetPage =",
    "export type EvidencePackageCreateResponse =",
    "export type InvestigationPage =",
    "export type InvestigationPageHistory =",
    "export type VerdictCreateResponse =",
    "export type AppealCreateResponse =",
    "export type AppealResolutionResponse =",
    "export type EvaluationDatasetView =",
    "export type EvaluationDatasetPage =",
    "export type EvaluationRunView =",
    "export type EvaluationRunPage =",
    "export type ModelAdmissionView =",
    "export type ModelAdmissionPage =",
    "export type OperationsLifecycleResponse =",
):
    if forbidden_fragment in api_client:
        errors.append(
            "@geo/api-client must not publicly re-export the secret-capable generated schema: "
            f"{forbidden_fragment}"
        )
if re.search(r"export type \w+ = Omit<", api_client):
    errors.append(
        "@geo/api-client public browser read types must be fixed-field Pick or explicit "
        "object shapes, never Omit over the generated contract"
    )
if re.search(r"export type ProjectPageResponse\s*=\s*paths\[", api_client):
    errors.append(
        "@geo/api-client must keep the raw generated project page private and export only "
        "the fixed-field S01 compatibility view"
    )
for fragment in (
    "type AnalyticsOverviewContractMetric = AnalyticsOverviewResponse[number]",
    "type ProjectPageContractResponse =",
    "export type ProjectSummary = Pick<",
    "export type ProjectPageResponse = {",
    "data: ProjectSummary[];",
    "export type AnalyticsOverviewMetric = Pick<",
    "export type AnalyticsOverviewSafeResponse = AnalyticsOverviewMetric[]",
    "AnalyticsOverviewProjection = ProjectedCollection<AnalyticsOverviewMetric>",
    "export type AnalyticsAnswerSafeView = {",
    "export type AnalyticsAnswerProjection = ProjectedContractPage<AnalyticsAnswerSafeView>",
    "export type AnalyticsCitationSafeView = Pick<",
    "export type AnalyticsBoundingBoxSafeView = {",
    "export type AnalyticsAnchorSafeView = Pick<",
    "export type AnalyticsEvidenceSafeView = Pick<",
    "export type AnalyticsHistorySafeView = {",
    "history: AnalyticsHistorySafeView[];",
    "bbox: AnalyticsBoundingBoxSafeView | null;",
    "Number.isFinite(width)",
    "width <= 0",
    "cited_text: citedText,",
    "export type InvestigationScoreSafeView = {",
    "export type InvestigationClaimSafeView = {",
    "export type InvestigationEvidenceSafeView = {",
    "export type InvestigationSourceSafeView = {",
    "export type InvestigationGraphSafeView = {",
    "export type InvestigationAppealSafeView = {",
    "export type InvestigationVerdictSafeView = {",
    "export type AnalyticsCompetitorSafeResponse = AnalyticsCompetitorSafeView[]",
    "export type AnalyticsDeltaSafeResponse = Partial<",
    "export type InvestigationVisualDiffSafeView = Pick<",
    "export type InvestigationVisualDiffSafeResponse = InvestigationVisualDiffSafeView[]",
    "export type InvestigationSummarySafeView = {",
    "export type InvestigationPageProjection = ProjectedContractPage<InvestigationSummarySafeView>",
    "export type InvestigationPageHistorySafeView = {",
    "export type InvestigationPageHistoryProjection =",
    "ProjectedCollection<InvestigationPageHistorySafeView>",
    "export type EvaluationDatasetSafeView = {",
    "export type EvaluationDatasetPageProjection = "
    "ProjectedContractPage<EvaluationDatasetSafeView>",
    "export type EvaluationRunSafeView = {",
    "export type EvaluationRunPageProjection = ProjectedContractPage<EvaluationRunSafeView>",
    "export type ModelAdmissionSafeView = {",
    "export type ModelAdmissionPageProjection = ProjectedContractPage<ModelAdmissionSafeView>",
    "declare const safeStructuredRecordBrand: unique symbol",
    "export type SafeStructuredRecord = {",
    "readonly [safeStructuredRecordBrand]: true",
    "export type ReportComponentSafeView = Pick<",
    "export type ReportFrozenFactSafeView = Pick<",
    "export type ReportArtifactSafeView = Pick<",
    "export type ReportEvidenceBindingSafeView = Pick<",
    "export type ReportReviewSafeView = Pick<",
    "export type ReportCommentSafeView = Pick<",
    "export type ReportEventSafeView = Pick<",
    "export type ReportVersionSafeView = Pick<",
    "export type EffectRetestSafeView = Pick<",
    "export type OptimizationActionSafeView = Pick<",
    "export type ReportDetailProjection = Pick<",
    "export type ReportSummarySafeView = {",
    "export type ReportPageProjection = ProjectedContractPage<ReportSummarySafeView>",
    "export type ReportDeliverySafeView = Pick<",
    "confirmation_comment: null;",
    "export type ReportDeliveryProjection = ProjectedCollection<ReportDeliverySafeView>",
    "export type ProjectResourceSafeData = {",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client public browser projection is missing {fragment}")
for projector_name in (
    "projectHealthBoundary",
    "projectIdentitySessionBoundary",
    "projectIdentityProjectPageBoundary",
    "projectAnalyticsOverviewBoundary",
    "projectAnalyticsBreakdownBoundary",
    "projectAnalyticsDeltaBoundary",
    "projectAnalyticsCompetitorBoundary",
    "projectAnalyticsAnswerBoundary",
    "projectAnalyticsAnswerPageBoundary",
    "projectAnalyticsCitationBoundary",
    "projectAnalyticsAnchorBoundary",
    "projectAnalyticsEvidenceBoundary",
    "projectAnalyticsHistoryBoundary",
    "projectAnalyticsAnswerRelationsBoundary",
    "projectCustomerAccountView",
    "projectCustomerPairingView",
    "projectResponsibleMemberView",
    "projectCustomerEventView",
    "projectOperationsLifecycleSnapshot",
    "projectIdentityMemberView",
    "projectOidcBindingView",
    "projectReportPage",
    "projectReportComponentBoundary",
    "projectReportFrozenFactBoundary",
    "projectReportArtifactBoundary",
    "projectReportEvidenceBindingBoundary",
    "projectReportReviewBoundary",
    "projectReportCommentBoundary",
    "projectReportEventBoundary",
    "projectReportVersionBoundary",
    "projectEffectRetestBoundary",
    "projectOptimizationActionBoundary",
    "projectReportDetailBoundary",
    "projectEvidenceAssetPageBoundary",
    "projectInvestigationScoreBoundary",
    "projectInvestigationClaimBoundary",
    "projectInvestigationEvidenceBoundary",
    "projectInvestigationSourceBoundary",
    "projectInvestigationGraphBoundary",
    "projectInvestigationAppealBoundary",
    "projectInvestigationVerdictBoundary",
    "projectInvestigationDetailBoundary",
    "projectInvestigationHistoryBoundary",
    "projectInvestigationVisualDiffBoundary",
    "projectInvestigationPage",
    "projectEvaluationDatasetView",
    "projectEvaluationDatasetPage",
    "projectEvaluationRunView",
    "projectEvaluationRunPage",
    "projectModelAdmissionView",
    "projectModelAdmissionPage",
):
    unknown_entry = re.compile(
        rf"(?:export function {projector_name}\s*\(\s*value: unknown|"
        rf"const {projector_name}\s*=\s*\(\s*value: unknown)"
    )
    if not unknown_entry.search(api_client):
        errors.append(f"@geo/api-client untrusted projector must accept unknown: {projector_name}")
for app_name in apps:
    app_root = root / "apps" / app_name / "app"
    for source_path in sorted((*app_root.rglob("*.ts"), *app_root.rglob("*.tsx"))):
        if app_name == "operations-web" and (
            "features/execution" in source_path.relative_to(app_root).as_posix()
            or "features/onboarding" in source_path.relative_to(app_root).as_posix()
            or "features/services" in source_path.relative_to(app_root).as_posix()
        ):
            continue
        source = source_path.read_text(encoding="utf-8")
        for forbidden_fragment in (
            "schema.generated",
            "createGeoApiClient",
            "GeoApiClient",
            "apiClient",
            "ProjectedApiClientOverride",
        ):
            if forbidden_fragment in source:
                errors.append(
                    f"{source_path.relative_to(root)} must consume only projected "
                    f"@geo/api-client exports, not {forbidden_fragment}"
                )
for fragment in (
    "async function secureGeoApiFetch(",
    "reportArtifactMediaTypes",
    "headers.set(",
    "'Accept'",
    "cache: 'no-store'",
    "redirect: 'error'",
    "referrerPolicy: 'no-referrer'",
    "response.body?.cancel()",
    "GEO Platform response media type is unavailable",
    "geoApiJsonResponseMaxBytes = 25 * 1024 * 1024",
    "async function boundGeoApiJsonResponse(",
    "total > maxBytes",
    "const probe = response.clone()",
    "const reader = probe.body.getReader()",
    "response.body.cancel()",
    "reader.releaseLock()",
    "GEO Platform response body is unavailable",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client is missing the hardened response boundary: {fragment}")
if "value.has_more === true && nextCursor !== null" not in api_client:
    errors.append("@geo/api-client must fail closed when has_more and safe cursor disagree")
if "isReportVersionPubId" not in api_client or "rptv_" not in api_client:
    errors.append(
        "@geo/api-client must accept the backend's real rptv_ report-version identity family"
    )
if "Promise<ProjectResourceResult<unknown>>" in api_client:
    errors.append("@geo/api-client must not expose unprojected unknown write receipts")
for raw_bootstrap_result in (
    "getHealth(): Promise<HealthResponse>",
    "session: sessionResult.data",
    "projects: projectsResult.data",
):
    if raw_bootstrap_result in api_client:
        errors.append(
            "@geo/api-client must not expose an unprojected bootstrap response: "
            f"{raw_bootstrap_result}"
        )
for raw_governance_result in (
    "ProjectResourceResult<ProjectResourceView[]>",
    "ProjectResourceResult<ClientProfilePage>",
    "ProjectResourceResult<AssetConfirmationPage>",
    "ProjectResourceResult<AnalyticsAnswerPage>",
    "ProjectResourceResult<AnalyticsAnswerRelations>",
    "ProjectResourceResult<ReportDeliveryView[]>",
    "ProjectResourceResult<EvidenceAssetPage>",
    "ProjectResourceResult<ReportDetail>",
    "ProjectResourceResult<InvestigationDetail>",
    "ProjectResourceResult<InvestigationPageHistory>",
    "ProjectResourceResult<InvestigationVisualDiffs>",
    "ProjectResourceResult<InvestigationPage>",
    "ProjectResourceResult<EvaluationDatasetPage>",
    "ProjectResourceResult<EvaluationRunPage>",
    "ProjectResourceResult<ModelAdmissionPage>",
    "ProjectResourceResult<IdentityMemberView[]>",
    "ProjectResourceResult<OidcBindingView[]>",
    "ProjectResourceResult<AnalyticsDeltaResponse>",
):
    if raw_governance_result in api_client:
        errors.append(
            f"@geo/api-client must not expose an unprojected browser read: {raw_governance_result}"
        )
for raw_write_result in (
    "ProjectResourceResult<ReportReviewCreateResponse>",
    "ProjectResourceResult<ReportActionCreateResponse>",
    "ProjectResourceResult<ReportEffectRetestCreateResponse>",
    "ProjectResourceResult<AppealCreateResponse>",
    "ProjectResourceResult<AppealResolutionResponse>",
):
    if raw_write_result in api_client:
        errors.append(
            "@geo/api-client must not expose an unprojected generic write receipt: "
            f"{raw_write_result}"
        )
for fragment in (
    "projectCustomerAccountWriteView",
    "projectCustomerPairingWriteView",
    "customerAccountRegistrationMatches",
    "customerAuthorizationWriteMatches",
    "customerPairingWriteMatches",
    "projectCustomerAccountView",
    "projectCustomerPairingView",
    "projectResponsibleMemberView",
    "projectCustomerEventView",
    "projectSafeAccountMask",
    "isNumericBrowserSecret",
    "!isNumericBrowserSecret(value)",
    "/1[3-9]\\d{9}/.test(projected)",
    "projectBoundedCollection",
    "ProjectedCollection",
    "ProjectedCursorPage",
    "customerAccountLifecycleProjectionLimits",
    "customerGovernanceProjectionLimits",
    "projectGovernanceCursorPage",
    "projectClientProfileBoundaryView",
    "projectAssetConfirmationBoundaryView",
    "projectProjectResourceView",
    "projectResourceWriteMatches",
    "clientProfileWriteMatches",
    "assetConfirmationWriteMatches",
    "customerAnalyticsProjectionLimits",
    "AnalyticsOverviewProjection",
    "AnalyticsBreakdownProjection",
    "AnalyticsDeltaProjection",
    "AnalyticsCompetitorProjection",
    "projectAnalyticsOverviewBoundary",
    "projectAnalyticsBreakdownBoundary",
    "projectAnalyticsDeltaBoundary",
    "projectAnalyticsCompetitorBoundary",
    "rowCount > 0",
    "evaluationDatasetRegistrationMatches",
    "evaluationDatasetApprovalMatches",
    "evaluationRunWriteMatches",
    "modelAdmissionWriteMatches",
    "customerEvidenceReadProjectionLimits",
    "ProjectedContractPage",
    "AnalyticsAnswerProjection",
    "AnalyticsAnswerRelationsProjection",
    "projectAnalyticsAnswerBoundary",
    "projectAnalyticsAnswerPageBoundary",
    "projectAnalyticsCitationBoundary",
    "projectAnalyticsEvidenceBoundary",
    "projectAnalyticsAnchorBoundary",
    "projectAnalyticsHistoryBoundary",
    "projectAnalyticsAnswerRelationsBoundary",
    "projectIdentityMemberWriteView",
    "expectedDisplayName",
    "expectedRole",
    "expectedServiceAccount",
    "projectOidcBindingWriteView",
    "projectIdentityMemberView",
    "projectOidcBindingView",
    "maskIdentitySubject",
    "IdentityMemberProjection",
    "OidcBindingProjection",
    "identityReadProjectionLimits",
    "projectBoundedUniqueCollection",
    "HealthProjection",
    "IdentitySessionProjection",
    "IdentityProjectPageProjection",
    "projectHealthBoundary",
    "projectIdentitySessionBoundary",
    "projectIdentityProjectPageBoundary",
    "identityReadProjectionLimits.projects",
    "permissions: []",
    "CustomerRevocationSafeReceipt",
    "workflowId === `account-revocation/${tenantPubId}/${safeAccountPubId}`",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client is missing customer lifecycle write projection: {fragment}")
for fragment in (
    "(?:^|[^\\w])\\d{6}(?:[^\\w]|$)",
    "|1[3-9]\\d{9}|",
):
    if fragment not in browser_security_source:
        errors.append(
            f"Canonical browser security is missing customer lifecycle secret detection: {fragment}"
        )
for fragment in (
    "ReportDeliveryProjection",
    "EvidenceAssetProjection",
    "ReportDetailProjection",
    "ReportDetailResult",
    "customerReportReadProjectionLimits",
    "reportDetailReadProjectionLimits",
    "projectReportDeliveryBoundary",
    "projectEvidenceAssetPageBoundary",
    "projectReportDetailBoundary",
    "projectSafeStructuredValue",
    "InvestigationDetailProjection",
    "InvestigationDetailResult",
    "InvestigationPageHistoryProjection",
    "InvestigationVisualDiffsProjection",
    "InvestigationPageProjection",
    "EvaluationDatasetPageProjection",
    "EvaluationRunPageProjection",
    "ModelAdmissionPageProjection",
    "intelligenceReadProjectionLimits",
    "normalizeReadProjectionLimit",
    "projectIntelligenceCursorPage",
    "projectInvestigationSummaryBoundary",
    "projectInvestigationDetailBoundary",
    "projectInvestigationHistoryBoundary",
    "projectInvestigationVisualDiffBoundary",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client is missing a deep read projection boundary: {fragment}")
for fragment in (
    "Number.isSafeInteger(value)",
    "safeTimestamp",
    "export const projectSafeIsoTimestamp",
    "strictIsoTimestampPattern",
    "day > daysInMonth[month - 1]!",
    "safeUnitDecimal(item.probability)",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client is missing strict list-domain projection: {fragment}")
# Enum domains are asserted against a whitespace-squashed copy so the freeze survives
# prettier re-wrapping of the fixed literal arrays.
api_client_compact = re.sub(r"\s+", " ", api_client)
for fragment in (
    "'draft', 'review', 'approved', 'published', 'superseded'",
    "'draft', 'collecting', 'review', 'decided', 'appealed', 'corrected'",
    "safeBrowserEnum(item.access_class, ['public', 'customer_private'] as const)",
    "'likely', 'unlikely', 'uncertain', 'insufficient'",
):
    if fragment not in api_client_compact:
        errors.append(f"@geo/api-client is missing strict list-domain projection: {fragment}")
for app_shell in (
    "apps/customer-web/app/shell.tsx",
    "apps/report-studio/app/shell.tsx",
    "apps/intelligence-web/app/shell.tsx",
):
    source = (root / app_shell).read_text(encoding="utf-8")
    if "projectSafeIsoTimestamp" not in source:
        errors.append(f"{app_shell} must reuse the shared strict browser timestamp projection")
    if "Date.parse(" in source:
        errors.append(f"{app_shell} must not parse broad or ambiguous API timestamps locally")
api_client_tests = (root / "packages/api-client/src/index.test.ts").read_text(encoding="utf-8")
for fragment in (
    "type ProjectedClientOverrideMethodKeys = Extract<",
    "keyof NonNullable<Parameters<typeof getHealth>[0]>",
    "projectedWrappersExcludeRawClientMethods",
    "does not expose raw generated methods through projected wrapper overrides",
    "type AnalyticsProjectedAnchorAllowsArbitraryKeys =",
    "type AnalyticsProjectedAnchorBboxAllowsRecord =",
    "analyticsProjectedAnchorsExcludeArbitraryKeys",
    "analyticsProjectedAnchorBboxIsBounded",
    "does not expose arbitrary analytics anchor maps through the browser projection",
    "type InvestigationProjectionAllowsArbitraryKeys =",
    "investigationProjectionExcludesArbitraryKeys",
    "does not expose arbitrary investigation records through the browser projection",
    "type AnalyticsDeltaProjectionAllowsArbitraryKeys =",
    "type AnalyticsCompetitorProjectionAllowsArbitraryKeys =",
    "type InvestigationVisualDiffProjectionAllowsArbitraryKeys =",
    "type InvestigationTextDiffAllowsArbitraryKeys =",
    "does not expose arbitrary analytics or visual-diff maps through projected reads",
    "type ProjectResourceDataAllowsArbitraryKeys =",
    "type ReportDetailProjectionAllowsArbitraryKeys =",
    "type RawRecordCanMasqueradeAsSafeStructuredRecord =",
    "safeStructuredRecordRequiresProjection",
    "requires projected report records and explicit project-resource fields",
):
    if fragment not in api_client_tests:
        errors.append(
            f"@geo/api-client projected-wrapper type tests are missing coverage: {fragment}"
        )
for fragment in (
    "projectMediaPricesDataset",
    "projectMediaPricesStringMap",
    "projectMediaPricesBooleanMap",
    "projectMediaPricesCountMap",
    "mediaPricesGeoKeys",
    "Every returned object is a bounded allow-listed projection",
    "rows.length > 200_000",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client media dataset DLP projection is missing: {fragment}")
for fragment in (
    "projects a strict media dataset allow-list and fails closed on secret-shaped display data",
    "dataset-envelope-canary",
    "expect(projected?.rows[0]?.ids).toEqual({ prfabu: '12345' })",
    "accepts a completed real-zero media dataset and rejects malformed numeric or GEO rows",
    "fails closed on secret-bearing or unbounded media refresh projections",
    "rejects semantically incomplete completed media refresh projections",
    "media-refresh-secret-canary",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client media dataset DLP tests are missing: {fragment}")
operations_media_prices_e2e = (root / "tests/e2e/operations-media-prices.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "media refresh status and write receipts fail closed on secret-shaped display fields",
    "refresh-status-secret-canary",
    "refresh-write-canary",
):
    if fragment not in operations_media_prices_e2e:
        errors.append(f"Operations media refresh E2E DLP coverage is missing: {fragment}")
if "inv_safe_but_not_more" not in api_client_tests:
    errors.append("@geo/api-client tests must cover a safe cursor paired with has_more=false")
for fragment in (
    "rptc_boundary_fullwidth_key_invalid",
    "rptc_boundary_zero_width_key_invalid",
    "rptc_boundary_encoded_key_invalid",
    "fullwidth-key-canary",
    "zero-width-key-canary",
    "encoded-key-canary",
):
    if fragment not in api_client_tests:
        errors.append(
            f"@geo/api-client normalized secret-key tests are missing coverage: {fragment}"
        )
for fragment in (
    "rpt_invalid_state",
    "rpt_invalid_time",
    "inv_invalid_domain",
    "inv_invalid_probability",
    "inv_invalid_verdict",
    "Number.MAX_VALUE",
    "probability: '1.5'",
    "projectSafeIsoTimestamp('1')",
    "2026-02-30T22:10:00Z",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client tests are missing invalid list-domain coverage: {fragment}")
for fragment in (
    "rejects mismatched or secret-shaped write receipts before application state",
    "comment-receipt-canary",
    "delivery-receipt-canary",
    "confirmation-receipt-canary",
    "metric-export-receipt-canary",
    "evidence-package-receipt-canary",
    "verdict-receipt-canary",
    "report-review-receipt-canary",
    "report-action-receipt-canary",
    "effect-retest-receipt-canary",
    "appeal-receipt-canary",
    "appeal-resolution-receipt-canary",
    "rejects secret-shaped, cross-account or input-mismatched lifecycle write responses",
    "尾号 · 9999",
    "pairing-cross-account-canary",
    "revocation-cross-account-canary",
    "rejects input-mismatched member and cross-target identity write responses",
    "错误成员",
    "identity-cross-member-canary",
    "oidc-cross-member-canary",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client tests are missing safe write-receipt coverage: {fragment}")
for fragment in (
    "rejects unsafe identity and OIDC list rows before application state",
    "member-list-secret-canary",
    "oidc-list-lifecycle-canary",
    "member-list-filtered-canary",
    "oidc-list-filtered-canary",
    "member-list-write-boundary-canary",
    "oidc-list-write-boundary-canary",
    "bounds identity governance lists before projection and rejects duplicate identities",
    "member-boundary-extension-canary",
    "oidc-boundary-extension-canary",
    "projection: { total: 102, shown: 99, invalid: true }",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client tests are missing identity list DLP coverage: {fragment}")
for fragment in (
    "reconstructs the health probe before its status reaches the shared shell",
    "health-status-canary",
    "rejects non-contract response media types before JSON parsing",
    "application/problem+json",
    "bounds decoded JSON bytes before generated parsing or projection",
    "new Uint8Array(16 * 1024 * 1024)",
    "geoApiJsonResponseMaxBytes + 1",
    "expect(outbound.cache).toBe('no-store')",
    "expect(outbound.redirect).toBe('error')",
    "expect(outbound.referrerPolicy).toBe('no-referrer')",
    "validates identity and project context through generated contracts",
    "bootstrap-permission-canary",
    "projection: { total: 1, shown: 1, invalid: false }",
    "fails closed on cross-tenant or duplicate project bootstrap rows",
    "cross-tenant-bootstrap-canary",
    "duplicate-bootstrap-canary",
):
    if fragment not in api_client_tests:
        errors.append(
            "@geo/api-client tests are missing health/identity bootstrap projection coverage: "
            f"{fragment}"
        )
for fragment in (
    "projects bounded customer-account lifecycle reads before application state",
    "responsible-read-canary",
    "event-read-canary",
    "pairing-extension-canary",
    "projectSafeAccountMask('customer@example.test')",
    "projectSafeAccountMask('account13800138000***')",
    "report13800138000component-phone-canary",
    "请使用验证码 824911 完成原生挑战",
    "challenge: 824911",
    "contact: 13800138000",
    "projection: { total: 102, shown: 99, invalid: true }",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client tests are missing account read DLP coverage: {fragment}")
for fragment in (
    "uses generated customer confirmation paths with bounded cursor queries and idempotency",
    "projects catalog rows by project and kind and rejects mismatched write receipts",
    "profile-version-response-canary",
    "nested-catalog-response-canary",
    "catalog-row-extension-canary",
    "expect(created).toEqual({ kind: 'unavailable' })",
):
    if fragment not in api_client_tests:
        errors.append(
            f"@geo/api-client tests are missing customer governance projection coverage: {fragment}"
        )
for fragment in (
    "reconstructs bounded analytics monitoring responses before they cross the browser boundary",
    "analytics-trace-canary",
    "breakdown-question-canary",
    "delta-root-canary",
    "competitor-proxy-password-canary",
    "projection: { total: 5, shown: 3, invalid: true }",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client tests are missing analytics read DLP coverage: {fragment}")
for fragment in (
    "projects answer pages and nested relations before application state",
    "answer-visible-extension-canary",
    "citation-prose-canary",
    "anchor-bbox-canary",
    "relation-history-canary",
    "citations: { total: 201, shown: 198, invalid: true }",
    "anchors: { total: 201, shown: 200, invalid: false }",
):
    if fragment not in api_client_tests:
        errors.append(
            f"@geo/api-client tests are missing answer/relation read DLP coverage: {fragment}"
        )
for fragment in (
    "projects report deliveries and evidence asset pages before application state",
    "reconstructs nested report details and preserves per-collection projection facts",
    "reconstructs investigation detail and history before browser state",
    "history-extension-secret",
    "diff-extension-secret",
    "score-explanation-secret",
    "matrix-rationale-secret",
    "projection: { total: 2, shown: 1, invalid: true }",
    "bounds Intelligence list pages and preserves invalid-row and cursor facts",
    "dataset-list-extension-secret",
    "investigation-list-extension-secret",
    "projection: { total: 3, shown: 1, invalid: true }",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client tests are missing deep read DLP coverage: {fragment}")
for fragment in (
    "mergeLifecycleProjection",
    "result.data.projection",
    "只填写带 *、尾号或其他明确隐藏标记的账号掩码",
    "useCustomerAccountMutationGuard",
    "accountWrite.begin(headers)",
    "accountWrite.isCurrent(writeTicket)",
    "accountWrite.finish(writeTicket)",
    "accountMutationBusy",
    "exportWrite.begin(headers)",
    "packageWrite.begin(headers)",
    "reportWrite.begin(headers)",
    "exportWrite.isCurrent(writeTicket)",
    "packageWrite.isCurrent(writeTicket)",
    "reportWrite.isCurrent(writeTicket)",
    "reportMutationBusy",
):
    if fragment not in customer_shell_source:
        errors.append(f"Customer account UI is missing safe read projection handling: {fragment}")
for fragment in (
    "type CustomerAccountMutationTicket",
    "useCustomerMutationGuard",
    "useCustomerAccountMutationGuard",
    "createStructuredClientScopeKey([",
    "getValidatedIdentityHeaders()",
    "beginFixture()",
    "generation.current += 1",
    "ticket.identity === fixtureIdentity",
):
    if fragment not in customer_account_mutation_guard:
        errors.append(
            "apps/customer-web/app/account-mutation-guard.ts is missing synchronous "
            f"identity/context ownership: {fragment}"
        )
for fragment in (
    "memberProjectionIncomplete",
    "oidcProjectionIncomplete",
    "governanceWritesLocked",
    "const memberWrite = useCustomerMutationGuard(memberMutationContext)",
    "optionalExperienceScope(experience)",
    "createStructuredClientScopeKey([",
    "memberWrite.begin(headers)",
    "memberWrite.finish(writeTicket)",
    "memberWritesLocked",
    "result.data.data",
    "bindingResult.data.data",
    "成员安全投影不完整",
    "邀请、角色、移除和 OIDC 写操作全部锁定",
    "type MemberGovernanceReconciliation",
    "type MemberAuthoritySnapshot",
    "const readMemberAuthority = async (",
    "result.data.projection.total !== result.data.projection.shown",
    "const memberAuthorityConfirms = (",
    "const reconcileMemberGovernance = async (",
    "const retryMemberGovernanceReconciliation = async () =>",
    "写入已接受，正在重新读取权威成员与 OIDC 绑定投影。",
):
    if fragment not in customer_shell_source:
        errors.append(
            f"Customer member UI is missing identity projection write locking: {fragment}"
        )
for fragment in (
    "tenant member writes stay serialized and bound to the initiating member",
    "getByRole('button', { name: '移出项目' })).toBeDisabled()",
    "getByRole('button', { name: '管理 成员乙' })).toBeDisabled()",
    "getByRole('button', { name: '发送邀请' })).toBeDisabled()",
    "opaque-idp-member-alpha",
    "delayed-member-binding-canary",
    "expect(writes).toHaveLength(1)",
    "'x-actor-id': 'tenant-admin-integrity'",
    "'x-actor-role': 'admin'",
    "await page.getByRole('button', { name: '经营总览', exact: true }).click()",
    "getByRole('button', { name: '项目成员', exact: true })",
    "expect(memberReads).toBe(1)",
    "expect(oidcReads).toBe(1)",
    "await expect.poll(() => writeResponseSent).toBe(true)",
):
    if fragment not in customer_member_integrity_e2e:
        errors.append(f"Customer member mutation-integrity E2E is missing coverage: {fragment}")
for fragment in (
    "inviteReconciliationReads",
    "await expect(reconciliationFailure).toBeVisible()",
    "await reconciliationFailure.getByRole('button', { name: '重试此区域' }).click()",
    "expect(inviteReconciliationReads).toBe(2)",
    "expect(writes).toHaveLength(4)",
    "oidcActive = request.method() === 'PUT'",
    "memberRevoked = true",
):
    if fragment not in customer_product_e2e:
        errors.append(
            f"Customer member authority-reconciliation E2E is missing coverage: {fragment}"
        )
for fragment in (
    "type ReportQuestionReconciliation",
    "const questionAuthorityConfirms = (",
    "detail.projection.versions.total !== detail.projection.versions.shown",
    "comment.pub_id === expected.commentId",
    "comment.author_pub_id === expected.authorId",
    "const reconcileQuestion = async (",
    "const retryQuestionReconciliation = async () =>",
    "setPendingQuestionReconciliation(expected)",
    "reportMutationLocked",
    "写入已接受，正在重新读取权威报告评论投影。",
):
    if fragment not in customer_shell_source:
        errors.append(f"Customer report question authority reconciliation is missing: {fragment}")
for fragment in (
    "reportQuestionAuthorityReads",
    "expect(reportQuestionAuthorityReads).toBe(1)",
    "expect(reportQuestionAuthorityReads).toBe(2)",
    "expect(reportQuestionBodies).toHaveLength(1)",
    "releaseDelayedReportQuestion",
    "请解释导航后的回执隔离边界",
    "await page.getByRole('button', { name: '前往监测导出' }).click()",
):
    if fragment not in customer_product_e2e:
        errors.append(
            f"Customer report question reconciliation E2E is missing coverage: {fragment}"
        )
customer_forbidden_marker = (
    "test('customer product 404 fails closed without revealing whether analytics exist'"
)
customer_forbidden_tail = customer_product_e2e.partition(customer_forbidden_marker)
customer_forbidden_segment = customer_forbidden_tail[2].partition(
    "test('validated tenant admin manages masked customer members"
)[0]
if (
    not customer_forbidden_tail[1]
    or "await page.route('**/api/v2/health'" not in customer_forbidden_segment
):
    errors.append(
        "Customer product forbidden E2E must mock the generated health read instead of depending "
        "on an external API process"
    )
for fragment in (
    "brandResult.data.projection",
    "questionResult.data.projection",
    "catalogProjection.invalid",
    "profileWrite.begin(headers)",
    "assetWrite.begin(headers)",
    "questionWrite.begin(headers)",
    "profileWrite.finish(writeTicket)",
    "assetWrite.finish(writeTicket)",
    "questionWrite.finish(writeTicket)",
    "projectClientProfilePage(",
    "projectAssetConfirmationPage(",
    "value.projection !== undefined",
):
    if fragment not in customer_shell_source:
        errors.append(
            f"Customer governance UI is missing generated-client projection handling: {fragment}"
        )
for fragment in (
    "project form writes reject synchronous duplicate submits and retain no secret receipts",
    "form.dispatchEvent(new Event('submit'",
    "expect(profileWrites).toBe(1)",
    "expect(assetWrites).toBe(1)",
    "expect(questionWrites).toBe(1)",
    "'x-actor-id': 'customer-project-write-integrity'",
    "'x-actor-role': 'customer'",
    "delayed-profile-write-canary",
    "delayed-asset-write-canary",
    "delayed-question-write-canary",
):
    if fragment not in customer_project_write_integrity_e2e:
        errors.append(f"Customer project mutation-integrity E2E is missing coverage: {fragment}")
for fragment in (
    "customerAnalyticsProjectionLimits",
    "mergeMonitoringProjection",
    "projectAnalyticsOverviewResult(result.data.data)",
    "projectAnalyticsCompetitors(competitorResult.data.data)",
    "projectAnalyticsBreakdownResult(dayResult.data.data, 'day')",
):
    if fragment not in customer_shell_source:
        errors.append(
            f"Customer monitoring UI is missing generated-client projection handling: {fragment}"
        )
for fragment in (
    "customerEvidenceReadProjectionLimits",
    "mergeAnswerRelationProjection",
    "result.data.projection.total",
    "result.data.projection.invalid",
    "result.data.projection)",
):
    if fragment not in customer_shell_source:
        errors.append(
            f"Customer evidence UI is missing generated-client projection handling: {fragment}"
        )
for fragment in (
    "mergeReportDeliveryProjection",
    "projectCustomerReportVersions(",
    "boundary?.version_collections",
    "boundary.versions.total !== values.length",
    "latestBoundary.total !== artifacts.length",
    "firstRetainedIndex",
    "detail.kind === 'invalid'",
    "type SafeCustomerReportArtifact",
    "CustomerReportHtmlPreview",
    "const reportReadScope = createStructuredClientScopeKey([",
    "liveResultScope !== reportReadScope ? 'loading' : liveState",
    "setLiveResultScope(reportReadScope)",
    "result.data.blob.arrayBuffer()",
    "result.data.blob.text()",
    "projectSafeHtmlDocument(html)",
    '<SafeHtmlDocument projection={content} label="客户报告在线预览" />',
):
    if fragment not in customer_shell_source:
        errors.append(
            f"Customer report UI is missing generated-client projection handling: {fragment}"
        )
customer_shell_tests = (root / "apps/customer-web/app/shell.test.tsx").read_text(encoding="utf-8")
for fragment in (
    "trace_tokens: ['Bearer trace-token-canary']",
    "expect(projected[0]).not.toHaveProperty('trace_tokens')",
):
    if fragment not in customer_shell_tests:
        errors.append(
            "Customer analytics DLP tests must prove server trace tokens are absent from "
            f"the browser projection: {fragment}"
        )
for fragment in (
    "unsafeOmittedVersion",
    "boundaryTruncated",
    "boundaryTruncatedArtifact",
    "omitted-version-canary",
):
    if fragment not in customer_shell_tests:
        errors.append(
            "Customer report projection tests are missing truncated/current-version "
            f"safety: {fragment}"
        )
if "reportArtifactUrl" in customer_shell_source:
    errors.append(
        "Customer report previews must not bypass the generated integrity-checked artifact client"
    )
if "dangerouslySetInnerHTML" in customer_shell_source:
    errors.append("Customer report previews must render verified HTML through inert React text")
for fragment in (
    "type SafeHtmlDocumentProjection",
    "projectSafeHtmlDocument",
    "forbiddenHtmlElementTags",
    "unsafeHtmlMarkupPattern",
    "unsafeHtmlMarkupPattern.test(html)",
    "renderSafeHtmlNode",
    'rel="noopener noreferrer"',
    "HTML 完整性与活动内容已校验",
):
    if fragment not in design_system_source:
        errors.append(f"Design system is missing safe semantic HTML rendering: {fragment}")
for fragment in (
    "safePdfDocumentLimits",
    "safePdfDocumentOptions",
    "pageCount: 500",
    "canvasDimension: 4_096",
    "canvasPixels: 8_388_608",
    "maxImageSize: safePdfDocumentLimits.imagePixels",
    "useWorkerFetch: false",
    "projectSafePdfPageViewport",
    "clearSafePdfCanvas",
):
    if fragment not in design_system_source:
        errors.append(f"Design system is missing bounded PDF.js projection: {fragment}")
for fragment in (
    "type SafeGeneratedFileDownload",
    "function downloadSafeGeneratedFile",
    "isSafeGeneratedJsonValue",
    "isSafeGeneratedCsv",
    "safeGeneratedFileMaxBytes",
    "type VerifiedBlobDownloadResult",
    "function VerifiedBlobDownload",
    "containsClientSecret(fileName)",
    "const active = useRef(false)",
    "const requestScope = createStructuredClientScopeKey([resourceKey, fileName])",
    "if (active.current) return",
    "containsUnsafeClientControlCharacter(resourceKey)",
    "URL.createObjectURL(result.blob)",
    "result.blob.size > 50 * 1024 * 1024",
):
    if fragment not in design_system_source:
        errors.append(f"Design system is missing verified Blob download handling: {fragment}")
for fragment in (
    "reconstructs a rich static HTML report",
    "rejects active, externally loading, event-bearing or secret-bearing HTML documents",
    "expect(parser).not.toHaveBeenCalled()",
    'iframe srcdoc="<p>跟踪</p>"',
    'style="background:url(https://tracker.invalid)"',
    "downloads only a verified non-empty Blob with a DLP-safe filename",
    "serializes synchronous download activation and discards a prior resource result",
    "expect(firstLoad).toHaveBeenCalledOnce()",
    "expect(secondLoad).toHaveBeenCalledOnce()",
    "never creates a download URL for invalid results or secret-shaped filenames",
    "downloads bounded browser-generated JSON and CSV through the shared DLP boundary",
    "creates no generated download for secret keys or values, numeric secrets or CSV formulas",
    "normalized-secret-key.json",
    "normalized-secret-header.csv",
    "opaque-csv-key-canary",
    "bounds PDF.js pages, canvas allocation and built-in resource loading",
    "safePdfDocumentLimits.pageCount + 1",
    "width: 3_000, height: 3_000",
):
    if fragment not in design_system_primitives_tests:
        errors.append(f"Design-system HTML projection tests are missing coverage: {fragment}")
for fragment in (
    ".safe-html-document",
    ".safe-html-table-scroll",
    ".safe-html-table-scroll table",
    ".graph-table-scroll",
):
    if fragment not in design_system_styles:
        errors.append(f"Design system is missing responsive safe-HTML styling: {fragment}")
if "min-width: 640px" in design_system_styles:
    errors.append("Design system must not force the mobile React Flow canvas beyond its panel")
for fragment in (
    "customer-live-html-report-preview.png",
    "HTML 完整性与活动内容已校验",
    "getByRole('table', { name: '关键指标' })",
    "toHaveAttribute('rel', 'noopener noreferrer')",
):
    if fragment not in customer_product_e2e:
        errors.append(f"Customer live HTML preview is missing visual/semantic coverage: {fragment}")
for fragment in (
    "safePdfDocumentOptions",
    "projectSafePdfPageViewport",
    "clearSafePdfCanvas",
    "renderTask?.cancel()",
    "PDF page exceeds browser preview limits",
):
    if fragment not in customer_shell_source:
        errors.append(f"Customer PDF preview is missing bounded PDF.js handling: {fragment}")

report_shell_source = (root / "apps/report-studio/app/shell.tsx").read_text(encoding="utf-8")
for fragment in (
    "reportDetailReadProjectionLimits",
    "type ReportDetailProjection",
    "value.projection",
    "boundary.version_collections",
    "boundary.action_retests",
    "evidenceBindingIds",
    "linkedSelectedSections",
    "evidenceBindingIds.has(evidencePubId)",
    "detail.kind === 'invalid'",
    "loadProjectReportCatalog",
    "detail.kind === 'forbidden' ? 'forbidden' : 'failed'",
    "key={`review:${liveTarget.reportPubId}:${liveTarget.versionPubId}`}",
    "key={`outcomes:${liveTarget.reportPubId}:${liveTarget.versionPubId}`}",
    "VerifiedArtifactDownload",
    "VerifiedBlobDownload",
    "const reportReadScope = createStructuredClientScopeKey([",
    "const ownsCurrentReportResult =",
    "!ownsCurrentReportResult ? 'loading' : liveState",
    "setLiveResultScope(reportReadScope)",
    "resourceKey={createStructuredClientScopeKey([",
    (
        "key={createStructuredClientScopeKey([\n"
        "                target.reportPubId,\n"
        "                target.versionPubId,\n"
        "                'pdf',"
    ),
    "result.data.blob.arrayBuffer()",
    "type ReportMutationTicket",
    "useReportMutationGuard",
    "createStructuredClientScopeKey([",
    "'revision', target.reportPubId, target.versionPubId",
    "'review',",
    "'outcomes',",
    (
        "active.current = true;\n"
        "      generation.current += 1;\n"
        "      return { context: contextRef.current, generation: generation.current, identity }"
    ),
    "revisionWrite.begin(headers)",
    "revisionWrite.finish(ticket)",
    "reviewWrite.begin(headers)",
    "reviewWrite.finish(ticket)",
    "type ReportReleaseReconciliation",
    "type ReportRevisionReconciliation",
    "type ReportOutcomeReconciliation",
    "const reconcileRelease = async (",
    "const retryReleaseReconciliation = async () =>",
    "releaseProjectionConfirms",
    "const reconcileRevision = async (",
    "const retryRevisionReconciliation = async () =>",
    "projected.versionPubId !== expected.reportVersionPubId",
    "readLiveReportProjection(reportPubId, false)",
    "const reconcileOutcome = async (",
    "const retryOutcomeReconciliation = async () =>",
    "retest.pubId === expected.effectRetestPubId",
    "onReconcile={reconcileLiveReport}",
    "onReconcileDelivery={reconcileReportDelivery}",
    "currentReportReadScopeRef.current !== ownedScope",
    "result.data.projection.invalid",
    "写入已接受，正在重新读取同一报告的权威发布投影。",
    "outcomeWrite.begin(headers)",
    "outcomeWrite.isCurrent(ticket)",
    "outcomeWrite.finish(ticket)",
):
    if fragment not in report_shell_source:
        errors.append(
            f"Report Studio is missing generated-client detail projection handling: {fragment}"
        )
for fragment in (
    "projectReportCatalogReadLimits",
    "maxBatches: 10",
    "loadProjectReportCatalog",
    "report.project_pub_id === safeProjectPubId",
    "scanned",
    "incomplete",
):
    if fragment not in api_client_source:
        errors.append(
            f"Generated client is missing bounded project report catalog handling: {fragment}"
        )
report_shell_tests = (root / "apps/report-studio/app/shell.test.tsx").read_text(encoding="utf-8")
for fragment in (
    "danglingSectionEvidence",
    "evd_unit_linked_safe",
    "evd_unit_dangling_canary",
    "invalidProjection).toContain('sectionEvidenceIds')",
):
    if fragment not in report_shell_tests:
        errors.append(
            f"Report Studio projection tests are missing section/evidence closure: {fragment}"
        )
if "href={reportArtifactUrl" in report_shell_source:
    errors.append(
        "Report Studio must not bypass the generated integrity-checked artifact client with "
        "a raw download link"
    )
if "URL.createObjectURL" in report_shell_source:
    errors.append("Report Studio must use the shared verified Blob download state machine")
if "resourceKey={createStructuredClientScopeKey([" not in customer_shell_source:
    errors.append("Customer verified report downloads must be bound to an explicit resource scope")
for fragment in (
    "safePdfDocumentOptions",
    "projectSafePdfPageViewport",
    "clearSafePdfCanvas",
    "renderTask?.cancel()",
    "PDF page exceeds browser preview limits",
):
    if fragment not in report_shell_source:
        errors.append(f"Report Studio PDF preview is missing bounded PDF.js handling: {fragment}")
for fragment in (
    "type VerifiedReportArtifact",
    "expected.byteSize > 50 * 1024 * 1024",
    "globalThis.crypto.subtle.digest",
    "actualSha256 === expectedSha256",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client is missing report artifact integrity binding: {fragment}")
for fragment in (
    "createEvidencePackagePubId",
    "const digitLetters = 'ghijklmnop'",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client is missing DLP-safe package ID generation: {fragment}")
intelligence_package_shell_source = (root / "apps/intelligence-web/app/shell.tsx").read_text(
    encoding="utf-8"
)
for fragment in (
    "const caseReadScope = createStructuredClientScopeKey([",
    "setLiveResultScope(caseReadScope)",
    "setHistoryResultScope(caseReadScope)",
    "liveResultScope !== caseReadScope ? 'loading' : liveState",
    "historyResultScope !== caseReadScope",
    "currentCaseReadScopeRef.current !== ownedScope",
    "onReconcile={reconcileLiveInvestigation}",
):
    if fragment not in intelligence_package_shell_source:
        errors.append(
            f"Intelligence Web is missing current-request presentation ownership: {fragment}"
        )
for app_name, app_source in (
    ("Customer", customer_shell_source),
    ("Intelligence", intelligence_package_shell_source),
):
    if "package_pub_id: createEvidencePackagePubId()" not in app_source:
        errors.append(f"{app_name} must use the shared DLP-safe evidence package ID generator")
    if "downloadSafeGeneratedFile" not in app_source:
        errors.append(f"{app_name} must use the shared safe generated-file download boundary")
for fragment in (
    "binds report artifact bytes to the projected MIME, size and SHA-256",
    "5685e2d63d2a3b750e0850b8654c06f87fe9a1b138525deef264166e4152efbc",
):
    if fragment not in api_client_tests:
        errors.append(f"@geo/api-client tests are missing artifact integrity coverage: {fragment}")
customer_report_integrity_e2e = (root / "tests/e2e/customer-report-integrity.spec.ts").read_text(
    encoding="utf-8"
)
pdf_fixture_e2e = (root / "tests/e2e/pdf-fixtures.ts").read_text(encoding="utf-8")
for fragment in (
    "createSinglePagePdf",
    "[0, 0, 20_000, 20_000]",
    "createHash('sha256')",
    "oversizedPagePdfIntegrity",
):
    if fragment not in pdf_fixture_e2e:
        errors.append(f"PDF resource-bound E2E fixture is missing coverage: {fragment}")
for fragment in (
    "customer renders neither HTML nor PDF when artifact bytes violate the projected hash",
    "customer rejects an integrity-valid HTML artifact that contains active document content",
    "customer rejects an integrity-valid oversized PDF page before canvas allocation",
    "customerReportHtmlSha256",
    "customerReportPdfSha256",
    "oversizedPagePdfIntegrity",
    "externalResourceReads",
    "https://tracker.invalid/**",
    "expect(externalResourceReads).toBe(0)",
):
    if fragment not in customer_report_integrity_e2e:
        errors.append(
            f"Customer report E2E is missing artifact byte-integrity coverage: {fragment}"
        )
for fragment in (
    "CustomerReportPdfDownload",
    'label="下载 PDF"',
    'failureLabel="报告制品完整性校验失败"',
):
    if fragment not in customer_shell_source:
        errors.append(f"Customer report UI is missing verified artifact download: {fragment}")
for fragment in (
    "reportDownloadPromise",
    "rpt_customer_safe-rptv_customer_safe.pdf",
):
    if fragment not in customer_product_e2e:
        errors.append(f"Customer live report download is missing success coverage: {fragment}")
if "expect(downloads).toBe(0)" not in customer_report_integrity_e2e:
    errors.append("Customer report integrity E2E is missing failed-download coverage")
intelligence_shell_tests = (root / "apps/intelligence-web/app/shell.test.tsx").read_text(
    encoding="utf-8"
)
intelligence_integrity_e2e = (root / "tests/e2e/intelligence-integrity.spec.ts").read_text(
    encoding="utf-8"
)
intelligence_live_e2e = (root / "tests/e2e/intelligence-live.spec.ts").read_text(encoding="utf-8")
for fragment in (
    "expectedInvestigationPubId?: string",
    "investigationPubId !== expectedInvestigationPubId",
    "projectLiveInvestigation(detail.data, investigationPubId)",
):
    if fragment not in intelligence_package_shell_source:
        errors.append(f"Intelligence Web is missing root-resource detail binding: {fragment}")
for fragment in (
    "binds a projected investigation detail to the requested root resource",
    "inv_requested_safe",
    "inv_actual_safe",
):
    if fragment not in intelligence_shell_tests:
        errors.append(f"Intelligence projection tests are missing root binding: {fragment}")
for fragment in (
    "filterProjectedCollection",
    "projectAnalyticsPubId(value.pub_id, 'ce_')",
    "projectAnalyticsPubId(value.pub_id, 'srca_')",
    "claimPubIds.has(claimPubId)",
    "seenClaimEvidencePairs",
    "seenAssessedSourcePubIds",
    "intelligenceGraphRelations.some((candidate) => candidate === value.relation)",
    "seenGraphEdges.has(edgeKey)",
    "scores: 200",
    "projectAnalyticsPubId(value.pub_id, 'score_')",
    "filterUniqueChronologicalCollection",
    "filterVerdictSupersessionChain",
    "supersedesPubId === previousVerdictPubId",
    "filterAppealVerdictConsistency",
    "matchedReplacementVerdicts",
    "verdict.createdAt <= createdAt",
    "projectAnalyticsPubId(value.reviewer_pub_id, 'usr_')",
    "safeBrowserString(value.rationale, 10_000)",
    "verdictAtResolution.reviewerPubId !== resolvedByPubId",
    "verdict.rationale === resolutionRationale",
    "replacedVerdict.reviewerPubId === resolvedByPubId",
    "projectAnalyticsPubId(value.submitted_by_pub_id, 'usr_')",
    "resolution === state",
    "resolvedByPubId !== submittedByPubId",
    "resolutionRationale.trim().length > 0",
    "projectAnalyticsPubId(value.pub_id, 'vrd_')",
    "projectSafeIsoTimestamp(value.updated_at)",
):
    if fragment not in api_client:
        errors.append(
            f"@geo/api-client is missing Intelligence relational projection closure: {fragment}"
        )
for fragment in (
    "duplicate-claim-canary",
    "cross-claim-relation-canary",
    "duplicate-source-assessment-canary",
    "evidenceMatrix: { total: 4, shown: 1, invalid: true }",
    "sourceIndependence: { total: 2, shown: 1, invalid: true }",
    "duplicate-graph-edge-canary",
    "invalid-graph-relation-canary",
    "graph: { total: 3, shown: 1, invalid: true }",
    "duplicate-score-record-canary",
    "reverse-score-record-canary",
    "duplicate-appeal-record-canary",
    "reverse-appeal-record-canary",
    "duplicate-verdict-record-canary",
    "reverse-verdict-record-canary",
    "scores: { total: 3, shown: 1, invalid: true }",
    "appeals: { total: 3, shown: 1, invalid: true }",
    "verdicts: { total: 3, shown: 1, invalid: true }",
    "accepts only a linear investigation verdict supersession chain",
    "broken-verdict-chain-canary",
    "verdicts: { total: 2, shown: 1, invalid: true }",
    "accepts only appeals backed by the projected verdict history",
    "impossible-appeal-history-canary",
    "appeals: { total: 1, shown: 0, invalid: true }",
    "accepts only appeal rows consistent with the resolution transaction",
    "active-resolution-canary",
    "mismatched-resolution-canary",
    "same-reviewer-canary",
    "secret-rationale-canary",
    "appeals: { total: 6, shown: 2, invalid: true }",
    "closes resolved appeals over the independent verdict reviewer and rationale",
    "replacement-reviewer",
    "secret-verdict-rationale-canary",
):
    if fragment not in api_client_tests:
        errors.append(
            f"@geo/api-client tests are missing Intelligence relational negatives: {fragment}"
        )
for fragment in (
    "safePrefixedId",
    "claimPubIds.has(claimId)",
    "seenClaimEvidencePairs",
    "seenAssessedSourcePubIds",
    "safeResourceId",
    "safePrefixedId(edge.evidence_pub_id, 'evd_')",
    "seenGraphEdges.has(edgeKey)",
    "safePrefixedId(score.pub_id, 'score_')",
    "seenScorePubIds.has(pubId)",
    "seenAppealPubIds.has(id)",
    "seenVerdictPubIds.has(id)",
    "verdictChainIsValid",
    "supersedesPubId !== previousVerdictPubId",
    "matchedReplacementVerdicts",
    "verdict.createdAt).getTime() <= appealCreatedAt",
    "projectSafeIsoTimestamp(appeal.updated_at)",
    "safePrefixedId(verdict.reviewer_pub_id, 'usr_')",
    "verdictAtResolution.reviewerPubId !== appeal.resolvedByPubId",
    "verdict.rationale === appeal.resolutionRationale",
    "replacedVerdict.reviewerPubId === appeal.resolvedByPubId",
    "safePrefixedId(appeal.submitted_by_pub_id, 'usr_')",
    "resolution === state",
    "resolvedByPubId !== submittedByPubId",
    "resolutionRationale.trim().length > 0",
    "useIntelligenceMutationGuard",
    "governanceWrite.begin(headers)",
    "governanceWrite.finish(ticket)",
    "packageWrite.begin(headers)",
    "packageWrite.finish(ticket)",
    "key={liveTarget.investigationPubId}",
    "key={`${liveTarget.investigationPubId}:${verdict}`}",
    "export function projectLiveSourceRows(",
    "id: item.id",
    "cluster: source?.cluster ?? item.cluster",
    "projectLiveSourceRows(liveTarget)",
    "export function liveGraphEdgeIdentity(",
    "id: liveGraphEdgeIdentity(edge)",
    "<tr key={liveGraphEdgeIdentity(edge)}>",
    "export function projectLiveGraphEdges(",
    "const pairTotals = new Map<string, number>()",
    "curvature: 0.2 + ordinal * 0.35",
    "projectLiveGraphEdges(liveTarget.graph)",
    "function LiveParallelGraphEdge(",
    "const labelOffset = (ordinal - (pairTotal - 1) / 2) * 28",
    "labelY={labelY + labelOffset}",
    "edgeTypes={liveGraphEdgeTypes}",
    "window.matchMedia('(max-width: 620px)').matches",
    "const sparseCompactGraph = compactGraph && liveNodeIds.length <= 8",
    "maxZoom: sparseCompactGraph ? 1.1 : compactGraph ? 0.8 : 1.5",
    'className="table-scroll graph-table-scroll"',
    'aria-label="传播图关系表滚动区域"',
    "export function selectLiveHistoryView(",
    "latestPageByContent.has(requestedContentPubId)",
    "diff.contentPubId === activeContentPubId",
    "diff.beforeVersionPubId === previousPage.versionPubId",
    "diff.afterVersionPubId === currentPage.versionPubId",
    'aria-label="选择历史页面"',
    "disabled={!historyView.previousPage}",
    "const detailBlocksHistory =",
    "detail.kind === 'forbidden' || detail.kind === 'invalid'",
    "if (detailBlocksHistory)",
):
    if fragment not in intelligence_package_shell_source:
        errors.append(
            f"Intelligence Web is missing claim/evidence/source relational closure: {fragment}"
        )
for fragment in (
    "ce_integrity_cross_claim",
    "ce_integrity_duplicate_pair",
    "srca_integrity_duplicate_source",
    "duplicate-claim-canary",
    "duplicate-graph-canary",
    "invalid-graph-canary",
    "duplicate-score-canary",
    "reverse-score-canary",
    "duplicate-appeal-canary",
    "reverse-appeal-canary",
    "duplicate-verdict-canary",
    "reverse-verdict-canary",
    "projectionNotices.appeals",
    "projectionNotices.verdicts",
    "accepts only a linear verdict supersession chain",
    "broken-verdict-chain-canary",
    "accepts only appeals backed by the projected verdict history",
    "impossible-appeal-history-canary",
    "fails closed on appeal rows that contradict the resolution transaction",
    "same-reviewer-rationale-canary",
    "requires a corrected appeal to preserve the independent reviewer transaction",
    "prior-self-review",
    "keeps repeated evidence relations uniquely keyed under the assessed source cluster",
    "ce_shared_source_a",
    "ce_shared_source_b",
    "assessed-source-cluster",
    "uses the service relation tuple as the stable graph edge identity",
    "live-edge:evd_shared_graph:supports:clm_shared_graph",
    "live-edge:evd_shared_graph:mentions:clm_shared_graph",
    "pathOptions: { curvature: 0.2 }",
    "pathOptions: { curvature: 0.55 }",
    "data: { labelOffset: -14 }",
    "data: { labelOffset: 14 }",
    "binds the selected history page and diff to one content item",
    "selectLiveHistoryView(projection, 'cnt_history_trailing')",
    "expect(trailingView.selectedDiff).toBeNull()",
):
    if fragment not in intelligence_shell_tests:
        errors.append(f"Intelligence projection tests are missing relational closure: {fragment}")
for fragment in (
    "integrity-duplicate-graph-canary",
    "integrity-invalid-graph-canary",
    "integrity-duplicate-score-canary",
    "integrity-reverse-score-canary",
    "integrity-duplicate-appeal-canary",
    "integrity-reverse-appeal-canary",
    "integrity-duplicate-verdict-canary",
    "integrity-reverse-verdict-canary",
    "a broken verdict supersession chain is disclosed and write locked",
    "broken-verdict-chain-e2e-canary",
    "裁决记录含未通过安全校验的数据；相关写操作已锁定",
    "appeals inconsistent with verdict history or resolution transaction "
    "are disclosed and write locked",
    "appeal-before-verdict-e2e-canary",
    "appeal-missing-correction-e2e-canary",
    "appeal-resolution-mismatch-e2e-canary",
    "a non-independent appeal resolver is disclosed and write locked",
    "non-independent-appeal-e2e-canary",
    "申诉记录含未通过安全校验的数据；相关写操作已锁定",
    "评分记录、原子 Claim、证据关系、来源独立性、传播关系、"
    "申诉记录、裁决记录含未通过安全校验的数据",
    "传播图节点与关系",
    "expect(writes).toEqual([])",
):
    if fragment not in intelligence_integrity_e2e:
        errors.append(f"Intelligence E2E is missing graph-integrity coverage: {fragment}")
for fragment in (
    "ce_live_safe_a",
    "ce_live_safe_b",
    "cluster-live-assessed",
    "page.getByRole('heading', { name: 'evd_live_safe' })).toHaveCount(2)",
    "evidence_pub_ids: ['evd_live_safe']",
    "relation: 'mentions'",
    "page.getByRole('cell', { name: 'mentions' })",
    "getByRole('row')).toHaveCount(",
    "content_pub_id: 'cnt_live_trailing'",
    "selectOption('cnt_live_trailing')",
    "page.getByText('75.0%')).toHaveCount(0)",
    "page.getByRole('button', { name: '上一版本' })).toBeDisabled()",
    "await expectAccessible(page)",
):
    if fragment not in intelligence_live_e2e:
        errors.append(
            f"Intelligence live E2E is missing repeated-source relation coverage: {fragment}"
        )
for fragment in (
    "investigation 403 fails closed without probing a case detail",
    "await page.route('**/api/v2/health'",
    "intelligence-catalog-forbidden",
    "syntheticHttpResponseCount(page, 'intelligence-detail-forbidden')",
):
    if fragment not in intelligence_live_e2e:
        errors.append(
            f"Intelligence forbidden E2E is missing self-contained runtime coverage: {fragment}"
        )
for fragment in (
    "pageByVersionPubId",
    "before.evidencePubId !== diff.beforeEvidencePubId",
    "diff.beforeHash !== before.bodyHash",
):
    if fragment not in intelligence_package_shell_source:
        errors.append(
            f"Intelligence Web is missing page-history and diff referential closure: {fragment}"
        )
for fragment in (
    "drops visual diffs that do not close over the projected version and evidence chain",
    "diff_chain_wrong_content",
    "diff_chain_wrong_evidence",
    "diff_chain_wrong_hash",
):
    if fragment not in intelligence_shell_tests:
        errors.append(
            f"Intelligence projection tests are missing history/diff closure coverage: {fragment}"
        )
for fragment in (
    "a detail response bound to another investigation fails closed",
    "inv_requested_root_binding",
    "inv_other_root_binding",
    "cnt_other_root_history",
    "page.getByText(/跨案件历史/)).toHaveCount(0)",
    "page.getByText('93.0%')).toHaveCount(0)",
    "expect(writes).toEqual([])",
):
    if fragment not in intelligence_integrity_e2e:
        errors.append(f"Intelligence E2E is missing root binding coverage: {fragment}")
for fragment in (
    "projectEvaluationDatasetPage",
    "projectEvaluationRunPage",
    "projectModelAdmissionPage",
    "registerEvaluationDataset",
    "runEvaluationDataset",
    "listEvaluationRuns",
    "admitEvaluatedModel",
):
    if fragment not in api_client:
        errors.append(f"@geo/api-client is missing governed Anti-GEO boundary: {fragment}")

calibration_e2e = (root / "tests/e2e/intelligence-calibration.spec.ts").read_text(encoding="utf-8")
for fragment in (
    "analyst registration and evaluation stay single under synchronous duplicate submissions",
    "reviewer governance writes stay single under synchronous duplicate submissions",
    "training_propagation_cluster_digests",
    "calibration-dataset-canary",
    "malformed dataset projection retries locally",
    "calibration-local-retry-canary",
    "calibration cursor pagination restores browser history",
    "cal_dataset_cursor=dset_cursor_safe_02",
    "oversized or unsafe calibration pages stay explicit and governance-write locked",
    "cross-target Anti-GEO write receipts fail closed without success claims or leakage",
    "calibration success waits for the exact governance projection refresh",
    "reconciliationRequested",
    "datasetPanel.getByText('draft', { exact: true })",
    "calibration history closes prior-page review and suppresses its delayed receipt",
    "dset_scope_page_02/approve",
    "数据集已由独立审核者批准/)).toHaveCount(0)",
    "approval-receipt-canary",
    "admission-receipt-canary",
    "浏览器安全视图展示 19 条",
    "calibration-page-extension-secret",
    "button.click();",
):
    if fragment not in calibration_e2e:
        errors.append(
            "tests/e2e/intelligence-calibration.spec.ts is missing governed "
            f"Anti-GEO coverage: {fragment}"
        )

calibration_workspace = (root / "apps/intelligence-web/app/calibration-workspace.tsx").read_text(
    encoding="utf-8"
)
for fragment in (
    "useSearchParams",
    "readCalibrationPagination",
    "safeCalibrationCursor",
    "cal_${key}_page",
    "cal_${key}_cursor",
):
    if fragment not in calibration_workspace:
        errors.append(
            "apps/intelligence-web/app/calibration-workspace.tsx is missing safe "
            f"URL-bound calibration pagination: {fragment}"
        )
for fragment in (
    "type EvaluationDatasetPageProjection",
    "type EvaluationRunPageProjection",
    "type ModelAdmissionPageProjection",
    "CalibrationProjectionNotice",
    "readCalibrationProjection",
    "datasetProjectionIncomplete",
    "runProjectionIncomplete",
    "React Query owns the generic, secret-free failure state",
    "真实 API · 部分不可用",
    "useIntelligenceMutationGuard",
    "governanceWrite.begin(requestHeaders)",
    "governanceWrite.beginFixture()",
    "governanceWrite.finish(started.ticket)",
    "governanceMutationPending",
    "requestHeaders: started.requestHeaders",
    "const identityScope =",
    "createSafeExperienceScopeKey(experience)",
    "const calibrationViewScope = createStructuredClientScopeKey([",
    "'evaluation-datasets', identityScope, datasetPage, datasetCursor",
    "'evaluation-runs', identityScope, runPage, runCursor",
    "'model-admissions', identityScope, admissionPage, admissionCursor",
    "useIntelligenceMutationGuard(calibrationViewScope)",
    "datasetDialogScope === calibrationViewScope",
    "reviewTarget?.scope === calibrationViewScope",
    "mutationOwnsCurrentView",
    "const currentViewScopeRef = useRef(calibrationViewScope)",
    "const reconcileGovernance = async (scope: string)",
    "await reconcileGovernance(writeScope)",
    "reconciliationScope === calibrationViewScope",
):
    if fragment not in calibration_workspace:
        errors.append(
            "apps/intelligence-web/app/calibration-workspace.tsx is missing generated-client "
            f"list projection handling: {fragment}"
        )

intelligence_mutation_guard = (root / "apps/intelligence-web/app/mutation-guard.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "type IntelligenceMutationTicket",
    "useIntelligenceMutationGuard",
    "createStructuredClientScopeKey([",
    "getValidatedIdentityHeaders()",
    "beginFixture()",
    "ticket.identity === fixtureIdentity",
):
    if fragment not in intelligence_mutation_guard:
        errors.append(
            "apps/intelligence-web/app/mutation-guard.ts is missing synchronous "
            f"identity/context ownership: {fragment}"
        )
if calibration_e2e.count("button.click();") < 8:
    errors.append(
        "tests/e2e/intelligence-calibration.spec.ts must synchronously activate all four "
        "governance writes twice before asserting one generated-client request"
    )
if intelligence_live_e2e.count("addEventListener('click'") < 2:
    errors.append(
        "tests/e2e/intelligence-live.spec.ts must synchronously activate verdict and package "
        "writes twice before asserting one generated-client request"
    )
for fragment in (
    "target.verdictPubIds.includes(expected.verdictPubId)",
    "target.openAppealPubId === expected.appealPubId",
    "appealStates: appeals.map(({ id, state }) => ({ id, state }))",
    "verdictPubIds: verdicts.map(({ id }) => id)",
    "setPendingReconciliation(expected)",
    "if (!governanceWrite.isCurrent(ticket)) {",
    "retryReconciliation",
):
    if fragment not in intelligence_package_shell_source:
        errors.append(
            f"Intelligence Web is missing exact post-write governance reconciliation: {fragment}"
        )
for fragment in (
    "postWriteDetailReads",
    "写入已接受，正在重新读取同一案件的权威治理投影。",
    "page.getByRole('button', { name: '重试此区域' }).click()",
    "await expect.poll(() => postWriteDetailReads).toBe(2)",
    "appeal and independent review reconcile by read-only retry without duplicate writes",
    "expect(appealWrites).toBe(1)",
    "expect(resolutionWrites).toBe(1)",
    "expect(appealReconciliationReads).toBe(2)",
    "expect(resolutionReconciliationReads).toBe(2)",
):
    if fragment not in intelligence_live_e2e:
        errors.append(
            "tests/e2e/intelligence-live.spec.ts is missing delayed/stale governance "
            f"reconciliation coverage: {fragment}"
        )
for fragment in (
    "let detailReadsAfterWriteReceipt = 0",
    "if (delayedWriteResolved) detailReadsAfterWriteReceipt += 1",
    "expect(detailReadsAfterWriteReceipt).toBe(0)",
):
    if fragment not in (root / "tests/e2e/intelligence-local-retry.spec.ts").read_text(
        encoding="utf-8"
    ):
        errors.append(
            "tests/e2e/intelligence-local-retry.spec.ts is missing superseded governance "
            f"reconciliation no-probe coverage: {fragment}"
        )

intelligence_shell = (root / "apps/intelligence-web/app/shell.tsx").read_text(encoding="utf-8")
for fragment in (
    "investigationProjectionLimits",
    "intelligenceReadProjectionLimits",
    "ProjectionLimitNotice",
    "onlyRenderVisibleElements",
    "evidenceProjectionIncomplete",
    "invalidProjection",
    "hasIncompleteInvestigationProjection",
    "boundaryProjection",
    "type InvestigationPageProjection",
    "pageProjectionIncomplete",
    "live API · 安全子集",
    "真实评分不足",
    "contradicts",
    "historyPages",
    "historyDiffs",
    "案件 manifest 合同待补齐",
    "当前 OpenAPI 未把案件、裁决、申诉、规则解释或历史版本绑定",
    "未由当前 package 合同绑定。",
    "生成证据对象包",
    "未声明为完整案件包",
):
    if fragment not in intelligence_shell:
        errors.append(
            "apps/intelligence-web/app/shell.tsx is missing bounded, explicit large-detail "
            f"projection: {fragment}"
        )

for fragment in (
    "await expect(page.getByText('案件 manifest 合同待补齐')).toBeVisible()",
    "manifest SHA-256 cccccccccccc…",
    "未声明为完整案件包",
    "案件证据已通过真实 evidence package 合同冻结",
):
    if fragment not in intelligence_live_e2e:
        errors.append(
            "tests/e2e/intelligence-live.spec.ts is missing truthful evidence-package "
            f"boundary coverage: {fragment}"
        )

intelligence_performance_e2e = (root / "tests/e2e/intelligence-performance.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "oversized propagation graph stays bounded",
    "Array.from({ length: 500 }",
    "浏览器安全视图展示 119 条",
    "toHaveCount(119)",
    "expectAccessible(page)",
    "large-graph-root-canary",
):
    if fragment not in intelligence_performance_e2e:
        errors.append(
            "tests/e2e/intelligence-performance.spec.ts is missing large-graph "
            f"performance/DLP coverage: {fragment}"
        )

intelligence_integrity_e2e = (root / "tests/e2e/intelligence-integrity.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "unsafe detail rows fail closed",
    "invalid case summary domain values reveal neither the row nor a detail probe",
    "integrity-evidence-canary",
    "integrity-cross-claim-canary",
    "integrity-duplicate-source-canary",
    "评分记录、原子 Claim、证据关系、来源独立性、传播关系、"
    "申诉记录、裁决记录含未通过安全校验的数据",
    "integrity-duplicate-appeal-canary",
    "invalid-summary-canary",
    "diff_intelligence_history_mismatch",
    "history-chain-canary",
    "视觉 Diff含未通过安全校验的数据",
    "created_at: '1'",
    "expect(detailRequests).toBe(0)",
    "安全投影不完整",
    "toBeDisabled()",
    "expectAccessible(page)",
    "expect(writes).toEqual([])",
):
    if fragment not in intelligence_integrity_e2e:
        errors.append(
            "tests/e2e/intelligence-integrity.spec.ts is missing below-limit "
            f"projection integrity/DLP coverage: {fragment}"
        )

report_performance_e2e = (root / "tests/e2e/reports-performance.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "oversized report detail stays bounded",
    "rptv_report_performance_003",
    "浏览器安全视图展示 500 条",
    "保存不可变报告版本",
    "批准发布",
    "效果复测：服务返回 201 条",
    "浏览器安全视图展示 199 条",
    "expectAccessible(page)",
    "oversized-report-root-canary",
    "oversized-report-retest-canary",
):
    if fragment not in report_performance_e2e:
        errors.append(
            "tests/e2e/reports-performance.spec.ts is missing bounded report "
            f"performance/governance/DLP coverage: {fragment}"
        )

report_live_e2e = (root / "tests/e2e/reports-live.spec.ts").read_text(encoding="utf-8")
for fragment in (
    "synchronous duplicate submissions",
    "review_pub_id",
    "rvw_live_safe",
    "effect_retest_pub_id",
    "rts_live_safe",
    "rptev_live_risk_safe",
    "evidence_pub_id: 'evd_report_risk_safe'",
    "retest-receipt-canary",
    "expect(writes).toHaveLength(11)",
    "artifactDownload.suggestedFilename()",
    "校验后下载",
    "expect(revisionIdempotencyKeys).toHaveLength(1)",
    "commentReconciliationReads",
    "await page.getByRole('button', { name: '重试此区域' }).click()",
    "expect(commentReconciliationReads).toBe(2)",
    "expect(deliveryReads).toBe(1)",
    "expect(revisionReconciliationReads).toBe(2)",
    "expect(actionReconciliationReads).toBe(2)",
):
    if fragment not in report_live_e2e:
        errors.append(
            "tests/e2e/reports-live.spec.ts is missing effect-retest receipt "
            f"integrity/write-gate coverage: {fragment}"
        )
if report_live_e2e.count("button.click();") < 14:
    errors.append(
        "tests/e2e/reports-live.spec.ts must synchronously activate every protected "
        "Report Studio write twice before asserting one generated-client request"
    )

report_catalog_integrity_e2e = (root / "tests/e2e/reports-catalog-integrity.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "an unsafe oversized catalog fails before any mismatched detail can be adopted",
    "an embedded full phone in report detail fails closed before cache or rendering",
    "a bare six-digit OTP in report detail fails closed before query cache",
    "numeric OTP and phone fields are rejected before structured report state",
    "a hash-mismatched PDF is neither downloaded nor rendered",
    "an integrity-valid oversized PDF page is rejected before canvas allocation",
    "a cross-version artifact locks preview and all release writes",
    "a dangling section evidence id is removed and locks report release writes",
    "a cross-project-only catalog row is omitted before any report detail probe",
    (
        "tenant-scoped catalog scanning selects the current-project report "
        "without probing another project"
    ),
    "a forbidden report detail remains non-inferential instead of degrading to a generic failure",
    "browser history rekeys review and action state to the exact report version",
    "browser back discards a slower superseded report detail response",
    "数据正在安全获取，请稍候。",
    "getByRole('heading', { name: '第一页报告' })).toHaveCount(0)",
    "当前检索窗口内的项目报告：服务返回 2 条，浏览器安全视图展示 1 条",
    "/api/v2/reports/rpt_catalog_project_safe",
    "/api/v2/reports/rpt_state_01/actions/act_state_01",
    "report-detail-forbidden",
    "expect(detailRequests).toBe(0)",
    "expect(artifactReads).toBe(0)",
    "expect(downloads).toBe(0)",
    "oversizedPagePdfIntegrity",
    "expect(writes).toEqual([])",
    "report-cross-artifact-canary",
    "evd_report_section_dangling_canary",
    "report-section-link-canary",
    "report13800138000detail-phone-canary",
    "请在原生页面输入 824911 完成验证",
    "rptev_cross_version_binding",
    "cmt_cross_version_comment",
    "rptf_cross_version_fact",
    "rvw_cross_version_review",
    "evt_cross_version_event",
    "冻结事实、版本产物、证据绑定、审核评论、审核决定、报告事件含未通过安全校验的数据",
    "stale-report-detail-canary",
    "expectAccessible(page)",
):
    if fragment not in report_catalog_integrity_e2e:
        errors.append(
            "tests/e2e/reports-catalog-integrity.spec.ts is missing "
            f"catalog/detail binding, race or DLP coverage: {fragment}"
        )

customer_report_integrity_e2e = (root / "tests/e2e/customer-report-integrity.spec.ts").read_text(
    encoding="utf-8"
)
for fragment in (
    "customer discloses an oversized catalog and rejects a mismatched detail without leakage",
    "customer omits a legitimate cross-project-only catalog without probing its detail",
    "customer bounds a cross-project tenant scan and exposes safe continuation",
    "customer selects the current-project report from a mixed tenant catalog",
    "customer locks receipt confirmation when delivery ownership is inconsistent",
    "customer locks preview, questions and delivery reads for a cross-version artifact",
    "customer treats a truncated version chain as incomplete and performs no downstream reads",
    "customer browser back discards a slower superseded report detail",
    "getByRole('article', { name: '客户报告在线预览' })",
    "数据正在安全获取，请稍候。",
    "await expect(page.getByRole('dialog')).toHaveCount(0)",
    "报告目录：服务返回 2 条，浏览器安全视图展示 1 条",
    "expect(detailRequests).toBe(0)",
    "expect(catalogRequests).toBe(10)",
    "expect(confirmationWrites).toBe(0)",
    "expect(deliveryReads).toBe(0)",
    "cross-recipient-delivery-canary",
    "cross-version-artifact-canary",
    "customer-omitted-version-canary",
    "报告版本：服务返回 101 条，浏览器安全视图展示 100 条",
    "stale-customer-report-detail-canary",
    "expectAccessible(page)",
):
    if fragment not in customer_report_integrity_e2e:
        errors.append(
            "tests/e2e/customer-report-integrity.spec.ts is missing "
            f"customer report binding, race or DLP coverage: {fragment}"
        )

production_acceptance = (root / "tools/production_browser_acceptance.mjs").read_text(
    encoding="utf-8"
)
production_runtime_evidence = (root / "tools/browser_runtime_evidence.mjs").read_text(
    encoding="utf-8"
)
production_runtime_evidence_test = (root / "tools/browser_runtime_evidence.test.mjs").read_text(
    encoding="utf-8"
)
production_mock_scan = (root / "tools/production_mock_scan.mjs").read_text(encoding="utf-8")
for fragment in (
    "name: 'operations-overview'",
    "expectedHeading: '项目组合'",
    "name: 'operations-sessions'",
    "expectedHeading: '平台账号目录与 Profile 健康'",
    "name: 'operations-interventions'",
    "expectedHeading: '人工接管队列'",
    "name: 'operations-events'",
    "expectedHeading: '工作流与会话时间线'",
):
    if fragment not in production_acceptance:
        errors.append(
            "tools/production_browser_acceptance.mjs must independently qualify every "
            f"S03-owned Operations workspace: missing {fragment}"
        )
for fragment in (
    "collectBrowserRuntimeEvidence(page)",
    "isBrowserRuntimeEvidenceClean(result.runtime_issue_counts)",
    "persistSafeBrowserScreenshot(page, screenshot, screenshotText)",
    "runtime_issue_counts: { ...runtimeEvidence.counts }",
    "secret_material_absent: safeScreenshot.secretMaterialAbsent",
    "screenshot: safeScreenshot.screenshot",
):
    if fragment not in production_acceptance:
        errors.append(
            "tools/production_browser_acceptance.mjs must retain only safe browser-runtime "
            f"counts: missing {fragment}"
        )
for forbidden_fragment in (
    "message.text()",
    "request.url()",
    "response.url()",
    "request.failure()",
    "console_errors:",
    "page_errors:",
    "failed_requests:",
    "error_responses:",
    "browser_actor_header_requests:",
):
    if forbidden_fragment in production_acceptance:
        errors.append(
            "tools/production_browser_acceptance.mjs must not persist raw browser diagnostics "
            f"or legacy event arrays: {forbidden_fragment}"
        )
for fragment in (
    "console_error: 0",
    "page_error: 0",
    "request_failed: 0",
    "error_response: 0",
    "forbidden_actor_header: 0",
    "Object.seal(",
    "isBrowserRuntimeEvidenceClean",
    "containsBrowserSecretMaterial",
    "isBrowserSurfaceSecretMaterialAbsent",
    "isBrowserMachineReadableVisualSurfaceSafe",
    "persistSafeBrowserScreenshot",
    "value.normalize('NFKC').replace(zeroWidthCharacters, '')",
    "browserEvidenceTextLimit",
    "browserEvidenceStorageEntryLimit",
    "semanticAttributeNames",
    "for (const pseudo of [null, '::before', '::after'])",
    "'background-image'",
    "'mask-image'",
    "'border-image-source'",
    "'list-style-image'",
    "computedStyle.getPropertyValue(property)",
    "content !== 'none'",
    "document.cookie.length !== 0",
    "globalThis.cookieStore.getAll()",
    "history.state",
    "globalThis.name.length !== 0",
    "indexedDB.databases()",
    "globalThis.caches.keys()",
    "navigator.serviceWorker.getRegistrations()",
    "navigator.storage.getDirectory()",
    "for await (const _entry of root.values()) return false",
    "navigator.storageBuckets.keys()",
    "bucketNames.length !== 0",
    "globalThis.webkitRequestFileSystem",
    "legacyFileSystemRootIsEmpty",
    "!(await legacyFileSystemRootIsEmpty(0))",
    "!(await legacyFileSystemRootIsEmpty(1))",
    "url.username",
    "containsSecretKey(decodedHash)",
    "containsSecretKey(decodedSegment)",
    "'[role=\"img\"],img,canvas,svg,object,embed,picture,video'",
    "dataset.visualEvidence === 'payload-free'",
    "getComputedStyle(element, pseudo)",
    "machineReadableVisualsSafe",
    "await rm(path, { force: true })",
    "await page.screenshot({ path, fullPage: true })",
    "for (let attempt = 0; attempt < 2; attempt += 1)",
    "if (captureError) throw captureError",
):
    if fragment not in production_runtime_evidence:
        errors.append(f"Production browser runtime evidence boundary is missing: {fragment}")
for forbidden_fragment in (".text()", ".url()", ".failure()", ".errorText"):
    if forbidden_fragment in production_runtime_evidence:
        errors.append(
            "Production browser runtime evidence must not inspect raw diagnostic data: "
            f"{forbidden_fragment}"
        )
for fragment in (
    "Bearer production-browser-secret-canary",
    "OTP 824911",
    "access_token=production-browser-secret-canary",
    "not.toMatch",
    "collector.stop()",
    "rejects normalized screenshot secrets",
    "Ｃｏｏｋｉｅ\\u200b＝session=screenshot-secret-canary",
    "x'.repeat(2_000_001)",
    "removes stale output, retries capture once and never screenshots rendered secrets",
    "transient screenshot protocol failure",
    "rejects hidden DOM, URL, Window.name, storage, script-readable Cookie, history, "
    "IndexedDB, Cache Storage, Service Worker, OPFS, named Storage Bucket and Legacy "
    "FileSystem surfaces without returning values",
    "Bearer window-name-persistence-canary",
    "profile_path: '/secret/browser/profile/history-canary'",
    "Bearer css-generated-content-canary",
    "Bearer css-resource-canary",
    "geo_runtime_cookie_probe=opaque-value-without-sensitive-shape",
    "/platform/customer/access%255Ftoken#profile%255Fpath",
    "geo-browser-persistence-canary",
    "Bearer persistence-canary",
    "geo-browser-cache-canary",
    "Bearer cache-persistence-canary",
    "geo-runtime-evidence-sw.js",
    "Bearer service-worker-persistence-canary",
    "geo-browser-opfs-canary",
    "Bearer opfs-persistence-canary",
    "geo-browser-storage-bucket-canary",
    "Bearer storage-bucket-persistence-canary",
    "geo-browser-legacy-temporary-canary",
    "Bearer legacy-temporary-persistence-canary",
    "geo-browser-legacy-persistent-canary",
    "Bearer legacy-persistent-persistence-canary",
    "rejects resource-backed machine visuals before a production screenshot",
    ".unsafe-pairing span::before",
    "stale-sensitive-bitmap",
    "rejects.toMatchObject({ code: 'ENOENT' })",
    "retains count-only page-error observability after raw global defaults are suppressed",
    "GEO_SAFE_WINDOW_ERROR",
    "GEO_SAFE_UNHANDLED_REJECTION",
    "Bearer raw-window-error-canary OTP 824911",
    "Cookie=session-raw-rejection-canary profile_path=/secret/profile/canary",
    ".poll(() => collector.counts.page_error",
    "page_error: 2",
    "console_error: 0",
):
    if fragment not in production_runtime_evidence_test:
        errors.append(
            f"Production browser runtime evidence test is missing DLP coverage: {fragment}"
        )
for fragment in (
    "collectBrowserRuntimeEvidence(page)",
    "isBrowserRuntimeEvidenceClean(check.runtime_issue_counts)",
    "runtime_issue_counts: { ...runtimeEvidence.counts }",
):
    if fragment not in production_mock_scan:
        errors.append(
            "tools/production_mock_scan.mjs must share the safe browser-runtime evidence "
            f"boundary: missing {fragment}"
        )
for forbidden_fragment in (
    "message.text()",
    "request.url()",
    "request.failure()",
    "runtime_errors:",
    "page.on('console'",
    "page.on('pageerror'",
    "page.on('requestfailed'",
):
    if forbidden_fragment in production_mock_scan:
        errors.append(
            "tools/production_mock_scan.mjs must not duplicate or persist raw browser "
            f"diagnostics: {forbidden_fragment}"
        )

generated = (root / "packages/api-client/src/schema.generated.ts").read_text(encoding="utf-8")
if "This file was auto-generated by openapi-typescript" not in generated:
    errors.append("schema.generated.ts is missing its generator provenance header")

playwright = (root / "playwright.config.ts").read_text(encoding="utf-8")
runtime_guard = (root / "tests/e2e/runtime-guard.ts").read_text(encoding="utf-8")
runtime_fixture = (root / "tests/e2e/runtime-fixture.ts").read_text(encoding="utf-8")
runtime_guard_tests = (root / "tests/e2e/runtime-guard.test.ts").read_text(encoding="utf-8")
screenshot_safety = (root / "tests/e2e/screenshot-safety.ts").read_text(encoding="utf-8")
screenshot_safety_tests = (root / "tests/e2e/screenshot-safety.test.ts").read_text(encoding="utf-8")
e2e_artifact_guard = (root / "scripts/check_e2e_artifacts.py").read_text(encoding="utf-8")
e2e_artifact_guard_tests = (root / "tests/unit/test_e2e_artifact_dlp.py").read_text(
    encoding="utf-8"
)
synthetic_http = (root / "tests/e2e/synthetic-http.ts").read_text(encoding="utf-8")
for fragment in (
    "collectBrowserRuntimeIssues",
    "message.type() === 'error'",
    "page.on('pageerror'",
    "page.on('requestfailed'",
    "summarizeBrowserRuntimeIssues",
):
    if fragment not in runtime_guard:
        errors.append(f"Global browser runtime collector is missing: {fragment}")
if ".text()" in runtime_guard or ".url()" in runtime_guard:
    errors.append(
        "Global browser runtime collector must not retain raw console text or request URLs"
    )
for fragment in (
    "browserRuntimeGuard",
    "{ auto: true }",
    "await page.route('**/api/v2/health'",
    "service: 'geo-platform-v2'",
    "version: 'contract-v2'",
    "browser-runtime-guard-summary",
    "page.waitForTimeout(150)",
    "'console-error': 0",
    "'page-error': 0",
    "'request-failed': 0",
    "'window-name-length': 0",
    "'script-readable-cookie-length': 0",
    "'cookie-store-entries': 0",
    "'indexed-db-databases': 0",
    "'cache-storage-entries': 0",
    "'service-worker-registrations': 0",
    "'opfs-root-entries': 0",
    "'storage-buckets': 0",
    "'legacy-filesystem-temporary-root-entries': 0",
    "'legacy-filesystem-persistent-root-entries': 0",
    "projectBrowserPersistenceCounts",
    "globalThis.name.length",
    "document.cookie.length",
    "cookieStore.getAll()",
    "indexedDB.databases()",
    "globalThis.caches.keys()",
    "navigator.serviceWorker.getRegistrations()",
    "navigator.storage.getDirectory()",
    "for await (const _entry of root.values())",
    "storageBuckets.keys()",
    "countLegacyFileSystemRootEntries(0)",
    "countLegacyFileSystemRootEntries(1)",
    "literal zero console errors, page errors, failed requests, Window.name length, "
    "script-readable Cookie length, Cookie Store entries, IndexedDB databases, Cache "
    "Storage entries, Service Worker registrations, OPFS root entries, named Storage "
    "Buckets and Legacy FileSystem temporary/persistent root entries",
    "raw messages, URLs, Window.name contents, Cookie names/values, database names, cache "
    "names, worker URLs, file names and bucket names are intentionally excluded from "
    "failure output",
):
    if fragment not in runtime_fixture:
        errors.append(f"Global Playwright runtime fixture is missing: {fragment}")
for fragment in (
    "projectSyntheticHttpResponseRules",
    "installSyntheticHttpResponses",
    "syntheticHttpResponseCount",
    "Synthetic HTTP responses are restricted to explicit 4xx/5xx tests or "
    "passthrough 204 successes",
    "const syntheticHttpCounts = new WeakMap",
    "page.exposeBinding('__geoSyntheticHttpTake'",
    "Only bodyless 204 responses may use synthetic HTTP passthrough",
    "globalThis.fetch = async",
    "const syntheticResponse = new Response",
    "Object.defineProperties(syntheticResponse",
    "'Cache-Control': 'no-store'",
):
    if fragment not in synthetic_http:
        errors.append(f"Safe synthetic HTTP response harness is missing: {fragment}")
for forbidden_fragment in (
    "localStorage.",
    "sessionStorage.",
    "console.",
    "message.text()",
    "request.failure()",
):
    if forbidden_fragment in synthetic_http:
        errors.append(
            "Safe synthetic HTTP response harness must not retain browser secrets or raw "
            f"diagnostics: {forbidden_fragment}"
        )
for fragment in (
    "Bearer runtime-guard-secret-canary",
    "OTP 824911",
    "access_token=runtime-guard-secret-canary",
    "not.toMatch",
    "collector.stop()",
):
    if fragment not in runtime_guard_tests:
        errors.append(f"Global Playwright runtime guard tests are missing: {fragment}")
for fragment in (
    "containsClientSecret",
    "containsClientSecretKey",
    "isSafeScreenshotSurface",
    "captureSafeScreenshot",
    "expectSafePageScreenshot",
    "expectSafeLocatorScreenshot",
    "document.body.innerText",
    "document.createTreeWalker",
    "semanticAttributeNames",
    "attributeNames",
    "scriptReadableCookieLength: document.cookie.length",
    "cookieStoreEntryCount",
    "windowName: globalThis.name",
    "historyState",
    "url.username.length > 0",
    "containsClientSecretKey(decodeURIComponent(url.hash))",
    "containsClientSecretKey(decodeURIComponent(segment))",
    "machineReadableVisuals",
    "computedGeneratedContentSafe",
    "computedResourceStylesSafe",
    "generatedContentContainsSecret",
    "computedGeneratedContentSize > 2_000_000",
    "issues.push('computed-generated-content')",
    "issues.push('computed-resource-style')",
    "dataset.visualEvidence",
    "getComputedStyle(candidate, pseudo)",
    "candidate.hasAttribute('style')",
    "machine-readable-visual",
    "Object.entries(localStorage)",
    "Object.entries(sessionStorage)",
    "indexedDbDatabaseCount",
    "cacheStorageCount",
    "serviceWorkerRegistrationCount",
    "opfsRootEntryCount",
    "storageBucketCount",
    "storageBuckets.keys()",
    "legacyFileSystemTemporaryRootEntryCount",
    "legacyFileSystemPersistentRootEntryCount",
    "countLegacyFileSystemRootEntries(0)",
    "countLegacyFileSystemRootEntries(1)",
    "issues.push('script-readable-cookie')",
    "issues.push('cookie-store')",
    "issues.push('window-name')",
    "indexedDB.databases()",
    "globalThis.caches.keys()",
    "navigator.serviceWorker.getRegistrations()",
    "navigator.storage.getDirectory()",
    "issues.push('indexed-db')",
    "issues.push('cache-storage')",
    "issues.push('service-worker')",
    "issues.push('opfs')",
    "issues.push('storage-buckets')",
    "issues.push('legacy-filesystem-temporary')",
    "issues.push('legacy-filesystem-persistent')",
    "await rm(target, { force: true })",
    "raw rendered values are intentionally omitted",
):
    if fragment not in screenshot_safety:
        errors.append(f"Local visual-evidence DLP boundary is missing: {fragment}")
for fragment in (
    'data-visual-evidence="payload-free"',
    "二维码视觉不包含可提取的配对 payload",
):
    if fragment not in customer_shell_source:
        errors.append(f"Customer pairing visual safety boundary is missing: {fragment}")
for fragment in (
    "rejects secrets from DOM, controls, URL and browser storage",
    "windowName: 'Bearer window-name-screenshot-secret-canary'",
    "computedGeneratedContentSafe: false",
    "computedResourceStylesSafe: false",
    "scriptReadableCookieLength: 1",
    "scriptReadableCookieLength: null",
    "cookieStoreEntryCount: 1",
    "cookieStoreEntryCount: null",
    "data-access_token",
    "historyState: null",
    "ordinary-user:ordinary-pass",
    "access%255Ftoken",
    "profile%255Fpath",
    "indexedDbDatabaseCount: 1",
    "indexedDbDatabaseCount: null",
    "cacheStorageCount: 1",
    "cacheStorageCount: null",
    "serviceWorkerRegistrationCount: 1",
    "serviceWorkerRegistrationCount: null",
    "opfsRootEntryCount: 1",
    "opfsRootEntryCount: null",
    "storageBucketCount: 1",
    "storageBucketCount: null",
    "legacyFileSystemTemporaryRootEntryCount: 1",
    "legacyFileSystemTemporaryRootEntryCount: null",
    "legacyFileSystemPersistentRootEntryCount: 1",
    "legacyFileSystemPersistentRootEntryCount: null",
    "removes a stale approved-path PNG",
    "Ｃｏｏｋｉｅ\\u200b＝session=screenshot-secret-canary",
    "rejection).not.toMatch",
    "rejects.toMatchObject({ code: 'ENOENT' })",
):
    if fragment not in screenshot_safety_tests:
        errors.append(f"Local visual-evidence DLP tests are missing: {fragment}")
customer_visual_e2e = (root / "tests/e2e/customer-visual.spec.ts").read_text(encoding="utf-8")
if "unmarked machine-readable QR pixels are rejected before visual evidence" not in (
    customer_visual_e2e
):
    errors.append("Customer visual E2E must reject unmarked machine-readable QR pixels")
for fragment in (
    "captureSafeScreenshot(page",
    "machine-readable-rejection-${testInfo.project.name}.png",
    ".unsafe-pairing span::before",
    "hidden browser surface secrets are rejected before visual evidence",
    "browser-surface-rejection-${testInfo.project.name}.png",
    "profile_path: '/secret/browser/profile/history-canary'",
    "pairing_token=browser-cookie-canary",
    "data-access_token",
    "await rm(temporaryScreenshot, { force: true })",
):
    if fragment not in customer_visual_e2e:
        errors.append(
            "Customer machine-readable visual negative test must use a temporary, "
            f"always-cleaned manual screenshot: missing {fragment}"
        )
for e2e_source_path in sorted((root / "tests/e2e").glob("*.ts")):
    e2e_source = e2e_source_path.read_text(encoding="utf-8")
    if re.search(r"(?<![\w.])test(?:\s*\(|\.)", e2e_source) and e2e_source_path.name not in {
        "runtime-fixture.ts",
        "runtime-guard.test.ts",
    }:
        if "from './runtime-fixture'" not in e2e_source:
            errors.append(
                f"{e2e_source_path.relative_to(root)} registers tests outside the global "
                "browser runtime guard"
            )
    if e2e_source_path.name != "runtime-fixture.ts" and re.search(
        r"\btest\b[^;\n]*from ['\"]@playwright/test['\"]", e2e_source
    ):
        errors.append(
            f"{e2e_source_path.relative_to(root)} bypasses the global browser runtime fixture"
        )
    if e2e_source_path.name != "screenshot-safety.ts" and (
        ".screenshot(" in e2e_source or ".toHaveScreenshot(" in e2e_source
    ):
        errors.append(
            f"{e2e_source_path.relative_to(root)} bypasses the visual-evidence DLP boundary"
        )
    runtime_allowance_markers = (
        "expectConsoleErrorsFromMockedHttpFailures",
        "expectRequestFailuresFromMockedNoContentResponses",
        "Invalid browser runtime negative-test allowance",
        "Invalid no-content transport allowance",
    )
    if any(marker in e2e_source for marker in runtime_allowance_markers):
        errors.append(
            f"{e2e_source_path.relative_to(root)} weakens the literal-zero browser runtime gate"
        )
    if "message.text()" in e2e_source or "message.location().url" in e2e_source:
        errors.append(
            f"{e2e_source_path.relative_to(root)} may not persist raw browser console diagnostics"
        )
    duplicate_runtime_markers = (
        "const consoleErrors",
        "const failedRequests",
        "page.on('console'",
        "page.on('pageerror'",
        "page.on('requestfailed'",
        "expectCleanRuntime",
    )
    if e2e_source_path.name != "runtime-guard.ts" and any(
        marker in e2e_source for marker in duplicate_runtime_markers
    ):
        errors.append(
            f"{e2e_source_path.relative_to(root)} duplicates the single global browser "
            "runtime collector"
        )
if root_package["scripts"].get("test:e2e-runtime-unit") != (
    "vitest run tests/e2e/runtime-guard.test.ts "
    "tests/e2e/screenshot-safety.test.ts "
    "tools/browser_runtime_evidence.test.mjs "
    "tools/production_browser_topology.test.mjs --environment node"
):
    errors.append("package.json must retain the browser runtime guard unit-test command")
if "pnpm test:e2e-runtime-unit" not in root_package["scripts"].get("test", ""):
    errors.append("The exact repository test gate must execute browser runtime guard unit tests")
if root_package["scripts"].get("check:e2e-artifacts") != (
    ".venv/bin/python scripts/check_e2e_artifacts.py"
):
    errors.append("package.json must retain the E2E artifact DLP guard command")
if "pnpm check:e2e-artifacts" not in root_package["scripts"].get("check", ""):
    errors.append("The exact repository gate must reject stale or secret-bearing E2E artifacts")
for fragment in (
    "browser-surface-rejection",
    "negative browser-safety test artifact",
):
    if fragment not in e2e_artifact_guard:
        errors.append(
            f"E2E artifact DLP must reject browser-surface negative baselines: {fragment}"
        )
if "browser-surface-rejection-customer-tablet.png" not in e2e_artifact_guard_tests:
    errors.append("E2E artifact DLP tests must freeze browser-surface negative baseline rejection")
for fragment in (
    "const reuseExistingE2eServer = process.env.GEO_E2E_REUSE_SERVER === '1'",
    "reuseExistingServer: reuseExistingE2eServer",
    "trace: 'off'",
    "screenshot: 'off'",
):
    if fragment not in playwright:
        errors.append(f"Playwright must fail closed against stale application bundles: {fragment}")
if "reuseExistingServer: true" in playwright:
    errors.append(
        "Playwright must not reuse an arbitrary pre-existing application bundle by default"
    )
for width, height in ((1600, 1100), (1024, 768), (390, 844)):
    viewport = rf"viewport\s*:\s*\{{\s*width\s*:\s*{width},\s*height\s*:\s*{height}\s*\}}"
    if len(re.findall(viewport, playwright)) != 5:
        errors.append(f"Playwright must keep {width}x{height} for all five applications")

for product, coverage in page_coverage.items():
    shell = (root / "apps" / str(coverage["app"]) / "app" / "shell.tsx").read_text(encoding="utf-8")
    if product == "operations":
        nav_match = re.search(r"\bconst\s+operationsNav\s*=\s*\[(.*?)\];", shell, flags=re.DOTALL)
    else:
        nav_match = re.search(r"\bconst\s+nav\s*=\s*\[(.*?)\];", shell, flags=re.DOTALL)
    if nav_match is None:
        errors.append(f"{coverage['app']}: unable to locate the product navigation definition")
        navigation_sections: set[str] = set()
    else:
        navigation_sections = set(re.findall(r"\bid\s*:\s*['\"]([^'\"]+)['\"]", nav_match.group(1)))
    expected_navigation = coverage["sections"] | coverage.get("external_sections", set())
    if navigation_sections != expected_navigation:
        missing = sorted(expected_navigation - navigation_sections)
        unexpected = sorted(navigation_sections - expected_navigation)
        errors.append(
            f"{coverage['app']} navigation drifted; missing={missing}, unexpected={unexpected}"
        )
    if coverage["app"] == "customer-web":
        compatibility_match = re.search(
            r"\bconst\s+customerLegacyAnalyticsSectionIds\s*=\s*\[(.*?)\]\s*as const;",
            shell,
            flags=re.DOTALL,
        )
        compatibility_sections = (
            set(re.findall(r"['\"]([^'\"]+)['\"]", compatibility_match.group(1)))
            if compatibility_match
            else set()
        )
        expected_compatibility_sections = coverage.get("compatibility_sections", set())
        if compatibility_sections != expected_compatibility_sections:
            errors.append(
                "customer-web legacy analytics compatibility sections drifted; "
                f"missing={sorted(expected_compatibility_sections - compatibility_sections)}, "
                f"unexpected={sorted(compatibility_sections - expected_compatibility_sections)}"
            )
        entry_match = re.search(
            r"installClientBrowserSecurity\(\s*\[(.*?)\]\s*\)",
            client_entries["customer-web"],
            flags=re.DOTALL,
        )
        entry_sections = (
            set(re.findall(r"['\"]([^'\"]+)['\"]", entry_match.group(1))) if entry_match else set()
        )
        expected_entry_sections = navigation_sections | compatibility_sections
        if entry_sections != expected_entry_sections:
            errors.append(
                "customer-web URL security allowlist drifted from product navigation and "
                "declared compatibility sections; "
                f"missing={sorted(expected_entry_sections - entry_sections)}, "
                f"unexpected={sorted(entry_sections - expected_entry_sections)}"
            )

    spec_path = root / "tests" / "e2e" / str(coverage["spec"])
    spec = spec_path.read_text(encoding="utf-8")
    sections = set(re.findall(r"\bsection\s*:\s*['\"]([^'\"]+)['\"]", spec))
    expected_sections = coverage["sections"]
    if sections != expected_sections:
        missing = sorted(expected_sections - sections)
        unexpected = sorted(sections - expected_sections)
        errors.append(
            f"{spec_path.relative_to(root)} visual sections drifted; "
            f"missing={missing}, unexpected={unexpected}"
        )

    snapshots = re.findall(r"\bsnapshot\s*:\s*['\"]([^'\"]+)\.png['\"]", spec)
    snapshot_root = root / "tests" / "e2e" / f"{spec_path.name}-snapshots"
    for snapshot in snapshots:
        for viewport in ("desktop", "tablet", "mobile"):
            expected = snapshot_root / f"{snapshot}-{product}-{viewport}-linux.png"
            if not expected.is_file():
                errors.append(f"missing visual baseline: {expected.relative_to(root)}")

for relative_path in (
    "apps/customer-web/app/account-mutation-guard.ts",
    "apps/customer-web/app/shell.tsx",
    "apps/intelligence-web/app/calibration-workspace.tsx",
    "apps/intelligence-web/app/mutation-guard.ts",
    "apps/report-studio/app/shell.tsx",
):
    client_scope_source = (root / relative_path).read_text(encoding="utf-8")
    if ".join('\\u0000')" in client_scope_source or '.join("\\u0000")' in client_scope_source:
        errors.append(
            f"{relative_path} must encode identity and mutation scopes structurally, "
            "without NUL-delimited strings"
        )

adr = root / "docs/contract-gaps/S03-ADR-0005-konva-evidence-annotation.md"
if not adr.exists():
    errors.append("the S03-owned Konva/Fabric decision record is missing")
else:
    decision = adr.read_text(encoding="utf-8")
    for required in ("Use **Konva", "Fabric.js is not selected", "Acceptance owner: S00/S04"):
        if required not in decision:
            errors.append(f"{adr.relative_to(root)} is missing: {required}")

if errors:
    raise SystemExit("Frontend contract guard failed:\n- " + "\n- ".join(errors))

print(
    "Frontend contract guard passed: five React 19 Framework SPA apps, strict TypeScript, "
    "the frozen frontend stack, generated API boundary, Konva ADR and three-viewport "
    "coverage for every S03-owned workspace are intact."
)
