import json
import re
from pathlib import Path

root = Path(__file__).parents[1]
apps = {
    "customer-web": "/platform/customer",
    "operations-web": "/platform/operations",
    "report-studio": "/platform/reports",
    "intelligence-web": "/platform/intelligence",
}
page_coverage = {
    "customer": {
        "app": "customer-web",
        "sections": {
            "home",
            "profile",
            "assets",
            "questions",
            "monitoring",
            "evidence",
            "reports",
            "members",
            "accounts",
        },
        "spec": "customer-visual.spec.ts",
    },
    # S01 owns execution and its visual evidence. S03 owns every other Operations workspace.
    "operations": {
        "app": "operations-web",
        "sections": {"overview", "sessions", "interventions", "events"},
        "external_sections": {"execution"},
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
        "sections": {"cases", "claims", "sources", "graph", "history", "verdict", "package"},
        "spec": "intelligence-visual.spec.ts",
    },
}
errors: list[str] = []

base_tsconfig = json.loads((root / "tsconfig.base.json").read_text(encoding="utf-8"))
compiler = base_tsconfig.get("compilerOptions", {})
for option in ("strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes"):
    if compiler.get(option) is not True:
        errors.append(f"tsconfig.base.json must keep compilerOptions.{option}=true")

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
    vite_config = (app_root / "vite.config.ts").read_text(encoding="utf-8")
    if not re.search(rf"\bbase\s*:\s*['\"]{re.escape(basename)}/['\"]", vite_config):
        errors.append(f"{app}: Vite base must be {basename}/ for isolated production assets")

    for source in (app_root / "app").rglob("*"):
        if source.suffix not in {".ts", ".tsx"} or ".test." in source.name:
            continue
        relative = source.relative_to(root)
        # S01 owns the complete execution feature, including its temporary handwritten API boundary.
        if app == "operations-web" and source.is_relative_to(
            app_root / "app" / "features" / "execution"
        ):
            continue
        text = source.read_text(encoding="utf-8")
        if re.search(r"\bfetch\s*\(", text):
            errors.append(f"{relative}: direct fetch is forbidden; use @geo/api-client")
        if "/api/v2/" in text:
            errors.append(f"{relative}: API path literals are forbidden; use generated paths")
        if re.search(r"\bfrom\s+['\"]zustand['\"]", text):
            errors.append(
                f"{relative}: Zustand is not currently justified; add an ADR before introducing it"
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
    "apps/intelligence-web/package.json": {"@xyflow/react"},
    "apps/report-studio/package.json": {"konva", "react-konva", "pdfjs-dist"},
}
for package_path, required in required_dependencies.items():
    package = json.loads((root / package_path).read_text(encoding="utf-8"))
    declared = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    missing = sorted(required - declared)
    if missing:
        errors.append(f"{package_path} is missing frozen stack dependencies: {missing}")

implementation_invariants = {
    "packages/design-system/src/index.tsx": (
        "QueryClientProvider",
        "new QueryClient(",
    ),
    "apps/customer-web/app/shell.tsx": (
        "useReactTable(",
        "useForm<",
        "zodResolver(",
        "<GeoBarChart",
    ),
    "packages/charts/src/index.tsx": (
        "import('echarts/core')",
        "geo-chart-table",
    ),
    "apps/intelligence-web/app/shell.tsx": (
        "<ReactFlow",
        "传播图节点与关系",
        "<table",
    ),
    "apps/report-studio/app/shell.tsx": (
        "import('pdfjs-dist')",
        "pdf.worker.min.mjs",
        "<Stage",
    ),
}
for source_path, required_fragments in implementation_invariants.items():
    source = (root / source_path).read_text(encoding="utf-8")
    for fragment in required_fragments:
        if fragment not in source:
            errors.append(f"{source_path} is missing frozen implementation invariant: {fragment}")

root_package = json.loads((root / "package.json").read_text(encoding="utf-8"))
root_dev_dependencies = root_package.get("devDependencies", {})
for dependency in ("@playwright/test", "@testing-library/react"):
    if dependency not in root_dev_dependencies:
        errors.append(f"package.json must retain frontend test dependency {dependency}")
for package_path in sorted((root / "apps").glob("*/package.json")) + sorted(
    (root / "packages").glob("*/package.json")
):
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if "test" in package.get("scripts", {}) and "vitest" not in package.get("devDependencies", {}):
        errors.append(
            f"{package_path.relative_to(root)} has a test task but does not declare Vitest"
        )

api_client = (root / "packages/api-client/src/index.ts").read_text(encoding="utf-8")
if "import type { paths } from './schema.generated'" not in api_client:
    errors.append("@geo/api-client must derive public types from schema.generated.ts")
if "createClient<paths>" not in api_client:
    errors.append("@geo/api-client must instantiate openapi-fetch with generated paths")

generated = (root / "packages/api-client/src/schema.generated.ts").read_text(encoding="utf-8")
if "This file was auto-generated by openapi-typescript" not in generated:
    errors.append("schema.generated.ts is missing its generator provenance header")

playwright = (root / "playwright.config.ts").read_text(encoding="utf-8")
for width, height in ((1600, 1100), (1024, 768), (390, 844)):
    viewport = rf"viewport\s*:\s*\{{\s*width\s*:\s*{width},\s*height\s*:\s*{height}\s*\}}"
    if len(re.findall(viewport, playwright)) != 4:
        errors.append(f"Playwright must keep {width}x{height} for all four applications")

for product, coverage in page_coverage.items():
    shell = (root / "apps" / str(coverage["app"]) / "app" / "shell.tsx").read_text(encoding="utf-8")
    if product == "operations":
        nav_match = re.search(r"\bnav\s*=\s*\{\[(.*?)\]\}", shell, flags=re.DOTALL)
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
    "Frontend contract guard passed: four React 19 Framework SPA apps, strict TypeScript, "
    "the frozen frontend stack, generated API boundary, Konva ADR and three-viewport "
    "coverage for every S03-owned workspace are intact."
)
