from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.api_service import (
    configure_gc_session,
    initialize_editable_roster,
)

from ui.session_state import reset_team_scoped_ui_state
from ui.team_entry import (
    render_team_entry_panel,
)


SAMPLE_TEAM_NAME = "Sample Team"
SAMPLE_GC_CSV_CANDIDATES = [
    Path("assets/Generic GC Stats.csv"),
]

def find_sample_gc_csv_path() -> Path | None:
    for path in SAMPLE_GC_CSV_CANDIDATES:
        if path.exists():
            return path
    return None


def seed_sample_team_for_new_user(
    *,
    session_id: str,
    team_id: str,
    user_id: str,
    user_email: str,
) -> bool:
    sample_csv = find_sample_gc_csv_path()
    if sample_csv is None:
        return False

    try:
        configure_gc_session(
            session_id,
            csv_path=sample_csv,
            adjustments_path=None,
            data_source="gc",
        )
        initialize_editable_roster(session_id)

        from core.analytics import safe_log_event

        safe_log_event(
            event_type="sample_team_seeded",
            user_id=user_id,
            user_email=user_email,
            session_id=session_id,
            team_id=team_id,
            metadata={
                "team_name": SAMPLE_TEAM_NAME,
                "source_file": str(sample_csv),
            },
        )
        return True
    except Exception:
        return False


# NOTE:
# Team persistence now lives behind SessionManager -> TeamRepository.
# Streamlit UI should avoid direct assumptions about JSON/file-backed storage.
def get_team_records_for_ui() -> list:
    try:
        from core.session_manager import get_session_manager
        from core.auth import get_current_user

        manager = get_session_manager()
        current_user = get_current_user()

        return manager.list_teams_for_user(current_user.user_id)
    except Exception:
        return []


def ensure_selected_team() -> None:
    """
    Make sure the current Streamlit session is attached to a valid team
    owned by the current user.
    """
    from core.session_manager import get_session_manager
    from core.auth import get_current_user

    manager = get_session_manager()
    current_user = get_current_user()
    if not current_user.user_id:
        raise ValueError("Authenticated user is missing a stable user_id.")
    session_obj = manager.get_session(st.session_state.optimizer_session_id)
    team_summaries = manager.list_team_summaries_for_user(current_user.user_id)

    if not team_summaries:
        team = manager.create_team(
            owner_user_id=current_user.user_id,
            team_name=SAMPLE_TEAM_NAME,
        )
        manager.attach_session_to_team(session_obj.session_id, team_id=team.team_id)

        sample_seeded = seed_sample_team_for_new_user(
            session_id=session_obj.session_id,
            team_id=team.team_id,
            user_id=current_user.user_id,
            user_email=current_user.email,
        )

        from core.analytics import safe_log_event

        safe_log_event(
            event_type="team_created",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_obj.session_id,
            team_id=team.team_id,
            metadata={
                "team_name": SAMPLE_TEAM_NAME,
                "creation_mode": "bootstrap_sample",
                "sample_seeded": sample_seeded,
            },
        )

        safe_log_event(
            event_type="team_loaded",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=session_obj.session_id,
            team_id=team.team_id,
            metadata={
                "team_name": SAMPLE_TEAM_NAME,
                "load_reason": "bootstrap_sample",
                "sample_seeded": sample_seeded,
            },
        )

        st.session_state.selected_team_id = team.team_id
        st.session_state.sync_team_selector_dropdown = True
        st.session_state.show_team_loader = False
        st.session_state.active_results_tab = "Coach Lab"
        return

    valid_team_ids = {team["team_id"] for team in team_summaries}
    selected_team_id = st.session_state.get("selected_team_id")

    if selected_team_id and selected_team_id in valid_team_ids:
        if session_obj.team_id != selected_team_id:
            manager.attach_session_to_team(session_obj.session_id, team_id=selected_team_id)

            from core.analytics import safe_log_event
            safe_log_event(
                event_type="team_loaded",
                user_id=current_user.user_id,
                user_email=current_user.email,
                session_id=session_obj.session_id,
                team_id=selected_team_id,
                metadata={
                    "load_reason": "session_state_selection",
                },
            )
        return

    if session_obj.team_id and session_obj.team_id in valid_team_ids:
        st.session_state.selected_team_id = session_obj.team_id
        st.session_state.sync_team_selector_dropdown = True
        return

    first_team = team_summaries[0]
    manager.attach_session_to_team(session_obj.session_id, team_id=first_team["team_id"])

    from core.analytics import safe_log_event

    safe_log_event(
        event_type="team_loaded",
        user_id=current_user.user_id,
        user_email=current_user.email,
        session_id=session_obj.session_id,
        team_id=first_team["team_id"],
        metadata={
            "load_reason": "fallback_first_team",
        },
    )

    st.session_state.selected_team_id = first_team["team_id"]
    st.session_state.sync_team_selector_dropdown = True


