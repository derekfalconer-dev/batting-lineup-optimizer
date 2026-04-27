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
    build_team_from_aggregate_players,
    build_team_from_archetypes,
    build_team_from_gamechanger,
    build_team_from_gc_records,
    build_team_from_manual_traits,
    profile_from_gc_record,
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

from core.player_aggregation import (
    aggregate_player_to_gc_record,
    apply_gc_preview_decisions_to_team,
    load_incoming_gc_records_from_files,
    normalize_person_name,
    preview_incoming_gc_records_against_team,
)

from core.schemas import (
    ImportApplySummarySchema,
    ImportPreviewRowSchema,
    ImportPreviewSchema,
    ImportPreviewSummarySchema,
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
    Merge persisted/uploaded adjustments with team-level Coach Lab nudges.

    Current policy:
    - uploaded JSON adjustments are the starting point
    - persisted coach adjustments override same player/field pairs
    """
    merged: dict[str, dict[str, float]] = {}

    if session.adjustments:
        for player_name, values in session.adjustments.items():
            merged[player_name] = {str(k): float(v) for k, v in values.items()}

    if session.team_id:
        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(session.session_id)

        for player_name, values in team.coach_adjustments_by_name.items():
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

    if session.team_id and session.data_source in {"gc", "gc_plus_tweaks", "gc_merged"}:
        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(session.session_id)

        if team.aggregate_player_records:
            bundle = build_team_from_aggregate_players(
                list(team.aggregate_player_records.values()),
                adjustments_by_name=_merged_adjustments_for_session(session),
                source="team_aggregate_players",
            )
            return list(bundle.profiles)

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
    if not session.team_id:
        return None

    manager = get_session_manager()
    team = manager.get_workspace_team_for_session(session.session_id)

    if not team.editable_profiles:
        return None

    benched_names = set(team.benched_player_names)
    return [
        profile for profile in team.editable_profiles
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

    if session.team_id and csv_path:
        from core.gc_loader import load_gamechanger_records

        loaded_records = load_gamechanger_records(
            csv_path=csv_path,
            min_pa=5,
            name_format="full",
        )
        total_pa_added = sum(
            int(float(record.get("PA", 0) or 0))
            for record in loaded_records
        )

        manager.seed_team_aggregates_from_gc_records(
            session.team_id,
            records=loaded_records,
            import_event={
                "import_type": "gc_single_seed",
                "players_loaded": len(loaded_records),
                "plate_appearances_added": total_pa_added,
                "source_file": str(Path(csv_path).name),
            },
        )

        team = manager.get_team_for_session(session_id)
        team.import_history.append(
            {
                "import_type": "gc_single_seed",
                "players_loaded": len(loaded_records),
                "plate_appearances_added": total_pa_added,
                "source_file": str(Path(csv_path).name),
            }
        )
        manager.save_team(team)

    manager.clear_result(session_id)
    manager.refresh_workspace_team(session_id)
    refreshed = manager.get_session(session_id)
    return _present_session(refreshed)


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

    if session.team_id:
        total_pa_added = sum(
            int(float(record.get("PA", 0) or 0))
            for record in merged_records
        )

        manager.seed_team_aggregates_from_gc_records(
            session.team_id,
            records=list(merged_records),
            import_event={
                "import_type": "gc_merged_seed",
                "players_loaded": len(merged_records),
                "plate_appearances_added": total_pa_added,
            },
        )

        team = manager.get_team_for_session(session_id)
        team.import_history.append(
            {
                "import_type": "gc_merged_seed",
                "players_loaded": len(merged_records),
                "plate_appearances_added": total_pa_added,
            }
        )
        manager.save_team(team)

    manager.clear_result(session_id)
    manager.refresh_workspace_team(session_id)

    refreshed = manager.get_session(session_id)
    return _present_session(refreshed)


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


def preview_gamechanger_data_addition(
    session_id: str,
    *,
    csv_paths: list[str | Path],
    min_pa: int = 5,
) -> ImportPreviewSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.team_id:
        raise ValueError("Session is not attached to a team.")

    team = manager.get_workspace_team_for_session(session_id)

    incoming_records = load_incoming_gc_records_from_files(
        csv_paths=csv_paths,
        min_pa=min_pa,
        name_format="full",
    )

    preview_rows_raw = preview_incoming_gc_records_against_team(
        incoming_records=incoming_records,
        aggregate_player_records=team.aggregate_player_records,
        player_aliases=team.player_aliases,
    )

    summary = ImportPreviewSummarySchema(
        files_processed=len(csv_paths),
        incoming_records=len(preview_rows_raw),
        matched_existing_count=sum(1 for row in preview_rows_raw if row["classification"] == "matched_existing"),
        new_player_count=sum(1 for row in preview_rows_raw if row["classification"] == "new_player"),
        ambiguous_match_count=sum(1 for row in preview_rows_raw if row["classification"] == "ambiguous_match"),
        plate_appearances_available=sum(int(row["pa"]) for row in preview_rows_raw),
    )

    rows = [
        ImportPreviewRowSchema(
            incoming_name=row["incoming_name"],
            normalized_name=row["normalized_name"],
            pa=int(row["pa"]),
            source_file=str(row["source_file"]),
            classification=str(row["classification"]),
            matched_player_id=row.get("matched_player_id"),
            matched_player_name=row.get("matched_player_name"),
            suggested_action=str(row.get("suggested_action", "skip")),
            candidate_player_ids=list(row.get("candidate_player_ids", [])),
            candidate_player_names=list(row.get("candidate_player_names", [])),
        )
        for row in preview_rows_raw
    ]

    # Store raw preview payload in Streamlit session backend memory for apply step.
    session.manual_roster = preview_rows_raw
    session.touch()

    return ImportPreviewSchema(
        rows=rows,
        summary=summary,
    )


def apply_gamechanger_data_addition(
    session_id: str,
    *,
    reviewed_rows: list[dict[str, Any]],
    source_file_names: list[str] | None = None,
) -> tuple[SessionStateSchema, ImportApplySummarySchema]:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.team_id:
        raise ValueError("Session is not attached to a team.")

    team = manager.get_workspace_team_for_session(session_id)

    import_event = {
        "import_type": "gc_additional_data",
        "source_file_names": list(source_file_names or []),
    }

    applied = apply_gc_preview_decisions_to_team(
        preview_rows=reviewed_rows,
        aggregate_player_records=dict(team.aggregate_player_records),
        player_aliases=dict(team.player_aliases),
        import_event=import_event,
    )

    team.aggregate_player_records = dict(applied["aggregate_player_records"])
    team.player_aliases = dict(applied["player_aliases"])

    summary_payload = applied["summary"]
    import_history_entry = {
        "import_type": "gc_additional_data",
        "source_file_names": list(source_file_names or []),
        "incoming_records": len(reviewed_rows),
        "merged_existing_count": int(summary_payload["merged_existing_count"]),
        "added_new_count": int(summary_payload["added_new_count"]),
        "skipped_count": int(summary_payload["skipped_count"]),
        "plate_appearances_added": int(summary_payload["plate_appearances_added"]),
    }
    team.import_history.append(import_history_entry)
    manager.save_team(team)

    # Rebuild editable roster from updated aggregates.
    initialize_editable_roster(session_id)

    manager.clear_result(session_id)
    manager.flush_workspace_team(session_id)
    manager.refresh_workspace_team(session_id)
    refreshed_session = manager.get_session(session_id)

    apply_summary = ImportApplySummarySchema(
        files_processed=len(source_file_names or []),
        incoming_records=len(reviewed_rows),
        merged_existing_count=int(summary_payload["merged_existing_count"]),
        added_new_count=int(summary_payload["added_new_count"]),
        skipped_count=int(summary_payload["skipped_count"]),
        plate_appearances_added=int(summary_payload["plate_appearances_added"]),
    )

    return _present_session(refreshed_session), apply_summary


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
    optimized_names = list(result.optimized.lineup)
    manager.set_custom_lineup(session_id, lineup_names=optimized_names)
    manager.flush_workspace_team(session_id)

    try:
        from core.analytics import safe_log_event

        user_id = None
        user_email = None
        try:
            from core.auth import get_current_user
            current_user = get_current_user()
            user_id = current_user.user_id
            user_email = current_user.email
        except Exception:
            pass

        optimizer_meta = {}
        try:
            optimizer_meta = dict(
                getattr(getattr(result, "coach_summary", None), "optimizer_meta", {}) or {}
            )
        except Exception:
            optimizer_meta = {}

        optimized_mean_runs = None
        original_mean_runs = None
        mean_run_delta = None

        try:
            optimized_mean_runs = float(result.optimized.metrics.mean_runs)
            original_mean_runs = float(result.original.metrics.mean_runs)
            mean_run_delta = optimized_mean_runs - original_mean_runs
        except Exception:
            pass

        safe_log_event(
            event_type="optimize_run",
            user_id=user_id,
            user_email=user_email,
            session_id=session.session_id,
            team_id=session.team_id,
            metadata={
                "data_source": session.data_source,
                "target_runs": target_runs,
                "optimized_mean_runs": optimized_mean_runs,
                "original_mean_runs": original_mean_runs,
                "mean_run_delta": mean_run_delta,
                "optimizer_meta": optimizer_meta,
            },
        )
    except Exception:
        pass

    return result


def analyze_absent_player_shock(
    session_id: str,
    *,
    output_dir: str | Path = "output",
    target_runs: float = 4.0,
    optimizer_config: dict[str, Any] | None = None,
    rules: Any | None = None,
) -> dict[str, Any]:
    """
    Leave-one-player-out analysis.

    For each currently active player:
    - remove that player from the active profile list
    - re-run the existing optimizer workflow
    - compare against the full-roster optimized baseline

    This does not mutate bench state, lineup order, saved scenarios, or team persistence.
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.is_ready_to_run:
        raise ValueError(f"Session {session_id} is not ready to run absent-player shock analysis.")

    active_profiles = _active_profiles_for_session(session)
    if not active_profiles:
        active_profiles = _build_profiles_from_session_inputs(session)

    if len(active_profiles) < 2:
        raise ValueError("At least two active players are required for absent-player shock analysis.")

    merged_adjustments = _merged_adjustments_for_session(session)

    shock_config = dict(optimizer_config or {})
    shock_config.setdefault("mode", "fast")
    shock_config.setdefault("target_runs", target_runs)
    shock_config.setdefault("search_games", 40)
    shock_config.setdefault("refine_games", 1200)
    shock_config.setdefault("top_n", 3)
    shock_config.setdefault("seed", 42)
    shock_config.setdefault("beam_width", 8)
    shock_config.setdefault("max_rounds", 6)

    baseline = run_optimizer_workflow(
        data_source=session.data_source,
        csv_path=session.csv_path,
        adjustments_path=session.adjustments_path,
        roster_path=session.roster_path,
        rules=rules,
        output_dir=output_dir,
        target_runs=target_runs,
        optimizer_config=shock_config,
        present=True,
        adjustments_by_name=merged_adjustments,
        profiles_override=active_profiles,
    )

    baseline_metrics = baseline.optimized.metrics

    rows: list[dict[str, Any]] = []

    for profile in active_profiles:
        remaining_profiles = [
            p for p in active_profiles
            if p.name != profile.name
        ]

        if len(remaining_profiles) < 1:
            continue

        shock_result = run_optimizer_workflow(
            data_source=session.data_source,
            csv_path=session.csv_path,
            adjustments_path=session.adjustments_path,
            roster_path=session.roster_path,
            rules=rules,
            output_dir=output_dir,
            target_runs=target_runs,
            optimizer_config=shock_config,
            present=True,
            adjustments_by_name=merged_adjustments,
            profiles_override=remaining_profiles,
        )

        shock_metrics = shock_result.optimized.metrics

        rows.append(
            {
                "player": profile.name,
                "baseline_mean_runs": float(baseline_metrics.mean_runs),
                "absent_mean_runs": float(shock_metrics.mean_runs),
                "runs_lost": float(baseline_metrics.mean_runs - shock_metrics.mean_runs),
                "baseline_prob_ge_target": float(baseline_metrics.prob_ge_target),
                "absent_prob_ge_target": float(shock_metrics.prob_ge_target),
                "target_prob_lost": float(
                    baseline_metrics.prob_ge_target - shock_metrics.prob_ge_target
                ),
                "baseline_p10": float(baseline_metrics.p10_runs),
                "absent_p10": float(shock_metrics.p10_runs),
                "floor_lost": float(baseline_metrics.p10_runs - shock_metrics.p10_runs),
                "baseline_lineup": list(baseline.optimized.lineup),
                "absent_lineup": list(shock_result.optimized.lineup),
            }
        )

    rows.sort(key=lambda row: row["runs_lost"], reverse=True)

    return {
        "baseline": {
            "mean_runs": float(baseline_metrics.mean_runs),
            "prob_ge_target": float(baseline_metrics.prob_ge_target),
            "p10_runs": float(baseline_metrics.p10_runs),
            "p90_runs": float(baseline_metrics.p90_runs),
            "lineup": list(baseline.optimized.lineup),
        },
        "rows": rows,
        "target_runs": float(target_runs),
        "n_players": len(active_profiles),
        "optimizer_config": shock_config,
    }


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


def flush_workspace(session_id: str) -> SessionStateSchema:
    manager = get_session_manager()
    manager.flush_workspace_team(session_id)
    return _present_session(manager.get_session(session_id))


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

    existing = manager.get_editable_roster(session_id)
    if existing:
        return existing

    if session.data_source in {"gc", "gc_plus_tweaks", "gc_merged", "manual_archetypes", "manual_traits"}:
        profiles = _build_profiles_from_session_inputs(session)
        manager.set_editable_roster(session_id, profiles=profiles)

    return manager.get_editable_roster(session_id)


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

    roster = manager.get_editable_roster(session_id)
    existing = next((p for p in roster if p.name == player_name), None)
    if existing is None:
        raise ValueError(f"Editable roster player not found: {player_name}")

    updated_metadata = dict(getattr(existing, "metadata", {}) or {})
    updated_metadata["player_mode"] = "manual_override"

    updated_profile = replace(
        existing,
        base_traits=PlayerTraits.from_mapping(traits),
        metadata=updated_metadata,
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

    roster = manager.get_editable_roster(session_id)
    existing = next((p for p in roster if p.name == player_name), None)
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

    if not session.team_id:
        raise ValueError("Session is not attached to a team.")

    team = manager.get_workspace_team_for_session(session_id)

    benched_names = set(team.benched_player_names)
    active_profiles = [p for p in team.editable_profiles if p.name not in benched_names]
    benched_profiles = [p for p in team.editable_profiles if p.name in benched_names]

    idx = next((i for i, p in enumerate(active_profiles) if p.name == player_name), None)
    if idx is None:
        raise ValueError(f"Active roster player not found: {player_name}")

    new_index = max(0, min(int(new_index), len(active_profiles) - 1))

    profile = active_profiles.pop(idx)
    active_profiles.insert(new_index, profile)

    team.editable_profiles = active_profiles + benched_profiles
    team.touch()
    manager.mark_workspace_dirty(session_id)

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


def revert_player_to_imported_gc_baseline(
    session_id: str,
    *,
    player_name: str,
    clear_gc_adjustment: bool = True,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.team_id:
        raise ValueError("Session is not attached to a team.")

    team = manager.get_workspace_team_for_session(session_id)
    roster = manager.get_editable_roster(session_id)
    existing = next((p for p in roster if p.name == player_name), None)

    if existing is None:
        raise ValueError(f"Editable roster player not found: {player_name}")

    metadata = dict(getattr(existing, "metadata", {}) or {})
    player_id = str(metadata.get("player_id", "")).strip()

    if not player_id:
        normalized_name = normalize_person_name(player_name)
        player_id = str(team.player_aliases.get(normalized_name, "")).strip()

    if not player_id or player_id not in team.aggregate_player_records:
        raise ValueError(
            f"No imported GameChanger aggregate baseline was found for player '{player_name}'."
        )

    aggregate_record = team.aggregate_player_records[player_id]
    gc_record = aggregate_player_to_gc_record(aggregate_record)

    rebuilt_profile = profile_from_gc_record(
        gc_record,
        handedness=existing.handedness,
        metadata={
            "player_id": player_id,
            "player_mode": "gc_baseline",
            "alias_count": len(aggregate_record.merged_from_names),
            "import_event_count": len(aggregate_record.import_events),
        },
    )

    manager.replace_player_profile(
        session_id,
        player_name=player_name,
        profile=rebuilt_profile,
    )

    if clear_gc_adjustment:
        team = manager.get_workspace_team_for_session(session_id)
        team.coach_adjustments_by_name.pop(player_name, None)
        team.touch()
        manager.mark_workspace_dirty(session_id)

    manager.clear_result(session_id)
    manager.flush_workspace_team(session_id)
    return _present_session(manager.get_session(session_id))


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


def set_custom_lineup_result_payload(
    session_id: str,
    *,
    result_payload: dict[str, Any],
) -> SessionStateSchema:
    """
    Restore the backend session's custom lineup result from a cached UI payload.

    This is used when the UI reuses a previously simulated lineup result instead
    of re-running evaluate_custom_lineup(...), but we still want Save Scenario
    to persist that result.
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)
    session.custom_lineup_result = dict(result_payload)
    session.touch()
    return _present_session(session)


def apply_lineup_to_active_roster(
    session_id: str,
    *,
    lineup_names: list[str],
    preserve_result: bool = True,
) -> SessionStateSchema:
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if not session.team_id:
        raise ValueError("Session is not attached to a team.")

    team = manager.get_workspace_team_for_session(session_id)

    if not team.editable_profiles:
        raise ValueError("No editable roster exists for this session.")

    benched_names = set(team.benched_player_names)
    active_profiles = [p for p in team.editable_profiles if p.name not in benched_names]
    benched_profiles = [p for p in team.editable_profiles if p.name in benched_names]

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

    team.editable_profiles = reordered_active + remaining_active + benched_profiles
    team.touch()
    manager.mark_workspace_dirty(session_id)

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
        if session.result is not None:
            manager.set_custom_lineup(
                session_id,
                lineup_names=list(session.result.optimized.lineup),
            )
            session = manager.get_session(session_id)
        else:
            raise ValueError("No custom lineup is set for this session.")

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

    try:
        from core.analytics import safe_log_event

        user_id = None
        user_email = None
        try:
            from core.auth import get_current_user
            current_user = get_current_user()
            user_id = current_user.user_id
            user_email = current_user.email
        except Exception:
            pass

        safe_log_event(
            event_type="simulate_run",
            user_id=user_id,
            user_email=user_email,
            session_id=session.session_id,
            team_id=session.team_id,
            metadata={
                "data_source": session.data_source,
                "display_name": display_name,
                "target_runs": target_runs,
                "n_games": n_games,
                "lineup_size": len(session.custom_lineup_names),
                "lineup_names": list(session.custom_lineup_names),
            },
        )
    except Exception:
        pass

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

    if not session.team_id:
        raise ValueError("Session is not attached to a team.")

    team = manager.get_workspace_team_for_session(session_id)

    serializable_result = None
    if session.custom_lineup_result:
        raw = session.custom_lineup_result.get("custom_lineup")
        if raw:
            serializable_result = {
                # Always use the saved scenario's name as the display label in
                # charts/tables, even if the source evaluation was previously
                # labeled "Coach Custom" or "Optimized Workspace".
                "display_name": str(name),
                # Always use the currently saved lineup names as the persisted lineup.
                "lineup": list(session.custom_lineup_names),
                "mean_runs": float(raw.get("mean_runs", 0.0)),
                "median_runs": float(raw.get("median_runs", 0.0)),
                "std_runs": float(raw.get("std_runs", 0.0)),
                "prob_ge_target": float(raw.get("prob_ge_target", 0.0)),
                "sortino": float(raw.get("sortino", 0.0)),
                "p10_runs": float(raw.get("p10_runs", 0.0)),
                "p90_runs": float(raw.get("p90_runs", 0.0)),
                "n_games": int(raw.get("n_games", 0)),
                "target_runs": float(raw.get("target_runs", 0.0)),
                "runs_scored_distribution": [int(x) for x in raw.get("runs_scored_distribution", [])],
                "simulation_telemetry": dict(raw.get("simulation_telemetry", {}) or {}),
            }

    scenario = manager.save_scenario(
        session_id,
        name=name,
        lineup_names=session.custom_lineup_names,
        adjustments_by_name=team.coach_adjustments_by_name,
        result=serializable_result,
    )

    try:
        from core.analytics import safe_log_event

        user_id = None
        user_email = None
        try:
            from core.auth import get_current_user
            current_user = get_current_user()
            user_id = current_user.user_id
            user_email = current_user.email
        except Exception:
            pass

        safe_log_event(
            event_type="scenario_saved",
            user_id=user_id,
            user_email=user_email,
            session_id=session.session_id,
            team_id=session.team_id,
            metadata={
                "scenario_name": name,
                "scenario_id": scenario.scenario_id,
                "lineup_size": len(session.custom_lineup_names),
                "has_result": serializable_result is not None,
            },
        )
    except Exception:
        pass

    manager.flush_workspace_team(session_id)
    return present_saved_scenario(scenario)

    manager.flush_workspace_team(session_id)
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
    manager.flush_workspace_team(session_id)
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
    manager.flush_workspace_team(session_id)