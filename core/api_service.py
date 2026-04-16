from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from core.app_service import evaluate_lineup_workflow, run_optimizer_workflow
from core.archetypes import (
    Handedness,
    PlayerArchetype,
    PlayerProfile,
    PlayerTraits,
    create_player_from_archetype,
)

from core.player_factory import (
    build_team_from_archetypes,
    build_team_from_gamechanger,
    build_team_from_gc_records,
    build_team_from_manual_traits,
)

from core.json_io import load_json_file

from core.session_manager import OptimizerSession, get_session_manager

from core.presenters import present_saved_scenario, present_saved_scenarios
from core.schemas import (
    SavedScenarioSchema,
    ScenarioCollectionSchema,
    SessionStateSchema,
    WorkflowResponseSchema,
)


# ---------------------------------------------------------------------
# Session serialization helpers
# ---------------------------------------------------------------------

def _present_session(session: OptimizerSession) -> SessionStateSchema:
    return SessionStateSchema(
        session_id=session.session_id,
        status=_infer_session_status(session),
        data_source=session.data_source,
        csv_path=str(session.csv_path) if session.csv_path else None,
        adjustments_path=str(session.adjustments_path) if session.adjustments_path else None,
        roster_path=str(session.roster_path) if session.roster_path else None,
        workflow_response=session.result,
        warnings=[],
        errors=[],
    )


def _infer_session_status(session: OptimizerSession) -> str:
    if session.result is not None:
        return "completed"
    if session.is_ready_to_run:
        return "ready"
    if session.has_inputs:
        return "configured"
    return "created"


def _merged_adjustments_for_session(session: OptimizerSession) -> dict[str, dict[str, float]] | None:
    """
    Merge persisted/uploaded adjustments with in-app Coach Lab nudges.

    Current policy:
    - uploaded JSON adjustments are the starting point
    - in-app coach adjustments override same player/field pairs
    """
    merged: dict[str, dict[str, float]] = {}

    if session.adjustments:
        for player_name, values in session.adjustments.items():
            merged[player_name] = {str(k): float(v) for k, v in values.items()}

    for player_name, values in session.coach_adjustments_by_name.items():
        existing = merged.setdefault(player_name, {})
        for k, v in values.items():
            existing[str(k)] = float(v)

    return merged or None


def _build_profiles_from_session_inputs(session: OptimizerSession) -> list[PlayerProfile]:
    """
    Build editable PlayerProfile objects from the session's current source inputs.

    This seeds the in-session editable roster from:
    - GameChanger CSV
    - manual roster JSON
    - in-memory manual roster list (including empty roster startup)
    """
    if session.data_source == "gc":
        if session.csv_path is None:
            raise ValueError("Session csv_path is missing for GC roster initialization.")
        bundle = build_team_from_gamechanger(
            csv_path=session.csv_path,
            min_pa=5,
            name_format="full",
            adjustments_by_name=None,
        )
        return list(bundle.profiles)

    if session.data_source == "gc_plus_tweaks":
        if session.csv_path is None:
            raise ValueError("Session csv_path is missing for GC+tweaks roster initialization.")
        bundle = build_team_from_gamechanger(
            csv_path=session.csv_path,
            min_pa=5,
            name_format="full",
            adjustments_by_name=session.adjustments or None,
        )
        return list(bundle.profiles)

    if session.data_source == "gc_merged":
        if session.manual_roster is None:
            return []

        bundle = build_team_from_gc_records(
            session.manual_roster,
            source="gamechanger_merged_records",
        )
        return list(bundle.profiles)

    if session.data_source == "manual_archetypes":
        if session.manual_roster is not None:
            bundle = build_team_from_archetypes(session.manual_roster)
            return list(bundle.profiles)

        if session.roster_path:
            roster = load_json_file(session.roster_path)
            bundle = build_team_from_archetypes(roster)
            return list(bundle.profiles)

        return []

    if session.data_source == "manual_traits":
        if session.manual_roster is not None:
            bundle = build_team_from_manual_traits(session.manual_roster)
            return list(bundle.profiles)

        if session.roster_path:
            roster = load_json_file(session.roster_path)
            bundle = build_team_from_manual_traits(roster)
            return list(bundle.profiles)

        return []

    raise ValueError(
        f"Unsupported session data_source for editable roster initialization: {session.data_source}"
    )


