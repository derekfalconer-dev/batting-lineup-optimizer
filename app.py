from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable, Any
import altair as alt
import pandas as pd

import streamlit as st

from ui.styles import inject_custom_styles

from dataclasses import replace

from ui.auth import (
    render_login_gate,
    require_authenticated_user,
    render_signed_in_banner,
)

from ui.copy_blocks import (
    render_how_to_use_panel,
    render_model_limitations_panel,
    render_model_limitations,
)

from ui.upload_helpers import (
    save_uploaded_file,
    save_uploaded_files,
    reset_multi_gc_ui_state,
    find_backend_additional_preview_row,
)

from ui.session_state import (
    clear_lineup_order_widget_state,
    reset_team_scoped_ui_state,
)

from ui.team_switcher import (
    ensure_selected_team,
    render_team_switcher,
)

from ui.run_status import (
    build_direct_simulation_summary,
    build_optimizer_simulation_summary,
    clear_run_status_tile,
    set_run_status_tile,
    render_run_status_tile,
)

from ui.team_entry import (
    render_team_entry_panel,
    render_additional_gc_data_panel,
)

from ui.sidebar import render_sidebar

from core.models import (
    RulesConfig,
    GameStrategy,
    CoachingStyle,
    OpposingPitchingStrength,
    OpponentLevel,
)

from core.archetypes import PlayerArchetype, get_archetype_definition

from core.api_service import (
    add_player_from_archetype,
    apply_lineup_to_active_roster,
    bench_player,
    revert_player_to_imported_gc_baseline,
    clear_all_adjustments,
    clear_custom_lineup,
    clear_player_adjustment,
    configure_empty_manual_session,
    configure_gc_session,
    configure_reconciled_gc_session,
    create_session,
    delete_player,
    delete_saved_scenario,
    evaluate_custom_lineup,
    get_editable_roster,
    get_results,
    get_session,
    initialize_editable_roster,
    list_saved_scenarios,
    move_player_down,
    move_player_up,
    rename_saved_scenario,
    reset_session_results,
    run_optimization,
    save_current_scenario,
    set_custom_lineup,
    set_custom_lineup_result_payload,
    set_player_adjustment,
    set_player_order,
    unbench_player,
    update_player_identity,
    update_player_traits,
    apply_gamechanger_data_addition,
    preview_gamechanger_data_addition,
    analyze_absent_player_shock,
)

from core.chart_data import (
    build_bucket_bar_chart_data,
    build_comparison_table_rows,
    build_density_chart_data,
    build_survival_curve_chart_data,
)

from core.roster_reconciliation import (
    DuplicateCandidate,
    find_possible_duplicate_candidates,
    merge_selected_records,
    reconcile_gamechanger_files,
)

from core.schemas import (
    LeaderboardSchema,
    LineupEvaluationSchema,
    PlayerProfileSchema,
    SessionStateSchema,
    WorkflowResponseSchema,
)


# =============================================================================
# App config
# =============================================================================

APP_TITLE = "Batting Lineup Optimizer"
APP_SUBTITLE = "Lineup optimization for baseball coaches"
NUDGE_TOOLTIP_BY_FIELD = {
    "contact": "Small adjustment to how often the hitter puts the ball in play and reaches safely. Higher values usually mean more balls in play, more hits, and fewer strikeouts.",
    "power": "Small adjustment to extra-base hit and home run upside. Higher values increase damage potential when the hitter connects.",
    "speed": "Small adjustment to foot speed and pressure on the bases. Higher values improve stolen-base pressure and taking extra bases.",
    "plate_discipline": "Small adjustment to swing decisions and walk potential. Higher values help the hitter avoid weak swings and earn more free passes.",
}

MANUAL_OVERRIDE_TOOLTIP_BY_FIELD = {
    "contact": "How reliably this hitter puts the ball in play and gets on base via contact. Higher values generally improve batting average and reduce empty at-bats.",
    "power": "How much damage this hitter does on contact. Higher values increase doubles, triples, and home run upside.",
    "speed": "Raw running speed. Higher values help with steals, infield pressure, and taking extra bases.",
    "baserunning": "How well this player advances on the bases. Higher values improve decisions like first-to-third, scoring from second, and pressure plays.",
    "plate_discipline": "How selective and controlled the hitter is at the plate. Higher values improve at-bat quality and walk rate.",
    "strikeout_tendency": "How often the hitter swings through or gets put away. Higher values mean more strikeouts and fewer balls in play.",
    "walk_skill": "Direct walk ability. Higher values increase the chance the hitter reaches base without a hit.",
    "chase_tendency": "How often the hitter expands the zone. Higher values mean more chasing of pitches outside the strike zone.",
    "aggression": "How assertive the player is on the bases and in pressure situations. Higher values increase push-the-action behavior.",
    "clutch": 'A light situational trait intended to reflect performance under pressure. Right now it is more of a soft modifier than a major driver.',
    "sacrifice_ability": "How capable the player is at productive outs, bunts, and move-the-runner style execution. Higher values help small-ball situations.",
}


# =============================================================================
# Session + file helpers
# =============================================================================

def ensure_ui_state() -> None:
    """
    Initialize Streamlit-side state used to coordinate with the backend session.
    """
    if "optimizer_session_id" not in st.session_state:
        session_state: SessionStateSchema = create_session()
        st.session_state.optimizer_session_id = session_state.session_id

    if "work_root" not in st.session_state:
        session_id = st.session_state.optimizer_session_id
        root = Path(tempfile.gettempdir()) / "batting_lineup_optimizer" / session_id
        uploads_dir = root / "uploads"
        output_dir = root / "output"

        uploads_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        st.session_state.work_root = str(root)
        st.session_state.uploads_dir = str(uploads_dir)
        st.session_state.output_dir = str(output_dir)

    if "coach_lab_player_profiles_cache" not in st.session_state:
        st.session_state.coach_lab_player_profiles_cache = []

    if "coach_lab_last_custom_eval" not in st.session_state:
        st.session_state.coach_lab_last_custom_eval = None

    if "coach_lab_workspace_mode" not in st.session_state:
        st.session_state.coach_lab_workspace_mode = None

    if "coach_lab_saved_nudge_messages" not in st.session_state:
        st.session_state.coach_lab_saved_nudge_messages = []

    if "show_team_loader" not in st.session_state:
        st.session_state.show_team_loader = False

    if "active_results_tab" not in st.session_state:
        st.session_state.active_results_tab = "Players"

    if "saved_scenarios_cache" not in st.session_state:
        st.session_state.saved_scenarios_cache = []

    if "scenario_rename_target" not in st.session_state:
        st.session_state.scenario_rename_target = None

    if "last_completed_results" not in st.session_state:
        st.session_state.last_completed_results = None

    if "coach_lab_saved_scenario_messages" not in st.session_state:
        st.session_state.coach_lab_saved_scenario_messages = []

    if "multi_gc_reconciliation_result" not in st.session_state:
        st.session_state.multi_gc_reconciliation_result = None

    if "multi_gc_final_records" not in st.session_state:
        st.session_state.multi_gc_final_records = None

    if "multi_gc_uploaded_file_names" not in st.session_state:
        st.session_state.multi_gc_uploaded_file_names = []

    if "multi_gc_import_summary" not in st.session_state:
        st.session_state.multi_gc_import_summary = None

    if "multi_gc_manual_merge_message" not in st.session_state:
        st.session_state.multi_gc_manual_merge_message = None

    if "additional_gc_preview" not in st.session_state:
        st.session_state.additional_gc_preview = None

    if "additional_gc_uploaded_file_names" not in st.session_state:
        st.session_state.additional_gc_uploaded_file_names = []

    if "additional_gc_apply_summary" not in st.session_state:
        st.session_state.additional_gc_apply_summary = None

    if "run_status_tile" not in st.session_state:
        st.session_state.run_status_tile = None

    if "selected_team_id" not in st.session_state:
        st.session_state.selected_team_id = None

    if "new_team_name" not in st.session_state:
        st.session_state.new_team_name = ""

    if "clear_new_team_name_input" not in st.session_state:
        st.session_state.clear_new_team_name_input = False

    if "rename_team_name_input" not in st.session_state:
        st.session_state.rename_team_name_input = ""

    if "show_team_management" not in st.session_state:
        st.session_state.show_team_management = False

    if "sync_team_selector_dropdown" not in st.session_state:
        st.session_state.sync_team_selector_dropdown = False

    if "analytics_login_logged" not in st.session_state:
        st.session_state.analytics_login_logged = False

    if "team_entry_expander_token" not in st.session_state:
        st.session_state.team_entry_expander_token = 0

    if "absent_player_shock" not in st.session_state:
        st.session_state.absent_player_shock = None

    if "absent_player_shock_status" not in st.session_state:
        st.session_state.absent_player_shock_status = None

    if "coach_lab_chart_compare_items" not in st.session_state:
        st.session_state.coach_lab_chart_compare_items = []


def run_absent_player_shock_analysis(run_settings: dict) -> None:
    with st.spinner("Testing one absent-player scenario at a time, can take up to 2 min..."):
        shock_optimizer_config = dict(run_settings["optimizer_config"])
        shock_optimizer_config.update(
            {
                "search_games": 40,
                "refine_games": 1200,
                "top_n": 3,
                "beam_width": 8,
                "max_rounds": 6,
            }
        )

        shock = analyze_absent_player_shock(
            st.session_state.optimizer_session_id,
            output_dir=st.session_state.output_dir,
            target_runs=run_settings["target_runs"],
            optimizer_config=shock_optimizer_config,
            rules=RulesConfig(**run_settings["rules_config"]),
        )

        for row in shock.get("rows", []) or []:
            row["absent_lineup"] = complete_lineup_with_remaining_active_players(
                list(row.get("absent_lineup", [])),
                absent_player_name=str(row.get("player", "")),
            )

        st.session_state.absent_player_shock = shock

        n_players = int(shock.get("n_players", 0))
        st.session_state.absent_player_shock_status = (
            f"Shock chart complete — tested {n_players} missing-player scenarios "
            "plus the full-roster baseline."
        )


def complete_lineup_with_remaining_active_players(
    lineup_names: list[str],
    *,
    absent_player_name: str | None = None,
) -> list[str]:
    """
    The optimizer often returns only the simulated batting group, especially
    when continuous batting is off. For Coach Lab ordering, we still want every
    active non-benched player represented so applying a suggested order does not
    drop reserve players.
    """
    suggested = [str(name) for name in lineup_names if str(name).strip()]
    absent = str(absent_player_name or "").strip()

    editable_profiles = get_editable_roster_for_ui()
    benched_names = set(get_benched_player_names_for_ui())

    active_names = [
        profile.name
        for profile in editable_profiles
        if profile.name not in benched_names and profile.name != absent
    ]

    seen = set()
    completed = []

    for name in suggested + active_names:
        if name == absent:
            continue
        if name in seen:
            continue
        seen.add(name)
        completed.append(name)

    return completed


def render_absent_player_shock_panel(run_settings: dict) -> None:
    with st.expander("Absent Player Shock Chart", expanded=True):
        st.caption(
            """
            Quantifies how much run production drops if any starter is missing.
            See which player your lineup can least afford to lose. 
            The app removes one active player at a time, re-optimizes the remaining lineup, 
            and ranks the offensive drop-off. 
            Uses hundreds of thousands of simulations to estimate each player's
            true lineup impact. Can take up to ~2 minutes."""
        )

        if st.button(
            "Run Shock Analysis",
            use_container_width=True,
            key="shock_panel_run_absent_player_analysis",
        ):
            try:
                run_absent_player_shock_analysis(run_settings)
                st.success(st.session_state.absent_player_shock_status)
                st.rerun()
            except Exception as exc:
                st.error(f"Could not generate shock chart: {exc}")

        status = st.session_state.get("absent_player_shock_status")
        if status:
            st.success(status)

        shock = st.session_state.get("absent_player_shock")
        if not shock:
            st.info("Click **Generate Shock Chart** above in Coach Action to populate this chart.")
            return

        rows = list(shock.get("rows", []))

        for row in rows:
            row["absent_lineup"] = complete_lineup_with_remaining_active_players(
                list(row.get("absent_lineup", [])),
                absent_player_name=str(row.get("player", "")),
            )

        if not rows:
            st.info("No absent-player results available yet.")
            return

        top = rows[0]

        st.markdown("#### Who can you least afford to lose?")
        st.metric(
            label=top["player"],
            value=f"{top['runs_lost']:.2f} runs/game lost",
        )

        chart_df = pd.DataFrame(rows)
        chart_df["Runs Lost"] = chart_df["runs_lost"]
        chart_df["Player"] = chart_df["player"]
        chart_df["Offense Impact"] = -chart_df["runs_lost"]
        chart_df["Goal Odds Impact"] = -chart_df["target_prob_lost"]

        target_runs = float(shock.get("target_runs", run_settings.get("target_runs", 4.0)))

        chart = (
            alt.Chart(chart_df)
            .mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6)
            .encode(
                y=alt.Y(
                    "Player:N",
                    sort="-x",
                    title=None,
                ),
                x=alt.X(
                    "Runs Lost:Q",
                    title="Lineup impact if player is unavailable",
                ),
                tooltip=[
                    alt.Tooltip("Player:N", title="If this player is out"),
                    alt.Tooltip("Offense Impact:Q", title="Runs/game change", format="+.2f"),
                    alt.Tooltip(
                        "Goal Odds Impact:Q",
                        title=f"Odds change for {target_runs:.0f}+ runs",
                        format="+.1%",
                    ),
                ],
            )
            .properties(height=max(280, 32 * len(rows)))
        )

        st.altair_chart(chart, use_container_width=True)

        target_runs = float(shock.get("target_runs", run_settings.get("target_runs", 4.0)))

        st.markdown("#### Suggested orders if a player is out")
        st.caption(
            "Click the Use button on the row that matches tonight’s missing player. "
            "The app will bench that player and load the re-optimized order "
            "in the Active Batting Order panel above."
        )

        header_cols = st.columns([1.65, 0.75, 1.1, 1.0, 3.25])
        header_cols[0].markdown("**If This Player Is Out**")
        header_cols[1].markdown("**Apply**")
        header_cols[2].markdown("**Runs/Game**")
        header_cols[3].markdown(f"**Odds of {target_runs:.1f}+**")
        header_cols[4].markdown("**Suggested Batting Order**")

        st.markdown(
            "<hr style='margin:.2rem 0 .5rem 0; border-color:rgba(255,255,255,.22);'>",
            unsafe_allow_html=True,
        )

        for idx, row in enumerate(rows):
            player_name = str(row.get("player", "Unknown player"))
            suggested_order = complete_lineup_with_remaining_active_players(
                list(row.get("absent_lineup", [])),
                absent_player_name=player_name,
            )

            runs_change = -float(row["runs_lost"])
            chance_change = -float(row["target_prob_lost"])

            row_cols = st.columns([1.65, 0.75, 1.1, 1.0, 3.25])

            with row_cols[0]:
                st.markdown(f"**{player_name}**")

            with row_cols[1]:
                if st.button(
                    "Use",
                    key=f"apply_absent_order_{idx}_{player_editor_key(player_name)}",
                    help=f"Apply suggested order for when {player_name} is out",
                ):
                    try:
                        bench_player(
                            st.session_state.optimizer_session_id,
                            player_name=player_name,
                        )

                        apply_lineup_to_active_roster(
                            st.session_state.optimizer_session_id,
                            lineup_names=suggested_order,
                            preserve_result=True,
                        )

                        set_custom_lineup(
                            st.session_state.optimizer_session_id,
                            lineup_names=suggested_order,
                        )

                        st.session_state.coach_lab_workspace_mode = "custom"
                        clear_lineup_order_widget_state()

                        st.success(
                            f"Benched {player_name} and applied the suggested batting order."
                        )
                        st.rerun()

                    except Exception as exc:
                        st.error(f"Could not apply suggested order: {exc}")

            with row_cols[2]:
                st.markdown(f"**{runs_change:+.2f}**<br><span style='opacity:.72;'>runs/game</span>", unsafe_allow_html=True)

            with row_cols[3]:
                st.markdown(f"**{chance_change:+.1%}**")

            with row_cols[4]:
                st.markdown(
                    f"<div style='font-size:0.84rem; opacity:.82; line-height:1.38; max-width:420px;'>"
                    f"{' → '.join(suggested_order)}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            st.markdown(
                "<hr style='margin:.45rem 0 .65rem 0; border:0; border-top:1px solid rgba(255,255,255,.16);'>",
                unsafe_allow_html=True,
            )

        st.caption(
            "Each backup batting order is re-optimized with that player removed. "
            "Use it as a starting point, then adjust for defense, pitching, and game context."
        )


