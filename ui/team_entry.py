from pathlib import Path
from typing import Any

import streamlit as st

from core.roster_reconciliation import (
    DuplicateCandidate,
    find_possible_duplicate_candidates,
    merge_selected_records,
    reconcile_gamechanger_files,
)

from core.schemas import SessionStateSchema

from core.api_service import (
    apply_gamechanger_data_addition,
    configure_empty_manual_session,
    configure_gc_session,
    configure_reconciled_gc_session,
    initialize_editable_roster,
    preview_gamechanger_data_addition,
    reset_session_results,
)

from ui.upload_helpers import (
    save_uploaded_file,
    save_uploaded_files,
    reset_multi_gc_ui_state,
    find_backend_additional_preview_row,
)


def team_entry_expander_token() -> int:
    return int(st.session_state.get("team_entry_expander_token", 0))


def bump_team_entry_expander_token() -> None:
    st.session_state["team_entry_expander_token"] = (
        int(st.session_state.get("team_entry_expander_token", 0)) + 1
    )


def render_team_loaded_next_steps(session_state: SessionStateSchema) -> None:
    source_label_map = {
        "gc": "GameChanger roster",
        "gc_plus_tweaks": "GameChanger roster",
        "gc_merged": "Merged multi-file GameChanger roster",
        "manual_archetypes": "Manual roster",
        "manual_traits": "Manual roster",
    }
    source_label = source_label_map.get(session_state.data_source, session_state.data_source or "Roster")

    with st.container(border=True):
        st.markdown(f"### Current team source: {source_label}")
        st.caption("You are working in Coach Lab now. Change team source only if you want to replace the current roster.")

        st.markdown("**Suggested next steps**")
        st.markdown(
            """
1. Bench any absent players in **Coach Lab**  
2. Reorder the lineup if you want to test your own intuition  
3. Click **Simulate My Lineup** to test the order you built  
4. Click **Save Scenario for Charts** if you want that lineup to show up below in the comparison charts  
5. Click **Optimize Current Roster** to compare your version against the model’s recommendation  
            """
        )