def _active_profiles_for_session(session: OptimizerSession) -> list[PlayerProfile] | None:
    """
    Return the active editable roster for this session, excluding benched players.
    Falls back to None if no editable roster exists yet.
    """
    if not session.editable_profiles:
        return None

    benched_names = set(session.benched_player_names)
    return [
        profile for profile in session.editable_profiles
        if profile.name not in benched_names
    ]


# ---------------------------------------------------------------------
# Public API-like service functions
# ---------------------------------------------------------------------

def create_session() -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.create_session()
    return _present_session(session)


def get_session(session_id: str) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)
    return _present_session(session)


def delete_session(session_id: str) -> None:
    manager = get_session_manager()
    manager.delete_session(session_id)


def configure_gc_session(
    session_id: str,
    *,
    csv_path: str | Path,
    adjustments_path: str | Path | None = None,
    data_source: str = "gc",
) -> SessionStateSchema:
    if data_source not in {"gc", "gc_plus_tweaks"}:
        raise ValueError(
            "configure_gc_session only supports data_source='gc' or 'gc_plus_tweaks'"
        )

    manager = get_session_manager()
    session = manager.set_data_source(
        session_id,
        data_source=data_source,
        csv_path=Path(csv_path),
        adjustments_path=Path(adjustments_path) if adjustments_path else None,
        roster_path=None,
    )

    if adjustments_path:
        manager.set_adjustments(session_id, load_json_file(adjustments_path))
    else:
        manager.set_adjustments(session_id, {})

    manager.clear_result(session_id)
    return _present_session(session)


def configure_reconciled_gc_session(
    session_id: str,
    *,
    merged_records: list[dict[str, Any]],
    data_source: str = "gc_merged",
) -> SessionStateSchema:
    """
    Configure a session from already-reconciled GameChanger records.

    This is the backend seam for future multi-file import UI:
    - parse many CSVs
    - reconcile/merge records
    - coach reviews duplicates
    - final merged records seed Coach Lab
    """
    if data_source != "gc_merged":
        raise ValueError("configure_reconciled_gc_session only supports data_source='gc_merged'")

    manager = get_session_manager()

    session = manager.set_data_source(
        session_id,
        data_source=data_source,
        csv_path=None,
        adjustments_path=None,
        roster_path=None,
    )

    manager.set_adjustments(session_id, {})
    manager.set_manual_roster(session_id, list(merged_records))
    manager.clear_result(session_id)

    return _present_session(session)


def configure_manual_session(
    session_id: str,
    *,
    roster_path: str | Path,
    data_source: str,
) -> SessionStateSchema:
    if data_source not in {"manual_archetypes", "manual_traits"}:
        raise ValueError(
            "configure_manual_session only supports "
            "data_source='manual_archetypes' or 'manual_traits'"
        )

    manager = get_session_manager()
    roster_path = Path(roster_path)
    loaded_roster = load_json_file(roster_path)

    session = manager.set_data_source(
        session_id,
        data_source=data_source,
        csv_path=None,
        adjustments_path=None,
        roster_path=roster_path,
    )

    manager.set_adjustments(session_id, {})
    manager.set_manual_roster(session_id, loaded_roster)
    manager.clear_result(session_id)

    return _present_session(session)


