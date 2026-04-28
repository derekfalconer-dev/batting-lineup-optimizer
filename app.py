from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable, Any
import altair as alt
import pandas as pd

import streamlit as st

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


def build_pressure_wave_rows(lineup_profiles: list) -> list[dict]:
    rows = []

    for idx, profile in enumerate(lineup_profiles, start=1):
        traits = getattr(profile, "effective_traits", getattr(profile, "base_traits", None))
        if traits is None:
            continue

        contact = float(getattr(traits, "contact", 0.0))
        discipline = float(getattr(traits, "plate_discipline", 0.0))
        speed = float(getattr(traits, "speed", 0.0))
        power = float(getattr(traits, "power", 0.0))
        baserunning = float(getattr(traits, "baserunning", 0.0))

        pressure_score = (
            0.34 * contact
            + 0.24 * discipline
            + 0.18 * speed
            + 0.14 * power
            + 0.10 * baserunning
        )

        rows.append(
            {
                "Spot": idx,
                "Player": profile.name,
                "Pressure Score": round(max(0.0, min(100.0, pressure_score)), 1),
                "Contact": round(contact, 1),
                "Discipline": round(discipline, 1),
                "Speed": round(speed, 1),
                "Power": round(power, 1),
            }
        )

    return rows


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


def render_pressure_wave_panel(lineup_profiles: list) -> None:
    st.markdown("### Pressure Wave")
    with st.expander("Lineup Pressure Wave", expanded=False):
        st.caption(
            "Pressure Wave shows where your lineup applies sustained offensive pressure. "
            "It combines contact, on-base skill, speed, power, and baserunning to estimate "
            "which batting spots can stress pitchers, force defensive mistakes, and keep innings alive."
        )

        rows = build_pressure_wave_rows(lineup_profiles)

        if len(rows) < 2:
            st.info("Add at least two active lineup players to build the Pressure Wave.")
            return

        df = pd.DataFrame(rows)

        df["Spot"] = pd.to_numeric(df["Spot"], errors="coerce").astype(int)
        df["Player"] = df["Player"].astype(str)
        df["Player Label"] = df["Player"].apply(short_player_label)
        df = df.sort_values(["Lineup", "Spot"])

        avg_pressure = float(df["Pressure Score"].mean())
        peak_row = df.sort_values("Pressure Score", ascending=False).iloc[0]

        metric_cols = st.columns(3)
        metric_cols[0].metric("Pressure Index", f"{avg_pressure:.0f}/100")
        metric_cols[1].metric("Biggest Pressure Spot", f"#{int(peak_row['Spot'])}")
        metric_cols[2].metric("Primary Rally Igniter", str(peak_row["Player"]))

        pressure_std = float(df["Pressure Score"].std())

        if pressure_std < 8:
            diagnosis = "Very balanced lineup pressure."
        elif pressure_std < 14:
            diagnosis = "Moderate peaks with a few pressure pockets."
        else:
            diagnosis = "High-variance lineup with big peaks and possible dead zones."

        st.success(f"Wave diagnosis: {diagnosis}")

        base = alt.Chart(df).encode(
            x=alt.X(
                "Player Label:N",
                title="Batting order",
                sort=None,
                axis=alt.Axis(labelAngle=-45, labelAlign="right"),
            ),
            y=alt.Y(
                "Pressure Score:Q",
                title="Pressure on pitcher / defense",
                scale=alt.Scale(domain=[0, 100]),
            ),
            tooltip=[
                alt.Tooltip("Spot:O", title="Batting spot"),
                alt.Tooltip("Player:N"),
                alt.Tooltip("Pressure Score:Q", title="Pressure score", format=".1f"),
                alt.Tooltip("Contact:Q", format=".1f"),
                alt.Tooltip("Discipline:Q", format=".1f"),
                alt.Tooltip("Speed:Q", format=".1f"),
                alt.Tooltip("Power:Q", format=".1f"),
                alt.Tooltip("Estimated Pitches/PA:Q", title="Estimated pitches/PA", format=".2f"),
                alt.Tooltip("Walk Rate:Q", title="Walk rate", format=".1%"),
                alt.Tooltip("Deep Count Rate:Q", title="Deep-count PA rate", format=".1%"),
            ],
        )

        wave = base.mark_area(
            opacity=0.35,
            interpolate="monotone",
        )

        line = base.mark_line(
            point=True,
            interpolate="monotone",
        )

        chart = (wave + line).properties(height=340)

        st.altair_chart(chart, use_container_width=True)

        st.info(
            "How to read it:\n"
            "• Peaks = pressure points where rallies often start or grow\n"
            "• Valleys = possible lineup dead zones where innings may stall\n"
            "• Back-to-back peaks can create sustained pressure on pitch counts and defense\n"
            "• A smoother wave often means deeper, tougher lineups"
        )

        st.caption(
            "Coach read: Look for dead zones you may want to break up, and for clusters of pressure that can wear down pitchers. "
            "In youth baseball, sustained pressure often creates walks, errors, and big innings."
        )

        pretty_rows = [
            {
                "Spot": row["Spot"],
                "Player": row["Player"],
                "Pressure": row["Pressure Score"],
                "Why It Matters": (
                    "Can stress the defense"
                    if row["Pressure Score"] >= 70
                    else "Solid lineup spot"
                    if row["Pressure Score"] >= 55
                    else "Possible dead zone"
                ),
            }
            for row in rows
        ]


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
        df["Player Label"] = df["Player"].apply(short_player_label)
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
                    "Player Label:N",
                    title="Batting order",
                    sort=None,
                    axis=alt.Axis(labelAngle=-45, labelAlign="right"),
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