def render_additional_gc_data_panel(session_state: SessionStateSchema) -> None:
    if session_state.data_source not in {"gc", "gc_plus_tweaks", "gc_merged"}:
        return

    with st.container(border=True):
        st.markdown("### Add additional GameChanger data to this team")
        st.caption(
            "Upload one or more new GameChanger CSV files. "
            "Matched players will merge into the current team. "
            "New players can be selectively added or skipped."
        )

        additional_files = st.file_uploader(
            "Additional GameChanger team stats files",
            type=["csv"],
            accept_multiple_files=True,
            key="additional_gc_csv_upload",
        )

        preview_col1, preview_col2 = st.columns([1.2, 1])

        with preview_col1:
            if st.button(
                "Preview Added Data",
                use_container_width=True,
                key="preview_additional_gc_data_btn",
            ):
                if not additional_files:
                    st.error("Please upload at least one GameChanger CSV file.")
                else:
                    try:
                        saved_paths = save_uploaded_files(
                            additional_files,
                            prefix="additional_gc",
                        )
                        preview = preview_gamechanger_data_addition(
                            st.session_state.optimizer_session_id,
                            csv_paths=saved_paths,
                        )
                        st.session_state.additional_gc_preview = preview
                        st.session_state.additional_gc_uploaded_file_names = [f.name for f in additional_files]
                        st.success("Built additional-data preview.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not preview additional GC data: {exc}")

        with preview_col2:
            if st.button(
                "Clear Preview",
                use_container_width=True,
                key="clear_additional_gc_preview_btn",
            ):
                st.session_state.additional_gc_preview = None
                st.session_state.additional_gc_uploaded_file_names = []
                st.session_state.additional_gc_apply_summary = None
                st.rerun()

        preview = st.session_state.get("additional_gc_preview")
        if preview is None:
            return

        if preview.summary is not None:
            summary = preview.summary
            stat_col1, stat_col2, stat_col3, stat_col4, stat_col5 = st.columns(5)
            stat_col1.metric("Files", summary.files_processed)
            stat_col2.metric("Incoming rows", summary.incoming_records)
            stat_col3.metric("Matched", summary.matched_existing_count)
            stat_col4.metric("New", summary.new_player_count)
            stat_col5.metric("Ambiguous", summary.ambiguous_match_count)
            st.caption(f"Plate appearances available in upload: {summary.plate_appearances_available}")

        matched_rows = [row for row in preview.rows if row.classification == "matched_existing"]
        new_rows = [row for row in preview.rows if row.classification == "new_player"]
        ambiguous_rows = [row for row in preview.rows if row.classification == "ambiguous_match"]

        if matched_rows:
            with st.expander("Matched existing players", expanded=True):
                st.caption("These are safe merges into current team players.")
                for idx, row in enumerate(matched_rows, start=1):
                    st.checkbox(
                        f"{row.incoming_name} ({row.pa} PA) → merge into {row.matched_player_name}",
                        value=True,
                        key=f"additional_gc_merge_existing_{idx}_{row.incoming_name}",
                    )

        if new_rows:
            with st.expander("New players found", expanded=True):
                st.caption("Check only the new players you want to add to this team.")
                for idx, row in enumerate(new_rows, start=1):
                    st.checkbox(
                        f"Add {row.incoming_name} ({row.pa} PA) from {Path(row.source_file).name}",
                        value=False,
                        key=f"additional_gc_add_new_{idx}_{row.incoming_name}",
                    )

        if ambiguous_rows:
            with st.expander("Possible duplicate / needs review", expanded=True):
                st.caption("Choose whether to merge into an existing player, add as new, or skip.")
                for idx, row in enumerate(ambiguous_rows, start=1):
                    action_key = f"additional_gc_ambiguous_action_{idx}_{row.incoming_name}"
                    choice = st.selectbox(
                        f"{row.incoming_name} ({row.pa} PA)",
                        options=["Skip", "Add as New"] + [f"Merge into {name}" for name in row.candidate_player_names],
                        index=0,
                        key=action_key,
                    )
                    st.caption(f"Candidates: {', '.join(row.candidate_player_names)}")

        if st.button(
            "Apply Selected Additional GC Data",
            use_container_width=True,
            type="primary",
            key="apply_additional_gc_data_btn",
        ):
            try:
                reviewed_rows: list[dict[str, Any]] = []

                matched_idx = 0
                new_idx = 0
                ambiguous_idx = 0

                for row in preview.rows:
                    raw_row = {
                        "incoming_name": row.incoming_name,
                        "normalized_name": row.normalized_name,
                        "pa": row.pa,
                        "source_file": row.source_file,
                        "classification": row.classification,
                        "matched_player_id": row.matched_player_id,
                        "matched_player_name": row.matched_player_name,
                        "suggested_action": row.suggested_action,
                        "candidate_player_ids": list(row.candidate_player_ids),
                        "candidate_player_names": list(row.candidate_player_names),
                    }

                    # Pull raw preview row from backend-stored manual_roster payload
                    backend_match = find_backend_additional_preview_row(
                        incoming_name=row.incoming_name,
                        pa=row.pa,
                        source_file=row.source_file,
                    )
                    if backend_match is None:
                        raise ValueError(f"Could not find backend preview row for {row.incoming_name}.")
                    raw_row["record"] = dict(backend_match.get("record") or {})

                    if row.classification == "matched_existing":
                        matched_idx += 1
                        checked = st.session_state.get(
                            f"additional_gc_merge_existing_{matched_idx}_{row.incoming_name}",
                            True,
                        )
                        raw_row["chosen_action"] = "merge_existing" if checked else "skip"

                    elif row.classification == "new_player":
                        new_idx += 1
                        checked = st.session_state.get(
                            f"additional_gc_add_new_{new_idx}_{row.incoming_name}",
                            False,
                        )
                        raw_row["chosen_action"] = "add_new" if checked else "skip"

                    elif row.classification == "ambiguous_match":
                        ambiguous_idx += 1
                        choice = st.session_state.get(
                            f"additional_gc_ambiguous_action_{ambiguous_idx}_{row.incoming_name}",
                            "Skip",
                        )

                        if choice == "Skip":
                            raw_row["chosen_action"] = "skip"
                        elif choice == "Add as New":
                            raw_row["chosen_action"] = "add_new"
                        elif str(choice).startswith("Merge into "):
                            selected_name = str(choice).replace("Merge into ", "", 1)
                            selected_idx = row.candidate_player_names.index(selected_name)
                            raw_row["chosen_action"] = "merge_existing"
                            raw_row["matched_player_id"] = row.candidate_player_ids[selected_idx]
                            raw_row["matched_player_name"] = selected_name
                        else:
                            raise ValueError(f"Unsupported ambiguous choice: {choice}")

                    reviewed_rows.append(raw_row)

                _, apply_summary = apply_gamechanger_data_addition(
                    st.session_state.optimizer_session_id,
                    reviewed_rows=reviewed_rows,
                    source_file_names=st.session_state.get("additional_gc_uploaded_file_names", []),
                )

                st.session_state.additional_gc_apply_summary = apply_summary
                st.session_state.additional_gc_preview = None

                from core.analytics import safe_log_event
                from core.auth import get_current_user
                from core.session_manager import get_session_manager

                current_user = get_current_user()
                manager = get_session_manager()
                session_obj = manager.get_session(st.session_state.optimizer_session_id)

                safe_log_event(
                    event_type="gc_additional_data_applied",
                    user_id=current_user.user_id,
                    user_email=current_user.email,
                    session_id=session_obj.session_id,
                    team_id=session_obj.team_id,
                    metadata={
                        "source_file_names": list(st.session_state.get("additional_gc_uploaded_file_names", [])),
                        "merged_existing_count": apply_summary.merged_existing_count,
                        "added_new_count": apply_summary.added_new_count,
                        "skipped_count": apply_summary.skipped_count,
                        "plate_appearances_added": apply_summary.plate_appearances_added,
                    },
                )

                st.success("Additional GameChanger data applied to the team.")
                st.rerun()

            except Exception as exc:
                st.error(f"Could not apply additional GC data: {exc}")

        apply_summary = st.session_state.get("additional_gc_apply_summary")
        if apply_summary is not None:
            st.markdown("#### Last additional-data import")
            st.caption(
                f"Merged into {apply_summary.merged_existing_count} existing players, "
                f"added {apply_summary.added_new_count} new players, "
                f"skipped {apply_summary.skipped_count}, "
                f"added {apply_summary.plate_appearances_added} plate appearances."
            )

