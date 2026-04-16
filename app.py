from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable
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

from core.archetypes import PlayerArchetype

from core.api_service import (
    add_player_from_archetype,
    apply_lineup_to_active_roster,
    bench_player,
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
    set_player_adjustment,
    set_player_order,
    unbench_player,
    update_player_identity,
    update_player_traits,
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
APP_SUBTITLE = "Monte Carlo lineup optimization for baseball coaches"


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
        st.session_state.show_team_loader = True

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

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("**1. Player absent tonight**")
            st.caption(
                "Bench the absent player, then optimize or simulate again to see how the order should shift."
            )

        with col2:
            st.markdown("**2. Try a new player**")
            st.caption(
                "Add a player from an archetype, place him in the order, and simulate how he changes the lineup."
            )

        with col3:
            st.markdown("**3. Compare your intuition**")
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


def render_sidebar(session_state: SessionStateSchema) -> dict:

    target_runs = 4.0   #keeps old code happy but unused as of 4/14/2026

    st.sidebar.markdown("## ⚾ Game Rules")
    st.sidebar.caption("Set the game conditions.")

    innings_per_game = st.sidebar.slider("Innings / Game", 3, 9, 6)

    continuous_batting = st.sidebar.checkbox("Continuous Batting", value=True)

    use_inning_run_limit = st.sidebar.checkbox("Inning Run Limit", value=True)

    inning_run_limit = None
    if use_inning_run_limit:
        inning_run_limit = st.sidebar.number_input("Max runs per inning", min_value=1, max_value=20, value=5)

    diamond_size = st.sidebar.selectbox(
        "Diamond Size",
        ["46/60", "50/70", "60/90"],
        index=1,
    )

    leadoffs_allowed = st.sidebar.checkbox("Leadoffs Allowed", value=False)

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
    st.markdown("## Build your team and lineup")
    st.caption(
        "Start with a GameChanger import or begin with an empty roster and build the team in Coach Lab."
    )

    if session_state.data_source and not st.session_state.get("show_team_loader", True):
        loaded_col1, loaded_col2 = st.columns([3, 1])

        with loaded_col1:
            render_team_loaded_next_steps(session_state)

            import_summary = st.session_state.get("multi_gc_import_summary")
            if session_state.data_source == "gc_merged" and import_summary:
                with st.container(border=True):
                    st.markdown("#### Multi-file import summary")
                    st.caption(
                        f"Built from {len(import_summary.get('file_names', []))} GameChanger files "
                        f"into {import_summary.get('final_player_count', 0)} merged players."
                    )

        with loaded_col2:
            with st.container(border=True):
                st.markdown("### Team source")
                if st.button(
                    "Change Team Source",
                    use_container_width=True,
                    key="show_team_loader_button",
                ):
                    st.session_state.show_team_loader = True
                    st.rerun()
        return

    with st.container(border=True):
        st.markdown("### Start here")

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
                            st.session_state.optimizer_session_id,
                            csv_path=csv_path,
                            adjustments_path=None,
                            data_source="gc",
                        )

                        initialize_editable_roster(st.session_state.optimizer_session_id)

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
                        st.session_state.optimizer_session_id,
                        data_source="manual_archetypes",
                    )

                    initialize_editable_roster(st.session_state.optimizer_session_id)

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
            reset_session_results(st.session_state.optimizer_session_id)
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
        session_obj = get_session_manager().get_session(st.session_state.optimizer_session_id)
        benched_names = set(session_obj.benched_player_names)
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
        session_obj = get_session_manager().get_session(st.session_state.optimizer_session_id)
        return list(session_obj.benched_player_names)
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

    archetype_value = getattr(profile.archetype, "value", str(profile.archetype))
    handedness_value = getattr(profile.handedness, "value", str(profile.handedness))

    base_traits = getattr(profile, "base_traits")
    effective_traits = getattr(profile, "effective_traits", base_traits)
    current_adj = get_profile_adjustment_dict(profile)

    slot_label = f"#{slot_number}" if slot_number is not None else "Bench"
    status_label = "Benched" if is_benched else "Active"

    archetype_label = format_archetype_label(archetype_value)

    summary = (
        f"{slot_label} • {profile.name} • {archetype_label} • {status_label} | "
        f"C {effective_traits.contact:.0f}  "
        f"P {effective_traits.power:.0f}  "
        f"S {effective_traits.speed:.0f}  "
        f"Disc {effective_traits.plate_discipline:.0f}"
    )

    with st.expander(summary, expanded=False):
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
                key=f"edit_name_{key}",
            )

        archetype_options = [a.value for a in PlayerArchetype]
        with id_col2:
            new_archetype = st.selectbox(
                "Archetype",
                options=archetype_options,
                index=archetype_options.index(archetype_value) if archetype_value in archetype_options else 0,
                key=f"edit_archetype_{key}",
            )

        handedness_options = ["R", "L", "S", "U"]
        with id_col3:
            new_handedness = st.selectbox(
                "Handedness",
                options=handedness_options,
                index=handedness_options.index(handedness_value) if handedness_value in handedness_options else 3,
                key=f"edit_handedness_{key}",
            )

        st.markdown("##### Traits")

        trait_col1, trait_col2, trait_col3 = st.columns(3)

        with trait_col1:
            contact = st.slider("Contact", 0, 100, int(round(base_traits.contact)), key=f"contact_{key}")
            baserunning = st.slider("Baserunning", 0, 100, int(round(base_traits.baserunning)), key=f"baserunning_{key}")
            walk_skill = st.slider("Walk Skill", 0, 100, int(round(base_traits.walk_skill)), key=f"walk_skill_{key}")
            aggression = st.slider("Aggression", 0, 100, int(round(base_traits.aggression)), key=f"aggression_{key}")

        with trait_col2:
            power = st.slider("Power", 0, 100, int(round(base_traits.power)), key=f"power_{key}")
            plate_discipline = st.slider("Plate Discipline", 0, 100, int(round(base_traits.plate_discipline)), key=f"plate_discipline_{key}")
            chase_tendency = st.slider("Chase Tendency", 0, 100, int(round(base_traits.chase_tendency)), key=f"chase_tendency_{key}")
            clutch = st.slider("Clutch", 0, 100, int(round(base_traits.clutch)), key=f"clutch_{key}")

        with trait_col3:
            speed = st.slider("Speed", 0, 100, int(round(base_traits.speed)), key=f"speed_{key}")
            strikeout_tendency = st.slider("K Tendency", 0, 100, int(round(base_traits.strikeout_tendency)), key=f"strikeout_tendency_{key}")
            sacrifice_ability = st.slider("Sacrifice Ability", 0, 100, int(round(base_traits.sacrifice_ability)), key=f"sacrifice_ability_{key}")

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

        save_cols = st.columns(2)

        with save_cols[0]:
            if st.button("Save changes", use_container_width=True, key=f"save_player_editor_{key}"):
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

                        st.session_state.coach_lab_workspace_mode = "custom"
                        st.success(f"Saved changes to {cleaned_name}.")
                        st.rerun()
                except Exception as exc:
                    st.error(f"Could not save player changes: {exc}")

        with save_cols[1]:
            if st.button("Clear saved nudge", use_container_width=True, key=f"clear_player_nudge_from_editor_{key}"):
                try:
                    clear_player_adjustment(
                        st.session_state.optimizer_session_id,
                        player_name=profile.name,
                    )
                    st.success(f"Cleared saved nudge for {profile.name}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not clear saved nudge: {exc}")


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
        items.append(scenario.result)

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
    # Survival curve
    # -----------------------------
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

    # -----------------------------
    # Bucket comparison
    # -----------------------------
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
                metrics = scenario.result.metrics
                target_runs = metrics.target_runs or 4.0

                metric_cols = st.columns(3)
                metric_cols[0].metric("Avg runs", f"{metrics.mean_runs:.2f}")
                metric_cols[1].metric(
                    f"Chance of {target_runs:.0f}+ runs",
                    f"{metrics.prob_ge_target:.1%}",
                )
                metric_cols[2].metric("Median runs", f"{metrics.median_runs:.2f}")
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

        st.markdown("##### Coach decision workflows")
        st.caption(
            "Use this area for the three most common decisions: who is absent tonight, where a new player fits, "
            "and whether your own lineup grades better or worse than the optimized one."
        )

        workflow_cols = st.columns(3)
        with workflow_cols[0]:
            st.info("**Absent player tonight**\n\nBench him, then optimize or simulate again.")
        with workflow_cols[1]:
            st.info("**New player insertion**\n\nAdd a player below, place him in the order, then simulate.")
        with workflow_cols[2]:
            st.info("**My lineup vs optimized**\n\nSimulate your order, save it, then compare it to the optimized order in charts.")

        st.caption(
            "How to use these controls right now: "
            "Bench an absent player in the active lineup below. "
            "Use Add player from archetype to test a new player. "
            "Reorder the lineup, click Simulate My Lineup, then click Save Scenario for Charts to compare it."
        )
        st.divider()

        lineup_action_col1, lineup_action_col2 = st.columns([1.45, 2.55])

        with lineup_action_col1:
            scenario_name = st.text_input(
                "Scenario name",
                value=f"Scenario {len(get_saved_scenarios_for_ui()) + 1}",
                key="dashboard_scenario_name",
            )

        with lineup_action_col2:
            action_cols = st.columns(3)

            with action_cols[0]:
                if st.button(
                        "Optimize Current Roster",
                        use_container_width=True,
                        key="dashboard_optimize_current_roster",
                ):
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

                            clear_lineup_order_widget_state()

                        st.success("Optimized lineup loaded into dashboard.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not optimize current roster: {exc}")

            with action_cols[1]:
                if st.button(
                        "Simulate My Lineup",
                        use_container_width=True,
                        type="primary",
                        key="dashboard_simulate_lineup",
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

                        custom_eval = evaluate_custom_lineup(
                            st.session_state.optimizer_session_id,
                            target_runs=run_settings["target_runs"],
                            n_games=run_settings["optimizer_config"]["refine_games"],
                            seed=run_settings["optimizer_config"]["seed"],
                            display_name="Coach Custom Order",
                            rules=RulesConfig(**run_settings["rules_config"]),
                        )
                        st.session_state.coach_lab_last_custom_eval = custom_eval
                        st.session_state.coach_lab_workspace_mode = "custom"
                        st.success("Current custom batting order simulated.")
                    except Exception as exc:
                        st.error(f"Could not simulate custom order: {exc}")

            with action_cols[2]:
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

                        saved_name = scenario_name.strip() or f"Scenario {len(get_saved_scenarios_for_ui()) + 1}"

                        custom_eval = evaluate_custom_lineup(
                            st.session_state.optimizer_session_id,
                            target_runs=run_settings["target_runs"],
                            n_games=run_settings["optimizer_config"]["refine_games"],
                            seed=run_settings["optimizer_config"]["seed"],
                            display_name=saved_name,
                            rules=RulesConfig(**run_settings["rules_config"]),
                        )
                        st.session_state.coach_lab_last_custom_eval = custom_eval

                        save_current_scenario(
                            st.session_state.optimizer_session_id,
                            name=saved_name,
                        )

                        existing = st.session_state.get("coach_lab_saved_scenario_messages", [])
                        existing.append(f"Saved scenario: {saved_name}")
                        st.session_state.coach_lab_saved_scenario_messages = existing[-12:]

                        st.success(f"Saved scenario: {saved_name}. It now appears in the comparison charts below.")
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
    render_coach_lab_comparison_section(results=baseline_results)

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
    st.markdown("## Coach Lab")

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
        "weak_hitter": "Weak Hitter",
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
    backend_session = get_backend_session()

    st.title(APP_TITLE)
    st.caption(APP_SUBTITLE)

    run_settings = render_sidebar(backend_session)
    st.session_state.run_settings_cache = run_settings

    render_how_to_use_panel()
    render_model_limitations_panel()

    render_team_entry_panel(backend_session)
    st.markdown("")

    existing_results = safe_get_results()
    render_results(existing_results)


if __name__ == "__main__":
    main()