def build_pitcher_stress_rows(lineup_profiles: list) -> list[dict]:
    rows = []

    for idx, profile in enumerate(lineup_profiles, start=1):
        traits = getattr(profile, "effective_traits", getattr(profile, "base_traits", None))
        if traits is None:
            continue

        contact = float(getattr(traits, "contact", 0.0))
        discipline = float(getattr(traits, "plate_discipline", 0.0))
        speed = float(getattr(traits, "speed", 0.0))
        baserunning = float(getattr(traits, "baserunning", 0.0))
        power = float(getattr(traits, "power", 0.0))

        stress_score = (
            0.30 * discipline
            + 0.25 * contact
            + 0.18 * speed
            + 0.15 * baserunning
            + 0.12 * power
        )

        rows.append(
            {
                "Spot": idx,
                "Player": profile.name,
                "Stress Score": round(max(0.0, min(100.0, stress_score)), 1),
                "Contact": round(contact, 1),
                "Discipline": round(discipline, 1),
                "Speed": round(speed, 1),
                "Baserunning": round(baserunning, 1),
                "Power": round(power, 1),
            }
        )

    return rows


def pitcher_stress_coach_read(row: dict) -> str:
    walk_rate = float(row.get("Walk Rate",0))
    deep_count = float(row.get("Deep Count Rate",0))
    damage = float(row.get("Rally Damage/100 PA",0))
    extend = float(row.get("Rally Extensions/100 PA",0))
    starts = float(row.get("Rally Starts/100 PA",0))
    pressure = float(row.get("Pressure Score",0))

    if walk_rate >= .14:
        return "Works walks"

    if deep_count >= .28:
        return "Runs deep counts"

    if damage >= 18:
        return "Punishes traffic"

    if extend >= 28:
        return "Extends innings"

    if starts >= 12:
        return "Starts pressure"

    if pressure >= 70:
        return "Creates traffic"

    if walk_rate >= .10:
        return "Patient at-bat"

    return "Support bat"


def rally_ignition_coach_read(row: dict) -> str:
    starts_per_100 = float(row.get("Rally Starts/100 PA", 0.0) or 0.0)
    extensions_per_100 = float(row.get("Rally Extensions/100 PA", 0.0) or 0.0)
    damage_per_100 = float(row.get("Rally Damage/100 PA", 0.0) or 0.0)

    if starts_per_100 >= 15:
        return "Leadoff-style spark"

    if starts_per_100 >= 10 and extensions_per_100 >= 12:
        return "Starts and sustains"

    if damage_per_100 >= 20:
        return "Cashes in traffic"

    if damage_per_100 >= 14 and extensions_per_100 >= 20:
        return "Keeps rally dangerous"

    if extensions_per_100 >= 28:
        return "Turns lineup over"

    if extensions_per_100 >= 18:
        return "Extends rallies"

    if starts_per_100 >= 7:
        return "Can spark innings"

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
        _, signature_rows = select_signature_chart_rows(
            compare_items,
            key=selector_key,
            label="Scenario",
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

        metric_cols = st.columns(3)
        metric_cols[0].metric("Lineup Pressure on Pitchers", f"{stress_index:.0f}/100")
        metric_cols[1].metric("Toughest PA", str(peak_row["Player"]))
        metric_cols[2].metric(
            "Pressure Cluster",
            cluster_label
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
            "• Walk Rate = how often this hitter forces free passes\n"
            "• Long At-Bat Rate = share of plate appearances likely pushing deep counts\n"
            "• Stress reflects traffic created, innings extended, and damage done under pressure\n"
            "• Pressure Signatures are inferred from simulated behavior, not static traits"
        )

        pretty_rows = []
        for row in rows:
            role = pitcher_stress_coach_read(row)

            pretty_rows.append(
                {
                    "Spot": row["Spot"],
                    "Player": row["Player"],
                    "Stress": row["Stress Score"],
                    "Pressure Signature": role,
                    "Walk Rate": f"{float(row.get('Walk Rate', 0.0)):.1%}",
                    "Long At-Bat Rate": f"{float(row.get('Deep Count Rate', 0.0)):.1%}",
                }
            )

        st.dataframe(pretty_rows, use_container_width=True, hide_index=True)


def build_rally_ignition_rows(lineup_profiles: list) -> list[dict]:
    rows = []

    for idx, profile in enumerate(lineup_profiles, start=1):
        traits = getattr(profile, "effective_traits", getattr(profile, "base_traits", None))
        if traits is None:
            continue

        contact = float(getattr(traits, "contact", 0.0))
        discipline = float(getattr(traits, "plate_discipline", 0.0))
        speed = float(getattr(traits, "speed", 0.0))
        baserunning = float(getattr(traits, "baserunning", 0.0))
        power = float(getattr(traits, "power", 0.0))

        ignition_score = (
            0.32 * contact
            + 0.26 * discipline
            + 0.20 * speed
            + 0.12 * baserunning
            + 0.10 * power
        )

        extension_score = (
            0.28 * contact
            + 0.24 * discipline
            + 0.22 * baserunning
            + 0.16 * speed
            + 0.10 * power
        )

        damage_score = (
            0.38 * power
            + 0.26 * contact
            + 0.18 * discipline
            + 0.10 * speed
            + 0.08 * baserunning
        )

        rows.append(
            {
                "Spot": idx,
                "Player": profile.name,
                "Ignition": round(max(0.0, min(100.0, ignition_score)), 1),
                "Extension": round(max(0.0, min(100.0, extension_score)), 1),
                "Damage": round(max(0.0, min(100.0, damage_score)), 1),
                "Contact": round(contact, 1),
                "Discipline": round(discipline, 1),
                "Speed": round(speed, 1),
                "Baserunning": round(baserunning, 1),
                "Power": round(power, 1),
            }
        )

    return rows


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

        _, signature_rows = select_signature_chart_rows(
            compare_items,
            key=selector_key,
            label="Scenario",
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

        spark_row = df.sort_values("Ignition", ascending=False).iloc[0]
        extender_row = df.sort_values("Extension", ascending=False).iloc[0]
        damage_row = df.sort_values("Damage", ascending=False).iloc[0]

        metric_cols = st.columns(3)
        metric_cols[0].metric("Best Rally Starter", str(spark_row["Player"]))
        metric_cols[1].metric("Best Rally Extender", str(extender_row["Player"]))
        metric_cols[2].metric("Best Damage Bat", str(damage_row["Player"]))

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
                ],
            )
            .properties(height=300)
        )

        st.altair_chart(chart, use_container_width=True)

        st.info(
            "How to read it:\n"
            "• Ignition = started rallies in the simulated games\n"
            "• Extension = kept rally innings alive\n"
            "• Damage = cashed in traffic with extra-base impact or run-producing plays"
        )

        pretty_rows = []
        for row in rows:
            coach_read = rally_ignition_coach_read(row)

            pretty_rows.append(
                {
                    "Spot": row["Spot"],
                    "Player": row["Player"],
                    "Rally Signature": coach_read,
                    "Rallies Started/100 PA": row.get("Rally Starts/100 PA", 0.0),
                    "Rallies Extended/100 PA": row.get("Rally Extensions/100 PA", 0.0),
                    "Traffic Cashed In/100 PA": row.get("Rally Damage/100 PA", 0.0),
                }
            )

        st.dataframe(pretty_rows, use_container_width=True, hide_index=True)


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


