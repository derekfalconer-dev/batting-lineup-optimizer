from __future__ import annotations

from typing import Any, Mapping, Sequence

from core.archetypes import PlayerArchetype, PlayerTraits


VALID_ADJUSTMENT_FIELDS = {
    "contact",
    "power",
    "speed",
    "baserunning",
    "plate_discipline",
    "strikeout_tendency",
    "walk_skill",
    "chase_tendency",
    "aggression",
    "clutch",
    "sacrifice_ability",
}

REQUIRED_TRAIT_FIELDS = set(PlayerTraits.__dataclass_fields__.keys())


def validate_profiles(profiles: Sequence[Any]) -> list[str]:
    """
    Validate a loaded profile roster before optimization.
    """
    errors: list[str] = []

    if not profiles:
        errors.append("No player profiles were loaded.")
        return errors

    if len(profiles) < 5:
        errors.append(
            f"Roster only has {len(profiles)} players. "
            "That is too small to meaningfully optimize."
        )
    elif len(profiles) < 9:
        errors.append(
            f"Roster has {len(profiles)} players. "
            "A full batting order usually needs 9 players."
        )

    names = [getattr(p, "name", "").strip() for p in profiles]
    blank_name_count = sum(1 for n in names if not n)
    if blank_name_count:
        errors.append(f"{blank_name_count} player profile(s) are missing a name.")

    duplicates = sorted({name for name in names if name and names.count(name) > 1})
    if duplicates:
        errors.append(f"Duplicate player names detected: {', '.join(duplicates)}")

    return errors


def validate_adjustments(
    profiles: Sequence[Any],
    adjustments_by_name: Mapping[str, Mapping[str, Any]] | None,
) -> list[str]:
    """
    Validate coach adjustment payloads against loaded profiles.
    """
    if not adjustments_by_name:
        return []

    errors: list[str] = []
    valid_names = {getattr(p, "name", "").strip() for p in profiles if getattr(p, "name", "").strip()}

    for player_name, fields in adjustments_by_name.items():
        if valid_names and player_name not in valid_names:
            errors.append(f"Adjustment provided for unknown player: {player_name}")
            continue

        if not isinstance(fields, Mapping):
            errors.append(f"Adjustments for player '{player_name}' must be an object/dict.")
            continue

        for field_name, value in fields.items():
            if field_name not in VALID_ADJUSTMENT_FIELDS:
                errors.append(
                    f"Unknown adjustment field '{field_name}' for player '{player_name}'."
                )
                continue

            try:
                float(value)
            except (TypeError, ValueError):
                errors.append(
                    f"Adjustment field '{field_name}' for player '{player_name}' "
                    f"must be numeric. Got: {value!r}"
                )

    return errors


def validate_manual_archetype_roster(
    roster: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """
    Validate JSON roster used by build_team_from_archetypes(...).
    """
    if roster is None:
        return ["Manual archetype roster is missing."]
    if not isinstance(roster, Sequence) or isinstance(roster, (str, bytes)):
        return ["Manual archetype roster must be a list of player objects."]

    errors: list[str] = []

    for idx, row in enumerate(roster, start=1):
        if not isinstance(row, Mapping):
            errors.append(f"Roster entry #{idx} must be an object/dict.")
            continue

        name = str(row.get("name", "")).strip()
        if not name:
            errors.append(f"Roster entry #{idx} is missing required field 'name'.")

        archetype_raw = row.get("archetype")
        if archetype_raw in (None, ""):
            errors.append(f"Roster entry #{idx} is missing required field 'archetype'.")
        else:
            try:
                PlayerArchetype(archetype_raw)
            except ValueError:
                valid = ", ".join(a.value for a in PlayerArchetype)
                errors.append(
                    f"Roster entry #{idx} has invalid archetype '{archetype_raw}'. "
                    f"Valid values: {valid}"
                )

        handedness = row.get("handedness")
        if handedness not in (None, "", "R", "L", "S", "U"):
            errors.append(
                f"Roster entry #{idx} has invalid handedness '{handedness}'. "
                "Expected one of: R, L, S, U"
            )

        adjustment = row.get("adjustment")
        if adjustment is not None:
            if not isinstance(adjustment, Mapping):
                errors.append(f"Roster entry #{idx} field 'adjustment' must be an object/dict.")
            else:
                errors.extend(_validate_adjustment_mapping(adjustment, context=f"roster entry #{idx}"))

    return errors


def validate_manual_traits_roster(
    roster: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """
    Validate JSON roster used by build_team_from_manual_traits(...).
    """
    if roster is None:
        return ["Manual traits roster is missing."]
    if not isinstance(roster, Sequence) or isinstance(roster, (str, bytes)):
        return ["Manual traits roster must be a list of player objects."]

    errors: list[str] = []

    for idx, row in enumerate(roster, start=1):
        if not isinstance(row, Mapping):
            errors.append(f"Roster entry #{idx} must be an object/dict.")
            continue

        name = str(row.get("name", "")).strip()
        if not name:
            errors.append(f"Roster entry #{idx} is missing required field 'name'.")

        handedness = row.get("handedness")
        if handedness not in (None, "", "R", "L", "S", "U"):
            errors.append(
                f"Roster entry #{idx} has invalid handedness '{handedness}'. "
                "Expected one of: R, L, S, U"
            )

        archetype_raw = row.get("archetype")
        if archetype_raw not in (None, ""):
            try:
                PlayerArchetype(archetype_raw)
            except ValueError:
                valid = ", ".join(a.value for a in PlayerArchetype)
                errors.append(
                    f"Roster entry #{idx} has invalid archetype '{archetype_raw}'. "
                    f"Valid values: {valid}"
                )

        traits = row.get("traits")
        if not isinstance(traits, Mapping):
            errors.append(f"Roster entry #{idx} is missing required object field 'traits'.")
        else:
            errors.extend(_validate_traits_mapping(traits, context=f"roster entry #{idx}"))

        adjustment = row.get("adjustment")
        if adjustment is not None:
            if not isinstance(adjustment, Mapping):
                errors.append(f"Roster entry #{idx} field 'adjustment' must be an object/dict.")
            else:
                errors.extend(_validate_adjustment_mapping(adjustment, context=f"roster entry #{idx}"))

    return errors


def _validate_adjustment_mapping(
    adjustment: Mapping[str, Any],
    *,
    context: str,
) -> list[str]:
    errors: list[str] = []

    for field_name, value in adjustment.items():
        if field_name not in VALID_ADJUSTMENT_FIELDS:
            errors.append(f"{context}: unknown adjustment field '{field_name}'.")
            continue
        try:
            float(value)
        except (TypeError, ValueError):
            errors.append(
                f"{context}: adjustment field '{field_name}' must be numeric. Got: {value!r}"
            )

    return errors


def _validate_traits_mapping(
    traits: Mapping[str, Any],
    *,
    context: str,
) -> list[str]:
    errors: list[str] = []

    trait_keys = set(traits.keys())

    missing = sorted(REQUIRED_TRAIT_FIELDS - trait_keys)
    extra = sorted(trait_keys - REQUIRED_TRAIT_FIELDS)

    if missing:
        errors.append(f"{context}: missing required trait fields: {', '.join(missing)}")
    if extra:
        errors.append(f"{context}: unknown trait fields: {', '.join(extra)}")

    for field_name in sorted(REQUIRED_TRAIT_FIELDS & trait_keys):
        value = traits[field_name]
        try:
            float(value)
        except (TypeError, ValueError):
            errors.append(
                f"{context}: trait field '{field_name}' must be numeric. Got: {value!r}"
            )

    return errors