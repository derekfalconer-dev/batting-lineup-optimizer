from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import random

from core.evaluator import evaluate_lineup_with_telemetry
from core.models import RulesConfig, compile_rules_context
from core.optimizer import find_best_lineups
from core.visualization import (
    plot_lineup_histograms,
    plot_lineup_cdfs,
    plot_lineup_survival_curves,
    plot_lineup_bucket_bars,
)
from core.workflow import (
    load_gc_team,
    load_manual_archetype_team,
    load_manual_traits_team,
)

from core.presenters import present_workflow_result

from core.json_io import load_json_file
from core.validation import (
    validate_profiles,
    validate_adjustments,
    validate_manual_archetype_roster,
    validate_manual_traits_roster,
)


@dataclass(slots=True)
class WorkflowResult:
    team_source: str
    profiles: list[Any]
    players: list[Any]

    results: dict[str, list[dict[str, Any]]]

    optimized: dict[str, Any]
    original: dict[str, Any]
    random_lineup: dict[str, Any]
    worst_lineup: dict[str, Any]

    comparison_set: list[dict[str, Any]]

    summary: dict[str, Any]
    chart_paths: dict[str, str]

    custom_lineup: dict[str, Any] | None = None


def run_optimizer_workflow(
    *,
    data_source: str,
    csv_path: str | Path | None = None,
    adjustments_path: str | Path | None = None,
    roster_path: str | Path | None = None,
    rules: RulesConfig | None = None,
    output_dir: str | Path = "output",
    target_runs: float = 4.0,
    optimizer_config: dict[str, Any] | None = None,
    present: bool = False,
    adjustments_by_name: dict[str, dict[str, float]] | None = None,
    profiles_override: Sequence[Any] | None = None,
):
    """
    Main orchestration entry point for CLI / Streamlit / FastAPI.

    Supported data_source values:
        - "gc"
        - "gc_plus_tweaks"
        - "manual_archetypes"
        - "manual_traits"

    Returns:
        - WorkflowResult when present=False
        - WorkflowResponseSchema when present=True
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rules = rules or RulesConfig(
        innings=6,
        max_runs_per_inning=5,
        steals_allowed=True,
        leadoffs_allowed=False,
        base_distance_ft=70,
        continuous_batting=True,
        lineup_size=9,
        steal_attempt_multiplier=1.0,
        steal_success_multiplier=1.0,
    )

    rules = compile_rules_context(rules)

    optimizer_defaults: dict[str, Any] = {
        "mode": "fast",
        "target_runs": target_runs,
        "search_games": 75,
        "refine_games": 3000,
        "top_n": 5,
        "seed": 42,
        "beam_width": 12,
        "max_rounds": 8,
    }
    if optimizer_config:
        optimizer_defaults.update(optimizer_config)

    if profiles_override is not None:
        from core.player_factory import build_team_from_profiles
        team = build_team_from_profiles(
            profiles_override,
            adjustments_by_name=adjustments_by_name,
            source="session_roster",
        )
    else:
        team = _load_team(
            data_source=data_source,
            csv_path=csv_path,
            adjustments_path=adjustments_path,
            roster_path=roster_path,
            adjustments_by_name=adjustments_by_name,
        )

    profiles = team.profiles
    players = _apply_environment_to_players(team.players, rules)
    players = _resolve_active_players(players, rules)

    validation_errors = validate_profiles(profiles)

    if adjustments_by_name is not None:
        validation_errors.extend(validate_adjustments(profiles, adjustments_by_name))
    elif data_source == "gc_plus_tweaks" and adjustments_path is not None:
        file_adjustments = load_json_file(adjustments_path)
        validation_errors.extend(validate_adjustments(profiles, file_adjustments))

    if validation_errors:
        raise ValueError("\n".join(validation_errors))

    if not players:
        raise ValueError(f"No players were loaded for data_source={data_source}")

    results = find_best_lineups(
        players=players,
        rules=rules,
        mode=optimizer_defaults["mode"],
        target_runs=optimizer_defaults["target_runs"],
        search_games=optimizer_defaults["search_games"],
        refine_games=optimizer_defaults["refine_games"],
        top_n=optimizer_defaults["top_n"],
        seed=optimizer_defaults["seed"],
        beam_width=optimizer_defaults["beam_width"],
        max_rounds=optimizer_defaults["max_rounds"],
    )

    optimized = dict(results["top_mean"][0])
    optimized["display_name"] = "Optimized"

    original = _evaluate_named_lineup(
        display_name="Original",
        lineup=players,
        rules=rules,
        target_runs=target_runs,
        seed=101,
        n_games=optimizer_defaults["refine_games"],
    )

    random_players = players[:]
    random.Random(202).shuffle(random_players)
    random_lineup = _evaluate_named_lineup(
        display_name="Random",
        lineup=random_players,
        rules=rules,
        target_runs=target_runs,
        seed=202,
        n_games=optimizer_defaults["refine_games"],
    )

    worst_players = list(reversed(sorted(
        players,
        key=lambda p: p.p_bb + p.p_1b + p.p_2b + p.p_3b + p.p_hr,
        reverse=True,
    )))
    worst_lineup = _evaluate_named_lineup(
        display_name="Worst-Case",
        lineup=worst_players,
        rules=rules,
        target_runs=target_runs,
        seed=303,
        n_games=optimizer_defaults["refine_games"],
    )

    comparison_set = [
        optimized,
        original,
        random_lineup,
        worst_lineup,
    ]

    chart_paths = _build_charts(
        comparison_set=comparison_set,
        output_dir=output_dir,
    )

    summary = _build_summary(
        optimized=optimized,
        original=original,
        profiles=profiles,
        team_source=team.source,
        target_runs=target_runs,
        optimizer_results=results,
    )

    raw_result = WorkflowResult(
        team_source=team.source,
        profiles=profiles,
        players=players,
        results=results,
        optimized=optimized,
        original=original,
        random_lineup=random_lineup,
        worst_lineup=worst_lineup,
        comparison_set=comparison_set,
        summary=summary,
        chart_paths=chart_paths,
    )

    if present:
        return present_workflow_result(raw_result)

    return raw_result


def run_presented_optimizer_workflow(
    *,
    data_source: str,
    csv_path: str | Path | None = None,
    adjustments_path: str | Path | None = None,
    roster_path: str | Path | None = None,
    rules: RulesConfig | None = None,
    output_dir: str | Path = "output",
    target_runs: float = 4.0,
    optimizer_config: dict[str, Any] | None = None,
):
    return run_optimizer_workflow(
        data_source=data_source,
        csv_path=csv_path,
        adjustments_path=adjustments_path,
        roster_path=roster_path,
        rules=rules,
        output_dir=output_dir,
        target_runs=target_runs,
        optimizer_config=optimizer_config,
        present=True,
    )


def evaluate_lineup_workflow(
    *,
    data_source: str,
    lineup_names: list[str],
    csv_path: str | Path | None = None,
    adjustments_path: str | Path | None = None,
    roster_path: str | Path | None = None,
    rules: RulesConfig | None = None,
    target_runs: float = 4.0,
    n_games: int = 3000,
    seed: int = 777,
    adjustments_by_name: dict[str, dict[str, float]] | None = None,
    display_name: str = "Coach Custom",
    profiles_override: Sequence[Any] | None = None,
):
    """
    Evaluate one exact lineup, without searching for an optimum.

    This is the backend entry point for Coach Lab "What If" lineup testing.
    """
    rules = rules or RulesConfig(
        innings=6,
        max_runs_per_inning=5,
        steals_allowed=True,
        leadoffs_allowed=False,
        base_distance_ft=70,
        continuous_batting=True,
        lineup_size=9,
        steal_attempt_multiplier=1.0,
        steal_success_multiplier=1.0,
    )

    rules = compile_rules_context(rules)

    if profiles_override is not None:
        from core.player_factory import build_team_from_profiles
        team = build_team_from_profiles(
            profiles_override,
            adjustments_by_name=adjustments_by_name,
            source="session_roster",
        )
    else:
        team = _load_team(
            data_source=data_source,
            csv_path=csv_path,
            adjustments_path=adjustments_path,
            roster_path=roster_path,
            adjustments_by_name=adjustments_by_name,
        )

    profiles = team.profiles
    players = _apply_environment_to_players(team.players, rules)
    players = _resolve_active_players(players, rules)

    validation_errors = validate_profiles(profiles)

    if adjustments_by_name is not None:
        validation_errors.extend(validate_adjustments(profiles, adjustments_by_name))
    elif data_source == "gc_plus_tweaks" and adjustments_path is not None:
        file_adjustments = load_json_file(adjustments_path)
        validation_errors.extend(validate_adjustments(profiles, file_adjustments))

    if validation_errors:
        raise ValueError("\n".join(validation_errors))

    expected_size = len(players)

    ordered_players = _order_players_by_names(
        players,
        lineup_names,
        expected_size=expected_size,
    )

    custom_lineup = _evaluate_named_lineup(
        display_name=display_name,
        lineup=ordered_players,
        rules=rules,
        target_runs=target_runs,
        seed=seed,
        n_games=n_games,
    )

    return {
        "team_source": team.source,
        "profiles": profiles,
        "players": players,
        "custom_lineup": custom_lineup,
    }


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _trait(player: Any, name: str, default: float = 0.5) -> float:
    return _clamp_float(float(getattr(player, name, default) or default), 0.0, 1.0)


def _personalized_pitcher_effects(player: Any, rules: RulesConfig) -> dict[str, float]:
    """
    Convert selected opponent pitcher multipliers into batter-specific multipliers.

    Raw pitcher profile still matters:
    - Musicant's 1.85 strikeout pressure stays extreme.
    - Pannese's walk volatility stays high.

    But batter traits shape exposure:
    - high-contact bats resist K pressure better
    - high-K / chase bats get hurt more by high-K arms
    - disciplined / walk-skill bats benefit more from wild arms
    """

    pitcher_k = float(getattr(rules, "opponent_pitcher_strikeout_multiplier", 1.0) or 1.0)
    pitcher_bb = float(getattr(rules, "opponent_pitcher_walk_multiplier", 1.0) or 1.0)
    pitcher_contact = float(getattr(rules, "opponent_pitcher_contact_multiplier", 1.0) or 1.0)
    pitcher_power = float(getattr(rules, "opponent_pitcher_power_multiplier", 1.0) or 1.0)

    contact = _trait(player, "contact_trait")
    power = _trait(player, "power_trait")
    discipline = _trait(player, "discipline_trait")
    walk_skill = _trait(player, "walk_skill_trait")
    k_tendency = _trait(player, "strikeout_tendency_trait")
    chase = _trait(player, "chase_tendency_trait")

    # High-contact hitters are more resilient. High-K/chase hitters are more exposed.
    k_exposure = (
        0.90
        + 0.42 * k_tendency
        + 0.20 * chase
        - 0.38 * contact
        - 0.12 * discipline
    )
    k_exposure = _clamp_float(k_exposure, 0.55, 1.30)

    # Contact suppression also hurts low-contact/high-K bats more.
    contact_exposure = (
        0.95
        + 0.34 * k_tendency
        + 0.16 * chase
        - 0.36 * contact
    )
    contact_exposure = _clamp_float(contact_exposure, 0.55, 1.25)

    # Walk effects:
    # - wild pitchers: disciplined hitters gain more
    # - strike throwers: disciplined hitters are less suppressed
    if pitcher_bb >= 1.0:
        walk_exposure = (
            0.75
            + 0.45 * walk_skill
            + 0.35 * discipline
            - 0.25 * chase
        )
    else:
        walk_exposure = (
            1.15
            - 0.35 * walk_skill
            - 0.30 * discipline
            + 0.20 * chase
        )
    walk_exposure = _clamp_float(walk_exposure, 0.45, 1.45)

    # Power impact is light for now. Strong power hitters preserve more damage.
    power_exposure = 1.00 - 0.25 * power + 0.15 * k_tendency
    power_exposure = _clamp_float(power_exposure, 0.65, 1.20)

    return {
        "strikeout": 1.0 + ((pitcher_k - 1.0) * k_exposure),
        "walk": 1.0 + ((pitcher_bb - 1.0) * walk_exposure),
        "contact": 1.0 + ((pitcher_contact - 1.0) * contact_exposure),
        "power": 1.0 + ((pitcher_power - 1.0) * power_exposure),
    }


def _apply_environment_to_players(
    players: list[Any],
    rules: RulesConfig,
) -> list[Any]:
    """
    Apply matchup/environment adjustments after players are built from profiles.

    This keeps:
    - PlayerProfile as source of truth
    - RulesConfig as game-specific environment/context
    """
    tuned_players: list[Any] = []

    for p in players:
        player = replace(p)

        player.p_1b *= rules.contact_multiplier
        player.p_2b *= rules.contact_multiplier * rules.power_multiplier
        player.p_3b *= rules.contact_multiplier * rules.power_multiplier
        player.p_hr *= rules.power_multiplier
        player.p_bb *= rules.walk_multiplier
        player.p_so *= rules.strikeout_multiplier

        use_pitcher = (
                getattr(rules, "use_opponent_scouting", False)
                or getattr(rules, "use_manual_opponent_pitcher", False)
        )

        if use_pitcher:
            matchup = _personalized_pitcher_effects(player, rules)

            player.p_1b *= matchup["contact"]
            player.p_2b *= matchup["contact"] * matchup["power"]
            player.p_3b *= matchup["contact"] * matchup["power"]
            player.p_hr *= matchup["power"]
            player.p_bb *= matchup["walk"]
            player.p_so *= matchup["strikeout"]

        player.normalize()
        tuned_players.append(player)

    return tuned_players


def _resolve_active_players(
    players: list[Any],
    rules: RulesConfig,
) -> list[Any]:
    """
    Continuous batting uses the whole roster.
    Bat top 9 only uses the first lineup_size players from the active roster.
    """
    if getattr(rules, "continuous_batting", True):
        return players[:]

    lineup_size = max(1, min(int(getattr(rules, "lineup_size", 9)), len(players)))
    return players[:lineup_size]


def _load_team(
    *,
    data_source: str,
    csv_path: str | Path | None,
    adjustments_path: str | Path | None,
    roster_path: str | Path | None,
    adjustments_by_name: dict[str, dict[str, float]] | None = None,
):
    if data_source == "gc":
        if csv_path is None:
            raise ValueError("csv_path is required for data_source='gc'")

        if adjustments_by_name:
            from core.player_factory import build_team_from_gamechanger
            return build_team_from_gamechanger(
                csv_path=csv_path,
                min_pa=5,
                name_format="full",
                adjustments_by_name=adjustments_by_name,
            )

        return load_gc_team(
            csv_path=csv_path,
            adjustments_path=None,
        )

    if data_source == "gc_plus_tweaks":
        if csv_path is None:
            raise ValueError("csv_path is required for data_source='gc_plus_tweaks'")

        resolved_adjustments = adjustments_by_name

        if resolved_adjustments is None and adjustments_path is not None:
            resolved_adjustments = load_json_file(adjustments_path)

        if resolved_adjustments is not None:
            adjustment_errors = validate_adjustments([], resolved_adjustments)
            if adjustment_errors:
                raise ValueError("\n".join(adjustment_errors))

        if resolved_adjustments is not None:
            from core.player_factory import build_team_from_gamechanger
            return build_team_from_gamechanger(
                csv_path=csv_path,
                min_pa=5,
                name_format="full",
                adjustments_by_name=resolved_adjustments,
            )

        return load_gc_team(
            csv_path=csv_path,
            adjustments_path=None,
        )

    if data_source == "manual_archetypes":
        if roster_path is None:
            raise ValueError("roster_path is required for data_source='manual_archetypes'")

        roster = load_json_file(roster_path)
        roster_errors = validate_manual_archetype_roster(roster)
        if roster_errors:
            raise ValueError("\n".join(roster_errors))

        return load_manual_archetype_team(
            roster_path=roster_path,
        )

    if data_source == "manual_traits":
        if roster_path is None:
            raise ValueError("roster_path is required for data_source='manual_traits'")

        roster = load_json_file(roster_path)
        roster_errors = validate_manual_traits_roster(roster)
        if roster_errors:
            raise ValueError("\n".join(roster_errors))

        return load_manual_traits_team(
            roster_path=roster_path,
        )

    raise ValueError(f"Unknown data_source: {data_source}")


def _order_players_by_names(
    players: list[Any],
    lineup_names: list[str],
    *,
    expected_size: int | None = None,
) -> list[Any]:
    if not lineup_names:
        raise ValueError("custom lineup cannot be empty")

    player_map = {p.name: p for p in players}

    missing = [name for name in lineup_names if name not in player_map]
    if missing:
        raise ValueError(
            f"Custom lineup contains unknown player(s): {', '.join(missing)}"
        )

    if len(set(lineup_names)) != len(lineup_names):
        raise ValueError("Custom lineup contains duplicate player names")

    if expected_size is not None and len(lineup_names) != expected_size:
        raise ValueError(
            f"Custom lineup has {len(lineup_names)} players but expected {expected_size}"
        )

    return [player_map[name] for name in lineup_names]


def _evaluate_named_lineup(
    *,
    display_name: str,
    lineup: list[Any],
    rules: RulesConfig,
    target_runs: float,
    seed: int,
    n_games: int = 3000,
) -> dict[str, Any]:
    result, telemetry = evaluate_lineup_with_telemetry(
        lineup=lineup,
        rules=rules,
        n_games=n_games,
        target_runs=target_runs,
        seed=seed,
        display_name=display_name,
    )
    return {
        "display_name": display_name,
        "lineup": result.lineup,
        "players": lineup[:],
        "mean_runs": result.mean_runs,
        "median_runs": result.median_runs,
        "std_runs": result.std_runs,
        "prob_ge_target": result.prob_ge_target,
        "sortino": result.sortino,
        "p10_runs": result.p10_runs,
        "p90_runs": result.p90_runs,
        "n_games": result.n_games,
        "target_runs": target_runs,
        "runs_scored_distribution": result.runs_scored_distribution,
        "simulation_telemetry": telemetry,
    }


def _build_summary(
    *,
    optimized: dict[str, Any],
    original: dict[str, Any],
    profiles: list[Any],
    team_source: str,
    target_runs: float,
    optimizer_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    optimized_prob = optimized["prob_ge_target"]
    original_prob = original["prob_ge_target"]
    improvement = optimized_prob - original_prob

    source_mode_counts: dict[str, int] = {}
    for profile in profiles:
        mode = getattr(profile, "source_mode", None)
        key = mode.value if mode is not None else "unknown"
        source_mode_counts[key] = source_mode_counts.get(key, 0) + 1

    return {
        "team_source": team_source,
        "n_players": len(profiles),
        "target_runs": target_runs,
        "optimized_prob_ge_target": optimized_prob,
        "original_prob_ge_target": original_prob,
        "improvement_prob_ge_target": improvement,
        "optimized_mean_runs": optimized["mean_runs"],
        "original_mean_runs": original["mean_runs"],
        "improvement_mean_runs": optimized["mean_runs"] - original["mean_runs"],
        "optimized_lineup": optimized["lineup"],
        "original_lineup": original["lineup"],
        "source_mode_counts": source_mode_counts,
        "optimizer_meta": dict((optimizer_results or {}).get("_meta", {})),
    }


def _build_charts(
    *,
    comparison_set: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, str]:
    hist_path = str(output_dir / "comparison_histograms.png")
    cdf_path = str(output_dir / "comparison_cdfs.png")
    survival_path = str(output_dir / "comparison_survival_curves.png")
    bucket_path = str(output_dir / "comparison_bucket_bars.png")

    plot_lineup_histograms(
        comparison_set,
        density=True,
        output_path=hist_path,
    )

    plot_lineup_cdfs(
        comparison_set,
        output_path=cdf_path,
    )

    plot_lineup_survival_curves(
        comparison_set,
        max_runs=10,
        output_path=survival_path,
    )

    plot_lineup_bucket_bars(
        comparison_set,
        output_path=bucket_path,
    )

    return {
        "histograms": hist_path,
        "cdfs": cdf_path,
        "survival_curves": survival_path,
        "bucket_bars": bucket_path,
    }