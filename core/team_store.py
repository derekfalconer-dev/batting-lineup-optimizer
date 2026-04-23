from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    path = Path("data") / "teams"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _team_path(team_id: str) -> Path:
    return _data_dir() / f"{team_id}.json"


def create_team_file(team_id: str, payload: dict[str, Any]) -> None:
    with _team_path(team_id).open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def save_team(team_id: str, payload: dict[str, Any]) -> None:
    path = _team_path(team_id)
    tmp_path = path.with_suffix(".json.tmp")

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    tmp_path.replace(path)


def load_team(team_id: str) -> dict[str, Any]:
    path = _team_path(team_id)
    if not path.exists():
        raise ValueError(f"Team not found: {team_id}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_team_ids() -> list[str]:
    return sorted(path.stem for path in _data_dir().glob("*.json"))


def delete_team(team_id: str) -> None:
    path = _team_path(team_id)
    if path.exists():
        path.unlink()