def short_player_label(name: str) -> str:
    """
    Compact chart label for crowded x-axes.
    Examples:
    - Cy Falconer -> C.F.
    - Rogan Johnson -> R.J.
    - Luke Tourtellott -> L.T.
    """
    parts = [p for p in str(name).strip().split() if p]
    if not parts:
        return ""

    if len(parts) == 1:
        return parts[0][:3]

    return ".".join(part[0].upper() for part in parts[:2]) + "."


def get_simulation_telemetry_from_chart_item(item) -> dict | None:
    if item is None:
        return None

    if isinstance(item, dict):
        telemetry = item.get("simulation_telemetry")
        return telemetry if isinstance(telemetry, dict) else None

    telemetry = getattr(item, "simulation_telemetry", None)
    if isinstance(telemetry, dict):
        return telemetry

    return None


def build_signature_rows_from_telemetry(
    telemetry: dict | None,
    *,
    lineup_name: str = "Lineup",
) -> list[dict]:
    if not telemetry:
        return []

    rows = []
    for row in telemetry.get("player_rows", []) or []:
        out = dict(row)
        out["Lineup"] = lineup_name
        rows.append(out)

    rows.sort(key=lambda r: int(r.get("Spot", 0)))
    return rows


def render_pressure_wave_comparison_panel(
    *,
    results: WorkflowResponseSchema | None,
) -> None:
    st.markdown("### Pressure Wave")

    with st.expander("Pressure Wave", expanded=True):

        compare_items = list(st.session_state.get("coach_lab_chart_compare_items", []))

        st.caption(
            f"Pressure Wave is plotting {len(compare_items)} selected lineup scenario"
            f"{'' if len(compare_items) == 1 else 's'}."
        )

        if not compare_items:
            saved_scenarios = get_saved_scenarios_for_ui()
            custom_eval_payload = st.session_state.get("coach_lab_last_custom_eval")

            compare_items = build_chart_compare_set(
                results=results,
                custom_eval_payload=custom_eval_payload,
                saved_scenarios=saved_scenarios,
                include_live_custom=bool(st.session_state.get("coach_lab_include_live_custom", True)),
                include_random_and_worst=False,
            )

        rows = []

        for item in compare_items:
            if isinstance(item, dict):
                lineup_name = str(item.get("display_name", "Lineup"))
            else:
                lineup_name = str(getattr(item, "display_name", "Lineup"))

            telemetry = get_simulation_telemetry_from_chart_item(item)
            lineup_rows = build_signature_rows_from_telemetry(
                telemetry,
                lineup_name=lineup_name,
            )
            rows.extend(lineup_rows)

        if len(rows) < 2:
            st.caption("Simulate or save a lineup to build the Pressure Wave chart.")
            return

        df = pd.DataFrame(rows)

        df["Spot"] = pd.to_numeric(df["Spot"], errors="coerce").astype(int)
        df["Player"] = df["Player"].astype(str)
        df = df.sort_values(["Lineup", "Spot"])

        df["Traffic Opps / 100 PA"] = (
                100.0 * pd.to_numeric(df["Pressure Events"], errors="coerce")
                / pd.to_numeric(df["PA"], errors="coerce").clip(lower=1)
        ).round(1)

        df["Run Pressure / 100 PA"] = (
                100.0 * pd.to_numeric(df["Pressure Points"], errors="coerce")
                / pd.to_numeric(df["PA"], errors="coerce").clip(lower=1)
        ).round(1)

        plotted_lineups = sorted(df["Lineup"].dropna().unique().tolist())
        if len(plotted_lineups) <= 1:
            st.info(
                "Only one lineup is currently selected for Signature Charts. "
                "Use the saved-scenario selector above to include more saved lineups, "
                "or save another scenario."
            )

        chart = (
            alt.Chart(df)
            .mark_line(point=True, interpolate="monotone")
            .encode(
                x=alt.X(
                    "Spot:O",
                    title="Batting spot",
                    sort="ascending",
                ),
                y=alt.Y(
                    "Pressure Score:Q",
                    title="Pressure score",
                    scale=alt.Scale(domain=[0, 100]),
                ),
                color=alt.Color("Lineup:N", title="Lineup"),
                tooltip=[
                    alt.Tooltip("Lineup:N"),
                    alt.Tooltip("Spot:O", title="Batting spot"),
                    alt.Tooltip("Player:N"),
                    alt.Tooltip("Pressure Score:Q", format=".1f"),
                    alt.Tooltip("Traffic Opps / 100 PA:Q", title="Traffic opps / 100 PA", format=".1f"),
                    alt.Tooltip("Run Pressure / 100 PA:Q", title="Run pressure / 100 PA", format=".1f"),
                    alt.Tooltip("PA:Q", title="Simulated PA", format=","),
                ],
            )
            .properties(height=360)
        )

        st.altair_chart(chart, use_container_width=True)

        st.caption(
            "Compares how offensive pressure is distributed through each saved lineup."
        )

        st.caption(
            "Tooltip rates are normalized per 100 simulated plate appearances. "
            "Traffic opps = how often the hitter helped create or sustain baserunner pressure. "
            "Run pressure = weighted offensive pressure contribution."
        )


def pitcher_stress_coach_read(row: dict) -> str:
    stress_pct = float(row.get("Stress Percentile", 0.0) or 0.0)
    walk_pct = float(row.get("Walk Percentile", 0.0) or 0.0)
    deep_count_pct = float(row.get("Deep Count Percentile", 0.0) or 0.0)
    extension_pct = float(row.get("Extension Percentile", 0.0) or 0.0)
    pressure_pct = float(row.get("Pressure Percentile", 0.0) or 0.0)

    scores = {
        "stress": stress_pct,
        "walk": walk_pct,
        "deep_count": deep_count_pct,
        "pressure": pressure_pct,
        "extension": extension_pct,
    }

    primary = max(scores, key=scores.get)
    top_score = scores[primary]

    if top_score < 60:
        return "Support bat"

    if primary == "stress":
        return "Strong stress creator" if stress_pct >= 87 else "Creates pitcher stress"

    if primary == "walk":
        return "Strong walk pressure" if walk_pct >= 87 else "Builds walk pressure"

    if primary == "deep_count":
        return "Strong count grinder" if deep_count_pct >= 87 else "Works deep counts"

    if primary == "pressure":
        return "Strong pressure builder" if pressure_pct >= 87 else "Builds scoring pressure"

    if primary == "extension":
        return "Strong inning extender" if extension_pct >= 87 else "Extends innings"

    return "Support bat"


def rally_ignition_coach_read(row: dict) -> str:
    run_pct = float(row.get("Run Producer Percentile", 0.0) or 0.0)
    pressure_pct = float(row.get("Pressure Percentile", 0.0) or 0.0)
    extension_pct = float(row.get("Extension Percentile", 0.0) or 0.0)
    ignition_pct = float(row.get("Ignition Percentile", 0.0) or 0.0)

    scores = {
        "run": run_pct,
        "pressure": pressure_pct,
        "extension": extension_pct,
        "ignition": ignition_pct,
    }

    primary = max(scores, key=scores.get)
    top_score = scores[primary]

    if top_score < 60:
        return "Support bat"

    if primary == "run":
        return "Strong run converter" if run_pct >= 87 else "Run converter"

    if primary == "pressure":
        return "Strong pressure builder" if pressure_pct >= 87 else "Builds scoring pressure"

    if primary == "extension":
        return "Strong rally extender" if extension_pct >= 87 else "Extends rallies"

    if primary == "ignition":
        return "Strong rally starter" if ignition_pct >= 87 else "Can start rallies"

    return "Support bat"


def select_signature_chart_rows(
    compare_items: list,
    *,
    key: str,
    label: str,
) -> tuple[str, list[dict]]:
    if not compare_items:
        return "Lineup", []

    names = []
    item_by_name = {}

    for item in compare_items:
        if isinstance(item, dict):
            name = str(item.get("display_name", "Lineup"))
        else:
            name = str(getattr(item, "display_name", "Lineup"))

        if name not in item_by_name:
            names.append(name)
            item_by_name[name] = item

    selected_name = st.selectbox(
        label,
        options=names,
        index=0,
        key=key,
    )

    selected_item = item_by_name[selected_name]
    rows = build_signature_rows_from_telemetry(
        get_simulation_telemetry_from_chart_item(selected_item),
        lineup_name=selected_name,
    )

    return selected_name, rows


