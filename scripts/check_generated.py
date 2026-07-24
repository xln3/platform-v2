import hashlib
import json
from pathlib import Path

root = Path(__file__).parents[1]
manifest_path = root / "contracts" / "generated-manifest.json"
targets = [
    root / "contracts" / "openapi.json",
    root / "packages/api-client/src/schema.generated.ts",
]
manifest = {
    str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in targets
}
if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
    raise SystemExit("Generated OpenAPI artifacts differ from contracts/generated-manifest.json")
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