def duplicate_candidate_key(candidate: DuplicateCandidate) -> str:
    left = candidate.left_normalized_name.strip().lower()
    right = candidate.right_normalized_name.strip().lower()
    ordered = sorted([left, right])
    return f"{ordered[0]}__{ordered[1]}"


def build_multi_gc_preview_rows(records: list[dict]) -> list[dict]:
    rows = []

    for record in records:
        pa = int(float(record.get("PA", 0) or 0))
        avg = float(record.get("AVG", 0.0) or 0.0)
        obp = float(record.get("OBP", 0.0) or 0.0)
        slg = float(record.get("SLG", 0.0) or 0.0)
        k_rate = float(record.get("K_RATE", 0.0) or 0.0)
        bb_rate = float(record.get("BB_RATE", 0.0) or 0.0)

        rows.append(
            {
                "Player": record.get("name", ""),
                "PA": pa,
                "AVG": f"{avg:.3f}",
                "OBP": f"{obp:.3f}",
                "SLG": f"{slg:.3f}",
                "K%": f"{k_rate:.1%}",
                "BB%": f"{bb_rate:.1%}",
                "Files": int(record.get("source_file_count", 0) or 0),
                "Merged Rows": int(record.get("merged_record_count", 0) or 0),
                "Confidence": record.get("confidence_badge", record.get("confidence", "")),
            }
        )

    rows.sort(key=lambda row: (str(row["Player"]).lower(), -int(row["PA"])))
    return rows


def build_duplicate_candidate_rows(candidates: list[DuplicateCandidate]) -> list[dict]:
    rows = []

    for candidate in candidates:
        rows.append(
            {
                "Player A": candidate.left_name,
                "Player B": candidate.right_name,
                "Why flagged": candidate.reason,
                "A files": len(candidate.left_sources),
                "B files": len(candidate.right_sources),
            }
        )

    return rows


def filter_multi_gc_preview_rows(rows: list[dict], query: str) -> list[dict]:
    query = str(query or "").strip().lower()
    if not query:
        return rows

    filtered = []
    for row in rows:
        player = str(row.get("Player", "")).lower()
        if query in player:
            filtered.append(row)
    return filtered