def reset_team_scoped_ui_state() -> None:
    """
    Clear UI caches/results that should not bleed across teams.

    This should NOT decide whether the import/build panel is open.
    Callers can set st.session_state.show_team_loader after this function
    when they intentionally want to open it, such as after creating a new team.
    """
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
            st.login()


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

    manager = get_session_manager()
    session_obj = manager.get_session(st.session_state.optimizer_session_id)
    selected_team_id = st.session_state.get("selected_team_id")

    if not selected_team_id:
        raise ValueError("No active team is selected.")

    from core.auth import get_current_user
    current_user = get_current_user()
    manager.delete_team_for_user(selected_team_id, current_user.user_id)
    remaining = manager.list_teams_for_user(current_user.user_id)

    if remaining:
        non_untitled = [
            team for team in remaining
            if team.team_name.strip().lower() != "untitled team"
        ]
        next_team = non_untitled[0] if non_untitled else remaining[0]
        manager.attach_session_to_team(session_obj.session_id, team_id=next_team.team_id)
        st.session_state.selected_team_id = next_team.team_id
    else:
        new_team = manager.create_team(
            owner_user_id=current_user.user_id,
            team_name="Untitled Team",
        )
        manager.attach_session_to_team(session_obj.session_id, team_id=new_team.team_id)
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
                    delete_active_team_and_recover()
                    st.success("Team deleted.")
                    st.rerun()


def save_uploaded_file(uploaded_file, target_name: str) -> Path:
    """
    Persist a Streamlit UploadedFile to this session's temp upload directory.
    """
    uploads_dir = Path(st.session_state.uploads_dir)
    target_path = uploads_dir / target_name
    target_path.write_bytes(uploaded_file.getbuffer())
    return target_path


def safe_get_results() -> WorkflowResponseSchema | None:
    """
    Return results if they exist; otherwise None.
    """
    try:
        return get_results(st.session_state.optimizer_session_id)
    except ValueError:
        return None


def reset_multi_gc_ui_state() -> None:
    st.session_state.multi_gc_reconciliation_result = None
    st.session_state.multi_gc_final_records = None
    st.session_state.multi_gc_uploaded_file_names = []
    st.session_state.multi_gc_import_summary = None


def find_backend_additional_preview_row(
    *,
    incoming_name: str,
    pa: int,
    source_file: str,
) -> dict | None:
    from core.session_manager import get_session_manager

    # Streamlit is a thin UI shell.
    # Auth comes from core/auth.py.
    # Durable team access must stay owner-scoped via SessionManager.
    manager = get_session_manager()
    raw_session = manager.get_session(st.session_state.optimizer_session_id)
    preview_rows = raw_session.manual_roster or []

    for item in preview_rows:
        if (
            str(item.get("incoming_name", "")) == str(incoming_name)
            and int(item.get("pa", 0)) == int(pa)
            and str(item.get("source_file", "")) == str(source_file)
        ):
            return item

    return None


def save_uploaded_files(uploaded_files, *, prefix: str) -> list[Path]:
    """
    Persist multiple Streamlit UploadedFile objects to the session upload dir.
    """
    uploads_dir = Path(st.session_state.uploads_dir)
    saved_paths: list[Path] = []

    for idx, uploaded_file in enumerate(uploaded_files, start=1):
        original_name = Path(uploaded_file.name).name
        safe_name = f"{prefix}_{idx:02d}_{original_name}"
        target_path = uploads_dir / safe_name
        target_path.write_bytes(uploaded_file.getbuffer())
        saved_paths.append(target_path)

    return saved_paths


def format_int_compact(value: int) -> str:
    return f"{int(value):,}"


def build_direct_simulation_summary(
    *,
    label: str,
    n_games: int,
    innings_per_game: int,
) -> dict:
    total_innings = int(n_games) * int(innings_per_game)
    return {
        "label": label,
        "games": int(n_games),
        "innings": int(total_innings),
        "detail": (
            f"{label} complete — simulated {format_int_compact(n_games)} games "
            f"and {format_int_compact(total_innings)} innings."
        ),
    }