def delete_active_team_and_recover() -> None:
    """
    Delete the currently selected team, then attach the session to another team
    if one exists. If none remain, create a fresh Untitled Team.
    """
    from core.session_manager import get_session_manager
    from core.auth import get_current_user

    manager = get_session_manager()
    current_user = get_current_user()

    session_id = st.session_state.optimizer_session_id
    session_obj = manager.get_session(session_id)

    selected_team_id = st.session_state.get("selected_team_id") or session_obj.team_id
    if not selected_team_id:
        raise ValueError("No active team is selected.")

    # Important: flush current workspace before deleting so pending edits don't
    # later resurrect or overwrite team state.
    try:
        manager.flush_session_team(session_id)
    except Exception:
        pass

    team_id_to_delete = selected_team_id

    manager.delete_team_for_user(team_id_to_delete, current_user.user_id)

    # Use summaries here because render_team_switcher also uses summaries.
    remaining_summaries = manager.list_team_summaries_for_user(current_user.user_id)

    # Hard verification: deleted team should no longer appear.
    still_present = [
        team for team in remaining_summaries
        if team["team_id"] == team_id_to_delete
    ]
    if still_present:
        raise RuntimeError(
            f"Delete call returned, but team still exists: {team_id_to_delete}"
        )

    if remaining_summaries:
        non_untitled = [
            team for team in remaining_summaries
            if str(team["team_name"]).strip().lower() != "untitled team"
        ]
        next_team = non_untitled[0] if non_untitled else remaining_summaries[0]
        next_team_id = next_team["team_id"]

        manager.attach_session_to_team(session_id, team_id=next_team_id)
        st.session_state.selected_team_id = next_team_id
    else:
        new_team = manager.create_team(
            owner_user_id=current_user.user_id,
            team_name="Untitled Team",
        )
        manager.attach_session_to_team(session_id, team_id=new_team.team_id)
        st.session_state.selected_team_id = new_team.team_id

    st.session_state.sync_team_selector_dropdown = True
    reset_team_scoped_ui_state()


def prune_placeholder_untitled_team() -> None:
    """
    Remove empty placeholder 'Untitled Team' records once real teams exist.

    We keep one Untitled Team only as a bootstrap fallback when there are no
    other teams. If a real team exists, empty Untitled placeholders should go away.
    """
    from core.session_manager import get_session_manager

    manager = get_session_manager()
    from core.auth import get_current_user
    current_user = get_current_user()
    team_summaries = manager.list_team_summaries_for_user(current_user.user_id)

    if len(team_summaries) <= 1:
        return

    untitled_candidates = [
        team for team in team_summaries
        if str(team["team_name"]).strip().lower() == "untitled team"
    ]

    real_teams = [
        team for team in team_summaries
        if str(team["team_name"]).strip().lower() != "untitled team"
    ]

    if not real_teams:
        return

    for team in untitled_candidates:
        is_empty = (
            not team.editable_profiles
            and not team.saved_scenarios
            and not team.coach_adjustments_by_name
            and not team.data_source
        )

        if is_empty:
            manager.delete_team_for_user(team["team_id"], current_user.user_id)

            if st.session_state.get("selected_team_id") == team["team_id"]:
                st.session_state.selected_team_id = real_teams[0]["team_id"]


