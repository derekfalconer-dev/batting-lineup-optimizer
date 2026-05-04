import streamlit as st

from core.models import (
    GameStrategy,
    CoachingStyle,
    OpposingPitchingStrength,
    OpponentLevel,
)

from core.schemas import SessionStateSchema


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