def filter_duplicate_candidate_rows(
    candidates: list[DuplicateCandidate],
    query: str,
) -> list[DuplicateCandidate]:
    query = str(query or "").strip().lower()
    if not query:
        return candidates

    filtered: list[DuplicateCandidate] = []
    for candidate in candidates:
        left_name = candidate.left_name.lower()
        right_name = candidate.right_name.lower()
        reason = candidate.reason.lower()

        if query in left_name or query in right_name or query in reason:
            filtered.append(candidate)

    return filtered


def apply_duplicate_merge_decisions(
    *,
    records: list[dict],
    candidates: list[DuplicateCandidate],
) -> list[dict]:
    """
    Apply coach-selected manual merges from the duplicate review UI.

    MVP behavior:
    - each candidate has a checkbox
    - checked pairs are merged
    - overlapping selections are rejected to avoid ambiguous chains
    """
    selected_pairs: list[DuplicateCandidate] = []

    for candidate in candidates:
        key = duplicate_candidate_key(candidate)
        should_merge = st.session_state.get(f"merge_dup_{key}", False)
        if should_merge:
            selected_pairs.append(candidate)

    if not selected_pairs:
        return list(records)

    used_names: set[str] = set()
    merged_name_pairs: list[set[str]] = []

    for candidate in selected_pairs:
        pair = {
            candidate.left_name.strip(),
            candidate.right_name.strip(),
        }

        if used_names.intersection(pair):
            raise ValueError(
                "You selected overlapping duplicate merges. "
                "For this MVP, merge one pair at a time when the groups overlap."
            )

        used_names.update(pair)
        merged_name_pairs.append(pair)

    remaining_records = list(records)
    new_records: list[dict] = []

    for pair in merged_name_pairs:
        merged_record = merge_selected_records(
            remaining_records,
            selected_names=sorted(pair),
        )
        new_records.append(merged_record)

        remaining_records = [
            record
            for record in remaining_records
            if str(record.get("name", "")).strip() not in pair
        ]

    final_records = remaining_records + new_records
    final_records.sort(key=lambda r: str(r.get("name", "")).lower())
    return final_records


def apply_manual_merge_selection(
    *,
    records: list[dict],
    left_player_name: str,
    right_player_name: str,
) -> list[dict]:
    """
    Merge any two coach-selected players from the current preview roster.

    This is the fallback when heuristic duplicate detection misses a pair.
    """
    left_player_name = str(left_player_name).strip()
    right_player_name = str(right_player_name).strip()

    if not left_player_name or not right_player_name:
        raise ValueError("Please select two players to merge.")

    if left_player_name == right_player_name:
        raise ValueError("Please select two different players to merge.")

    merged_record = merge_selected_records(
        records,
        selected_names=[left_player_name, right_player_name],
    )

    remaining_records = [
        record
        for record in records
        if str(record.get("name", "")).strip() not in {left_player_name, right_player_name}
    ]

    final_records = remaining_records + [merged_record]
    final_records.sort(key=lambda r: str(r.get("name", "")).lower())
    return final_records


def finalize_multi_gc_import(
    *,
    final_records: list[dict],
    file_names: list[str],
) -> None:
    configure_reconciled_gc_session(
        st.session_state.optimizer_session_id,
        merged_records=final_records,
        data_source="gc_merged",
    )

    initialize_editable_roster(st.session_state.optimizer_session_id)

    from core.analytics import safe_log_event
    from core.auth import get_current_user
    from core.session_manager import get_session_manager

    current_user = get_current_user()
    manager = get_session_manager()
    session_obj = manager.get_session(st.session_state.optimizer_session_id)

    safe_log_event(
        event_type="gc_import_multi",
        user_id=current_user.user_id,
        user_email=current_user.email,
        session_id=session_obj.session_id,
        team_id=session_obj.team_id,
        metadata={
            "file_count": len(file_names),
            "file_names": list(file_names),
            "final_player_count": len(final_records),
            "data_source": "gc_merged",
        },
    )

    st.session_state.multi_gc_import_summary = {
        "file_names": list(file_names),
        "final_player_count": len(final_records),
    }

    st.session_state.show_team_loader = False
    st.session_state.active_results_tab = "Coach Lab"
    st.session_state.coach_lab_workspace_mode = "custom"
    st.session_state.coach_lab_last_custom_eval = None
    st.session_state.last_completed_results = None


