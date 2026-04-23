from __future__ import annotations

from typing import Any, Mapping, Sequence

from core.schemas import (
    ChartSchema,
    CoachSummarySchema,
    EvaluationMetricsSchema,
    LeaderboardSchema,
    LineupEvaluationSchema,
    LineupSlotSchema,
    PlayerProfileSchema,
    RosterSummarySchema,
    TraitSetSchema,
    WorkflowResponseSchema,
    SavedScenarioSchema,
    ScenarioCollectionSchema,
)


# ---------------------------------------------------------------------
# Player profile presenters
# ---------------------------------------------------------------------

def present_trait_set(traits: Any) -> TraitSetSchema:
    """
    Convert a PlayerTraits-like object or mapping into a TraitSetSchema.
    """
    if hasattr(traits, "as_dict"):
        data = traits.as_dict()
    elif isinstance(traits, Mapping):
        data = dict(traits)
    else:
        raise TypeError(f"Unsupported trait payload type: {type(traits)!r}")

    return TraitSetSchema.from_mapping(data)


def present_adjustment(adjustment: Any) -> dict[str, float]:
    """
    Convert a TraitAdjustment-like object or mapping into a plain dict.
    """
    if adjustment is None:
        return {}

    if hasattr(adjustment, "as_dict"):
        raw = adjustment.as_dict()
    elif isinstance(adjustment, Mapping):
        raw = dict(adjustment)
    else:
        raise TypeError(f"Unsupported adjustment payload type: {type(adjustment)!r}")

    return {str(k): float(v) for k, v in raw.items()}