def render_team_switcher() -> None:
    """
    Small top-of-app team switcher and creator.
    """
    from core.session_manager import get_session_manager

    manager = get_session_manager()
    from core.auth import get_current_user
    current_user = get_current_user()
    session_obj = manager.get_session(st.session_state.optimizer_session_id)
    team_summaries = manager.list_team_summaries_for_user(current_user.user_id)

    if not team_summaries:
        st.warning("No teams found.")
        return

    team_options = {team["team_name"]: team["team_id"] for team in team_summaries}

    selected_team_id = st.session_state.get("selected_team_id") or session_obj.team_id

    team_names = list(team_options.keys())
    team_ids_by_name = dict(team_options)
    team_names_by_id = {team_id: team_name for team_name, team_id in team_options.items()}

    selected_team_name = team_names_by_id.get(selected_team_id, team_names[0])

    # Only force-sync the dropdown when we explicitly changed teams in code
    # (create/delete/repair), not on every rerun.
    if st.session_state.get("sync_team_selector_dropdown"):
        st.session_state.team_selector_dropdown = selected_team_name
        st.session_state.sync_team_selector_dropdown = False

    with st.container(border=True):
        st.markdown("### Team")
        top_col1, top_col2 = st.columns([2.2, 1.8])

        with top_col1:
            chosen_name = st.selectbox(
                "Active team",
                options=team_names,
                key="team_selector_dropdown",
            )

            chosen_team_id = team_ids_by_name[chosen_name]

            if chosen_team_id != selected_team_id:
                manager.flush_session_team(st.session_state.optimizer_session_id)
                manager.attach_session_to_team(
                    st.session_state.optimizer_session_id,
                    team_id=chosen_team_id,
                )

                from core.analytics import safe_log_event

                safe_log_event(
                    event_type="team_loaded",
                    user_id=current_user.user_id,
                    user_email=current_user.email,
                    session_id=st.session_state.optimizer_session_id,
                    team_id=chosen_team_id,
                    metadata={
                        "team_name": chosen_name,
                        "load_reason": "team_switcher",
                    },
                )

                st.session_state.selected_team_id = chosen_team_id
                st.session_state.sync_team_selector_dropdown = True

                reset_team_scoped_ui_state()
                st.rerun()

        with top_col2:
            if st.session_state.get("clear_new_team_name_input"):
                st.session_state.new_team_name_input = ""
                st.session_state.clear_new_team_name_input = False
            new_team_name = st.text_input(
                "Create new team",
                value=st.session_state.get("new_team_name", ""),
                key="new_team_name_input",
                placeholder="Example: My Travel Team",
            )

            if st.button(
                    "Create Team",
                    use_container_width=True,
                    key="create_team_button",
            ):
                cleaned = new_team_name.strip()
                if not cleaned:
                    st.error("Please enter a team name.")
                else:
                    new_team = manager.create_team(
                        owner_user_id=current_user.user_id,
                        team_name=cleaned,
                    )

                    manager.attach_session_to_team(
                        st.session_state.optimizer_session_id,
                        team_id=new_team.team_id,
                    )

                    from core.analytics import safe_log_event

                    safe_log_event(
                        event_type="team_created",
                        user_id=current_user.user_id,
                        user_email=current_user.email,
                        session_id=st.session_state.optimizer_session_id,
                        team_id=new_team.team_id,
                        metadata={
                            "team_name": cleaned,
                            "creation_mode": "manual_create",
                        },
                    )

                    safe_log_event(
                        event_type="team_loaded",
                        user_id=current_user.user_id,
                        user_email=current_user.email,
                        session_id=st.session_state.optimizer_session_id,
                        team_id=new_team.team_id,
                        metadata={
                            "team_name": cleaned,
                            "load_reason": "post_create_attach",
                        },
                    )

                    st.session_state.selected_team_id = new_team.team_id
                    st.session_state.sync_team_selector_dropdown = True
                    st.session_state.new_team_name = ""
                    st.session_state.clear_new_team_name_input = True

                    from ui.team_entry import bump_team_entry_expander_token

                    reset_team_scoped_ui_state()

                    st.session_state.show_team_loader = True
                    bump_team_entry_expander_token()

                    st.success(f"Created team: {cleaned}")
                    st.rerun()

        active_team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        st.caption(f"Current team: {active_team.team_name}")

        with st.expander("Manage active team", expanded=False):
            rename_col1, rename_col2 = st.columns([2, 1])

            with rename_col1:
                rename_value = st.text_input(
                    "Rename team",
                    value=active_team.team_name,
                    key="rename_team_name_input",
                )

            with rename_col2:
                st.markdown("<div style='height: 1.8rem;'></div>", unsafe_allow_html=True)
                if st.button(
                    "Save Team Name",
                    use_container_width=True,
                    key="save_team_name_button",
                ):
                    cleaned = rename_value.strip()
                    if not cleaned:
                        st.error("Team name cannot be blank.")
                    else:
                        manager.rename_team_for_user(
                            active_team.team_id,
                            owner_user_id=current_user.user_id,
                            new_name=cleaned,
                        )
                        manager.refresh_workspace_team(st.session_state.optimizer_session_id)
                        st.session_state.selected_team_id = active_team.team_id
                        st.session_state.sync_team_selector_dropdown = True
                        st.success(f"Renamed team to: {cleaned}")
                        st.rerun()

            st.markdown("---")

            if len(team_summaries) <= 1:
                st.caption("You need at least one team. Delete is disabled while only one team exists.")
            else:
                st.warning("Delete permanently removes this team, its roster, coach nudges, and saved scenarios.")

                if st.button(
                        "Delete Active Team",
                        use_container_width=True,
                        key="delete_active_team_button",
                ):
                    try:
                        with st.spinner("Deleting team..."):
                            delete_active_team_and_recover()
                        st.success("Team deleted.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete team: {exc}")

        render_team_entry_panel(session_obj)
