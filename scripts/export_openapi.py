import json
from pathlib import Path

from geo_platform.main import app

target = Path(__file__).parents[1] / "contracts" / "openapi.json"
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