def render_team_entry_panel(session_state: SessionStateSchema) -> None:
    source_label_map = {
        "gc": "GameChanger roster",
        "gc_plus_tweaks": "GameChanger roster",
        "gc_merged": "Merged multi-file GameChanger roster",
        "manual_archetypes": "Manual roster",
        "manual_traits": "Manual roster",
    }
    source_label = source_label_map.get(
        session_state.data_source,
        session_state.data_source or "Not set",
    )

    expander_open = bool(
        st.session_state.get("show_team_loader", False)
        or not session_state.data_source
    )

    with st.expander("Manage Team Data", expanded=expander_open):
        st.caption(f"Current source: {source_label}")

        import_summary = st.session_state.get("multi_gc_import_summary")
        if session_state.data_source == "gc_merged" and import_summary:
            with st.container(border=True):
                st.markdown("#### Multi-file import summary")
                st.caption(
                    f"Built from {len(import_summary.get('file_names', []))} GameChanger files "
                    f"into {import_summary.get('final_player_count', 0)} merged players."
                )

        _render_team_entry_body(session_state)
        render_additional_gc_data_panel(session_state)


def _render_team_entry_body(session_state: SessionStateSchema) -> None:
    with st.container(border=True):
        st.markdown("### Import or build your roster")

        entry_tab_single, entry_tab_multi, entry_tab_empty = st.tabs(
            ["Single GC Import", "Multi-GC Import", "Start Empty Team"]
        )

        # -----------------------------------------------------------------
        # Single-file import
        # -----------------------------------------------------------------
        with entry_tab_single:
            st.markdown("#### Import one GameChanger CSV")
            st.caption("Use one GameChanger data file as your starting roster.")

            gc_file = st.file_uploader(
                "GameChanger team stats file",
                type=["csv"],
                key="gc_csv_upload",
            )

            if st.button(
                "Import Team",
                use_container_width=True,
                type="primary",
                key="import_gc_team_btn",
            ):
                if gc_file is None:
                    st.error("Please upload a GameChanger CSV first.")
                else:
                    try:
                        reset_multi_gc_ui_state()

                        csv_path = save_uploaded_file(gc_file, "gamechanger.csv")

                        updated = configure_gc_session(
                            session_state.session_id,
                            csv_path=csv_path,
                            adjustments_path=None,
                            data_source="gc",
                        )

                        from core.analytics import safe_log_event
                        from core.auth import get_current_user
                        from core.session_manager import get_session_manager

                        current_user = get_current_user()
                        manager = get_session_manager()
                        session_obj = manager.get_session(session_state.session_id)

                        safe_log_event(
                            event_type="gc_import_single",
                            user_id=current_user.user_id,
                            user_email=current_user.email,
                            session_id=session_obj.session_id,
                            team_id=session_obj.team_id,
                            metadata={
                                "file_name": gc_file.name,
                                "data_source": "gc",
                            },
                        )

                        initialize_editable_roster(session_state.session_id)

                        st.session_state.show_team_loader = False
                        st.session_state.active_results_tab = "Coach Lab"
                        st.session_state.coach_lab_workspace_mode = "custom"
                        st.session_state.coach_lab_last_custom_eval = None
                        st.session_state.last_completed_results = None

                        st.success("GameChanger roster imported.")
                        st.caption(f"Source mode: {updated.data_source}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not import GameChanger roster: {exc}")

        # -----------------------------------------------------------------
        # Multi-file import
        # -----------------------------------------------------------------
        with entry_tab_multi:
            st.markdown("#### Import multiple GameChanger CSVs")
            st.caption(
                "Use multiple imports to improve sample size. "
                "The app safely auto-merges exact name matches, then surfaces merge candidates for coach review."
            )

            multi_gc_files = st.file_uploader(
                "GameChanger team stats files",
                type=["csv"],
                accept_multiple_files=True,
                key="multi_gc_csv_upload",
            )

            review_col1, review_col2 = st.columns([1.2, 1])

            with review_col1:
                if st.button(
                    "Build merged roster preview",
                    use_container_width=True,
                    type="primary",
                    key="build_multi_gc_preview_btn",
                ):
                    if not multi_gc_files:
                        st.error("Please upload at least two GameChanger CSV files.")
                    elif len(multi_gc_files) < 2:
                        st.error("Please upload at least two files for the multi-GC workflow.")
                    else:
                        try:
                            saved_paths = save_uploaded_files(
                                multi_gc_files,
                                prefix="multi_gc",
                            )

                            reconciliation = reconcile_gamechanger_files(saved_paths)

                            st.session_state.multi_gc_reconciliation_result = reconciliation
                            st.session_state.multi_gc_final_records = list(reconciliation.auto_merged_records)
                            st.session_state.multi_gc_uploaded_file_names = [f.name for f in multi_gc_files]

                            st.success(
                                f"Built merged roster preview from {len(saved_paths)} files."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not build merged roster preview: {exc}")

            with review_col2:
                if st.button(
                    "Reset multi-file preview",
                    use_container_width=True,
                    key="reset_multi_gc_preview_btn",
                ):
                    reset_multi_gc_ui_state()
                    st.rerun()

            reconciliation = st.session_state.get("multi_gc_reconciliation_result")
            final_records = st.session_state.get("multi_gc_final_records")

            if reconciliation is not None and final_records is not None:
                with st.container(border=True):
                    st.markdown("##### Merge summary")
                    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)

                    st.caption(
                        f"Imported {len(reconciliation.input_files)} files → "
                        f"{reconciliation.raw_record_count} raw player rows → "
                        f"{len(final_records)} current merged players"
                    )

                    stat_col1.metric("Files", len(reconciliation.input_files))
                    stat_col2.metric("Raw player rows", reconciliation.raw_record_count)
                    stat_col3.metric("After safe auto-merge", len(reconciliation.auto_merged_records))
                    stat_col4.metric("Merge candidates", len(reconciliation.duplicate_candidates))

                    if reconciliation.auto_merge_groups:
                        with st.expander("Show safe auto-merges the app already applied", expanded=False):
                            for group in reconciliation.auto_merge_groups:
                                st.write(" + ".join(group))

                with st.container(border=True):
                    st.markdown("##### Merged roster preview")
                    st.caption(
                        "This is the current roster that will be sent into Coach Lab after any review merges."
                    )

                    preview_filter = st.text_input(
                        "Filter roster preview by player name",
                        value="",
                        key="multi_gc_preview_filter",
                        placeholder="Type part of a name like cy, max, cam, george...",
                    )

                    preview_rows = build_multi_gc_preview_rows(final_records)
                    filtered_preview_rows = filter_multi_gc_preview_rows(preview_rows, preview_filter)

                    if filtered_preview_rows:
                        st.dataframe(filtered_preview_rows, use_container_width=True, hide_index=True)
                    else:
                        st.caption("No players match that filter.")

                if reconciliation.duplicate_candidates:
                    with st.container(border=True):
                        st.markdown("Players you may want to combine")
                        st.caption(
                            "These names were not auto-merged, but the app thinks they may refer to the same player. "
                            "Only merge a pair when you are confident it is the same player."
                        )

                        candidate_filter = st.text_input(
                            "Filter merge candidates",
                            value="",
                            key="multi_gc_candidate_filter",
                            placeholder="Type part of a player name like cy, max, cam...",
                        )

                        filtered_candidates = filter_duplicate_candidate_rows(
                            reconciliation.duplicate_candidates,
                            candidate_filter,
                        )

                        if filtered_candidates:
                            duplicate_rows = build_duplicate_candidate_rows(filtered_candidates)
                            st.dataframe(duplicate_rows, use_container_width=True, hide_index=True)

                            for idx, candidate in enumerate(filtered_candidates, start=1):
                                key = duplicate_candidate_key(candidate)
                                label = (
                                    f"Merge {candidate.left_name} + {candidate.right_name} "
                                    f"({candidate.reason})"
                                )
                                st.checkbox(
                                    label,
                                    key=f"merge_dup_{key}",
                                    help="Unchecked means keep them separate for now.",
                                )

                            if st.button(
                                "Merge selected players",
                                use_container_width=True,
                                key="apply_selected_duplicate_merges_btn",
                            ):
                                try:
                                    updated_records = apply_duplicate_merge_decisions(
                                        records=list(reconciliation.auto_merged_records),
                                        candidates=reconciliation.duplicate_candidates,
                                    )
                                    updated_candidates = find_possible_duplicate_candidates(updated_records)

                                    st.session_state.multi_gc_final_records = updated_records
                                    reconciliation.auto_merged_records = list(updated_records)
                                    reconciliation.duplicate_candidates = updated_candidates
                                    st.session_state.multi_gc_reconciliation_result = reconciliation

                                    st.success("Applied selected review merges.")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Could not apply review merge decisions: {exc}")
                        else:
                            st.caption("No merge candidates match that filter.")

                else:
                    st.success("No merge candidates were found under the current conservative review rules.")
                    st.caption(
                        "That usually means the safe auto-merge pass did not see any obvious abbreviation-style name matches. "
                        "If you still know two rows are the same player, use the manual merge tool below."
                    )

                with st.container(border=True):
                    st.markdown("##### Manual merge players")
                    st.caption(
                        "Use this when you know two roster rows belong to the same player, even if the app did not surface them as a merge candidate."
                    )

                    available_names = [
                        str(record.get("name", "")).strip()
                        for record in final_records
                        if str(record.get("name", "")).strip()
                    ]
                    available_names = sorted(available_names, key=lambda x: x.lower())

                    if len(available_names) >= 2:
                        manual_merge_col1, manual_merge_col2 = st.columns(2)

                        with manual_merge_col1:
                            manual_merge_left = st.selectbox(
                                "Player A",
                                options=available_names,
                                index=0,
                                key="manual_merge_left_player",
                            )

                        with manual_merge_col2:
                            default_right_index = 1 if len(available_names) > 1 else 0
                            manual_merge_right = st.selectbox(
                                "Player B",
                                options=available_names,
                                index=default_right_index,
                                key="manual_merge_right_player",
                            )

                        if st.button(
                            "Merge selected players",
                            use_container_width=True,
                            key="manual_merge_selected_players_btn",
                        ):
                            try:
                                updated_records = apply_manual_merge_selection(
                                    records=final_records,
                                    left_player_name=manual_merge_left,
                                    right_player_name=manual_merge_right,
                                )
                                updated_candidates = find_possible_duplicate_candidates(updated_records)

                                reconciliation.auto_merged_records = list(updated_records)
                                reconciliation.duplicate_candidates = updated_candidates

                                st.session_state.multi_gc_final_records = updated_records
                                st.session_state.multi_gc_reconciliation_result = reconciliation
                                st.session_state.multi_gc_manual_merge_message = (
                                    f"Merged {manual_merge_left} + {manual_merge_right}."
                                )

                                st.success(f"Merged {manual_merge_left} + {manual_merge_right}.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Could not manually merge players: {exc}")
                    else:
                        st.caption("At least two players are needed for a manual merge.")

                with st.container(border=True):
                    st.markdown("##### Finalize import")
                    st.caption(
                        "This sends the merged roster into Coach Lab, where you can still delete stale players, "
                        "bench absences, and make coach adjustments."
                    )

                    if st.button(
                        "Use merged roster in Coach Lab",
                        use_container_width=True,
                        type="primary",
                        key="use_merged_roster_in_coach_lab_btn",
                    ):
                        try:
                            finalize_multi_gc_import(
                                final_records=final_records,
                                file_names=st.session_state.get("multi_gc_uploaded_file_names", []),
                            )
                            st.success("Merged GameChanger roster imported into Coach Lab.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not finalize merged roster import: {exc}")

        # -----------------------------------------------------------------
        # Empty-team flow
        # -----------------------------------------------------------------
        with entry_tab_empty:
            st.markdown("#### Start Empty Team")
            st.caption("Build your roster from scratch inside Coach Lab.")
            st.markdown(
                "Use this when you do not trust the scorebook, are at a draft, or want to build from scouting/archetypes."
            )

            if st.button(
                "Start Empty Roster",
                use_container_width=True,
                key="start_empty_roster_btn",
            ):
                try:
                    reset_multi_gc_ui_state()

                    configure_empty_manual_session(
                        session_state.session_id,
                        data_source="manual_archetypes",
                    )

                    initialize_editable_roster(session_state.session_id)

                    st.session_state.show_team_loader = False
                    st.session_state.active_results_tab = "Coach Lab"
                    st.session_state.coach_lab_workspace_mode = "custom"
                    st.session_state.coach_lab_last_custom_eval = None
                    st.session_state.last_completed_results = None

                    st.success("Started empty roster.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not start empty roster: {exc}")

        st.markdown("")
        if st.button("Clear Current Results", use_container_width=True, key="clear_results_from_entry_panel"):
            reset_session_results(session_state.session_id)
            st.info("Previous results cleared.")
