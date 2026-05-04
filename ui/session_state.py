import streamlit as st


def clear_lineup_order_widget_state() -> None:
    keys_to_delete = [
        key for key in st.session_state.keys()
        if str(key).startswith("order_")
    ]
    for key in keys_to_delete:
        del st.session_state[key]


def reset_team_scoped_ui_state() -> None:
    st.session_state.coach_lab_player_profiles_cache = []
    st.session_state.coach_lab_last_custom_eval = None
    st.session_state.coach_lab_workspace_mode = None
    st.session_state.coach_lab_saved_nudge_messages = []
    st.session_state.saved_scenarios_cache = []
    st.session_state.scenario_rename_target = None
    st.session_state.last_completed_results = None
    st.session_state.run_status_tile = None
    st.session_state.coach_lab_saved_scenario_messages = []

    st.session_state.multi_gc_reconciliation_result = None
    st.session_state.multi_gc_final_records = None
    st.session_state.multi_gc_uploaded_file_names = []
    st.session_state.multi_gc_import_summary = None
    st.session_state.multi_gc_manual_merge_message = None

    st.session_state.additional_gc_preview = None
    st.session_state.additional_gc_uploaded_file_names = []
    st.session_state.additional_gc_apply_summary = None

    st.session_state.show_team_loader = False

    st.session_state.absent_player_shock = None
    st.session_state.absent_player_shock_status = None
    st.session_state.coach_lab_chart_compare_items = []

    clear_lineup_order_widget_state()