def build_optimizer_simulation_summary(
    *,
    label: str,
    innings_per_game: int,
    optimizer_meta: dict | None = None,
    refine_games: int | None = None,
) -> dict:
    optimizer_meta = dict(optimizer_meta or {})

    total_games = optimizer_meta.get("total_games")
    search_total_games = optimizer_meta.get("search_total_games")
    refine_total_games = optimizer_meta.get("refine_total_games")

    if total_games is not None:
        total_games = int(total_games)
        total_innings = total_games * int(innings_per_game)

        detail_parts = [
            f"{label} complete — simulated {format_int_compact(total_games)} total games "
            f"and {format_int_compact(total_innings)} total innings."
        ]

        if search_total_games is not None:
            search_total_games = int(search_total_games)
            detail_parts.append(
                f"Search stage: {format_int_compact(search_total_games)} games "
                f"({format_int_compact(search_total_games * int(innings_per_game))} innings)."
            )

        if refine_total_games is not None:
            refine_total_games = int(refine_total_games)
            detail_parts.append(
                f"Final comparison stage: {format_int_compact(refine_total_games)} games "
                f"({format_int_compact(refine_total_games * int(innings_per_game))} innings)."
            )

        return {
            "label": label,
            "games": total_games,
            "innings": total_innings,
            "detail": " ".join(detail_parts),
        }

    # Fallback if optimizer meta is not available yet.
    fallback_refine_games = int(refine_games or 3000) * 4
    fallback_innings = fallback_refine_games * int(innings_per_game)

    return {
        "label": label,
        "games": fallback_refine_games,
        "innings": fallback_innings,
        "detail": (
            f"{label} complete — simulated at least {format_int_compact(fallback_refine_games)} games "
            f"and {format_int_compact(fallback_innings)} innings in the final comparison stage."
        ),
    }


def clear_run_status_tile() -> None:
    st.session_state.run_status_tile = None


def set_run_status_tile(
    *,
    kind: str,
    title: str,
    detail: str,
) -> None:
    st.session_state.run_status_tile = {
        "kind": str(kind),
        "title": str(title),
        "detail": str(detail),
    }


def render_run_status_tile() -> None:
    tile = st.session_state.get("run_status_tile")
    if not tile:
        return

    with st.container(border=True):
        st.markdown(f"#### {tile['title']}")

        if tile["kind"] == "success":
            st.success(tile["detail"])
        elif tile["kind"] == "error":
            st.error(tile["detail"])
        else:
            st.info(tile["detail"])


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


def render_how_to_use_panel() -> None:
    with st.container(border=True):
        st.markdown("### How coaches are using this")
        st.caption("The most common decisions this tool helps with right now.")

        col1, col2, col3, col4, col5 = st.columns(5)

        with col1:
            st.markdown("**1. Adjust for a hot or cold bat**")
            st.caption(
                "Use the nudge sliders to reflect a player who’s hot or in a slump, then re-run the optimizer to see if it actually changes where player should hit."
            )

        with col2:
            st.markdown("**2. Matchup against today’s pitcher**")
            st.caption(
                "Use nudges when you know a hitter matches up especially well or poorly against the opposing pitcher, "
                "then re-optimize to see if the lineup should change."
            )

        with col3:
            st.markdown("**3. Player absent tonight**")
            st.caption(
                "Bench the absent player, then optimize or simulate again to see how the order should shift."
            )

        with col4:
            st.markdown("**4. Try a new player**")
            st.caption(
                "Add a player from an archetype, place the player in the order, and simulate how the player changes the lineup."
            )

        with col5:
            st.markdown("**5. Compare your intuition**")
            st.caption(
                "Set up the order you like, simulate it, then compare it against the optimized order."
            )


