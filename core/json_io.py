from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_file(path: str | Path) -> Any:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)