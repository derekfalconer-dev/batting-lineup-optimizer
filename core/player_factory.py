from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.archetypes import (
    Handedness,
    PlayerArchetype,
    PlayerProfile,
    SourceMode,
    TraitAdjustment,
    create_player_from_archetype,
)
from core.gc_loader import load_gamechanger_records
from core.models import Player


@dataclass(slots=True)
class TeamBundle:
    """
    Canonical team object for app / CLI / future web flow.

    profiles:
        Source-of-truth baseball layer objects.
    players:
        Simulator-ready objects derived from profiles.
    profile_map:
        Name-keyed lookup for coach tweaks / UI editing.
    source:
        Describes where the team came from.
    """
    profiles: list[PlayerProfile]
    players: list[Player]
    profile_map: dict[str, PlayerProfile]
    source: str

    def names(self) -> list[str]:
        return [p.name for p in self.profiles]


# ---------------------------------------------------------------------
# Single-profile factories
# ---------------------------------------------------------------------

def profile_from_gc_record(
    record: Mapping[str, Any],
    *,
    handedness: Handedness = Handedness.UNKNOWN,
    adjustment: TraitAdjustment | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> PlayerProfile:
    merged_meta = dict(metadata or {})
    merged_meta.update(
        {
            "source_file": record.get("source_file"),
            "number": record.get("number"),
            "first": record.get("first"),
            "last": record.get("last"),
            "raw_row": record.get("raw_row"),
        }
    )

    gc_row = {
        k: v
        for k, v in record.items()
        if k not in {"name", "first", "last", "number", "source_file", "raw_row"}
    }

    return PlayerProfile.from_gamechanger(
        name=str(record["name"]),
        gc_row=gc_row,
        handedness=handedness,
        adjustment=adjustment,
        metadata=merged_meta,
    )


def profile_from_archetype(
    *,
    name: str,
    archetype: PlayerArchetype | str,
    handedness: Handedness = Handedness.UNKNOWN,
    adjustment: TraitAdjustment | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> PlayerProfile:
    return create_player_from_archetype(
        name=name,
        archetype=archetype,
        handedness=handedness,
        adjustment=adjustment,
        metadata=metadata,
    )


def profile_from_manual_traits(
    *,
    name: str,
    traits: Mapping[str, Any],
    handedness: Handedness = Handedness.UNKNOWN,
    archetype: PlayerArchetype = PlayerArchetype.UNKNOWN,
    adjustment: TraitAdjustment | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> PlayerProfile:
    return PlayerProfile.from_manual_traits(
        name=name,
        traits=traits,
        handedness=handedness,
        archetype=archetype,
        adjustment=adjustment,
        metadata=metadata,
    )


# ---------------------------------------------------------------------
# Adjustment helpers
# ---------------------------------------------------------------------

def _refresh_source_mode(profile: PlayerProfile) -> PlayerProfile:
    if profile.source_mode in {SourceMode.GC, SourceMode.GC_NUDGED}:
        new_mode = (
            SourceMode.GC_NUDGED
            if not profile.adjustment.is_zero()
            else SourceMode.GC
        )
        if new_mode != profile.source_mode:
            return PlayerProfile(
                name=profile.name,
                handedness=profile.handedness,
                archetype=profile.archetype,
                base_traits=profile.base_traits,
                adjustment=profile.adjustment,
                source=profile.source,
                source_mode=new_mode,
                metadata=dict(profile.metadata),
            )
    return profile


def apply_adjustments_to_profile(
    profile: PlayerProfile,
    adjustments: Mapping[str, float] | None = None,
) -> PlayerProfile:
    if not adjustments:
        return _refresh_source_mode(profile)

    updated = profile.bump(**{k: float(v) for k, v in adjustments.items()})
    return _refresh_source_mode(updated)


def apply_adjustments_to_profiles(
    profiles: Sequence[PlayerProfile],
    adjustments_by_name: Mapping[str, Mapping[str, float]] | None = None,
) -> list[PlayerProfile]:
    if not adjustments_by_name:
        return list(profiles)

    adjusted: list[PlayerProfile] = []
    for profile in profiles:
        delta = adjustments_by_name.get(profile.name)
        if delta:
            adjusted.append(apply_adjustments_to_profile(profile, delta))
        else:
            adjusted.append(profile)
    return adjusted


# ---------------------------------------------------------------------
# Team builders
# ---------------------------------------------------------------------

def build_team_from_gamechanger(
    csv_path: str | Path,
    *,
    min_pa: int = 5,
    include_names: Sequence[str] | None = None,
    exclude_names: Sequence[str] | None = None,
    default_handedness: Handedness = Handedness.UNKNOWN,
    name_format: str = "full",
    adjustments_by_name: Mapping[str, Mapping[str, float]] | None = None,
) -> TeamBundle:
    records = load_gamechanger_records(
        csv_path=csv_path,
        min_pa=min_pa,
        include_names=include_names,
        exclude_names=exclude_names,
        name_format=name_format,
    )
    return build_team_from_gc_records(
        records,
        default_handedness=default_handedness,
        adjustments_by_name=adjustments_by_name,
        source="gamechanger",
    )


def build_team_from_gc_records(
    records: Sequence[Mapping[str, Any]],
    *,
    default_handedness: Handedness = Handedness.UNKNOWN,
    adjustments_by_name: Mapping[str, Mapping[str, float]] | None = None,
    source: str = "gamechanger_records",
) -> TeamBundle:
    profiles = [
        profile_from_gc_record(
            record,
            handedness=default_handedness,
        )
        for record in records
    ]
    profiles = apply_adjustments_to_profiles(profiles, adjustments_by_name)
    return bundle_team(profiles, source=source)


def build_team_from_archetypes(
    roster: Sequence[Mapping[str, Any]],
) -> TeamBundle:
    """
    Example roster item:
    {
        "name": "Cy Falconer",
        "archetype": "table_setter",
        "handedness": "R",
        "adjustment": {"power": 8, "speed": 5}
    }
    """
    profiles: list[PlayerProfile] = []

    for row in roster:
        name = str(row["name"])
        archetype = PlayerArchetype(row["archetype"])

        handedness_raw = row.get("handedness", Handedness.UNKNOWN.value)
        handedness = Handedness(handedness_raw)

        adjustment_data = row.get("adjustment", {}) or {}
        adjustment = TraitAdjustment(
            **{k: float(v) for k, v in adjustment_data.items()}
        )

        profile = profile_from_archetype(
            name=name,
            archetype=archetype,
            handedness=handedness,
            adjustment=adjustment,
            metadata={"factory_source": "manual_archetype"},
        )
        profiles.append(profile)

    return bundle_team(profiles, source="manual_archetype")


def build_team_from_manual_traits(
    roster: Sequence[Mapping[str, Any]],
) -> TeamBundle:
    """
    Example roster item:
    {
        "name": "Cy Falconer",
        "traits": {...},
        "handedness": "R",
        "archetype": "balanced",
        "adjustment": {"power": 5}
    }
    """
    profiles: list[PlayerProfile] = []

    for row in roster:
        name = str(row["name"])
        traits = dict(row["traits"])

        handedness_raw = row.get("handedness", Handedness.UNKNOWN.value)
        handedness = Handedness(handedness_raw)

        archetype_raw = row.get("archetype", PlayerArchetype.UNKNOWN.value)
        archetype = PlayerArchetype(archetype_raw)

        adjustment_data = row.get("adjustment", {}) or {}
        adjustment = TraitAdjustment(
            **{k: float(v) for k, v in adjustment_data.items()}
        )

        profile = profile_from_manual_traits(
            name=name,
            traits=traits,
            handedness=handedness,
            archetype=archetype,
            adjustment=adjustment,
            metadata={"factory_source": "manual_traits"},
        )
        profiles.append(profile)

    return bundle_team(profiles, source="manual_traits")


def build_team_from_profiles(
    profiles: Sequence[PlayerProfile],
    *,
    adjustments_by_name: Mapping[str, Mapping[str, float]] | None = None,
    source: str = "profiles",
) -> TeamBundle:
    adjusted_profiles = apply_adjustments_to_profiles(profiles, adjustments_by_name)
    return bundle_team(adjusted_profiles, source=source)


# ---------------------------------------------------------------------
# Bundling
# ---------------------------------------------------------------------

def bundle_team(
    profiles: Sequence[PlayerProfile],
    *,
    source: str,
) -> TeamBundle:
    profile_map: dict[str, PlayerProfile] = {}
    duplicates: list[str] = []

    for profile in profiles:
        if profile.name in profile_map:
            duplicates.append(profile.name)
        else:
            profile_map[profile.name] = profile

    if duplicates:
        dupes = ", ".join(sorted(set(duplicates)))
        raise ValueError(f"Duplicate player names in team bundle: {dupes}")

    players = [profile.to_sim_player() for profile in profiles]

    return TeamBundle(
        profiles=list(profiles),
        players=players,
        profile_map=profile_map,
        source=source,
    )