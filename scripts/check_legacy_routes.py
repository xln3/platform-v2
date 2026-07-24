import hashlib
import json
from pathlib import Path

root = Path(__file__).parents[2]
manifest_path = Path(__file__).parents[1] / "contracts" / "legacy-route-manifest.json"
targets = [
    root / "server/geosys/app.py",
    root / "server/geosys/client.py",
    root / "server/geosys/portal.py",
    root / "server/geosys/console.py",
    root / "server/geosys/collect_console.py",
    root / "server/geosys/pipeline.py",
]
actual = {
    str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in targets
}
expected = json.loads(manifest_path.read_text(encoding="utf-8"))
if actual != expected:
    raise SystemExit("Legacy route files changed; V2 must not modify or redirect old routes")
