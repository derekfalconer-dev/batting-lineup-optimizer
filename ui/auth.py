import streamlit as st


def render_login_gate() -> None:
    with st.container(border=True):
        st.markdown("### Sign in")
        st.caption("This app requires login so each coach only sees their own teams.")

        if st.button(
            "Log in with Google",
            type="primary",
            use_container_width=True,
            key="login_with_google_button",
        ):
            st.login(provider="google")


def require_authenticated_user() -> None:
    if not getattr(st.user, "is_logged_in", False):
        render_login_gate()
        st.stop()


def render_signed_in_banner() -> None:
    from core.auth import get_current_user

    try:
        current_user = get_current_user()
    except Exception:
        return

    if not st.session_state.get("analytics_login_logged"):
        from core.analytics import safe_log_event

        safe_log_event(
            event_type="login",
            user_id=current_user.user_id,
            user_email=current_user.email,
            session_id=st.session_state.get("optimizer_session_id"),
            team_id=st.session_state.get("selected_team_id"),
            metadata={
                "display_name": current_user.display_name,
            },
        )
        st.session_state.analytics_login_logged = True

    with st.container(border=True):
        left_col, right_col = st.columns([4, 1])

        with left_col:
            st.caption(
                f"Signed in as {current_user.display_name} ({current_user.email})"
            )

        with right_col:
            if st.button("Log out", use_container_width=True, key="logout_button"):
                from core.session_manager import get_session_manager

                try:
                    manager = get_session_manager()
                    manager.flush_session_team(st.session_state.optimizer_session_id)
                except Exception:
                    # Logout should still proceed even if the explicit flush fails.
                    pass

                # Clear local UI state that should not survive user switching.
                for key in [
                    "selected_team_id",
                    "team_selector_dropdown",
                    "sync_team_selector_dropdown",
                    "new_team_name",
                    "rename_team_name_input",
                    "show_team_management",
                    "show_team_loader",
                    "coach_lab_player_profiles_cache",
                    "coach_lab_last_custom_eval",
                    "coach_lab_workspace_mode",
                    "coach_lab_saved_nudge_messages",
                    "saved_scenarios_cache",
                    "scenario_rename_target",
                    "last_completed_results",
                    "coach_lab_saved_scenario_messages",
                    "multi_gc_reconciliation_result",
                    "multi_gc_final_records",
                    "multi_gc_uploaded_file_names",
                    "multi_gc_import_summary",
                    "multi_gc_manual_merge_message",
                    "additional_gc_preview",
                    "additional_gc_uploaded_file_names",
                    "additional_gc_apply_summary",
                    "run_status_tile",
                    "analytics_login_logged",
                ]:
                    st.session_state.pop(key, None)

                st.logout()