def render_pitcher_stress_panel(
    compare_items: list,
    *,
    selector_key: str = "pitcher_stress_signature_scenario",
) -> None:
    st.markdown("### Pitcher Stress Meter")
    with st.expander("Pitcher Stress Meter", expanded=True):
        selected_name, signature_rows = select_signature_chart_rows(
            compare_items,
            key=selector_key,
            label="Scenario",
        )

        st.markdown(
            f"""
            <div style="
                margin: 0.35rem 0 1rem 0;
                padding: 0.8rem 1rem;
                border-radius: 12px;
                border: 1px solid rgba(120,160,255,.35);
                background: rgba(80,130,255,.12);
                font-size: 1.05rem;
                font-weight: 800;
            ">
                Viewing scenario: {selected_name}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.caption(
            "Shows how pitcher stress evolved in the simulated games. "
            "This uses simulator events: estimated pitches, traffic, reaches, "
            "and inning-extending plate appearances from thousands of simulated games."
        )

        rows = list(signature_rows)

        if len(rows) < 2:
            st.info("Add at least two active lineup players to build the Pitcher Stress Meter.")
            return

        df = pd.DataFrame(rows)

        df["Spot"] = pd.to_numeric(df["Spot"], errors="coerce").astype(int)
        df["Player"] = df["Player"].astype(str)
        df["Player Label"] = df["Player"]
        df = df.sort_values("Spot")

        df["Stress Percentile"] = df["Stress Score"].rank(pct=True) * 100
        df["Walk Percentile"] = df["Walk Rate"].rank(pct=True) * 100
        df["Deep Count Percentile"] = df["Deep Count Rate"].rank(pct=True) * 100
        df["Extension Percentile"] = df["Rally Extensions/100 PA"].rank(pct=True) * 100
        df["Pressure Percentile"] = df["Rally Damage/100 PA"].rank(pct=True) * 100


        stress_index = float(df["Stress Score"].mean())
        peak_row = df.sort_values("Stress Score", ascending=False).iloc[0]

        # Find strongest 3-spot consecutive pressure cluster
        spots = df.sort_values("Spot").reset_index(drop=True)

        best_cluster_score = -1
        best_cluster = None

        for i in range(len(spots) - 2):
            cluster = spots.iloc[i:i + 3]
            score = cluster["Stress Score"].sum()

            if score > best_cluster_score:
                best_cluster_score = score
                best_cluster = cluster

        cluster_label = " → ".join(
            str(int(x))
            for x in best_cluster["Spot"].tolist()
        )

        pressure_score = f"{stress_index:.0f}"
        toughest_player = str(peak_row["Player"])
        cluster_text = cluster_label


        metric_cols = st.columns(3)

        # Column 1 — Overall pressure score
        metric_cols[0].markdown(
            f"""
            <div style="line-height:1.2;">
                <div style="font-size:0.85rem; opacity:0.75;">Lineup Pressure on Pitchers</div>
                <div style="font-size:1.6rem; font-weight:800;">{pressure_score}/100</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Column 2 — Toughest PA (player)
        metric_cols[1].markdown(
            f"""
            <div style="line-height:1.2;">
                <div style="font-size:0.85rem; opacity:0.75;">Toughest PA</div>
                <div style="font-size:1.2rem; font-weight:700;">{toughest_player}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Column 3 — Pressure cluster
        metric_cols[2].markdown(
            f"""
            <div style="line-height:1.2;">
                <div style="font-size:0.85rem; opacity:0.75;">Pressure Cluster</div>
                <div style="font-size:1.3rem; font-weight:700;">{cluster_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if stress_index >= 70:
            diagnosis = "This lineup should make pitchers work."
        elif stress_index >= 55:
            diagnosis = "This lineup creates moderate pitcher stress."
        else:
            diagnosis = "This lineup may need more traffic and inning-extenders."

        st.success(f"Pitcher read: {diagnosis}")

        chart = (
            alt.Chart(df)
            .mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6)
            .encode(
                y=alt.Y(
                    "Player:N",
                    sort=alt.SortField(field="Spot", order="ascending"),
                    title=None,
                ),
                x=alt.X(
                    "Stress Score:Q",
                    title="Pitcher stress score by batting order",
                    scale=alt.Scale(domain=[0, 100]),
                ),
                color=alt.Color(
                    "Stress Score:Q",
                    legend=None,
                    scale=alt.Scale(scheme="reds"),
                ),
                tooltip=[
                    alt.Tooltip("Spot:O", title="Batting spot"),
                    alt.Tooltip("Player:N"),
                    alt.Tooltip("Stress Score:Q",
                                title="Stress score",
                                format=".1f"
                                ),
                    alt.Tooltip(
                        "Walk Rate:Q",
                        title="Walk rate",
                        format=".1%"
                    ),
                    alt.Tooltip(
                        "Deep Count Rate:Q",
                        title="Long at-bat rate",
                        format=".1%"
                    ),
                    alt.Tooltip(
                        "Rally Extensions/100 PA:Q",
                        title="Rallies extended /100 PA",
                        format=".1f"
                    ),
                    alt.Tooltip(
                        "Rally Damage/100 PA:Q",
                        title="Traffic cashed in /100 PA",
                        format=".1f"
                    ),
                ]
            )
            .properties(height=max(280, 32 * len(rows)))
        )

        st.altair_chart(chart, use_container_width=True)

        st.info(
            "How to read it:\n"
            "• These roles are relative to this team and this simulated lineup\n"
            "• They do not claim a player is elite versus the league, age group, or All-Star pool\n"
            "• Walk pressure = who most often forces free passes on this team\n"
            "• Long at-bat pressure = who most often pushes deeper counts on this team\n"
            "• Stress combines traffic created, innings extended, and pressure added"
        )

        pretty_rows = []
        for row in df.to_dict("records"):
            role = pitcher_stress_coach_read(row)

            pretty_rows.append(
                {
                    "Spot": row["Spot"],
                    "Player": row["Player"],
                    "Stress": row["Stress Score"],
                    "Pressure Signature": role,
                    "Walk Rate": f"{float(row.get('Walk Rate', 0.0)):.1%}",
                    "Long At-Bat Rate": f"{float(row.get('Deep Count Rate', 0.0)):.1%}",
                    "Stress Percentile": f"{float(row.get('Stress Percentile', 0.0)):.0f}",
                    "Walk Percentile": f"{float(row.get('Walk Percentile', 0.0)):.0f}",
                    "Deep Count Percentile": f"{float(row.get('Deep Count Percentile', 0.0)):.0f}",
                }
            )

        st.dataframe(pretty_rows, use_container_width=True, hide_index=True)


def render_rally_ignition_panel(
    compare_items: list,
    *,
    selector_key: str = "rally_ignition_signature_scenario",
) -> None:
    st.markdown("### Rally Ignition Map")
    with st.expander("Rally Ignition Map", expanded=True):
        st.caption(
            "Shows which hitters are most likely to start rallies, extend innings, or turn traffic into damage. "
            "This helps identify spark plugs, rally extenders, and run-producing pressure spots."
        )

        selected_name, signature_rows = select_signature_chart_rows(
            compare_items,
            key=selector_key,
            label="Scenario",
        )

        st.markdown(
            f"""
            <div style="
                margin: 0.35rem 0 1rem 0;
                padding: 0.8rem 1rem;
                border-radius: 12px;
                border: 1px solid rgba(120,160,255,.35);
                background: rgba(80,130,255,.12);
                font-size: 1.05rem;
                font-weight: 800;
            ">
                Viewing scenario: {selected_name}
            </div>
            """,
            unsafe_allow_html=True,
        )

        rows = list(signature_rows)

        if len(rows) < 2:
            st.info("Add at least two active lineup players to build the Rally Ignition Map.")
            return

        df = pd.DataFrame(rows)

        df["Spot"] = pd.to_numeric(df["Spot"], errors="coerce").astype(int)
        df["Player"] = df["Player"].astype(str)
        df["Player Label"] = df["Player"].apply(short_player_label)
        df = df.sort_values("Spot")

        if "Rally Runs" in df.columns and "PA" in df.columns:
            df["Rally Runs / 100 PA"] = (
                100.0 * pd.to_numeric(df["Rally Runs"], errors="coerce").fillna(0)
                / pd.to_numeric(df["PA"], errors="coerce").fillna(0).clip(lower=1)
            ).round(1)
        else:
            df["Rally Runs / 100 PA"] = 0.0

        df["Run Producer Percentile"] = df["Rally Runs / 100 PA"].rank(pct=True) * 100
        df["Pressure Percentile"] = df["Rally Damage/100 PA"].rank(pct=True) * 100
        df["Extension Percentile"] = df["Rally Extensions/100 PA"].rank(pct=True) * 100
        df["Ignition Percentile"] = df["Rally Starts/100 PA"].rank(pct=True) * 100

        spark_row = df.sort_values("Ignition", ascending=False).iloc[0]
        extender_row = df.sort_values("Extension", ascending=False).iloc[0]
        run_row = df.sort_values("Rally Runs / 100 PA", ascending=False).iloc[0]

        metric_cols = st.columns(3)

        metric_cols[0].markdown(
            f"**Best Rally Starter**  \n"
            f"<span style='font-size:1.15rem;font-weight:800;'>{spark_row['Player']}</span>",
            unsafe_allow_html=True,
        )
        metric_cols[1].markdown(
            f"**Best Rally Extender**  \n"
            f"<span style='font-size:1.15rem;font-weight:800;'>{extender_row['Player']}</span>",
            unsafe_allow_html=True,
        )
        metric_cols[2].markdown(
            f"**Best Run Producer**  \n"
            f"<span style='font-size:1.15rem;font-weight:800;'>{run_row['Player']}</span>  \n"
            f"<span style='opacity:.8;'>{float(run_row['Rally Runs / 100 PA']):.1f} runs / 100 PA</span>",
            unsafe_allow_html=True,
        )

        st.success(
            "Coach read: use this chart to see who starts innings, who keeps rallies alive, "
            "and who can cash in when traffic is on base."
        )

        long_df = df.melt(
            id_vars=[
                "Spot",
                "Player",
                "Player Label",
                "Rally Starts/100 PA",
                "Rally Extensions/100 PA",
                "Rally Damage/100 PA",
            ],
            value_vars=["Ignition", "Extension", "Damage"],
            var_name="Rally Role",
            value_name="Score",
        )

        label_order = df.sort_values("Spot")["Player Label"].tolist()

        chart = (
            alt.Chart(long_df)
            .mark_circle()
            .encode(
                x=alt.X(
                    "Player Label:N",
                    title="Batting order",
                    sort=label_order,
                    axis=alt.Axis(labelAngle=-45, labelAlign="right"),
                ),
                y=alt.Y(
                    "Rally Role:N",
                    title=None,
                    sort=["Ignition", "Extension", "Damage"],
                ),
                size=alt.Size(
                    "Score:Q",
                    title="Role strength",
                    scale=alt.Scale(range=[80, 1200]),
                ),
                color=alt.Color(
                    "Score:Q",
                    title="Score",
                    scale=alt.Scale(scheme="goldred", domain=[0, 100]),
                ),
                tooltip=[
                    alt.Tooltip("Spot:O", title="Batting spot"),
                    alt.Tooltip("Player:N"),
                    alt.Tooltip("Rally Role:N"),
                    alt.Tooltip("Score:Q", format=".1f"),
                    alt.Tooltip("Rally Starts/100 PA:Q", title="Ignites / 100 PA", format=".1f"),
                    alt.Tooltip("Rally Extensions/100 PA:Q", title="Extends / 100 PA", format=".1f"),
                    alt.Tooltip("Rally Damage/100 PA:Q", title="Damage / 100 PA", format=".1f"),
                    alt.Tooltip("Rally Runs / 100 PA:Q", title="Rally runs / 100 PA", format=".1f"),
                ],
            )
            .properties(height=300)
        )

        st.altair_chart(chart, use_container_width=True)

        telemetry = compare_items[0].get("simulation_telemetry", {})

        n_games = telemetry.get("n_games", 0)
        total_pa = telemetry.get("total_plate_appearances", 0)

        st.info(
            f"Analzyed {n_games:,} games and {total_pa:,} plate appearances:\n"
            "• Ignition = starts rallies\n"
            "• Extension = keeps innings alive\n"
            "• Pressure = increases scoring threat\n"
            "• Runs = converts opportunities into runs"
        )

        pretty_rows = []
        for row in df.to_dict("records"):
            coach_read = rally_ignition_coach_read(row)

            pretty_rows.append(
                {
                    "Spot": row["Spot"],
                    "Player": row["Player"],
                    "Rally Signature": coach_read,
                    "Rallies Started/100 PA": row.get("Rally Starts/100 PA", 0.0),
                    "Rallies Extended/100 PA": row.get("Rally Extensions/100 PA", 0.0),
                    "Pressure Created/100 PA": row.get("Rally Damage/100 PA", 0.0),
                    "Runs Converted/100 PA": row.get("Rally Runs / 100 PA", 0.0),
                    "Run %ile": f"{float(row.get('Run Producer Percentile', 0.0)):.0f}",
                    "Pressure %ile": f"{float(row.get('Pressure Percentile', 0.0)):.0f}",
                    "Extension %ile": f"{float(row.get('Extension Percentile', 0.0)):.0f}",
                    "Ignition %ile": f"{float(row.get('Ignition Percentile', 0.0)):.0f}",
                }
            )

        st.dataframe(pretty_rows, use_container_width=True, hide_index=True)

        st.info(
            "How to read it:\n"
            "• These roles are relative to this team and this simulated lineup\n"
            "• They do not claim a player is elite versus the league, age group, or All-Star pool\n"
            "• Ignition = starts rallies\n"
            "• Extension = keeps innings alive\n"
            "• Pressure = makes innings dangerous\n"
            "• Runs = converts rallies into runs"
        )


def team_entry_expander_token() -> int:
    return int(st.session_state.get("team_entry_expander_token", 0))


def bump_team_entry_expander_token() -> None:
    st.session_state["team_entry_expander_token"] = (
        int(st.session_state.get("team_entry_expander_token", 0)) + 1
    )


def extract_metrics(result) -> dict:
    """
    Normalize either:
    - a flat saved-scenario lineup result dict
    - an object with a .metrics attribute
    - an object whose .metrics is itself a dict-like payload
    """
    if result is None:
        return {}

    if isinstance(result, dict):
        # Saved scenarios are now stored as a flat dict:
        # {
        #   "display_name": ...,
        #   "lineup": [...],
        #   "mean_runs": ...,
        #   ...
        # }
        if "mean_runs" in result or "prob_ge_target" in result:
            return result

        nested = result.get("metrics")
        if isinstance(nested, dict):
            return nested

        return {}

    metrics_obj = getattr(result, "metrics", None)
    if metrics_obj is None:
        return {}

    if isinstance(metrics_obj, dict):
        return metrics_obj

    return {
        "mean_runs": getattr(metrics_obj, "mean_runs", 0.0),
        "median_runs": getattr(metrics_obj, "median_runs", 0.0),
        "std_runs": getattr(metrics_obj, "std_runs", 0.0),
        "prob_ge_target": getattr(metrics_obj, "prob_ge_target", 0.0),
        "sortino": getattr(metrics_obj, "sortino", 0.0),
        "p10_runs": getattr(metrics_obj, "p10_runs", 0.0),
        "p90_runs": getattr(metrics_obj, "p90_runs", 0.0),
        "n_games": getattr(metrics_obj, "n_games", 0),
        "target_runs": getattr(metrics_obj, "target_runs", 4.0),
    }


def get_backend_session() -> SessionStateSchema:
    """
    Return the backend session for the current Streamlit user.

    If Streamlit still has a session_id cached but the in-memory backend
    session registry was reset (for example after code reload), recreate
    the backend session cleanly.
    """
    session_id = st.session_state.get("optimizer_session_id")

    if not session_id:
        session_state: SessionStateSchema = create_session()
        st.session_state.optimizer_session_id = session_state.session_id
        return session_state

    try:
        return get_session(session_id)
    except ValueError:
        session_state = create_session()
        st.session_state.optimizer_session_id = session_state.session_id

        # rebuild temp working dirs for the new backend session
        root = Path(tempfile.gettempdir()) / "batting_lineup_optimizer" / session_state.session_id
        uploads_dir = root / "uploads"
        output_dir = root / "output"

        uploads_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        st.session_state.work_root = str(root)
        st.session_state.uploads_dir = str(uploads_dir)
        st.session_state.output_dir = str(output_dir)

        return session_state


def safe_get_results() -> WorkflowResponseSchema | None:
    """
    Return results if they exist; otherwise None.
    """
    try:
        return get_results(st.session_state.optimizer_session_id)
    except ValueError:
        return None


# =============================================================================
# Rendering helpers
# =============================================================================
def render_coach_lab_comparison_charts(
    *,
    results: WorkflowResponseSchema | None,
) -> None:
    saved_scenarios = get_saved_scenarios_for_ui()
    custom_eval_payload = st.session_state.get("coach_lab_last_custom_eval")

    compare_items = build_chart_compare_set(
        results=results,
        custom_eval_payload=custom_eval_payload,
        saved_scenarios=saved_scenarios,
        include_random_and_worst=False,
    )

    if len(compare_items) < 2:
        st.caption("Simulate a custom lineup or save a scenario to unlock comparison charts.")
        return

    survival = build_survival_curve_chart_data(compare_items, max_runs=14)
    buckets = build_bucket_bar_chart_data(compare_items)
    table_rows = build_comparison_table_rows(compare_items)

    st.markdown("### Scenario comparison")

    st.markdown("#### Comparison table")
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    st.markdown("#### Survival curve")
    survival_df = {"Runs": survival["x"]}
    for s in survival["series"]:
        survival_df[s["name"]] = s["y"]
    st.line_chart(survival_df, x="Runs")

    st.markdown("#### Bucket outcomes")
    bucket_rows = []
    for idx, bucket_label in enumerate(buckets["x"]):
        row = {"Bucket": bucket_label}
        for s in buckets["series"]:
            row[s["name"]] = s["y"][idx]
        bucket_rows.append(row)
    st.dataframe(bucket_rows, use_container_width=True, hide_index=True)


def _has_pitcher_matchup_context(rules_config: dict) -> bool:
    return bool(
        rules_config.get("use_opponent_scouting")
        or rules_config.get("use_manual_opponent_pitcher")
    )


def _build_generic_rules_for_matchup_baseline(rules_config: dict) -> dict:
    """
    Return a generic-opponent version of the current rules.

    Keeps game rules, strategy, scoring settings, etc.
    Removes imported pitcher scouting effects.
    """
    generic = dict(rules_config or {})

    generic["use_opponent_scouting"] = False
    generic["use_manual_opponent_pitcher"] = False

    generic["manual_pitcher_name"] = None
    generic["manual_pitcher_hand"] = None
    generic["manual_pitcher_strikeout_multiplier"] = 1.0
    generic["manual_pitcher_walk_multiplier"] = 1.0
    generic["manual_pitcher_contact_multiplier"] = 1.0
    generic["manual_pitcher_power_multiplier"] = 1.0

    generic["opponent_pitcher_name"] = None
    generic["opponent_pitcher_label"] = None
    generic["opponent_pitcher_strikeout_multiplier"] = 1.0
    generic["opponent_pitcher_walk_multiplier"] = 1.0
    generic["opponent_pitcher_contact_multiplier"] = 1.0
    generic["opponent_pitcher_power_multiplier"] = 1.0

    # Keep manual/generic opponent baseline average unless you intentionally
    # want generic baseline to include the imported opponent defense.
    generic["opponent_level"] = "average"

    return generic


def render_matchup_impact_card(
    *,
    run_settings: dict,
    optimized_result: object,
    generic_same_lineup_result: dict | None,
) -> None:
    rules_config = run_settings.get("rules_config", {}) or {}

    if not _has_pitcher_matchup_context(rules_config):
        return

    pitcher_name = rules_config.get("opponent_pitcher_name") or "selected pitcher"
    pitcher_label = rules_config.get("opponent_pitcher_label") or "Opponent profile"

    pitcher_sample_size = rules_config.get("opponent_pitcher_sample_size")
    pitcher_ip = rules_config.get("opponent_pitcher_innings_pitched")
    pitcher_bf = rules_config.get("opponent_pitcher_batters_faced")

    is_manual_pitcher = bool(rules_config.get("use_manual_opponent_pitcher"))

    if is_manual_pitcher:
        pitcher_sample_size = "Manual"

    sample_label = {
        "Low": "small",
        "Medium": "medium",
        "High": "large",
        "Manual": "coach-entered",
    }.get(str(pitcher_sample_size), "")

    sample_note = ""
    if pitcher_sample_size in {"Low", "Medium"}:
        try:
            sample_note = (
                f"\n\n⚠️ **Sample-size note:** {sample_label} data sample "
                f"({float(pitcher_ip):.1f} IP, {int(pitcher_bf)} BF). "
                "Use this as directional scouting and combine it with coach judgment."
            )
        except Exception:
            sample_note = (
                f"\n\n⚠️ **Sample-size note:** {sample_label or 'limited'} data sample. "
                "Use this as directional scouting and combine it with coach judgment."
            )

    if is_manual_pitcher:
        sample_note = (
            "\n\n📝 **Manual scouting note:** this profile is based on coach-entered "
            "pitcher traits, not imported stats."
        )

    matchup_mean = None

    try:
        matchup_mean = float(optimized_result.metrics.mean_runs)
    except Exception:
        try:
            matchup_mean = float(optimized_result.get("mean_runs"))
        except Exception:
            matchup_mean = None

    generic_mean = None
    if generic_same_lineup_result:
        raw = generic_same_lineup_result.get("custom_lineup") or generic_same_lineup_result
        try:
            generic_mean = float(raw.get("mean_runs"))
        except Exception:
            generic_mean = None

    if matchup_mean is None or generic_mean is None:
        st.info(
            f"**Why this lineup vs {pitcher_name}**\n\n"
            f"{pitcher_label}. The optimizer is using this pitcher's scouting profile."
        )
        return

    delta = matchup_mean - generic_mean
    delta_text = f"{delta:+.2f} runs/game"

    if delta < -0.05:
        impact_sentence = f"This pitcher projects to suppress scoring by about **{abs(delta):.2f} runs/game**."
    elif delta > 0.05:
        impact_sentence = f"This matchup projects to add about **{delta:.2f} runs/game** versus a generic opponent."
    else:
        impact_sentence = "This pitcher grades close to a generic opponent for this lineup."

    # Simple coach-facing bullets from the selected pitcher multipliers.
    kx = float(rules_config.get("opponent_pitcher_strikeout_multiplier", 1.0) or 1.0)
    bbx = float(rules_config.get("opponent_pitcher_walk_multiplier", 1.0) or 1.0)
    cx = float(rules_config.get("opponent_pitcher_contact_multiplier", 1.0) or 1.0)

    bullets = []

    if kx >= 1.25:
        bullets.append("Contact bats gain value because this pitcher creates extra strikeout pressure.")
    elif kx <= 0.85:
        bullets.append("More hitters can put the ball in play because this pitcher has lower strikeout pressure.")

    if bbx <= 0.80:
        bullets.append("Walk-reliant hitters lose some value because this pitcher limits free passes.")
    elif bbx >= 1.20:
        bullets.append("Patient hitters gain value because this pitcher is likely to give away baserunners.")

    if cx <= 0.90:
        bullets.append("Low-contact / high-chase bats are more exposed in this matchup.")
    elif cx >= 1.10:
        bullets.append("Contact-oriented hitters may benefit because this profile allows more balls in play.")

    px = float(rules_config.get("opponent_pitcher_power_multiplier", 1.0) or 1.0)
    if px <= 0.90:
        bullets.append("Power may be slightly muted, so stringing quality at-bats together matters more.")
    elif px >= 1.12:
        bullets.append("Damage bats gain value because this profile allows harder contact.")

    if is_manual_pitcher:
        bullets.append(
            "Manual scouting is treated as coach intelligence, so re-run if your read on the pitcher changes.")

    if not bullets:
        bullets.append("The lineup stays close to normal because this pitcher profile is fairly neutral.")

    bullet_md = "\n".join(f"- {b}" for b in bullets)

    st.info(
        f"### Why this lineup vs {pitcher_name}\n"
        f"**{pitcher_label}**{sample_note}\n\n"
        f"Expected scoring vs this pitcher: **{matchup_mean:.2f} runs/game**  \n"
        f"Generic opponent baseline, same lineup: **{generic_mean:.2f} runs/game**  \n"
        f"Impact: **{delta_text}**\n\n"
        f"{impact_sentence}\n\n"
        f"**Why the lineup may change:**\n"
        f"{bullet_md}"
    )


def render_run_section(run_settings: dict) -> None:
    st.markdown("## Run Lineup Analysis")
    st.caption("Once your team is loaded, run the optimizer to compare batting orders.")

    session_state = get_backend_session()

    with st.container(border=True):
        if not session_state.data_source:
            st.info("Start by loading a team above.")
            return

        status_col1, status_col2 = st.columns([2, 1])

        with status_col1:
            st.markdown("### Ready to Analyze")
            st.write(f"Current team source: **{session_state.data_source}**")
            st.write(f"Scoring goal: **{run_settings['target_runs']:.1f} runs per game**")

            rules_context = run_settings.get("rules_config", {})
            if _has_pitcher_matchup_context(rules_context):
                pitcher_name = rules_context.get("opponent_pitcher_name") or "selected pitcher"
                pitcher_label = rules_context.get("opponent_pitcher_label") or "opponent profile"

                if rules_context.get("use_manual_opponent_pitcher"):
                    st.write(f"Manual opponent pitcher: **{pitcher_name}** — {pitcher_label}")
                else:
                    st.write(f"Opponent scouting: **{pitcher_name}** — {pitcher_label}")

        with status_col2:
            run_clicked = st.button(
                "Run Optimization",
                type="primary",
                use_container_width=True,
            )

        if run_clicked:
            with st.spinner("Running simulations and building recommendations..."):
                try:
                    rules = RulesConfig(**run_settings["rules_config"])

                    run_optimization(
                        st.session_state.optimizer_session_id,
                        output_dir=st.session_state.output_dir,
                        target_runs=run_settings["target_runs"],
                        optimizer_config=run_settings["optimizer_config"],
                        rules=rules,
                    )

                    refreshed_results = safe_get_results()
                    st.session_state.last_completed_results = refreshed_results
                    st.session_state.active_results_tab = "Coach View"

                    from core.analytics import safe_log_event
                    from core.auth import get_current_user
                    from core.session_manager import get_session_manager

                    current_user = get_current_user()
                    manager = get_session_manager()
                    session_obj = manager.get_session(st.session_state.optimizer_session_id)

                    optimizer_meta = {}
                    if refreshed_results and getattr(refreshed_results, "coach_summary", None):
                        optimizer_meta = dict(getattr(refreshed_results.coach_summary, "optimizer_meta", {}) or {})

                    st.success("Analysis complete.")

                    generic_same_lineup_result = None

                    try:
                        rules_config = run_settings.get("rules_config", {}) or {}

                        if _has_pitcher_matchup_context(rules_config):
                            generic_rules_config = _build_generic_rules_for_matchup_baseline(rules_config)

                            generic_same_lineup_result = evaluate_custom_lineup(
                                st.session_state.optimizer_session_id,
                                target_runs=float(run_settings["target_runs"]),
                                n_games=int(run_settings["optimizer_config"].get("refine_games", 3000)),
                                seed=int(run_settings["optimizer_config"].get("seed", 42)) + 909,
                                display_name="Generic Opponent Baseline",
                                rules=RulesConfig(**generic_rules_config),
                            )

                    except Exception as exc:
                        generic_same_lineup_result = None
                        st.warning(f"Could not compute generic opponent baseline: {exc}")

                    st.session_state.matchup_impact_generic_baseline = generic_same_lineup_result

                    st.rerun()
                except Exception as exc:
                    st.error(f"Optimization failed: {exc}")


def get_coach_lab_profiles(results: WorkflowResponseSchema | None) -> list[PlayerProfileSchema]:
    if results is not None and results.player_profiles:
        st.session_state.coach_lab_player_profiles_cache = results.player_profiles
        return list(results.player_profiles)

    cached = st.session_state.get("coach_lab_player_profiles_cache", [])
    return list(cached)


def get_saved_scenarios_for_ui() -> list:
    """
    Prefer workspace-backed saved scenarios to avoid unnecessary service/repository
    round-trips during Streamlit reruns.
    """
    try:
        from core.session_manager import get_session_manager

        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        scenarios = list(team.saved_scenarios)
        st.session_state.saved_scenarios_cache = scenarios
        return scenarios
    except Exception:
        pass

    try:
        collection = list_saved_scenarios(st.session_state.optimizer_session_id)
        scenarios = list(collection.scenarios)
        st.session_state.saved_scenarios_cache = scenarios
        return scenarios
    except Exception:
        return list(st.session_state.get("saved_scenarios_cache", []))


def get_editable_roster_for_ui() -> list:
    """
    Return the current in-session editable roster.
    Seed it from source inputs if needed.
    """
    try:
        roster = get_editable_roster(st.session_state.optimizer_session_id)
        if roster:
            return roster
    except Exception:
        pass

    try:
        initialize_editable_roster(st.session_state.optimizer_session_id)
        return get_editable_roster(st.session_state.optimizer_session_id)
    except Exception:
        return []


def get_profile_adjustment_dict(profile) -> dict[str, float]:
    adjustment = getattr(profile, "adjustment", None)
    if adjustment is None:
        return {}
    if hasattr(adjustment, "as_dict"):
        return adjustment.as_dict()
    if isinstance(adjustment, dict):
        return dict(adjustment)
    return {}


def build_roster_manager_rows(editable_profiles: list) -> list[dict]:
    try:
        from core.session_manager import get_session_manager

        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        benched_names = set(team.benched_player_names)
    except Exception:
        benched_names = set()

    rows = []
    for profile in editable_profiles:
        traits = getattr(profile, "effective_traits", getattr(profile, "base_traits", None))
        rows.append(
            {
                "Player": profile.name,
                "Archetype": str(getattr(profile.archetype, "value", profile.archetype)).replace("_", " ").title(),
                "Status": "Benched" if profile.name in benched_names else "Active",
                "Contact": round(getattr(traits, "contact", 0.0), 1),
                "Power": round(getattr(traits, "power", 0.0), 1),
                "Speed": round(getattr(traits, "speed", 0.0), 1),
                "Discipline": round(getattr(traits, "plate_discipline", 0.0), 1),
            }
        )

    rows.sort(key=lambda r: (r["Status"] != "Active", r["Player"]))
    return rows


def get_benched_player_names_for_ui() -> list[str]:
    try:
        from core.session_manager import get_session_manager

        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        return list(team.benched_player_names)
    except Exception:
        return []


def get_current_active_lineup_names(
    editable_profiles: list,
    *,
    continuous_batting: bool,
    lineup_size: int = 9,
) -> list[str]:
    benched_names = set(get_benched_player_names_for_ui())
    active_names = [p.name for p in editable_profiles if p.name not in benched_names]

    if continuous_batting:
        return active_names

    return active_names[: min(lineup_size, len(active_names))]


def dataframe_height_for_rows(
    n_rows: int,
    *,
    row_height: int = 35,
    header_height: int = 38,
    min_height: int = 120,
    max_height: int = 520,
) -> int:
    """
    Compute a reasonable dataframe height so roster rows are actually visible.
    """
    estimated = header_height + (max(n_rows, 1) * row_height)
    return max(min_height, min(max_height, estimated))


def player_editor_key(name: str) -> str:
    return (
        name.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(".", "_")
    )


def player_editor_reset_token(player_name: str) -> int:
    state_key = f"player_editor_reset_token__{player_editor_key(player_name)}"
    return int(st.session_state.get(state_key, 0))


def bump_player_editor_reset_token(player_name: str) -> None:
    state_key = f"player_editor_reset_token__{player_editor_key(player_name)}"
    st.session_state[state_key] = int(st.session_state.get(state_key, 0)) + 1


def get_profile_metadata(profile) -> dict:
    metadata = getattr(profile, "metadata", None)
    return dict(metadata or {}) if isinstance(metadata, dict) else {}


def profile_confidence(profile) -> str | None:
    metadata = get_profile_metadata(profile)
    value = metadata.get("confidence")
    if value not in (None, ""):
        return str(value)

    pa = profile_pa(profile)
    if pa is None:
        return None
    if pa < 15:
        return "Low"
    if pa < 40:
        return "Medium"
    return "High"


def profile_pa(profile) -> int | None:
    metadata = get_profile_metadata(profile)
    value = metadata.get("pa")
    if value in (None, "", "-"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def profile_source_file_count(profile) -> int | None:
    metadata = get_profile_metadata(profile)
    value = metadata.get("source_file_count")
    if value in (None, "", "-"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def profile_player_mode(profile) -> str:
    metadata = get_profile_metadata(profile)
    explicit = str(metadata.get("player_mode", "")).strip().lower()

    if explicit in {"manual_override", "gc_baseline", "gc_adjusted"}:
        return explicit

    source_mode = str(getattr(getattr(profile, "source_mode", None), "value", "") or "").strip().lower()
    source = str(getattr(profile, "source", "") or "").strip().lower()

    if source_mode in {"manual_archetype", "manual_traits", "manual_profile"}:
        return "manual_override"

    if source in {"manual", "manual_archetype", "manual_traits", "manual_override"}:
        return "manual_override"

    if source_mode == "gc_nudged":
        return "gc_adjusted"

    return "gc_baseline"


def profile_player_mode_label(profile) -> str:
    mode = profile_player_mode(profile)
    if mode == "manual_override":
        return "Manual"
    if mode == "gc_adjusted":
        return "GC+Adj"
    return "GC"


def profile_player_data_source_label(profile) -> str:
    mode = profile_player_mode(profile)

    if mode == "manual_override":
        archetype_value = str(getattr(getattr(profile, "archetype", None), "value", getattr(profile, "archetype", "unknown")))
        return f"Manual override • Archetype baseline: {format_archetype_label(archetype_value)}"

    source_file_count = profile_source_file_count(profile)
    pa_value = profile_pa(profile)

    bits = ["GameChanger baseline"]
    if pa_value is not None:
        bits.append(f"{pa_value} PA")
    if source_file_count is not None:
        bits.append(f"{source_file_count} file{'s' if source_file_count != 1 else ''}")

    return " • ".join(bits)


def profile_confidence_action(profile) -> str | None:
    metadata = get_profile_metadata(profile)
    value = metadata.get("confidence_action")
    if value not in (None, ""):
        return str(value)

    pa = profile_pa(profile)
    if pa is None:
        return None
    if pa < 15:
        return (
            "Use the imported stats as a starting point, then manually inspect and tweak "
            "this player in Coach Lab before relying heavily on the recommendation."
        )
    if pa < 40:
        return (
            "Usable directional input. Coach review is still a good idea if this player’s "
            "recent quality or role has changed."
        )
    return (
        "Stronger baseline on the roster. Usually fine to use as-is unless you know "
        "something recent has changed."
    )


def profile_confidence_badge(profile) -> str:
    metadata = get_profile_metadata(profile)
    badge = metadata.get("confidence_badge")
    if badge not in (None, ""):
        return str(badge)

    confidence = profile_confidence(profile)
    if confidence == "Low":
        return "🔴 Low"
    if confidence == "Medium":
        return "🟡 Medium"
    if confidence == "High":
        return "🟢 High"

    # fallback from PA if confidence metadata is missing
    pa = profile_pa(profile)
    if pa is None:
        return "—"
    if pa < 15:
        return "🔴 Low"
    if pa < 40:
        return "🟡 Medium"
    return "🟢 High"


def build_confidence_summary(editable_profiles: list) -> tuple[int, int, int]:
    low = 0
    medium = 0
    high = 0

    for profile in editable_profiles:
        confidence = profile_confidence(profile)
        if confidence == "Low":
            low += 1
        elif confidence == "Medium":
            medium += 1
        elif confidence == "High":
            high += 1

    return low, medium, high


def clear_player_editor_widget_state(player_name: str) -> None:
    """
    Clear cached Streamlit widget state for one player's editor so sliders
    rebuild from the latest backend profile values on the next rerun.
    """
    key = player_editor_key(player_name)

    prefixes = [
        "edit_name",
        "edit_archetype",
        "edit_handedness",
        "nudge_contact",
        "nudge_power",
        "nudge_speed",
        "nudge_plate_discipline",
        "contact",
        "power",
        "speed",
        "baserunning",
        "plate_discipline",
        "strikeout",
        "strikeout_tendency",
        "walk",
        "walk_skill",
        "chase",
        "chase_tendency",
        "aggression",
        "clutch",
        "sacrifice",
        "sacrifice_ability",
    ]

    for prefix in prefixes:
        st.session_state.pop(f"{prefix}_{key}", None)


def apply_optimized_lineup_to_dashboard(
    optimized_lineup_names: list[str],
    *,
    continuous_batting: bool,
    lineup_size: int,
) -> None:
    """
    Apply the latest optimized lineup to the editable Coach Dashboard order.

    If continuous batting is off, the optimized lineup will usually only contain
    the active top-N lineup. Any remaining active players stay active and
    remain after the optimized group in their current relative order.
    """
    editable_profiles = get_editable_roster_for_ui()
    benched_names = set(get_benched_player_names_for_ui())
    active_names = [p.name for p in editable_profiles if p.name not in benched_names]

    if not active_names:
        raise ValueError("No active players are available.")

    optimized_name_set = set(optimized_lineup_names)
    active_name_set = set(active_names)

    if not optimized_name_set.issubset(active_name_set):
        missing = sorted(optimized_name_set - active_name_set)
        raise ValueError(
            f"Optimized lineup contains players not currently active: {', '.join(missing)}"
        )

    desired_active_order = list(optimized_lineup_names) + [
        name for name in active_names if name not in optimized_name_set
    ]

    apply_lineup_to_active_roster(
        st.session_state.optimizer_session_id,
        lineup_names=desired_active_order,
        preserve_result=True,
    )

    clear_lineup_order_widget_state()


def render_expandable_player_editor(
    profile,
    *,
    is_benched: bool,
    slot_number: int | None = None,
    current_position: int | None = None,
    total_active_players: int | None = None,
) -> None:
    """
    Unified Coach Dashboard row + expandable editor.
    """
    key = player_editor_key(profile.name)

    reset_token = player_editor_reset_token(profile.name)

    def rk(name: str) -> str:
        return f"{name}_{key}_{reset_token}"

    archetype_value = getattr(profile.archetype, "value", str(profile.archetype))
    handedness_value = getattr(profile.handedness, "value", str(profile.handedness))

    base_traits = getattr(profile, "base_traits")

    current_adj = get_profile_adjustment_dict(profile)

    raw_effective_traits = getattr(profile, "effective_traits", None)
    if raw_effective_traits is None:
        effective_traits = base_traits
    elif profile_player_mode(profile) == "gc_baseline" and not any(float(v) != 0.0 for v in current_adj.values()):
        effective_traits = base_traits
    else:
        effective_traits = raw_effective_traits

    nudge_contact = int(round(float(current_adj.get("contact", 0.0))))
    nudge_power = int(round(float(current_adj.get("power", 0.0))))
    nudge_speed = int(round(float(current_adj.get("speed", 0.0))))
    nudge_plate_discipline = int(round(float(current_adj.get("plate_discipline", 0.0))))

    slot_label = f"#{slot_number}" if slot_number is not None else "Bench"
    status_label = "Benched" if is_benched else "Active"

    archetype_label = format_archetype_label(archetype_value)

    pa_value = profile_pa(profile)
    source_file_count = profile_source_file_count(profile)
    confidence_badge = profile_confidence_badge(profile)

    mode_label = profile_player_mode_label(profile)

    confidence_bits = [mode_label]
    if pa_value is not None:
        confidence_bits.append(f"PA {pa_value}")
    if source_file_count is not None:
        confidence_bits.append(f"Files {source_file_count}")
    if confidence_badge != "—":
        confidence_bits.append(confidence_badge)

    confidence_text = " | ".join(confidence_bits)
    confidence_prefix = f"{confidence_text} | " if confidence_text else ""

    summary = (
        f"{slot_label} • {profile.name} • {archetype_label} • {status_label} | "
        f"{confidence_prefix}"
        f"C {effective_traits.contact:.0f} P {effective_traits.power:.0f} "
        f"S {effective_traits.speed:.0f} Disc {effective_traits.plate_discipline:.0f}"
    )

    with st.expander(summary, expanded=False):

        confidence_value = profile_confidence(profile)
        confidence_action = profile_confidence_action(profile)

        st.caption(f"Data source: {profile_player_data_source_label(profile)}")

        if confidence_value == "Low" and confidence_action:
            st.info(
                f"{profile_confidence_badge(profile)} {confidence_action} "
                "Recommended workflow: inspect this player, make a small trait tweak or choose a better-fit archetype if needed, then re-simulate or re-optimize."
            )
        elif confidence_value == "Medium" and confidence_action:
            st.caption(f"{profile_confidence_badge(profile)} {confidence_action}")

        if profile_player_mode(profile) == "manual_override":
            st.info(
                "This player is currently using a Manual Override. "
                "The trait sliders below are the active baseline for this player."
            )
        elif profile_player_mode(profile) == "gc_adjusted":
            st.info(
                "This player is using imported GameChanger data plus a saved GC Adjustment (nudge). "
                "The nudge section above persists across future data rebuilds."
            )
        else:
            st.caption(
                "This player is currently on the imported GameChanger baseline. "
                "Use the GC Adjustment section for small persistent nudges, or use the trait sliders below for a Manual Override."
            )

        action_cols = st.columns([0.9, 1, 1, 1])

        if not is_benched:
            with action_cols[0]:
                if current_position is not None and total_active_players is not None:
                    order_widget_key = f"order_{key}"
                    lineup_spot_options = list(range(1, total_active_players + 1))

                    # Only initialize from the current backend order if this widget
                    # has not been created yet on this page state.
                    if order_widget_key not in st.session_state:
                        st.session_state[order_widget_key] = current_position

                    # If roster shape changed, keep widget value valid.
                    if st.session_state[order_widget_key] not in lineup_spot_options:
                        st.session_state[order_widget_key] = current_position

                    current_option_index = lineup_spot_options.index(st.session_state[order_widget_key])

                    new_position = st.selectbox(
                        "Lineup Spot",
                        options=lineup_spot_options,
                        index=current_option_index,
                        key=order_widget_key,
                    )

                    if new_position != current_position:
                        try:
                            set_player_order(
                                session_id=st.session_state.optimizer_session_id,
                                player_name=profile.name,
                                new_index=new_position - 1,
                            )
                            st.session_state.coach_lab_workspace_mode = "custom"
                            clear_lineup_order_widget_state()
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not reorder player: {exc}")
                else:
                    st.caption("Not in active lineup")

            with action_cols[1]:
                if st.button("Bench", key=f"bench_{key}", use_container_width=True):
                    try:
                        bench_player(
                            st.session_state.optimizer_session_id,
                            player_name=profile.name,
                        )
                        st.session_state.coach_lab_workspace_mode = "custom"
                        clear_lineup_order_widget_state()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not bench player: {exc}")

            with action_cols[2]:
                if st.button("Delete", key=f"delete_active_{key}", use_container_width=True):
                    try:
                        delete_player(
                            st.session_state.optimizer_session_id,
                            player_name=profile.name,
                        )
                        st.session_state.coach_lab_workspace_mode = "custom"
                        clear_lineup_order_widget_state()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete player: {exc}")

            with action_cols[3]:
                st.empty()

        else:
            with action_cols[0]:
                st.caption("Benched")

            with action_cols[1]:
                if st.button("Unbench", key=f"unbench_{key}", use_container_width=True):
                    try:
                        unbench_player(
                            st.session_state.optimizer_session_id,
                            player_name=profile.name,
                        )
                        st.session_state.coach_lab_workspace_mode = "custom"
                        clear_lineup_order_widget_state()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not unbench player: {exc}")

            with action_cols[2]:
                if st.button("Delete", key=f"delete_benched_{key}", use_container_width=True):
                    try:
                        delete_player(
                            st.session_state.optimizer_session_id,
                            player_name=profile.name,
                        )
                        st.session_state.coach_lab_workspace_mode = "custom"
                        clear_lineup_order_widget_state()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete player: {exc}")

            with action_cols[3]:
                st.empty()

        if any(value != 0 for value in current_adj.values()):
            st.info(
                "This player still has a saved coach nudge applied on top of the base traits below. "
                "You can clear that nudge here if needed."
            )

        id_col1, id_col2, id_col3 = st.columns(3)

        with id_col1:
            new_name = st.text_input(
                "Player name",
                value=profile.name,
                key=rk("edit_name"),
            )

        archetype_options = [a.value for a in PlayerArchetype]
        with id_col2:
            new_archetype = st.selectbox(
                "Archetype",
                options=archetype_options,
                index=archetype_options.index(archetype_value) if archetype_value in archetype_options else 0,
                key=rk("edit_archetype"),
            )

        handedness_options = ["R", "L", "S", "U"]
        with id_col3:
            new_handedness = st.selectbox(
                "Handedness",
                options=handedness_options,
                index=handedness_options.index(handedness_value) if handedness_value in handedness_options else 3,
                key=rk("edit_handedness"),
            )

        st.caption(
            "Archetype is informational for imported GameChanger players. "
            "To use an archetype as a true manual baseline, click the button below to load that archetype into the sliders."
        )

        if st.button(
            "Load selected archetype baseline into sliders",
            use_container_width=True,
            key=f"load_archetype_baseline_{key}",
        ):
            try:
                definition = get_archetype_definition(new_archetype)
                baseline = definition.default_traits.as_dict()

                st.session_state[f"contact_{key}"] = int(round(baseline["contact"]))
                st.session_state[f"power_{key}"] = int(round(baseline["power"]))
                st.session_state[f"speed_{key}"] = int(round(baseline["speed"]))
                st.session_state[f"baserunning_{key}"] = int(round(baseline["baserunning"]))
                st.session_state[f"plate_discipline_{key}"] = int(round(baseline["plate_discipline"]))
                st.session_state[f"strikeout_tendency_{key}"] = int(round(baseline["strikeout_tendency"]))
                st.session_state[f"walk_skill_{key}"] = int(round(baseline["walk_skill"]))
                st.session_state[f"chase_tendency_{key}"] = int(round(baseline["chase_tendency"]))
                st.session_state[f"aggression_{key}"] = int(round(baseline["aggression"]))
                st.session_state[f"clutch_{key}"] = int(round(baseline["clutch"]))
                st.session_state[f"sacrifice_ability_{key}"] = int(round(baseline["sacrifice_ability"]))

                st.success(
                    f"Loaded {format_archetype_label(new_archetype)} baseline into sliders. "
                    "Click Save Manual Override to persist it."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Could not load archetype baseline: {exc}")

        st.markdown("##### GC Adjustment (Nudge)")
        st.info(
            "Use GC Adjustment when the imported GameChanger profile is mostly right but needs a small correction. "
            "These nudges sit on top of the imported baseline and persist across future GC data rebuilds."
        )

        nudge_col1, nudge_col2, nudge_col3, nudge_col4 = st.columns(4)

        with nudge_col1:
            nudge_contact_value = st.slider(
                "Nudge Contact",
                -25,
                25,
                nudge_contact,
                key=rk("nudge_contact"),
                help=NUDGE_TOOLTIP_BY_FIELD["contact"],
            )

        with nudge_col2:
            nudge_power_value = st.slider(
                "Nudge Power",
                -25,
                25,
                nudge_power,
                key=rk("nudge_power"),
                help=NUDGE_TOOLTIP_BY_FIELD["power"]
            )

        with nudge_col3:
            nudge_speed_value = st.slider(
                "Nudge Speed",
                -25,
                25,
                nudge_speed,
                key=rk("nudge_speed"),
                help=NUDGE_TOOLTIP_BY_FIELD["speed"]
            )

        with nudge_col4:
            nudge_plate_discipline_value = st.slider(
                "Nudge Plate Discipline",
                -25,
                25,
                nudge_plate_discipline,
                key=rk("nudge_plate_discipline"),
                help=NUDGE_TOOLTIP_BY_FIELD["plate_discipline"]
            )

        nudge_button_cols = st.columns(2)

        with nudge_button_cols[0]:
            if st.button(
                "Save GC Adjustment",
                use_container_width=True,
                key=f"save_gc_nudge_{key}",
            ):
                try:
                    set_player_adjustment(
                        st.session_state.optimizer_session_id,
                        player_name=profile.name,
                        adjustment={
                            "contact": float(nudge_contact_value),
                            "power": float(nudge_power_value),
                            "speed": float(nudge_speed_value),
                            "plate_discipline": float(nudge_plate_discipline_value),
                        },
                    )
                    initialize_editable_roster(st.session_state.optimizer_session_id)
                    bump_player_editor_reset_token(profile.name)
                    clear_player_editor_widget_state(profile.name)
                    st.success(f"Saved GC adjustment for {profile.name}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not save GC adjustment: {exc}")

        with nudge_button_cols[1]:
            if st.button(
                "Clear GC Adjustment",
                use_container_width=True,
                key=f"clear_gc_nudge_{key}",
            ):
                try:
                    clear_player_adjustment(
                        st.session_state.optimizer_session_id,
                        player_name=profile.name,
                    )
                    initialize_editable_roster(st.session_state.optimizer_session_id)
                    bump_player_editor_reset_token(profile.name)
                    clear_player_editor_widget_state(profile.name)
                    st.success(f"Cleared GC adjustment for {profile.name}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not clear GC adjustment: {exc}")

        st.divider()

        st.markdown("##### Manual Override Traits")
        st.info(
            "Use Manual Override when you want to replace the imported baseline with your own coach judgment. "
            "These sliders become the player's active baseline until you revert to GameChanger."
        )

        trait_col1, trait_col2, trait_col3 = st.columns(3)

        with trait_col1:
            contact = st.slider(
                "Contact",
                0,
                100,
                int(round(base_traits.contact)),
                key=rk("contact"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["contact"],
            )

            baserunning = st.slider(
                "Baserunning",
                0,
                100,
                int(round(base_traits.baserunning)),
                key=rk("baserunning"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["baserunning"],
            )

            walk_skill = st.slider(
                "Walk Skill",
                0,
                100,
                int(round(base_traits.walk_skill)),
                key=rk("walk"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["walk_skill"],
            )

            aggression = st.slider(
                "Aggression",
                0,
                100,
                int(round(base_traits.aggression)),
                key=rk("aggression"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["aggression"],
            )

        with trait_col2:
            power = st.slider(
                "Power",
                0,
                100,
                int(round(base_traits.power)),
                key=rk("power"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["power"],
            )

            plate_discipline = st.slider(
                "Plate Discipline",
                0,
                100,
                int(round(base_traits.plate_discipline)),
                key=rk("plate_discipline"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["plate_discipline"],
            )

            chase_tendency = st.slider(
                "Chase Tendency",
                0,
                100,
                int(round(base_traits.chase_tendency)),
                key=rk("chase"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["chase_tendency"],
            )

            clutch = st.slider(
                "Clutch",
                0,
                100,
                int(round(base_traits.clutch)),
                key=rk("clutch"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["clutch"],
            )

        with trait_col3:

            speed = st.slider(
                "Speed",
                0,
                100,
                int(round(base_traits.speed)),
                key=rk("speed"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["speed"],
            )

            strikeout_tendency = st.slider(
                "Strikeout Tendency",
                0,
                100,
                int(round(base_traits.strikeout_tendency)),
                key=rk("strikeout"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["strikeout_tendency"],
            )

            sacrifice_ability = st.slider(
                "Sacrifice Ability",
                0,
                100,
                int(round(base_traits.sacrifice_ability)),
                key=rk("sacrifice"),
                help=MANUAL_OVERRIDE_TOOLTIP_BY_FIELD["sacrifice_ability"],
            )

        new_traits = {
            "contact": float(contact),
            "power": float(power),
            "speed": float(speed),
            "baserunning": float(baserunning),
            "plate_discipline": float(plate_discipline),
            "strikeout_tendency": float(strikeout_tendency),
            "walk_skill": float(walk_skill),
            "chase_tendency": float(chase_tendency),
            "aggression": float(aggression),
            "clutch": float(clutch),
            "sacrifice_ability": float(sacrifice_ability),
        }

        save_cols = st.columns(3)

        with save_cols[0]:
            if st.button("Save Manual Override", use_container_width=True, key=f"save_player_editor_{key}"):
                try:
                    cleaned_name = new_name.strip()
                    if not cleaned_name:
                        st.error("Player name cannot be blank.")
                    else:
                        update_player_identity(
                            st.session_state.optimizer_session_id,
                            player_name=profile.name,
                            new_name=cleaned_name,
                            handedness=new_handedness,
                            archetype=new_archetype,
                        )

                        update_player_traits(
                            st.session_state.optimizer_session_id,
                            player_name=cleaned_name,
                            traits=new_traits,
                        )

                        bump_player_editor_reset_token(cleaned_name)
                        clear_player_editor_widget_state(cleaned_name)

                        if cleaned_name != profile.name:
                            bump_player_editor_reset_token(profile.name)
                            clear_player_editor_widget_state(profile.name)

                        st.session_state.coach_lab_workspace_mode = "custom"
                        st.success(f"Saved manual override for {cleaned_name}.")
                        st.rerun()

                except Exception as exc:
                    st.error(f"Could not save player changes: {exc}")

        with save_cols[1]:
            if st.button("Clear saved GC adjustment", use_container_width=True, key=f"clear_player_nudge_from_editor_{key}"):
                try:
                    clear_player_adjustment(
                        st.session_state.optimizer_session_id,
                        player_name=profile.name,
                    )
                    initialize_editable_roster(st.session_state.optimizer_session_id)
                    bump_player_editor_reset_token(profile.name)
                    clear_player_editor_widget_state(profile.name)
                    st.success(f"Cleared saved GC adjustment for {profile.name}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not clear saved nudge: {exc}")

        st.caption(
            "Manual Override replaces this player's imported GC baseline for now. "
            "Clear saved GC adjustment only removes the lighter adjustment layer; it does not reset manual slider edits."
        )

        with save_cols[2]:
            if st.button(
                "Revert to GC Baseline",
                use_container_width=True,
                key=f"revert_to_gc_baseline_{key}",
            ):
                try:
                    revert_player_to_imported_gc_baseline(
                        st.session_state.optimizer_session_id,
                        player_name=profile.name,
                        clear_gc_adjustment=True,
                    )
                    initialize_editable_roster(st.session_state.optimizer_session_id)
                    bump_player_editor_reset_token(profile.name)
                    clear_player_editor_widget_state(profile.name)
                    st.success(f"Reverted {profile.name} to imported GC baseline.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not revert player to GC baseline: {exc}")


def build_chart_compare_set(
    *,
    results: WorkflowResponseSchema | None,
    custom_eval_payload: dict | None,
    saved_scenarios: list,
    selected_saved_names: list[str] | None = None,
    include_live_custom: bool = True,
    include_random_and_worst: bool = False,
) -> list:
    items: list = []

    if include_live_custom and custom_eval_payload and custom_eval_payload.get("custom_lineup"):
        custom_item = dict(custom_eval_payload["custom_lineup"])
        if not custom_item.get("display_name"):
            custom_item["display_name"] = "Current Unsaved Custom Order"
        items.append(custom_item)

    selected_name_set = None if selected_saved_names is None else set(selected_saved_names)

    for scenario in saved_scenarios:
        if getattr(scenario, "result", None) is None:
            continue
        if selected_name_set is not None and scenario.name not in selected_name_set:
            continue

        scenario_item = dict(scenario.result) if isinstance(scenario.result, dict) else scenario.result
        if isinstance(scenario_item, dict):
            scenario_item["display_name"] = str(scenario.name)

        items.append(scenario_item)

    deduped = []
    seen = set()

    for item in items:
        if isinstance(item, dict):
            name = str(item.get("display_name", "Lineup"))
        else:
            name = str(getattr(item, "display_name", "Lineup"))

        if name in seen:
            continue

        seen.add(name)
        deduped.append(item)

    return deduped


def render_coach_lab_comparison_section(
    *,
    results: WorkflowResponseSchema | None,
    run_settings: dict,
) -> None:
    saved_scenarios = get_saved_scenarios_for_ui()
    custom_eval_payload = st.session_state.get("coach_lab_last_custom_eval")

    available_saved_names = [
        scenario.name for scenario in saved_scenarios
        if getattr(scenario, "result", None) is not None
    ]

    include_live_custom = st.checkbox(
        "Include current unsaved custom lineup",
        value=True,
        key="coach_lab_include_live_custom",
        help="Turn this off only if you do not want the current unsaved custom lineup included in the comparison charts.",
    )

    selected_saved_names = st.multiselect(
        "Saved scenarios to compare",
        options=available_saved_names,
        default=available_saved_names,
        key="coach_lab_compare_selected_scenarios",
        help="Saved scenarios are included by default. Remove any lines you do not want to compare.",
    )

    MAX_COMPARE_SCENARIOS = 5

    if len(selected_saved_names) > MAX_COMPARE_SCENARIOS:
        st.warning(
            f"Showing the first {MAX_COMPARE_SCENARIOS} selected scenarios. "
            "Remove a few saved scenarios to compare a different set."
        )
        selected_saved_names = selected_saved_names[:MAX_COMPARE_SCENARIOS]

    compare_items = build_chart_compare_set(
        results=results,
        custom_eval_payload=custom_eval_payload,
        saved_scenarios=saved_scenarios,
        selected_saved_names=selected_saved_names,
        include_live_custom=include_live_custom,
        include_random_and_worst=False,
    )

    st.session_state.coach_lab_chart_compare_items = compare_items

    st.markdown("### Compare lineup scenarios")
    st.caption("Saved scenarios appear here. You can also include the current unsaved custom lineup.")

    st.info(
        "To get a lineup into these charts: set up the batting order you want, click **Simulate My Lineup**, "
        "then click **Save Scenario for Charts**."
    )

    enough_to_plot = len(compare_items) >= 1

    if not enough_to_plot:
        st.info(
            "Create and save a scenario to build comparison plots. "
            "You can also include the current unsaved custom order."
        )

    # -----------------------------
    # Comparison table
    # -----------------------------
    st.markdown("#### Comparison table")

    if enough_to_plot:
        table_rows = build_comparison_table_rows(compare_items)

        pretty_rows = []
        for row in table_rows:
            pretty_rows.append(
                {
                    "Lineup": row["lineup"],
                    "Avg Runs": row["avg_runs"],
                    f"Chance of {row['target_runs']:.0f}+": f"{row['chance_ge_target']:.1%}",
                    "Median": row["median_runs"],
                    "P10": row["p10_runs"],
                    "P90": row["p90_runs"],
                }
            )

        st.dataframe(pretty_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No comparison data yet.")

    # -----------------------------
    # Bucket comparison
    # -----------------------------
    with st.expander("Bucket outcomes", expanded=True):
        st.markdown("#### How often each lineup lands in these scoring ranges")

        if enough_to_plot:
            buckets = build_bucket_bar_chart_data(compare_items)

            bucket_rows = []
            for idx, bucket_label in enumerate(buckets["x"]):
                row = {"Bucket": bucket_label}
                for series in buckets["series"]:
                    row[series["name"]] = series["y"][idx]
                bucket_rows.append(row)

            bucket_df = pd.DataFrame(bucket_rows)
            bucket_long_df = bucket_df.melt(
                id_vars="Bucket",
                var_name="Lineup",
                value_name="Probability",
            )

            bucket_order = buckets["x"]

            bucket_chart = (
                alt.Chart(bucket_long_df)
                .mark_bar()
                .encode(

                    x=alt.X(
                        "Bucket:N",
                        title="Runs Scored",
                        axis=alt.Axis(labelAngle=0),
                        sort=bucket_order,
                    ),
                    xOffset=alt.XOffset("Lineup:N"),
                    y=alt.Y(
                        "Probability:Q",
                        title="How often this happens",
                        axis=alt.Axis(format=".0%"),
                    ),
                    color=alt.Color("Lineup:N", title="Lineups"),
                    tooltip=[
                        alt.Tooltip("Bucket:N"),
                        alt.Tooltip("Lineup:N"),
                        alt.Tooltip("Probability:Q", format=".1%"),
                    ],
                )
                .properties(height=360)
            )

            st.altair_chart(bucket_chart, use_container_width=True)

            pretty_bucket_rows = []
            for row in bucket_rows:
                formatted = {"Bucket": row["Bucket"]}
                for key, value in row.items():
                    if key == "Bucket":
                        continue
                    formatted[key] = f"{value:.1%}"
                pretty_bucket_rows.append(formatted)

            st.markdown("#### Scoring range table")
            st.dataframe(pretty_bucket_rows, use_container_width=True, hide_index=True)
        else:
            with st.container(border=True):
                st.caption("Bucket comparison plot will appear here.")
                st.markdown(
                    "Save a lineup scenario, or include the current unsaved custom order, to populate this chart."
                )

            st.markdown("#### Scoring range table")
            st.caption("Scoring range table will appear once scenario data is available.")

    # -----------------------------
    # Survival curve
    # -----------------------------
    with st.expander("Chance of scoring at least X runs", expanded=True):
        st.markdown("#### Chance of scoring at least X runs")

        if enough_to_plot:
            survival = build_survival_curve_chart_data(compare_items, max_runs=14)

            survival_rows = []
            for idx, x_val in enumerate(survival["x"]):
                row = {"Runs": x_val}
                for series in survival["series"]:
                    row[series["name"]] = series["y"][idx]
                survival_rows.append(row)

            survival_df = pd.DataFrame(survival_rows)
            survival_long_df = survival_df.melt(
                id_vars="Runs",
                var_name="Lineup",
                value_name="Probability",
            )

            survival_chart = (
                alt.Chart(survival_long_df)
                .mark_line(point=True)
                .encode(
                    x=alt.X("Runs:Q", title="Runs"),
                    y=alt.Y(
                        "Probability:Q",
                        title="Chance of scoring at least X runs",
                        axis=alt.Axis(format=".0%"),
                        scale=alt.Scale(domain=[0, 1]),
                    ),
                    color=alt.Color("Lineup:N", title="Lineups"),
                    tooltip=[
                        alt.Tooltip("Runs:Q"),
                        alt.Tooltip("Lineup:N"),
                        alt.Tooltip("Probability:Q", format=".1%"),
                    ],
                )
                .properties(height=360)
            )

            st.altair_chart(survival_chart, use_container_width=True)
        else:
            with st.container(border=True):
                st.caption("Comparison plot will appear here.")
                st.markdown(
                    "Create and save a scenario, or include the current unsaved custom order, to populate this plot."
                )

    st.markdown("---")
    st.markdown("### Signature charts")

    render_pressure_wave_comparison_panel(results=results)

    compare_items = list(st.session_state.get("coach_lab_chart_compare_items", []))

    if not compare_items:
        saved_scenarios = get_saved_scenarios_for_ui()
        custom_eval_payload = st.session_state.get("coach_lab_last_custom_eval")

        compare_items = build_chart_compare_set(
            results=results,
            custom_eval_payload=custom_eval_payload,
            saved_scenarios=saved_scenarios,
            include_live_custom=bool(st.session_state.get("coach_lab_include_live_custom", True)),
            include_random_and_worst=False,
        )

    st.markdown("---")
    render_pitcher_stress_panel(compare_items)

    st.markdown("---")
    render_rally_ignition_panel(compare_items)

    st.markdown("---")
    render_absent_player_shock_panel(run_settings)


def render_saved_scenarios_panel() -> None:
    st.markdown("### Saved scenarios")
    st.caption("Save coaching experiments so you can compare different lineup ideas.")

    scenarios = get_saved_scenarios_for_ui()

    if not scenarios:
        st.caption("No saved scenarios yet.")
        return

    for scenario in scenarios:
        with st.container(border=True):
            top_col1, top_col2, top_col3 = st.columns([3, 1.2, 1])

            with top_col1:
                st.markdown(f"#### {scenario.name}")
                st.caption(f"Scenario ID: {scenario.scenario_id}")

            with top_col2:
                if st.button(
                    "Rename",
                    key=f"rename_scenario_btn_{scenario.scenario_id}",
                    use_container_width=True,
                ):
                    st.session_state.scenario_rename_target = scenario.scenario_id
                    st.rerun()

            with top_col3:
                if st.button(
                    "Delete",
                    key=f"delete_scenario_btn_{scenario.scenario_id}",
                    use_container_width=True,
                ):
                    delete_saved_scenario(
                        st.session_state.optimizer_session_id,
                        scenario_id=scenario.scenario_id,
                    )
                    if st.session_state.get("scenario_rename_target") == scenario.scenario_id:
                        st.session_state.scenario_rename_target = None
                    st.rerun()

            if st.session_state.get("scenario_rename_target") == scenario.scenario_id:
                rename_cols = st.columns([3, 1])
                with rename_cols[0]:
                    new_name = st.text_input(
                        "New scenario name",
                        value=scenario.name,
                        key=f"rename_input_{scenario.scenario_id}",
                    )
                with rename_cols[1]:
                    if st.button(
                        "Save Name",
                        key=f"rename_save_{scenario.scenario_id}",
                        use_container_width=True,
                    ):
                        rename_saved_scenario(
                            st.session_state.optimizer_session_id,
                            scenario_id=scenario.scenario_id,
                            new_name=new_name,
                        )
                        st.session_state.scenario_rename_target = None
                        st.rerun()

            if scenario.result is not None:
                metrics = extract_metrics(scenario.result)
                target_runs = float(metrics.get("target_runs", 4.0) or 4.0)

                metric_cols = st.columns(3)
                metric_cols[0].metric(
                    "Avg runs",
                    f"{float(metrics.get('mean_runs', 0.0)):.2f}",
                )
                metric_cols[1].metric(
                    f"Chance of {target_runs:.0f}+ runs",
                    f"{float(metrics.get('prob_ge_target', 0.0)):.1%}",
                )
                metric_cols[2].metric(
                    "Median runs",
                    f"{float(metrics.get('median_runs', 0.0)):.2f}",
                )
            else:
                st.caption("No saved simulation result attached yet.")

            if scenario.lineup_names:
                with st.expander("Show batting order"):
                    for idx, name in enumerate(scenario.lineup_names, start=1):
                        st.write(f"{idx}. {name}")

            with st.expander("Show player nudges"):
                if scenario.adjustments_by_name:
                    rows = []
                    for player_name, adj in scenario.adjustments_by_name.items():
                        rows.append(
                            {
                                "Player": player_name,
                                "Contact": adj.get("contact", 0.0),
                                "Power": adj.get("power", 0.0),
                                "Speed": adj.get("speed", 0.0),
                                "Plate Discipline": adj.get("plate_discipline", 0.0),
                            }
                        )
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                else:
                    st.caption("No player nudges in this scenario. This is a lineup-only test.")


def render_custom_lineup_result(
    custom_eval_payload: dict | None,
    *,
    optimized: LineupEvaluationSchema | None,
    original: LineupEvaluationSchema | None,
) -> None:
    if not custom_eval_payload:
        st.info("No custom lineup has been simulated yet.")
        return

    custom = custom_eval_payload.get("custom_lineup")
    if not custom:
        st.info("No custom lineup result is available yet.")
        return

    target_runs = custom.get("target_runs", 4.0)

    st.markdown("### Your custom lineup result")

    st.caption(
        "This is the result for the batting order currently shown in Coach Lab. "
        "If you want it to appear in comparison charts later, click **Save Scenario for Charts**."
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Custom lineup average runs", f"{custom['mean_runs']:.2f}")

    if optimized is not None:
        c2.metric("Optimized lineup avg runs", f"{optimized.metrics.mean_runs:.2f}")
    else:
        c2.metric("Optimized lineup avg runs", "—")

    if original is not None:
        c3.metric("Current roster baseline", f"{original.metrics.mean_runs:.2f}")
    else:
        c3.metric("Current lineup avg runs", "—")

    c4, c5, c6 = st.columns(3)
    c4.metric(
        f"Custom chance of {target_runs:.0f}+ runs",
        f"{custom['prob_ge_target']:.1%}",
    )

    if optimized is not None:
        c5.metric(
            f"Optimized chance of {target_runs:.0f}+",
            f"{optimized.metrics.prob_ge_target:.1%}",
        )
    else:
        c5.metric(f"Optimized chance of {target_runs:.0f}+", "—")

    if original is not None:
        c6.metric(
            f"Baseline chance of {target_runs:.0f}+",
            f"{original.metrics.prob_ge_target:.1%}",
        )
    else:
        c6.metric(f"Current chance of {target_runs:.0f}+", "—")

    st.markdown("#### Current custom batting order")
    for i, name in enumerate(custom["lineup"], start=1):
        st.write(f"{i}. {name}")


def render_coach_lab(
    results: WorkflowResponseSchema | None,
    run_settings: dict,
) -> None:
    editable_profiles = get_editable_roster_for_ui()

    benched_player_names = set(get_benched_player_names_for_ui())
    active_profiles = [p for p in editable_profiles if p.name not in benched_player_names]
    benched_profiles = [p for p in editable_profiles if p.name in benched_player_names]

    continuous_batting = run_settings["rules_config"].get("continuous_batting", True)
    lineup_size = int(run_settings["rules_config"].get("lineup_size", 9))

    current_lineup_names = get_current_active_lineup_names(
        editable_profiles,
        continuous_batting=continuous_batting,
        lineup_size=lineup_size,
    ) if editable_profiles else []

    saved_scenarios = get_saved_scenarios_for_ui()
    next_scenario_number = len(saved_scenarios) + 1

    current_lineup_name_set = set(current_lineup_names)
    lineup_profiles = [p for p in active_profiles if p.name in current_lineup_name_set]
    reserve_profiles = [p for p in active_profiles if p.name not in current_lineup_name_set]

    with st.container():
        if not editable_profiles:
            st.warning(
                "Your roster is empty. Start by adding your first player below."
            )
            st.markdown(
                """
                <div style="
                    margin: 0.5rem 0 1rem 0;
                    padding: 0.9rem 1rem;
                    border-radius: 12px;
                    background: rgba(255, 200, 0, 0.10);
                    border: 1px solid rgba(255, 200, 0, 0.30);
                    font-weight: 700;
                    font-size: 1rem;
                ">
                    ↓ Next step: Add your first player from an archetype below
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.divider()

        with st.container(border=True):
            st.markdown("### Game Plan")
            st.caption(
                "Choose the scenario you want to run for tonight’s game."
            )

            render_run_status_tile()

            action_cols = st.columns(3)

            with action_cols[0]:
                if st.button(
                        "Optimize Current Roster",
                        use_container_width=True,
                        key="dashboard_optimize_current_roster",
                ):
                    clear_run_status_tile()

                    try:
                        with st.spinner("Optimizing current roster..."):
                            fresh_results = run_optimization(
                                st.session_state.optimizer_session_id,
                                output_dir=st.session_state.output_dir,
                                target_runs=run_settings["target_runs"],
                                optimizer_config=run_settings["optimizer_config"],
                                rules=RulesConfig(**run_settings["rules_config"]),
                            )

                            st.session_state.last_completed_results = fresh_results

                            apply_optimized_lineup_to_dashboard(
                                fresh_results.optimized.lineup,
                                continuous_batting=continuous_batting,
                                lineup_size=lineup_size,
                            )

                            optimized_workspace_names = get_current_active_lineup_names(
                                get_editable_roster_for_ui(),
                                continuous_batting=continuous_batting,
                                lineup_size=lineup_size,
                            )

                            set_custom_lineup(
                                st.session_state.optimizer_session_id,
                                lineup_names=optimized_workspace_names,
                            )

                            custom_eval = evaluate_custom_lineup(
                                st.session_state.optimizer_session_id,
                                target_runs=run_settings["target_runs"],
                                n_games=run_settings["optimizer_config"]["refine_games"],
                                seed=run_settings["optimizer_config"]["seed"],
                                display_name="Optimized Workspace",
                                rules=RulesConfig(**run_settings["rules_config"]),
                            )

                            st.session_state.coach_lab_last_custom_eval = custom_eval

                            generic_same_lineup_result = None
                            rules_config = run_settings.get("rules_config", {}) or {}

                            if _has_pitcher_matchup_context(rules_config):
                                try:
                                    generic_rules_config = _build_generic_rules_for_matchup_baseline(rules_config)

                                    generic_same_lineup_result = evaluate_custom_lineup(
                                        st.session_state.optimizer_session_id,
                                        target_runs=float(run_settings["target_runs"]),
                                        n_games=int(run_settings["optimizer_config"].get("refine_games", 3000)),
                                        seed=int(run_settings["optimizer_config"].get("seed", 42)) + 909,
                                        display_name="Generic Opponent Baseline",
                                        rules=RulesConfig(**generic_rules_config),
                                    )
                                except Exception as exc:
                                    generic_same_lineup_result = None
                                    st.warning(f"Could not compute generic opponent baseline: {exc}")

                            st.session_state.matchup_impact_generic_baseline = generic_same_lineup_result

                            st.session_state.coach_lab_workspace_mode = "optimized"
                            st.session_state.coach_lab_include_live_custom = True

                            optimizer_meta = {}
                            try:
                                optimizer_meta = dict(
                                    getattr(getattr(fresh_results, "coach_summary", None), "optimizer_meta", {}) or {}
                                )
                            except Exception:
                                optimizer_meta = {}

                            summary = build_optimizer_simulation_summary(
                                label="Roster optimization",
                                innings_per_game=int(run_settings["rules_config"]["innings"]),
                                optimizer_meta=optimizer_meta,
                                refine_games=int(run_settings["optimizer_config"]["refine_games"]),
                            )

                            set_run_status_tile(
                                kind="success",
                                title="Roster optimization",
                                detail=summary["detail"],
                            )

                            clear_lineup_order_widget_state()

                        st.rerun()

                    except Exception as exc:
                        set_run_status_tile(
                            kind="error",
                            title="Roster optimization",
                            detail=f"Could not optimize current roster: {exc}",
                        )
                        st.rerun()

                st.caption(
                    "Searches thousands of lineup combinations\n"
                    "to recommend your highest-scoring order."
                )

            with action_cols[1]:
                if st.button(
                        "Simulate My Lineup",
                        use_container_width=True,
                        key="dashboard_simulate_my_lineup",
                ):
                    clear_run_status_tile()

                    try:
                        with st.spinner("Simulating current custom batting order..."):
                            current_workspace_names = get_current_active_lineup_names(
                                get_editable_roster_for_ui(),
                                continuous_batting=continuous_batting,
                                lineup_size=lineup_size,
                            )

                            set_custom_lineup(
                                st.session_state.optimizer_session_id,
                                lineup_names=current_workspace_names,
                            )

                            custom_eval = evaluate_custom_lineup(
                                st.session_state.optimizer_session_id,
                                target_runs=run_settings["target_runs"],
                                n_games=run_settings["optimizer_config"]["refine_games"],
                                seed=run_settings["optimizer_config"]["seed"],
                                display_name="Coach Custom",
                                rules=RulesConfig(**run_settings["rules_config"]),
                            )

                            st.session_state.coach_lab_last_custom_eval = custom_eval
                            st.session_state.coach_lab_workspace_mode = "custom"
                            st.session_state.coach_lab_include_live_custom = True

                            summary = build_direct_simulation_summary(
                                label="Custom lineup simulation",
                                n_games=int(run_settings["optimizer_config"]["refine_games"]),
                                innings_per_game=int(run_settings["rules_config"]["innings"]),
                            )

                            telemetry = (
                                custom_eval.get("custom_lineup", {})
                                .get("simulation_telemetry", {})
                                if isinstance(custom_eval, dict)
                                else {}
                            )

                            if telemetry:
                                summary["detail"] += (
                                    f" Signature chart data captured from "
                                    f"{int(telemetry.get('total_plate_appearances', 0)):,} simulated plate appearances, "
                                    f"{int(telemetry.get('total_pressure_events', 0)):,} pressure events, "
                                    f"and {int(telemetry.get('total_rally_innings', 0)):,} rally innings."
                                )

                            set_run_status_tile(
                                kind="success",
                                title="Custom lineup simulation",
                                detail=summary["detail"],
                            )

                        st.rerun()

                    except Exception as exc:
                        set_run_status_tile(
                            kind="error",
                            title="Custom lineup simulation",
                            detail=f"Could not simulate current lineup: {exc}",
                        )
                        st.rerun()

                st.caption(
                    "Tests your current batting order with\n"
                    "large-scale Monte Carlo game simulation."
                )

            with action_cols[2]:
                if st.button(
                        "Run Lineup Shock Analysis",
                        use_container_width=True,
                        key="dashboard_generate_absent_player_shock",
                ):
                    clear_run_status_tile()

                    set_run_status_tile(
                        kind="info",
                        title="Running Absent Player Analysis",
                        detail=(
                            "Running large-scale simulations for every player absence. "
                            "This may take up to 2 minutes."
                        ),
                    )

                    try:
                        run_absent_player_shock_analysis(run_settings)

                        set_run_status_tile(
                            kind="success",
                            title="Absent Player Analysis Ready",
                            detail=(
                                st.session_state.absent_player_shock_status
                            ),
                        )

                        st.rerun()

                    except Exception as exc:
                        set_run_status_tile(
                            kind="error",
                            title="Absent Player Analysis",
                            detail=f"Could not generate analysis: {exc}",
                        )
                        st.rerun()

                    except Exception as exc:
                        set_run_status_tile(
                            kind="error",
                            title="Absent Player Analysis",
                            detail=f"Could not generate absent-player analysis: {exc}",
                        )
                        st.rerun()

                st.caption(
                    "Measures how much offense drops if a starter\n"
                    "is absent using large-scale simulation."
                )

            st.markdown("---")
            save_cols = st.columns([2, 1])

            with save_cols[0]:
                scenario_name = st.text_input(
                    "Scenario name",
                    value=f"Scenario {next_scenario_number}",
                    key="dashboard_scenario_name",
                )

            with save_cols[1]:
                st.markdown("<div style='height: 1.8rem;'></div>", unsafe_allow_html=True)

                if st.button(
                        "Save Scenario for Charts",
                        use_container_width=True,
                        key="dashboard_save_scenario",
                ):
                    try:
                        latest_lineup_names = get_current_active_lineup_names(
                            get_editable_roster_for_ui(),
                            continuous_batting=continuous_batting,
                            lineup_size=lineup_size,
                        )

                        set_custom_lineup(
                            st.session_state.optimizer_session_id,
                            lineup_names=latest_lineup_names,
                        )

                        saved_name = scenario_name.strip() or f"Scenario {next_scenario_number}"

                        simulation_games = int(run_settings["optimizer_config"]["refine_games"])

                        cached_eval = st.session_state.get("coach_lab_last_custom_eval")
                        cached_lineup = None
                        if cached_eval and isinstance(cached_eval, dict):
                            cached_custom = cached_eval.get("custom_lineup") or {}
                            cached_lineup = list(cached_custom.get("lineup", []))

                        if cached_lineup == latest_lineup_names and cached_eval is not None:
                            custom_eval = cached_eval

                            set_custom_lineup_result_payload(
                                st.session_state.optimizer_session_id,
                                result_payload=custom_eval,
                            )
                        else:
                            custom_eval = evaluate_custom_lineup(
                                st.session_state.optimizer_session_id,
                                target_runs=run_settings["target_runs"],
                                n_games=simulation_games,
                                seed=run_settings["optimizer_config"]["seed"],
                                display_name=saved_name,
                                rules=RulesConfig(**run_settings["rules_config"]),
                            )

                        if custom_eval is not None:
                            st.session_state.coach_lab_last_custom_eval = custom_eval

                        save_current_scenario(
                            st.session_state.optimizer_session_id,
                            name=saved_name,
                        )

                        live_eval = st.session_state.get("coach_lab_last_custom_eval")
                        if isinstance(live_eval, dict):
                            custom_block = live_eval.get("custom_lineup")
                            if isinstance(custom_block, dict):
                                custom_block["display_name"] = str(saved_name)
                                st.session_state.coach_lab_last_custom_eval = live_eval

                        st.session_state.coach_lab_include_live_custom = False

                        existing = st.session_state.get("coach_lab_saved_scenario_messages", [])
                        existing.append(f"Saved scenario: {saved_name}")
                        st.session_state.coach_lab_saved_scenario_messages = existing[-12:]

                        set_run_status_tile(
                            kind="success",
                            title="Scenario saved",
                            detail=f"Saved scenario: {saved_name}. It now appears in the comparison charts below.",
                        )

                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not save scenario: {exc}")





        workspace_mode = st.session_state.get("coach_lab_workspace_mode")

        if workspace_mode == "optimized":
            st.markdown(
                """
                <div style="
                    margin: 0.4rem 0 1rem 0;
                    padding: 0.85rem 1rem;
                    border-radius: 12px;
                    background: rgba(50, 180, 120, 0.14);
                    border: 1px solid rgba(50, 180, 120, 0.35);
                    font-weight: 700;
                ">
                    Current workspace: Optimized lineup loaded into the dashboard
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif workspace_mode == "custom":
            st.markdown(
                """
                <div style="
                    margin: 0.4rem 0 1rem 0;
                    padding: 0.85rem 1rem;
                    border-radius: 12px;
                    background: rgba(80, 150, 220, 0.14);
                    border: 1px solid rgba(80, 150, 220, 0.35);
                    font-weight: 700;
                ">
                    Current workspace: Coach custom lineup
                </div>
                """,
                unsafe_allow_html=True,
            )


        with st.expander("Active batting order", expanded=True):
            st.caption(
                "Data confidence: 🟢 strong sample · 🟡 usable, review if important · 🔴 low sample, check/tweak before trusting."
            )

            if lineup_profiles:
                for idx, profile in enumerate(lineup_profiles, start=1):
                    render_expandable_player_editor(
                        profile,
                        is_benched=False,
                        slot_number=idx,
                        current_position=idx,
                        total_active_players=len(lineup_profiles),
                    )
            else:
                st.warning("No active lineup players are available.")

            if reserve_profiles:
                st.markdown("##### Active players outside the current top 9")
                st.caption("These players are active but not currently in the simulated lineup because continuous batting is off.")
                for profile in reserve_profiles:
                    current_position = active_profiles.index(profile) + 1
                    render_expandable_player_editor(
                        profile,
                        is_benched=False,
                        slot_number=None,
                        current_position=current_position,
                        total_active_players=len(active_profiles),
                    )

            if benched_profiles:
                st.markdown("##### Benched players")
                for profile in benched_profiles:
                    render_expandable_player_editor(
                        profile,
                        is_benched=True,
                        slot_number=None,
                    )

            if not editable_profiles:
                st.markdown("### Add your first player")
                st.caption("Choose an archetype, enter a name, and click Add Player to begin building the roster.")
            else:
                st.markdown("##### Add player from archetype")
                st.caption("Use this to test where a new player might fit in the lineup before game day.")

            add_col1, add_col2, add_col3, add_col4 = st.columns([1.2, 1.2, 0.8, 0.9])

            with add_col1:
                new_player_name = st.text_input(
                    "Player name",
                    key="dashboard_new_player_name",
                )

            archetype_options = [a.value for a in PlayerArchetype if a.value != "unknown"]
            with add_col2:
                selected_archetype = st.selectbox(
                    "Archetype",
                    options=archetype_options,
                    key="dashboard_new_player_archetype",
                )

            with add_col3:
                new_player_handedness = st.selectbox(
                    "Handedness",
                    options=["R", "L", "S", "U"],
                    index=3,
                    key="dashboard_new_player_handedness",
                )

            with add_col4:
                st.markdown("<div style='height: 1.8rem;'></div>", unsafe_allow_html=True)
                if st.button("Add Player", use_container_width=True, key="dashboard_add_player_btn"):
                    cleaned_name = new_player_name.strip()
                    if not cleaned_name:
                        st.error("Please enter a player name.")
                    else:
                        try:
                            add_player_from_archetype(
                                st.session_state.optimizer_session_id,
                                name=cleaned_name,
                                archetype=selected_archetype,
                                handedness=new_player_handedness,
                            )
                            st.session_state.coach_lab_workspace_mode = "custom"
                            clear_lineup_order_widget_state()
                            st.success(f"Added {cleaned_name} to the roster.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not add player: {exc}")


    render_model_limitations_panel()

    baseline_results = results or st.session_state.get("last_completed_results")

    if baseline_results is not None:
        render_matchup_impact_card(
            run_settings=run_settings,
            optimized_result=baseline_results.optimized,
            generic_same_lineup_result=st.session_state.get("matchup_impact_generic_baseline"),
        )

    render_custom_lineup_result(
        st.session_state.get("coach_lab_last_custom_eval"),
        optimized=baseline_results.optimized if baseline_results is not None else None,
        original=baseline_results.original if baseline_results is not None else None,
    )

    st.markdown("")
    render_coach_lab_comparison_section(
        results=baseline_results,
        run_settings=run_settings,
    )

    saved_scenario_msgs = st.session_state.get("coach_lab_saved_scenario_messages", [])
    if saved_scenario_msgs:
        st.markdown("#### Recently saved scenarios")
        chips_html = "".join(
            f"""
            <div style="
                display: inline-block;
                margin: 0.2rem 0.35rem 0.2rem 0;
                padding: 0.45rem 0.7rem;
                border-radius: 999px;
                background: rgba(80, 150, 220, 0.14);
                border: 1px solid rgba(80, 150, 220, 0.35);
                font-size: 0.9rem;
                font-weight: 600;
            ">{msg}</div>
            """
            for msg in saved_scenario_msgs
        )
        st.markdown(chips_html, unsafe_allow_html=True)

    st.markdown("")
    render_saved_scenarios_panel()


def render_coach_footer(results: WorkflowResponseSchema) -> None:
    st.caption(
        f"Roster loaded: {results.roster_summary.player_count} players."
    )

    if results.roster_summary.warnings:
        with st.expander("Roster notes"):
            for warning in results.roster_summary.warnings:
                st.warning(warning)


def render_results(results: WorkflowResponseSchema | None) -> None:
    render_coach_lab(results, st.session_state.run_settings_cache)

    st.markdown("---")
    st.markdown("## Lineup Insights")

    if results is None:
        st.caption(
            "Run an optimization to unlock player breakdowns, alternate lineup options, and a plain-English explanation of how the model works."
        )
        return

    tab_names = [
        "Players",
        "Other Options",
        "Model & Limitations",
        "Advanced",
    ]

    default_tab = st.session_state.get("active_results_tab", "Players")
    if default_tab not in tab_names:
        default_tab = "Players"

    selected_tab = st.radio(
        "Insights navigation",
        options=tab_names,
        horizontal=True,
        label_visibility="collapsed",
        key="results_nav_radio",
        index=tab_names.index(default_tab),
    )

    st.session_state.active_results_tab = selected_tab
    st.markdown("---")

    if selected_tab == "Players":
        render_player_profiles(results.player_profiles)

    elif selected_tab == "Other Options":
        render_leaderboards(results.leaderboards)

    elif selected_tab == "Model & Limitations":
        render_model_limitations()

    elif selected_tab == "Advanced":
        render_roster_summary(results)
        st.divider()
        render_debug_info(results)


def render_roster_summary(results: WorkflowResponseSchema) -> None:
    roster = results.roster_summary

    st.markdown("### Roster Details")

    c1, c2 = st.columns(2)
    c1.metric("Players Loaded", roster.player_count)
    c2.metric("Data Source Types", len(roster.source_mode_counts))

    if roster.source_mode_counts:
        st.markdown("**Profile source breakdown**")
        rows = [
            {"Profile Source": mode, "Count": count}
            for mode, count in sorted(roster.source_mode_counts.items())
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    if roster.warnings:
        for warning in roster.warnings:
            st.warning(warning)


def render_coach_summary(results: WorkflowResponseSchema) -> None:
    summary = results.coach_summary
    target_runs = results.optimized.metrics.target_runs or 4.0

    st.markdown("### What the tool recommends")

    c1, c2, c3 = st.columns(3)
    c1.metric("Average runs", f"{summary.optimized_mean_runs:.2f}")
    c2.metric("Current order", f"{summary.original_mean_runs:.2f}")
    c3.metric("Difference", f"{summary.improvement_mean_runs:+.2f}")

    c4, c5, c6 = st.columns(3)
    c4.metric(f"Chance of scoring {target_runs:.0f}+ runs", f"{summary.optimized_prob_ge_target:.1%}")
    c5.metric("Current order chance", f"{summary.original_prob_ge_target:.1%}")
    c6.metric("Difference", f"{summary.improvement_prob_ge_target:+.1%}")

    st.markdown("**Simple takeaway**")
    for bullet in summary.bullets:
        st.write(f"- {bullet}")

    st.info(
        "The recommended lineup is the best lineup found by the current fast search settings. "
        "A custom coach lineup can occasionally grade slightly higher."
    )


def render_featured_lineups(results: WorkflowResponseSchema) -> None:
    st.markdown("### Recommended batting order")

    cols = st.columns(2)
    with cols[0]:
        render_lineup_card(
            results.optimized,
            title="Recommended lineup",
        )
    with cols[1]:
        render_lineup_card(
            results.original,
            title="Current lineup",
        )

    with st.expander("Show extra comparison lineups"):
        extra_cols = st.columns(2)
        with extra_cols[0]:
            render_lineup_card(
                results.random_lineup,
                title="Random order",
                show_advanced=True,
            )
        with extra_cols[1]:
            render_lineup_card(
                results.worst_lineup,
                title="Worst-case order",
                show_advanced=True,
            )


def render_lineup_card(
    lineup_eval: LineupEvaluationSchema,
    *,
    title: str,
    show_advanced: bool = False,
) -> None:
    target_runs = lineup_eval.metrics.target_runs or 4.0

    st.markdown(f"#### {title}")

    for i, name in enumerate(lineup_eval.lineup, start=1):
        if i <= 4:
            st.markdown(
                f"""
                <div style="
                    padding: 0.5rem 0.75rem;
                    margin-bottom: 0.35rem;
                    border-radius: 10px;
                    background: rgba(255, 215, 0, 0.10);
                    border-left: 4px solid rgba(255, 215, 0, 0.80);
                    font-weight: 600;
                ">
                    <span style="display:inline-block; width: 1.8rem; font-weight: 800;">{i}.</span>
                    {name}
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
                <div style="
                    padding: 0.5rem 0.75rem;
                    margin-bottom: 0.35rem;
                    border-radius: 10px;
                    background: rgba(255,255,255,0.03);
                    border-left: 4px solid rgba(255,255,255,0.10);
                    font-weight: 500;
                ">
                    <span style="display:inline-block; width: 1.8rem; font-weight: 800;">{i}.</span>
                    {name}
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.caption(
        f"Average runs: {lineup_eval.metrics.mean_runs:.2f} | "
        f"Chance of scoring {target_runs:.0f}+ runs: {lineup_eval.metrics.prob_ge_target:.1%}"
    )

    st.caption("Top 4 spots are lightly highlighted to emphasize the top of the order.")

    if show_advanced:
        with st.expander("Advanced numbers"):
            st.write(f"Median runs: {lineup_eval.metrics.median_runs:.2f}")
            st.write(f"Std dev: {lineup_eval.metrics.std_runs:.2f}")
            st.write(f"Sortino: {lineup_eval.metrics.sortino:.3f}")
            st.write(f"P10: {lineup_eval.metrics.p10_runs:.2f}")
            st.write(f"P90: {lineup_eval.metrics.p90_runs:.2f}")


def format_archetype_label(archetype: str) -> str:
    mapping = {
        "elite_contact": "Elite Contact",
        "contact": "Contact",
        "gap_to_gap": "Gap to Gap",
        "power": "Power",
        "three_true_outcomes": "Three True Outcomes",
        "speedster": "Speedster",
        "table_setter": "Table Setter",
        "balanced": "Balanced",
        "weak_hitter": "Developing Bat",
        "unknown": "Unknown",
    }
    return mapping.get(archetype, archetype.replace("_", " ").title())


def format_source_label(source: str) -> str:
    mapping = {
        "gamechanger": "GC",
        "manual": "Manual",
        "manual_archetypes": "Manual",
        "manual_traits": "Manual",
    }
    return mapping.get(source, source.upper() if len(source) <= 4 else source.title())


def render_player_profiles(player_profiles: Iterable[PlayerProfileSchema]) -> None:
    st.markdown("### Player Profiles")
    st.caption(
        "These 0–100 scores are internal player ratings built from our analysis of each player's "
        "GameChanger stats. Archetypes are also assigned from GameChanger data and will become "
        "coach-editable along with player trait adjustments."
    )

    rows = []
    for profile in player_profiles:
        rows.append(
            {
                "Player": profile.name,
                "Archetype": format_archetype_label(profile.archetype),
                "Source": format_source_label(profile.source),
                "Contact": round(profile.effective_traits.contact, 1),
                "Power": round(profile.effective_traits.power, 1),
                "Speed": round(profile.effective_traits.speed, 1),
                "Baserunning": round(profile.effective_traits.baserunning, 1),
                "Plate Discipline": round(profile.effective_traits.plate_discipline, 1),
                "K Tendency": round(profile.effective_traits.strikeout_tendency, 1),
                "Walk Skill": round(profile.effective_traits.walk_skill, 1),
                "Chase Tendency": round(profile.effective_traits.chase_tendency, 1),
                "Aggression": round(profile.effective_traits.aggression, 1),
                "Clutch": round(profile.effective_traits.clutch, 1),
                "Handedness": profile.handedness,
                "Source Mode": profile.source_mode,
                "Adj?": "Yes" if profile.has_adjustment else "",
                "Warnings": " | ".join(profile.warnings),
            }
        )

    st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("What do these scores mean?"):
        st.write(
            "A score near 100 means the player grades very strongly in that area compared with the "
            "rest of the player pool we are modeling. A score near 50 is more middle-of-the-pack. "
            "A lower score means that trait showed up less strongly in the available data."
        )
        st.write(
            "These are model-generated ratings, not raw GameChanger fields. They are meant to turn "
            "game stats into a coach-friendly player profile for lineup decisions."
        )

    with st.expander("Show full player details"):
        for profile in player_profiles:
            st.markdown(f"**{profile.name}**")
            st.write(f"Archetype: {format_archetype_label(profile.archetype)}")
            st.write(f"Source: {format_source_label(profile.source)}")
            st.write(f"Source mode: {profile.source_mode}")
            st.write(f"Handedness: {profile.handedness}")

            base_cols = st.columns(5)
            base_cols[0].write(f"Base C: {profile.base_traits.contact:.1f}")
            base_cols[1].write(f"Base P: {profile.base_traits.power:.1f}")
            base_cols[2].write(f"Base S: {profile.base_traits.speed:.1f}")
            base_cols[3].write(f"Base BR: {profile.base_traits.baserunning:.1f}")
            base_cols[4].write(f"Base Disc: {profile.base_traits.plate_discipline:.1f}")

            eff_cols = st.columns(5)
            eff_cols[0].write(f"Eff C: {profile.effective_traits.contact:.1f}")
            eff_cols[1].write(f"Eff P: {profile.effective_traits.power:.1f}")
            eff_cols[2].write(f"Eff S: {profile.effective_traits.speed:.1f}")
            eff_cols[3].write(f"Eff BR: {profile.effective_traits.baserunning:.1f}")
            eff_cols[4].write(f"Eff Disc: {profile.effective_traits.plate_discipline:.1f}")

            if profile.adjustment:
                st.write("Adjustments:")
                st.json(profile.adjustment)

            if profile.warnings:
                for warning in profile.warnings:
                    st.warning(warning)

            st.divider()

def render_leaderboards(leaderboards: Iterable[LeaderboardSchema]) -> None:
    st.markdown("### Other strong lineup options")
    st.caption("These are other batting orders that also graded well in the simulations.")

    for leaderboard in leaderboards:
        st.markdown(f"#### {leaderboard.title}")

        simple_rows = []
        advanced_rows = []

        for idx, entry in enumerate(leaderboard.entries[:3], start=1):
            target_runs = entry.metrics.target_runs or 4.0

            simple_rows.append(
                {
                    "Rank": idx,
                    "Average Runs": round(entry.metrics.mean_runs, 2),
                    f"Chance of {target_runs:.0f}+ Runs": f"{entry.metrics.prob_ge_target:.1%}",
                    "Lineup": " | ".join(entry.lineup),
                }
            )

            advanced_rows.append(
                {
                    "Rank": idx,
                    "Average Runs": round(entry.metrics.mean_runs, 3),
                    "Median Runs": round(entry.metrics.median_runs, 3),
                    "Std Dev": round(entry.metrics.std_runs, 3),
                    f"P({target_runs:.0f}+ Runs)": round(entry.metrics.prob_ge_target, 4),
                    "Sortino": round(entry.metrics.sortino, 3),
                    "Lineup": " | ".join(entry.lineup),
                }
            )

        st.dataframe(simple_rows, use_container_width=True, hide_index=True)

        with st.expander(f"Show advanced numbers for {leaderboard.title}"):
            st.dataframe(advanced_rows, use_container_width=True, hide_index=True)


def render_comparison_set(comparison_set: Iterable[LineupEvaluationSchema]) -> None:
    st.markdown("### Quick comparison")

    comparison_list = list(comparison_set)

    simple_rows = []
    advanced_rows = []

    for entry in comparison_list[:2]:
        target_runs = entry.metrics.target_runs or 4.0

        simple_rows.append(
            {
                "Lineup": entry.display_name,
                "Average Runs": round(entry.metrics.mean_runs, 2),
                f"Chance of {target_runs:.0f}+ Runs": f"{entry.metrics.prob_ge_target:.1%}",
                "Batting Order": " | ".join(entry.lineup),
            }
        )

    for entry in comparison_list:
        target_runs = entry.metrics.target_runs or 4.0
        advanced_rows.append(
            {
                "Lineup": entry.display_name,
                "Average Runs": round(entry.metrics.mean_runs, 3),
                "Median Runs": round(entry.metrics.median_runs, 3),
                "Std Dev": round(entry.metrics.std_runs, 3),
                f"P({target_runs:.0f}+ Runs)": round(entry.metrics.prob_ge_target, 4),
                "Sortino": round(entry.metrics.sortino, 3),
                "P10": round(entry.metrics.p10_runs, 3),
                "P90": round(entry.metrics.p90_runs, 3),
                "Games": entry.metrics.n_games,
                "Batting Order": " | ".join(entry.lineup),
            }
        )

    st.dataframe(simple_rows, use_container_width=True, hide_index=True)

    with st.expander("Show full comparison details"):
        st.dataframe(advanced_rows, use_container_width=True, hide_index=True)


def render_charts(results: WorkflowResponseSchema) -> None:
    st.markdown("### Visual lineup comparison")
    st.caption("These charts help show why one batting order stands out over another.")

    compare_items = list(results.comparison_set)

    if len(compare_items) < 2:
        st.caption(
            "Save at least one scenario, or include the current unsaved custom order, "
            "to unlock comparison charts."
        )
        return

    # -----------------------------
    # Comparison table
    # -----------------------------
    table_rows = build_comparison_table_rows(compare_items)

    pretty_rows = []
    for row in table_rows:
        pretty_rows.append(
            {
                "Lineup": row["lineup"],
                "Avg Runs": row["avg_runs"],
                f"Chance of {row['target_runs']:.0f}+": f"{row['chance_ge_target']:.1%}",
                "Median": row["median_runs"],
                "P10": row["p10_runs"],
                "P90": row["p90_runs"],
            }
        )

    st.markdown("#### Comparison table")
    st.dataframe(pretty_rows, use_container_width=True, hide_index=True)

    # -----------------------------
    # Survival curve
    # -----------------------------
    survival = build_survival_curve_chart_data(compare_items, max_runs=14)

    survival_rows = []
    for idx, x_val in enumerate(survival["x"]):
        row = {"Runs": x_val}
        for series in survival["series"]:
            row[series["name"]] = series["y"][idx]
        survival_rows.append(row)

    survival_df = pd.DataFrame(survival_rows)
    survival_long_df = survival_df.melt(
        id_vars="Runs",
        var_name="Lineup",
        value_name="Probability",
    )

    st.markdown("#### Probability of scoring at least X runs")

    survival_chart = (
        alt.Chart(survival_long_df)
        .mark_line(point=True)
        .encode(
            x=alt.X("Runs:Q", title="Runs"),
            y=alt.Y(
                "Probability:Q",
                title="Chance of scoring at least this many runs",
                axis=alt.Axis(format=".0%"),
                scale=alt.Scale(domain=[0, 1]),
            ),
            color=alt.Color("Lineup:N", title="Lineups"),
            tooltip=[
                alt.Tooltip("Runs:Q"),
                alt.Tooltip("Lineup:N"),
                alt.Tooltip("Probability:Q", format=".1%"),
            ],
        )
        .properties(height=380)
    )

    st.altair_chart(survival_chart, use_container_width=True)

    # -----------------------------
    # Bucket comparison
    # -----------------------------
    buckets = build_bucket_bar_chart_data(compare_items)

    bucket_rows = []
    for idx, bucket_label in enumerate(buckets["x"]):
        row = {"Bucket": bucket_label}
        for series in buckets["series"]:
            row[series["name"]] = series["y"][idx]
        bucket_rows.append(row)

    bucket_df = pd.DataFrame(bucket_rows)
    bucket_long_df = bucket_df.melt(
        id_vars="Bucket",
        var_name="Lineup",
        value_name="Probability",
    )

    bucket_order = buckets["x"]

    st.markdown("#### Bucketed outcome comparison")

    bucket_chart = (
        alt.Chart(bucket_long_df)
        .mark_bar()
        .encode(
            x=alt.X(
                "Bucket:N",
                title="Runs Scored",
                axis=alt.Axis(labelAngle=0),
                sort=bucket_order,
            ),
            xOffset=alt.XOffset("Lineup:N"),
            y=alt.Y(
                "Probability:Q",
                title="How often this happens",
                axis=alt.Axis(format=".0%"),
            ),
            color=alt.Color("Lineup:N", title="Lineups"),
            tooltip=[
                alt.Tooltip("Bucket:N"),
                alt.Tooltip("Lineup:N"),
                alt.Tooltip("Probability:Q", format=".1%"),
            ],
        )
        .properties(height=380)
    )

    st.altair_chart(bucket_chart, use_container_width=True)

    # -----------------------------
    # Density chart
    # -----------------------------
    density = build_density_chart_data(compare_items, max_runs=12)

    density_rows = []
    for idx, x_val in enumerate(density["x"]):
        row = {"Runs": x_val}
        for series in density["series"]:
            row[series["name"]] = series["y"][idx]
        density_rows.append(row)

    density_df = pd.DataFrame(density_rows)
    density_long_df = density_df.melt(
        id_vars="Runs",
        var_name="Lineup",
        value_name="Density",
    )

    st.markdown("#### Run distribution density")

    density_chart = (
        alt.Chart(density_long_df)
        .mark_line()
        .encode(
            x=alt.X("Runs:Q", title="Runs scored"),
            y=alt.Y("Density:Q", title="Density"),
            color=alt.Color("Lineup:N", title="Lineups"),
            tooltip=[
                alt.Tooltip("Runs:Q", format=".1f"),
                alt.Tooltip("Lineup:N"),
                alt.Tooltip("Density:Q", format=".3f"),
            ],
        )
        .properties(height=380)
    )

    st.altair_chart(density_chart, use_container_width=True)


def render_debug_info(results: WorkflowResponseSchema) -> None:
    session_state = get_backend_session()

    st.markdown("### Advanced details")
    st.write(f"Session ID: `{session_state.session_id}`")
    st.write(f"Status: `{session_state.status}`")
    st.write(f"Data source: `{session_state.data_source}`")
    st.write(f"CSV path: `{session_state.csv_path}`")
    st.write(f"Adjustments path: `{session_state.adjustments_path}`")
    st.write(f"Roster path: `{session_state.roster_path}`")
    st.write(f"Output dir: `{st.session_state.output_dir}`")

    if results.warnings:
        st.markdown("**Workflow warnings**")
        for warning in results.warnings:
            st.warning(warning)

    if results.errors:
        st.markdown("**Workflow errors**")
        for error in results.errors:
            st.error(error)


# =============================================================================
# Main app
# =============================================================================

def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="⚾",
        layout="wide",
    )

    inject_custom_styles()

    ensure_ui_state()
    require_authenticated_user()
    backend_session = get_backend_session()
    ensure_selected_team()

    st.title(APP_TITLE)
    st.caption(APP_SUBTITLE)

    if not st.session_state.get("coach_usage_panel_dismissed", False):
        render_how_to_use_panel()

    render_team_switcher()

    run_settings = render_sidebar(backend_session)
    render_signed_in_banner()

    if not _has_pitcher_matchup_context(run_settings.get("rules_config", {}) or {}):
        st.session_state.matchup_impact_generic_baseline = None

    st.session_state.run_settings_cache = run_settings

    existing_results = safe_get_results()
    render_results(existing_results)


if __name__ == "__main__":
    main()