def configure_empty_manual_session(
    session_id: str,
    *,
    data_source: str = "manual_archetypes",
) -> SessionStateSchema:
    """
    Start a Coach Lab session from an empty roster.

    This replaces the old JSON-first manual workflow.
    Coaches can add players directly in-app using archetypes and trait edits.
    """
    if data_source not in {"manual_archetypes", "manual_traits"}:
        raise ValueError(
            "configure_empty_manual_session only supports "
            "data_source='manual_archetypes' or 'manual_traits'"
        )

    manager = get_session_manager()

    session = manager.set_data_source(
        session_id,
        data_source=data_source,
        csv_path=None,
        adjustments_path=None,
        roster_path=None,
    )

    manager.set_adjustments(session_id, {})
    manager.set_manual_roster(session_id, [])
    manager.set_editable_roster(session_id, profiles=[])
    manager.clear_result(session_id)

    return _present_session(session)


def update_adjustments_path(
    session_id: str,
    adjustments_path: str | Path | None,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    session.adjustments_path = Path(adjustments_path) if adjustments_path else None
    session.touch()
    manager.clear_result(session_id)

    if session.data_source == "gc":
        session.data_source = "gc_plus_tweaks" if adjustments_path else "gc"

    return _present_session(session)


def run_optimization(
    session_id: str,
    *,
    output_dir: str | Path = "output",
    target_runs: float = 4.0,
    optimizer_config: dict[str, Any] | None = None,
    rules: Any | None = None,
) -> WorkflowResponseSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.is_ready_to_run:
        raise ValueError(
            f"Session {session_id} is not ready to run. "
            f"Current status: {_infer_session_status(session)}"
        )

    merged_adjustments = _merged_adjustments_for_session(session)

    profiles_override = _active_profiles_for_session(session)

    result = run_optimizer_workflow(
        data_source=session.data_source,
        csv_path=session.csv_path,
        adjustments_path=session.adjustments_path,
        roster_path=session.roster_path,
        rules=rules,
        output_dir=output_dir,
        target_runs=target_runs,
        optimizer_config=optimizer_config,
        present=True,
        adjustments_by_name=merged_adjustments,
        profiles_override=profiles_override,
    )

    manager.set_result(session_id, result)
    return result


def get_results(session_id: str) -> WorkflowResponseSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if session.result is None:
        raise ValueError(f"Session {session_id} has no optimization result yet.")

    return session.result


def reset_session_results(session_id: str) -> SessionStateSchema:
    manager = get_session_manager()
    manager.clear_result(session_id)
    session = manager.get_session(session_id)
    return _present_session(session)


def initialize_editable_roster(session_id: str) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    profiles = _build_profiles_from_session_inputs(session)
    manager.set_editable_roster(session_id, profiles=profiles)
    manager.clear_result(session_id)

    refreshed = manager.get_session(session_id)
    return _present_session(refreshed)


def get_editable_roster(session_id: str) -> list[PlayerProfile]:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if session.editable_profiles:
        return list(session.editable_profiles)

    if session.data_source in {"gc", "gc_plus_tweaks", "gc_merged", "manual_archetypes", "manual_traits"}:
        profiles = _build_profiles_from_session_inputs(session)
        manager.set_editable_roster(session_id, profiles=profiles)

    refreshed = manager.get_session(session_id)
    return list(refreshed.editable_profiles)


def add_player_from_archetype(
    session_id: str,
    *,
    name: str,
    archetype: str,
    handedness: str = "U",
) -> SessionStateSchema:
    manager = get_session_manager()

    profile = create_player_from_archetype(
        name=name,
        archetype=PlayerArchetype(archetype),
        handedness=Handedness(handedness),
    )

    manager.add_player_profile(session_id, profile=profile)
    manager.clear_result(session_id)
    return _present_session(manager.get_session(session_id))


def update_player_traits(
    session_id: str,
    *,
    player_name: str,
    traits: dict[str, float],
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    existing = next((p for p in session.editable_profiles if p.name == player_name), None)
    if existing is None:
        raise ValueError(f"Editable roster player not found: {player_name}")

    updated_profile = replace(
        existing,
        base_traits=PlayerTraits.from_mapping(traits),
    )

    manager.replace_player_profile(
        session_id,
        player_name=player_name,
        profile=updated_profile,
    )
    manager.clear_result(session_id)
    return _present_session(manager.get_session(session_id))


def update_player_identity(
    session_id: str,
    *,
    player_name: str,
    new_name: str | None = None,
    handedness: str | None = None,
    archetype: str | None = None,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    existing = next((p for p in session.editable_profiles if p.name == player_name), None)
    if existing is None:
        raise ValueError(f"Editable roster player not found: {player_name}")

    updated_profile = replace(
        existing,
        name=new_name.strip() if new_name is not None else existing.name,
        handedness=Handedness(handedness) if handedness is not None else existing.handedness,
        archetype=PlayerArchetype(archetype) if archetype is not None else existing.archetype,
    )

    manager.replace_player_profile(
        session_id,
        player_name=player_name,
        profile=updated_profile,
    )
    manager.clear_result(session_id)
    return _present_session(manager.get_session(session_id))


def delete_player(
    session_id: str,
    *,
    player_name: str,
) -> SessionStateSchema:
    manager = get_session_manager()
    manager.delete_player_profile(session_id, player_name=player_name)
    manager.clear_result(session_id)
    return _present_session(manager.get_session(session_id))


def bench_player(
    session_id: str,
    *,
    player_name: str,
) -> SessionStateSchema:
    manager = get_session_manager()
    manager.bench_player(session_id, player_name=player_name)
    manager.clear_result(session_id)
    return _present_session(manager.get_session(session_id))


def unbench_player(
    session_id: str,
    *,
    player_name: str,
) -> SessionStateSchema:
    manager = get_session_manager()
    manager.unbench_player(session_id, player_name=player_name)
    manager.clear_result(session_id)
    return _present_session(manager.get_session(session_id))


def move_player_up(
    session_id: str,
    *,
    player_name: str,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.move_player_up(
        session_id,
        player_name=player_name,
    )
    manager.clear_result(session_id)
    return _present_session(session)


def move_player_down(
    session_id: str,
    *,
    player_name: str,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.move_player_down(
        session_id,
        player_name=player_name,
    )
    manager.clear_result(session_id)
    return _present_session(session)


def set_player_order(
    session_id: str,
    *,
    player_name: str,
    new_index: int,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    benched_names = set(session.benched_player_names)
    active_profiles = [p for p in session.editable_profiles if p.name not in benched_names]
    benched_profiles = [p for p in session.editable_profiles if p.name in benched_names]

    idx = next((i for i, p in enumerate(active_profiles) if p.name == player_name), None)
    if idx is None:
        raise ValueError(f"Active roster player not found: {player_name}")

    new_index = max(0, min(int(new_index), len(active_profiles) - 1))

    profile = active_profiles.pop(idx)
    active_profiles.insert(new_index, profile)

    session.editable_profiles = active_profiles + benched_profiles
    session.custom_lineup_result = None
    session.touch()

    manager.clear_result(session_id)
    return _present_session(session)


def set_player_adjustment(
    session_id: str,
    *,
    player_name: str,
    adjustment: dict[str, float],
) -> SessionStateSchema:
    # -----------------------------
    # Validate allowed adjustment fields
    # -----------------------------
    allowed_fields = {
        "contact",
        "power",
        "speed",
        "plate_discipline",
    }

    unknown = set(adjustment) - allowed_fields
    if unknown:
        raise ValueError(
            f"Unsupported adjustment fields: {', '.join(sorted(unknown))}"
        )

    # -----------------------------
    # Proceed with normal logic
    # -----------------------------
    manager = get_session_manager()

    session = manager.set_player_adjustment(
        session_id,
        player_name=player_name,
        adjustment=adjustment,
    )

    manager.clear_result(session_id)

    return _present_session(session)


def clear_player_adjustment(
    session_id: str,
    *,
    player_name: str,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.clear_player_adjustment(
        session_id,
        player_name=player_name,
    )
    manager.clear_result(session_id)
    return _present_session(session)


def clear_all_adjustments(session_id: str) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.clear_all_adjustments(session_id)
    manager.clear_result(session_id)
    return _present_session(session)


def set_custom_lineup(
    session_id: str,
    *,
    lineup_names: list[str],
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.set_custom_lineup(
        session_id,
        lineup_names=lineup_names,
    )
    return _present_session(session)


def clear_custom_lineup(session_id: str) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.clear_custom_lineup(session_id)
    return _present_session(session)


def apply_lineup_to_active_roster(
    session_id: str,
    *,
    lineup_names: list[str],
    preserve_result: bool = True,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.editable_profiles:
        raise ValueError("No editable roster exists for this session.")

    benched_names = set(session.benched_player_names)
    active_profiles = [p for p in session.editable_profiles if p.name not in benched_names]
    benched_profiles = [p for p in session.editable_profiles if p.name in benched_names]

    active_map = {p.name: p for p in active_profiles}

    if not lineup_names:
        raise ValueError("lineup_names cannot be empty.")

    if len(set(lineup_names)) != len(lineup_names):
        raise ValueError("lineup_names contains duplicate player names.")

    unknown_names = [name for name in lineup_names if name not in active_map]
    if unknown_names:
        raise ValueError(
            f"Cannot apply lineup. Unknown active player(s): {', '.join(unknown_names)}"
        )

    applied_name_set = set(lineup_names)
    reordered_active = [active_map[name] for name in lineup_names]
    remaining_active = [p for p in active_profiles if p.name not in applied_name_set]

    session.editable_profiles = reordered_active + remaining_active + benched_profiles
    session.custom_lineup_result = None
    session.custom_lineup_names = []
    session.touch()

    if not preserve_result:
        manager.clear_result(session_id)

    return _present_session(session)


def evaluate_custom_lineup(
    session_id: str,
    *,
    target_runs: float = 4.0,
    n_games: int = 3000,
    seed: int = 777,
    display_name: str = "Coach Custom",
    rules: Any | None = None,
):
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.is_ready_to_run:
        raise ValueError(
            f"Session {session_id} is not ready to run custom lineup evaluation."
        )

    if not session.custom_lineup_names:
        raise ValueError("No custom lineup has been set for this session.")

    merged_adjustments = _merged_adjustments_for_session(session)

    profiles_override = _active_profiles_for_session(session)

    result = evaluate_lineup_workflow(
        data_source=session.data_source,
        lineup_names=session.custom_lineup_names,
        csv_path=session.csv_path,
        adjustments_path=session.adjustments_path,
        roster_path=session.roster_path,
        rules=rules,
        target_runs=target_runs,
        n_games=n_games,
        seed=seed,
        adjustments_by_name=merged_adjustments,
        display_name=display_name,
        profiles_override=profiles_override,
    )

    session.custom_lineup_result = result
    session.touch()

    return result


def save_current_scenario(
    session_id: str,
    *,
    name: str,
) -> SavedScenarioSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.custom_lineup_names:
        raise ValueError("No custom lineup is set for this session.")

    scenario = manager.save_scenario(
        session_id,
        name=name,
        lineup_names=session.custom_lineup_names,
        adjustments_by_name=session.coach_adjustments_by_name,
        result=session.custom_lineup_result,
    )
    return present_saved_scenario(scenario)


def list_saved_scenarios(session_id: str) -> ScenarioCollectionSchema:
    manager = get_session_manager()
    scenarios = manager.list_saved_scenarios(session_id)
    return present_saved_scenarios(scenarios)


def rename_saved_scenario(
    session_id: str,
    *,
    scenario_id: str,
    new_name: str,
) -> SavedScenarioSchema:
    manager = get_session_manager()
    scenario = manager.rename_scenario(
        session_id,
        scenario_id=scenario_id,
        new_name=new_name,
    )
    return present_saved_scenario(scenario)


def delete_saved_scenario(
    session_id: str,
    *,
    scenario_id: str,
) -> None:
    manager = get_session_manager()
    manager.delete_scenario(
        session_id,
        scenario_id=scenario_id,
    )