def _safe_int_or_none(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_profile_warnings(profile: Any) -> list[str]:
    """
    Coach-facing warnings / trust flags.

    The goal is not to say the data is useless.
    The goal is to tell the coach what to do next.
    """
    warnings: list[str] = []

    source_mode = getattr(profile, "source_mode", None)
    source_mode_value = getattr(source_mode, "value", str(source_mode)) if source_mode is not None else "unknown"

    metadata = getattr(profile, "metadata", {}) or {}
    pa_value = _safe_int_or_none(metadata.get("pa"))

    if pa_value is None:
        gc_row = metadata.get("gc_row", {}) if isinstance(metadata, Mapping) else {}
        pa_value = _safe_int_or_none(gc_row.get("PA"))

    confidence = str(metadata.get("confidence") or "").strip()

    if confidence == "Low":
        warnings.append(
            "Low-confidence baseline: use Coach Lab to inspect this player and adjust traits if the imported stats do not match what you see on the field."
        )
    elif confidence == "Medium":
        warnings.append(
            "Medium-confidence baseline: directional input is useful here, but coach review can still improve accuracy."
        )

    if source_mode_value == "manual_archetype":
        warnings.append("Profile is based on archetype scaffolding rather than measured stats.")

    if source_mode_value == "manual_traits":
        warnings.append("Profile is based on manually entered traits.")

    if source_mode_value == "gc_nudged":
        warnings.append("Profile includes coach adjustments layered on top of GameChanger data.")

    source_file_count = _safe_int_or_none(metadata.get("source_file_count"))
    merged_record_count = _safe_int_or_none(metadata.get("merged_record_count"))

    if source_file_count and source_file_count > 1:
        warnings.append(
            f"Profile was merged from {source_file_count} GameChanger files."
        )

    if merged_record_count and merged_record_count > 1:
        warnings.append(
            f"Profile combines {merged_record_count} matching GameChanger stat rows."
        )

    return warnings


def present_player_profile(profile: Any) -> PlayerProfileSchema:
    """
    Convert a core.archetypes.PlayerProfile into PlayerProfileSchema.
    """
    handedness = getattr(profile, "handedness", None)
    archetype = getattr(profile, "archetype", None)
    source_mode = getattr(profile, "source_mode", None)

    metadata = dict(getattr(profile, "metadata", {}) or {})

    return PlayerProfileSchema(
        name=str(getattr(profile, "name")),
        handedness=getattr(handedness, "value", str(handedness)),
        archetype=getattr(archetype, "value", str(archetype)),
        source=str(getattr(profile, "source", "")),
        source_mode=getattr(source_mode, "value", str(source_mode)),
        base_traits=present_trait_set(getattr(profile, "base_traits")),
        adjustment=present_adjustment(getattr(profile, "adjustment", None)),
        effective_traits=present_trait_set(getattr(profile, "effective_traits")),
        plate_appearances=_safe_int_or_none(metadata.get("pa")),
        source_file_count=_safe_int_or_none(metadata.get("source_file_count")),
        confidence=str(metadata.get("confidence")) if metadata.get("confidence") not in (None, "") else None,
        confidence_badge=str(metadata.get("confidence_badge")) if metadata.get("confidence_badge") not in (None, "") else None,
        confidence_action=str(metadata.get("confidence_action")) if metadata.get("confidence_action") not in (None, "") else None,
        metadata=metadata,
        warnings=build_profile_warnings(profile),
    )


def present_player_profiles(profiles: Sequence[Any]) -> list[PlayerProfileSchema]:
    return [present_player_profile(profile) for profile in profiles]


def present_roster_summary(
    *,
    profiles: Sequence[Any],
    team_source: str,
) -> RosterSummarySchema:
    source_mode_counts: dict[str, int] = {}
    warnings: list[str] = []

    for profile in profiles:
        source_mode = getattr(profile, "source_mode", None)
        key = getattr(source_mode, "value", str(source_mode)) if source_mode is not None else "unknown"
        source_mode_counts[key] = source_mode_counts.get(key, 0) + 1

    if len(profiles) < 9:
        warnings.append(f"Roster has {len(profiles)} players; a full lineup usually expects 9.")

    return RosterSummarySchema(
        team_source=team_source,
        player_count=len(profiles),
        source_mode_counts=source_mode_counts,
        warnings=warnings,
    )


# ---------------------------------------------------------------------
# Lineup result presenters
# ---------------------------------------------------------------------

def present_lineup_slots(lineup_names: Sequence[str]) -> list[LineupSlotSchema]:
    return [
        LineupSlotSchema(
            batting_order=idx,
            player_name=str(name),
        )
        for idx, name in enumerate(lineup_names, start=1)
    ]


def present_evaluation_metrics(result: Mapping[str, Any]) -> EvaluationMetricsSchema:
    return EvaluationMetricsSchema(
        mean_runs=float(result["mean_runs"]),
        median_runs=float(result["median_runs"]),
        std_runs=float(result["std_runs"]),
        prob_ge_target=float(result["prob_ge_target"]),
        sortino=float(result["sortino"]),
        p10_runs=float(result["p10_runs"]),
        p90_runs=float(result["p90_runs"]),
        n_games=int(result["n_games"]),
        target_runs=float(result["target_runs"]) if "target_runs" in result and result["target_runs"] is not None else None,
    )


def present_lineup_evaluation(
    result: Mapping[str, Any],
    *,
    default_display_name: str | None = None,
    include_distribution: bool = True,
) -> LineupEvaluationSchema:
    lineup_names = [str(name) for name in result["lineup"]]
    display_name = str(result.get("display_name") or default_display_name or "Lineup")

    distribution: list[int] = []
    if include_distribution:
        raw_distribution = result.get("runs_scored_distribution", []) or []
        distribution = [int(x) for x in raw_distribution]

    return LineupEvaluationSchema(
        display_name=display_name,
        lineup=lineup_names,
        slots=present_lineup_slots(lineup_names),
        metrics=present_evaluation_metrics(result),
        runs_scored_distribution=distribution,
    )


def present_leaderboards(results: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[LeaderboardSchema]:
    leaderboard_titles = {
        "top_mean": "Top by Mean Runs",
        "top_sortino": "Top by Sortino",
        "top_prob": "Top by Probability of Beating Target",
    }

    leaderboards: list[LeaderboardSchema] = []

    for key in ("top_mean", "top_sortino", "top_prob"):
        entries = results.get(key, []) or []
        leaderboards.append(
            LeaderboardSchema(
                key=key,
                title=leaderboard_titles.get(key, key),
                entries=[
                    present_lineup_evaluation(
                        entry,
                        default_display_name=f"{leaderboard_titles.get(key, key)} #{idx}",
                    )
                    for idx, entry in enumerate(entries, start=1)
                ],
            )
        )

    return leaderboards


# ---------------------------------------------------------------------
# Charts / summary presenters
# ---------------------------------------------------------------------

def present_charts(chart_paths: Mapping[str, str]) -> list[ChartSchema]:
    chart_titles = {
        "histograms": "Run Distribution Densities",
        "cdfs": "Run Distribution CDFs",
        "survival_curves": "Probability of Scoring At Least X Runs",
        "bucket_bars": "Bucketed Outcome Comparison",
    }

    charts: list[ChartSchema] = []
    for key, path in chart_paths.items():
        charts.append(
            ChartSchema(
                key=str(key),
                title=chart_titles.get(str(key), str(key).replace("_", " ").title()),
                path=str(path),
            )
        )
    return charts


def build_coach_summary_bullets(
            *,
            optimized: Mapping[str, Any],
            original: Mapping[str, Any],
            summary: Mapping[str, Any] | None = None,
    ) -> list[str]:
        bullets: list[str] = []

        opt_mean = float(optimized["mean_runs"])
        orig_mean = float(original["mean_runs"])
        mean_delta = opt_mean - orig_mean

        opt_prob = float(optimized["prob_ge_target"])
        orig_prob = float(original["prob_ge_target"])
        prob_delta = opt_prob - orig_prob

        target_runs = summary.get("target_runs", 4.0) if summary else 4.0
        optimized_lineup = optimized.get("lineup", [])

        # Overall recommendation tone
        if mean_delta >= 0.15:
            bullets.append(
                "This batting order looks clearly better than the current order."
            )
        elif mean_delta >= 0.05:
            bullets.append(
                "This batting order looks a little better than the current order."
            )
        elif mean_delta >= 0:
            bullets.append(
                "This batting order is only slightly better than the current order."
            )
        else:
            bullets.append(
                "This batting order did not grade better than the current order in this run."
            )

        # Scoring target in plain language
        if prob_delta >= 0.03:
            bullets.append(
                f"It gives your team a meaningfully better chance to score {target_runs:.0f}+ runs."
            )
        elif prob_delta > 0:
            bullets.append(
                f"It gives your team a slightly better chance to score {target_runs:.0f}+ runs."
            )
        elif prob_delta == 0:
            bullets.append(
                f"It performs about the same as the current order for scoring {target_runs:.0f}+ runs."
            )
        else:
            bullets.append(
                f"It gives your team a slightly worse chance to score {target_runs:.0f}+ runs."
            )

        # Top of order
        if optimized_lineup:
            top_three = ", ".join(str(x) for x in optimized_lineup[:3])
            bullets.append(f"Best top-of-the-order group: {top_three}.")

        # Source note in coach language
        if summary:
            source_mode_counts = summary.get("source_mode_counts", {}) or {}
            total_players = sum(source_mode_counts.values())

            if total_players > 0:
                if list(source_mode_counts.keys()) == ["gc"]:
                    bullets.append("This run is based entirely on GameChanger stats.")
                elif list(source_mode_counts.keys()) == ["gc_nudged"]:
                    bullets.append("This run uses GameChanger stats plus coach adjustments.")
                elif "manual_archetype" in source_mode_counts:
                    bullets.append("Some player profiles are based on archetypes, not game stats.")
                elif "manual_traits" in source_mode_counts:
                    bullets.append("Some player profiles are based on manually entered traits.")

        return bullets


def present_coach_summary(
    *,
    optimized: Mapping[str, Any],
    original: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> CoachSummarySchema:
    return CoachSummarySchema(
        optimized_prob_ge_target=float(summary["optimized_prob_ge_target"]),
        original_prob_ge_target=float(summary["original_prob_ge_target"]),
        improvement_prob_ge_target=float(summary["improvement_prob_ge_target"]),
        optimized_mean_runs=float(summary["optimized_mean_runs"]),
        original_mean_runs=float(summary["original_mean_runs"]),
        improvement_mean_runs=float(summary["improvement_mean_runs"]),
        optimized_lineup=[str(x) for x in summary["optimized_lineup"]],
        original_lineup=[str(x) for x in summary["original_lineup"]],
        bullets=build_coach_summary_bullets(
            optimized=optimized,
            original=original,
            summary=summary,
        ),
        optimizer_meta=dict(summary.get("optimizer_meta", {})),
    )


# ---------------------------------------------------------------------
# Scenario presenters
# ---------------------------------------------------------------------

def present_saved_scenario(scenario: Any) -> SavedScenarioSchema:
    """
    Convert a SavedScenario-like object into SavedScenarioSchema.
    """

    raw_result = getattr(scenario, "result", None)
    lineup_result: LineupEvaluationSchema | None = None

    if raw_result:
        if isinstance(raw_result, dict) and "custom_lineup" in raw_result:
            lineup_result = present_lineup_evaluation(
                raw_result["custom_lineup"],
                default_display_name=getattr(scenario, "name", "Saved Scenario"),
            )
        elif isinstance(raw_result, dict) and "lineup" in raw_result:
            lineup_result = present_lineup_evaluation(
                raw_result,
                default_display_name=getattr(scenario, "name", "Saved Scenario"),
            )

    return SavedScenarioSchema(
        scenario_id=str(getattr(scenario, "scenario_id")),
        name=str(getattr(scenario, "name")),
        lineup_names=[str(x) for x in getattr(scenario, "lineup_names", [])],
        adjustments_by_name={
            str(player): {str(k): float(v) for k, v in values.items()}
            for player, values in getattr(scenario, "adjustments_by_name", {}).items()
        },
        result=lineup_result,
        created_at=getattr(scenario, "created_at", None),
        updated_at=getattr(scenario, "updated_at", None),
    )


def present_saved_scenarios(scenarios: Sequence[Any]) -> ScenarioCollectionSchema:
    return ScenarioCollectionSchema(
        scenarios=[present_saved_scenario(s) for s in scenarios]
    )


# ---------------------------------------------------------------------
# Top-level presenter
# ---------------------------------------------------------------------

def present_workflow_result(workflow_result: Any) -> WorkflowResponseSchema:
    """
    Convert the current app_service.WorkflowResult object into the stable
    schema contract used by future UI/API layers.
    """
    roster_summary = present_roster_summary(
        profiles=workflow_result.profiles,
        team_source=workflow_result.team_source,
    )

    player_profiles = present_player_profiles(workflow_result.profiles)

    optimized = present_lineup_evaluation(
        workflow_result.optimized,
        default_display_name="Optimized",
    )
    original = present_lineup_evaluation(
        workflow_result.original,
        default_display_name="Original",
    )
    random_lineup = present_lineup_evaluation(
        workflow_result.random_lineup,
        default_display_name="Random",
    )
    worst_lineup = present_lineup_evaluation(
        workflow_result.worst_lineup,
        default_display_name="Worst-Case",
    )

    comparison_set = [
        present_lineup_evaluation(item)
        for item in workflow_result.comparison_set
    ]

    leaderboards = present_leaderboards(workflow_result.results)
    coach_summary = present_coach_summary(
        optimized=workflow_result.optimized,
        original=workflow_result.original,
        summary=workflow_result.summary,
    )
    charts = present_charts(workflow_result.chart_paths)

    warnings: list[str] = []
    errors: list[str] = []

    return WorkflowResponseSchema(
        team_source=str(workflow_result.team_source),
        roster_summary=roster_summary,
        player_profiles=player_profiles,
        optimized=optimized,
        original=original,
        random_lineup=random_lineup,
        worst_lineup=worst_lineup,
        comparison_set=comparison_set,
        leaderboards=leaderboards,
        coach_summary=coach_summary,
        charts=charts,
        warnings=warnings,
        errors=errors,
    )