def render_model_limitations_panel() -> None:
    with st.expander("Model & Limitations", expanded=False):
        st.markdown(
            """
**What this tool is doing**
- It uses Monte Carlo simulation to play out many versions of the game and estimate run scoring outcomes.
- It focuses on lineup-level outputs like average runs, median runs, and the chance of scoring at least a target number of runs.
- It adjusts the environment based on your game settings such as inning length, run cap, diamond size, leadoffs, strategy, coaching style, and opponent strength.

**What the player data means**
- GameChanger imports are treated as directional input, not perfect truth.
- The app converts GameChanger batting stats into internal 0–100 player traits, then builds simulator probabilities from those traits.
- Coach edits and archetype players are meant to help when GameChanger data is sparse, noisy, or missing.
- Each player may show plate appearances, number of source files, and a confidence label.
- Low confidence does not mean “do not use.” It means the imported data is a weaker baseline and Coach Lab review is recommended.
- When confidence is low, the best workflow is to inspect that player, make a small trait adjustment or choose a better-fit archetype, then re-run the simulation.

**Important limitations**
- Bad scorekeeping will still affect the imported baseline.
- Small sample sizes can make the model noisy for individual players.
- This is better for comparing lineup ideas than for pretending to predict exact game outcomes.
- The optimized lineup is the best lineup found by the current fast search settings, not a mathematical proof that no better lineup exists.

**Best use cases**
- Rebuilding the order when a player is absent
- Stress-testing your intuition lineup vs an optimized lineup
- Seeing whether one weak bat or one added bat materially changes the offense
- Getting directional guidance before making a final coaching call
- Using imported stats as a baseline, then tightening up low-confidence players with coach knowledge
            """
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

    st.markdown("")
    with st.expander("Add additional GameChanger data to this team", expanded=False):
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


def get_saved_rules_for_active_team() -> tuple[str, dict]:
    try:
        from core.session_manager import get_session_manager

        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        return (
            getattr(team, "rules_preset", "High School") or "High School",
            dict(getattr(team, "rules_config", {}) or {}),
        )
    except Exception:
        return "High School", {}


def save_rules_for_active_team(
    *,
    rules_preset: str,
    rules_config: dict,
) -> None:
    try:
        from core.session_manager import get_session_manager

        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        team.rules_preset = str(rules_preset)
        team.rules_config = dict(rules_config)
        manager.mark_workspace_dirty(st.session_state.optimizer_session_id)
    except Exception:
        pass


def render_sidebar(session_state: SessionStateSchema) -> dict:

    saved_rules_preset, saved_rules_config = get_saved_rules_for_active_team()

    st.sidebar.markdown("## 🎯 Scoring Goal")
    target_runs = st.sidebar.slider(
        "Goal Runs Per Game",
        min_value=1.0,
        max_value=12.0,
        value=4.0,
        step=1.0,
        help="Used for charts that show the chance of scoring at least this many runs.",
    )
    st.sidebar.caption(f"Charts will show chance of scoring {target_runs:.1f}+ runs.")
    st.sidebar.markdown("---")

    st.sidebar.markdown("## ⚾ Game Rules")
    st.sidebar.caption("Choose a preset, then tweak if needed.")

    preset_options = [
        "Little League",
        "Intermediate",
        "High School",
        "College",
        "Manual",
    ]

    default_preset = (
        saved_rules_preset
        if saved_rules_preset in preset_options
        else "High School"
    )

    rules_preset = st.sidebar.selectbox(
        "Rules preset",
        options=preset_options,
        index=preset_options.index(default_preset),
        key=f"rules_preset_{st.session_state.get('selected_team_id', 'no_team')}",
    )

    preset_changed = rules_preset != saved_rules_preset
    saved_rules_config = dict(saved_rules_config or {})
    if preset_changed:
        saved_rules_config = {}

    preset_defaults = {
        "Little League": {
            "innings": 6,
            "diamond": "46/60",
            "leadoffs": False,
            "run_limit": False,
            "continuous_batting": True,
        },
        "Intermediate": {
            "innings": 7,
            "diamond": "50/70",
            "leadoffs": True,
            "run_limit": False,
            "continuous_batting": True,
        },
        "High School": {
            "innings": 7,
            "diamond": "60/90",
            "leadoffs": True,
            "run_limit": False,
            "continuous_batting": False,
        },
        "College": {
            "innings": 9,
            "diamond": "60/90",
            "leadoffs": True,
            "run_limit": False,
            "continuous_batting": False,
        },
        "Manual": {
            "innings": int(saved_rules_config.get("innings", 7)),
            "diamond": str(saved_rules_config.get("diamond_size", "60/90")),
            "leadoffs": bool(saved_rules_config.get("leadoffs_allowed", True)),
            "run_limit": bool(saved_rules_config.get("use_inning_run_limit", False)),
            "continuous_batting": bool(saved_rules_config.get("continuous_batting", False)),
        },
    }

    preset = preset_defaults[rules_preset]
    if rules_preset == "Manual":
        st.sidebar.info(
            "Manual mode: use the controls below to create any custom rule set."
        )
    team_key = str(st.session_state.get("selected_team_id", "no_team"))
    preset_key = f"{team_key}_{rules_preset.lower().replace(' ', '_').replace('/', '_')}"
    saved_rules_config = dict(saved_rules_config or {})

    if preset_changed:
        saved_rules_config = {}

    innings_per_game = st.sidebar.slider(
        "Innings / Game",
        3,
        9,
        int(saved_rules_config.get("innings", preset["innings"])),
        key=f"innings_per_game_{preset_key}",
    )

    continuous_batting = st.sidebar.checkbox(
        "Continuous Batting",
        value=bool(saved_rules_config.get("continuous_batting", preset["continuous_batting"])),
        key=f"continuous_batting_{preset_key}",
    )

    use_inning_run_limit = st.sidebar.checkbox(
        "Inning Run Limit",
        value=bool(saved_rules_config.get("use_inning_run_limit", preset["run_limit"])),
        key=f"use_inning_run_limit_{preset_key}",
    )

    inning_run_limit = None
    if use_inning_run_limit:
        inning_run_limit = st.sidebar.number_input(
            "Max runs per inning",
            min_value=1,
            max_value=20,
            value=5,
            key=f"inning_run_limit_{preset_key}",
        )

    diamond_options = ["46/60", "50/70", "60/90"]
    diamond_size = st.sidebar.selectbox(
        "Diamond Size",
        diamond_options,
        index=diamond_options.index(str(saved_rules_config.get("diamond_size", preset["diamond"]))),
        key=f"diamond_size_{preset_key}",
    )

    leadoffs_allowed = st.sidebar.checkbox(
        "Leadoffs Allowed",
        value=bool(saved_rules_config.get("leadoffs_allowed", preset["leadoffs"])),
        key=f"leadoffs_allowed_{preset_key}",
    )

    st.sidebar.markdown("---")

    st.sidebar.markdown("## ⚙️ Game Context")

    opposing_pitching_label = st.sidebar.selectbox(
        "Opposing Pitching Strength",
        ["Weak", "Average", "Strong"],
        index=1,
    )

    opponent_level_label = st.sidebar.selectbox(
        "Opponent Level",
        ["Weak", "Average", "Strong"],
        index=1,
    )

    st.sidebar.markdown("---")

    st.sidebar.markdown("## 🎯 Strategy")

    strategy_label = st.sidebar.selectbox(
        "Game Strategy",
        ["Small Ball", "Balanced", "Power"],
        index=1,
        help="Small Ball leans toward pressure and runner movement. Power leans toward damage and extra-base impact. Balanced stays in the middle.",
    )

    coaching_style_label = st.sidebar.selectbox(
        "Coaching Style",
        ["Conservative", "Balanced", "Aggressive"],
        index=1,
    )

    st.sidebar.markdown("---")

    with st.sidebar.expander("Advanced Settings"):
        simulation_detail = st.selectbox(
            "Simulation Detail",
            options=["Quick", "Standard", "Deep"],
            index=1,
            help="Quick runs faster. Deep runs more simulations and may give steadier results.",
        )

        if simulation_detail == "Quick":
            mode = "fast"
            search_games = 40
            refine_games = 1500
            top_n = 5
            beam_width = 8
            max_rounds = 6
        elif simulation_detail == "Standard":
            mode = "fast"
            search_games = 75
            refine_games = 3000
            top_n = 5
            beam_width = 12
            max_rounds = 8
        else:  # Deep
            mode = "fast"
            search_games = 150
            refine_games = 6000
            top_n = 7
            beam_width = 16
            max_rounds = 10

        seed = st.number_input(
            "Random Seed",
            min_value=0,
            max_value=999999,
            value=42,
            step=1,
            help="Keeps results repeatable while testing.",
        )

        with st.expander("Show internal optimizer settings"):
            st.write(f"Mode: `{mode}`")
            st.write(f"Search games: `{search_games}`")
            st.write(f"Refine games: `{refine_games}`")
            st.write(f"Top N lineups: `{top_n}`")
            st.write(f"Beam width: `{beam_width}`")
            st.write(f"Max rounds: `{max_rounds}`")

    with st.sidebar.expander("Session Info"):
        st.code(session_state.session_id)
        st.write(f"Status: **{session_state.status}**")
        st.write(f"Data source: **{session_state.data_source or 'Not set'}**")

    base_distance_lookup = {
        "46/60": 60,
        "50/70": 70,
        "60/90": 90,
    }

    strategy_lookup = {
        "Balanced": GameStrategy.BALANCED.value,
        "Small Ball": GameStrategy.SMALL_BALL.value,
        "Power": GameStrategy.POWER.value,
    }

    coaching_style_lookup = {
        "Conservative": CoachingStyle.CONSERVATIVE.value,
        "Balanced": CoachingStyle.BALANCED.value,
        "Aggressive": CoachingStyle.AGGRESSIVE.value,
    }

    opposing_pitching_lookup = {
        "Weak": OpposingPitchingStrength.WEAK.value,
        "Average": OpposingPitchingStrength.AVERAGE.value,
        "Strong": OpposingPitchingStrength.STRONG.value,
        "Elite": OpposingPitchingStrength.ELITE.value,
    }

    opponent_level_lookup = {
        "Weak": OpponentLevel.WEAK.value,
        "Average": OpponentLevel.AVERAGE.value,
        "Strong": OpponentLevel.STRONG.value,
    }

    rules_config = {
        "innings": int(innings_per_game),
        "max_runs_per_inning": int(inning_run_limit) if use_inning_run_limit else 999,
        "steals_allowed": True,
        "leadoffs_allowed": bool(leadoffs_allowed),
        "base_distance_ft": int(base_distance_lookup[diamond_size]),
        "continuous_batting": bool(continuous_batting),
        "lineup_size": 9,
        "steal_attempt_multiplier": 1.0,
        "steal_success_multiplier": 1.0,
        "game_strategy": strategy_lookup[strategy_label],
        "coaching_style": coaching_style_lookup[coaching_style_label],
        "opposing_pitching": opposing_pitching_lookup[opposing_pitching_label],
        "opponent_level": opponent_level_lookup[opponent_level_label],
    }

    saved_team_rules_config = {
        "innings": int(innings_per_game),
        "diamond_size": str(diamond_size),
        "leadoffs_allowed": bool(leadoffs_allowed),
        "continuous_batting": bool(continuous_batting),
        "use_inning_run_limit": bool(use_inning_run_limit),
        "inning_run_limit": int(inning_run_limit) if use_inning_run_limit else None,
    }

    save_rules_for_active_team(
        rules_preset=rules_preset,
        rules_config=saved_team_rules_config,
    )

    saved_rules_preset = rules_preset

    return {
        "target_runs": float(target_runs),
        "strategy": strategy_label,
        "coaching_style": coaching_style_label,
        "opposing_pitching": opposing_pitching_label,
        "opponent_level": opponent_level_label,
        "rules_config": rules_config,
        "optimizer_config": {
            "mode": mode,
            "search_games": int(search_games),
            "refine_games": int(refine_games),
            "top_n": int(top_n),
            "seed": int(seed),
            "beam_width": int(beam_width),
            "max_rounds": int(max_rounds),
            "target_runs": float(target_runs),
        },
    }


def inject_custom_styles() -> None:
    st.markdown(
        """
        <style>
        /* ---------- Tabs ---------- */
        div[data-baseweb="tab-list"] {
            gap: 0.5rem;
            border-bottom: 2px solid rgba(250,250,250,0.12);
            padding-bottom: 0.35rem;
            margin-bottom: 1rem;
        }
        
        div[data-testid="stButton"] button {
            padding: 0.35rem 0.65rem !important;
            min-height: 2.1rem !important;
            white-space: nowrap !important;
        }

        button[data-baseweb="tab"] {
            font-size: 1.05rem !important;
            font-weight: 700 !important;
            padding: 0.6rem 1rem !important;
            border: 1px solid rgba(250,250,250,0.12) !important;
            border-radius: 10px 10px 0 0 !important;
            background: rgba(255,255,255,0.02) !important;
        }

        button[data-baseweb="tab"][aria-selected="true"] {
            background: rgba(255,255,255,0.06) !important;
            border-color: rgba(255,255,255,0.24) !important;
        }

        /* ---------- Lineup card ---------- */
        .lineup-card {
            border: 1px solid rgba(250,250,250,0.14);
            border-radius: 16px;
            padding: 1rem 1rem 0.75rem 1rem;
            margin-bottom: 1rem;
            background: rgba(255,255,255,0.02);
        }

        .lineup-card-title {
            font-size: 1.15rem;
            font-weight: 800;
            margin-bottom: 0.75rem;
        }

        .lineup-slot {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            padding: 0.45rem 0.55rem;
            margin-bottom: 0.35rem;
            border-radius: 10px;
            background: rgba(255,255,255,0.025);
        }

        .lineup-slot.top4 {
            background: rgba(255, 215, 0, 0.10);
            border-left: 4px solid rgba(255, 215, 0, 0.75);
        }

        .lineup-slot-num {
            width: 2rem;
            min-width: 2rem;
            text-align: center;
            font-weight: 800;
            font-size: 1rem;
            opacity: 0.95;
        }

        .lineup-slot-name {
            font-size: 1.02rem;
            font-weight: 600;
        }

        .lineup-subnote {
            font-size: 0.92rem;
            opacity: 0.82;
            margin-top: 0.7rem;
        }

        .lineup-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.7rem;
            margin-bottom: 0.35rem;
        }

        .lineup-chip {
            display: inline-block;
            padding: 0.28rem 0.55rem;
            border-radius: 999px;
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            font-size: 0.88rem;
            font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_team_entry_panel(session_state: SessionStateSchema) -> None:
    from core.session_manager import get_session_manager
    from core.auth import get_current_user

    manager = get_session_manager()
    current_user = get_current_user()

    team_summaries = manager.list_team_summaries_for_user(current_user.user_id)
    has_existing_teams = len(team_summaries) > 0

    header = "Build team, change source, or import additional data"
    subheader = "Upload more GameChanger files or create a new team"

    # Source/change workflow stays available, but collapsed because it is occasional.
    if session_state.data_source and not st.session_state.get("show_team_loader", True):
        with st.expander("Current team source / change source", expanded=False):
            render_team_loaded_next_steps(session_state)

            import_summary = st.session_state.get("multi_gc_import_summary")
            if session_state.data_source == "gc_merged" and import_summary:
                with st.container(border=True):
                    st.markdown("#### Multi-file import summary")
                    st.caption(
                        f"Built from {len(import_summary.get('file_names', []))} GameChanger files "
                        f"into {import_summary.get('final_player_count', 0)} merged players."
                    )

            if st.button(
                "Change Team Source",
                use_container_width=True,
                key="show_team_loader_button",
            ):
                st.session_state.show_team_loader = True
                bump_team_entry_expander_token()
                st.rerun()

    expander_open = bool(st.session_state.get("show_team_loader", False))
    token = team_entry_expander_token()
    expander_label = header if token % 2 == 0 else f"{header} "

    if has_existing_teams:
        with st.expander(expander_label, expanded=expander_open):
            st.caption(subheader)
            _render_team_entry_body(session_state)
    else:
        st.markdown(f"## {header}")
        st.caption(subheader)
        _render_team_entry_body(session_state)


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


def clear_lineup_order_widget_state() -> None:
    """
    Clear cached lineup-order widget values so they rebuild from the latest
    backend roster order on the next rerun.
    """
    keys_to_delete = [
        key for key in st.session_state.keys()
        if str(key).startswith("order_")
    ]
    for key in keys_to_delete:
        del st.session_state[key]


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
        c2.metric("Best for current roster", f"{optimized.metrics.mean_runs:.2f}")
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
            f"Best roster chance of {target_runs:.0f}+",
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
    st.caption(
        "Manage the roster, adjust player traits, optimize the active roster, "
        "and test the custom batting order currently shown below."
    )

    with st.expander("What the model is assuming in Coach Lab", expanded=False):
        st.markdown(
            """
- This is a lineup comparison tool, not an exact score predictor.
- GameChanger data is used as directional input and can be noisy if scorekeeping is inconsistent.
- Coach edits and archetype players are meant to help when the imported data is sparse or misleading.
- The most useful question is usually: **Does this lineup tend to look better than my other options?**
            """
        )

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

    with st.container(border=True):
        st.markdown("### Roster and lineup workspace")
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
            st.markdown("### Coach Action")
            st.caption(
                "Run lineup analysis first. Then name and save any lineup you want to compare in the charts."
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

            low_conf_count, medium_conf_count, high_conf_count = build_confidence_summary(editable_profiles)

            if low_conf_count > 0 or medium_conf_count > 0 or high_conf_count > 0:
                with st.container(border=True):
                    st.markdown("#### Roster data confidence")
                    st.caption(
                        "How many players on your roster are this confidence level. Yellow and red players are good candidates for a quick profile check and small coach tweaks before simulating."
                    )

                    conf_col1, conf_col2, conf_col3 = st.columns(3)

                    with conf_col1:
                        if low_conf_count:
                            st.write(f"🔴 Low: {low_conf_count}")
                        else:
                            st.write("🔴 Low: 0")

                    with conf_col2:
                        if medium_conf_count:
                            st.write(f"🟡 Medium: {medium_conf_count}")
                        else:
                            st.write("🟡 Medium: 0")

                    with conf_col3:
                        if high_conf_count:
                            st.write(f"🟢 High: {high_conf_count}")
                        else:
                            st.write("🟢 High: 0")

                    if low_conf_count > 0:
                        st.markdown(
                            """
                    **Recommended workflow for low-confidence players**
                    1. Open that player in Coach Lab  
                    2. Check whether the imported profile matches what you see in real games  
                    3. Make a small trait edit or swap to a more realistic archetype if needed  
                    4. Re-simulate or re-optimize the lineup  
                            """
                        )

                    elif medium_conf_count > 0:
                        left, center, right = st.columns([1, 2, 1])
                        with center:
                            st.caption(
                                "Most players currently have moderate sample sizes. A quick inspection of detailed profiles can help you fine tune the lineup before simulating."
                            )

                    elif high_conf_count > 0:
                        left, center, right = st.columns([1, 2, 1])
                        with center:
                            st.caption(
                                "Most players have strong data behind them, so the imported profiles should be a solid starting point."
                            )

        st.markdown("##### Active batting order")
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

    baseline_results = results or st.session_state.get("last_completed_results")

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
    st.markdown(
        """
    <div style="
    padding:18px 22px;
    border-radius:18px;
    border:1px solid rgba(120,160,255,.28);
    background: linear-gradient(
    180deg,
    rgba(56,109,255,.12),
    rgba(56,109,255,.04)
    );
    margin-bottom:1rem;
    ">
    <div style="
    font-size:2rem;
    font-weight:800;
    letter-spacing:.01em;
    margin-bottom:.35rem;
    ">
    ⚾ Coach Lab
    </div>

    <div style="
    font-size:1.02rem;
    opacity:.88;
    ">
    Build, test, optimize, and compare batting orders using simulation.
    </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

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


def render_model_limitations() -> None:
    st.markdown("### How this tool works")
    st.write(
        "This tool builds a player profile for each hitter, then plays out many simulated games "
        "to compare batting orders under the same rules and game conditions."
    )

    st.markdown("### What it is best used for")
    st.write(
        "It is best used to compare lineup ideas and see which batting orders tend to score more over time."
    )
    st.write(
        "It is a coaching decision aid, not a promise of the exact score in your next game."
    )

    st.markdown("### How player edits affect the simulation")
    rows = [
        {
            "Slider / Trait": "Contact",
            "What it does": "Helps a hitter put the ball in play more often, get more singles, and strike out less.",
        },
        {
            "Slider / Trait": "Power",
            "What it does": "Raises extra-base hit and home run upside.",
        },
        {
            "Slider / Trait": "Speed",
            "What it does": "Helps with steals, pressure on the defense, and taking extra bases.",
        },
        {
            "Slider / Trait": "Baserunning",
            "What it does": "Helps runners make better decisions and take extra bases more often.",
        },
        {
            "Slider / Trait": "Plate Discipline",
            "What it does": "Helps a hitter work better at-bats and draw more walks.",
        },
        {
            "Slider / Trait": "Strikeout Tendency",
            "What it does": "Higher values mean the hitter strikes out more often.",
        },
        {
            "Slider / Trait": "Walk Skill",
            "What it does": "Raises walk rate directly.",
        },
        {
            "Slider / Trait": "Aggression",
            "What it does": "Makes runners more willing to push the action on the bases.",
        },
        {
            "Slider / Trait": "Sacrifice Ability",
            "What it does": "Supports small-ball and move-the-runner style play.",
        },
        {
            "Slider / Trait": "Chase Tendency",
            "What it does": "Tracked in the player profile, but not yet a major direct driver in the simulation.",
        },
        {
            "Slider / Trait": "Clutch",
            "What it does": "Stored in the player profile, but not yet a major direct driver in the simulation.",
        },
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("### How game settings affect the simulation")
    game_rows = [
        {
            "Setting": "Game Strategy",
            "What it does": "Small Ball leans more toward pressure and runner movement. Power leans more toward damage. Balanced stays in the middle.",
        },
        {
            "Setting": "Coaching Style",
            "What it does": "Changes how aggressive the team is on the bases.",
        },
        {
            "Setting": "Opposing Pitching Strength",
            "What it does": "Makes it easier or harder to make contact, draw walks, and do damage.",
        },
        {
            "Setting": "Opponent Level",
            "What it does": "Changes how easy it is to take extra bases and move runners.",
        },
        {
            "Setting": "Continuous Batting",
            "What it does": "If turned on, the full active roster bats. If turned off, only the top part of the lineup bats.",
        },
        {
            "Setting": "Inning Run Limit",
            "What it does": "Caps how many runs can score in one inning, which matters a lot in youth baseball.",
        },
        {
            "Setting": "Diamond Size",
            "What it does": "Smaller diamonds usually increase steals, speed value, and overall pressure on the defense.",
        },
        {
            "Setting": "Leadoffs Allowed",
            "What it does": "Greatly increases running pressure and usually reduces double-play chances.",
        },
    ]
    st.dataframe(game_rows, use_container_width=True, hide_index=True)

    st.markdown("### Important limits")
    limits_rows = [
        {
            "Area": "Exact score prediction",
            "What to know": "This tool compares lineups. It does not predict the exact score of a real game.",
        },
        {
            "Area": "GameChanger data quality",
            "What to know": "If the scorebook is wrong, the player profile can be off until the coach adjusts it.",
        },
        {
            "Area": "Batted-ball detail",
            "What to know": "It does not directly know each hitter's true ground-ball or fly-ball tendency from GameChanger.",
        },
        {
            "Area": "Youth baseball chaos",
            "What to know": "It does not separately model every overthrow, missed tag, or weird youth-baseball play.",
        },
        {
            "Area": "Defense and pitching",
            "What to know": "It does not fully model defensive positioning, pitcher fatigue, or detailed matchup effects.",
        },
        {
            "Area": "Double plays and fielder's choice",
            "What to know": "These are included only in a simplified way.",
        },
    ]
    st.dataframe(limits_rows, use_container_width=True, hide_index=True)

    st.markdown("### Best way to use it")
    st.write(
        "The best question to ask is: 'Does this lineup usually look stronger than my other options?'"
    )


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

    render_signed_in_banner()

    st.title(APP_TITLE)
    st.caption(APP_SUBTITLE)

    render_how_to_use_panel()
    render_team_switcher()

    run_settings = render_sidebar(backend_session)
    st.session_state.run_settings_cache = run_settings

    render_model_limitations_panel()

    render_team_entry_panel(backend_session)

    render_additional_gc_data_panel(backend_session)
    st.markdown("")

    existing_results = safe_get_results()
    render_results(existing_results)


if __name__ == "__main__":
    main()