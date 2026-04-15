from __future__ import annotations

from pathlib import Path

from core.json_io import load_json_file
from core.player_factory import (
    build_team_from_gamechanger,
    build_team_from_archetypes,
    build_team_from_manual_traits,
)


def load_gc_team(csv_path: str | Path, adjustments_path: str | Path | None = None):
    adjustments = load_json_file(adjustments_path) if adjustments_path else None
    return build_team_from_gamechanger(
        csv_path=csv_path,
        min_pa=5,
        name_format="full",
        adjustments_by_name=adjustments,
    )


def load_manual_archetype_team(roster_path: str | Path):
    roster = load_json_file(roster_path)
    return build_team_from_archetypes(roster)


def load_manual_traits_team(roster_path: str | Path):
    roster = load_json_file(roster_path)
    return build_team_from_manual_traits(roster)