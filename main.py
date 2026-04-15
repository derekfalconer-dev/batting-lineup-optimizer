from pathlib import Path

from core.app_service import run_optimizer_workflow
from core.visualization import print_lineup_summary_table
from core.optimizer import print_results


DATA_SOURCE = "gc_plus_tweaks"
OUTPUT_MODE = "presented"   # "raw" or "presented"


def print_imported_team(profiles):
    print("\n=== IMPORTED TEAM PROFILES ===")
    for profile in profiles:
        base = profile.base_traits
        eff = profile.effective_traits

        print(
            f"{profile.name:20s} "
            f"mode={profile.source_mode.value:16s} "
            f"arch={profile.archetype.value:20s} "
            f"C={base.contact:5.1f}->{eff.contact:5.1f} "
            f"P={base.power:5.1f}->{eff.power:5.1f} "
            f"S={base.speed:5.1f}->{eff.speed:5.1f} "
            f"Disc={base.plate_discipline:5.1f}->{eff.plate_discipline:5.1f}"
        )

        if not profile.adjustment.is_zero():
            print(f"    adjustment={profile.adjustment.as_dict()}")


def print_presented_result(result):
    print("\n=== ROSTER SUMMARY ===")
    print(f"Team source: {result.roster_summary.team_source}")
    print(f"Player count: {result.roster_summary.player_count}")
    print(f"Source mode counts: {result.roster_summary.source_mode_counts}")

    if result.roster_summary.warnings:
        print("Roster warnings:")
        for warning in result.roster_summary.warnings:
            print(f"  - {warning}")

    print("\n=== PLAYER PROFILES ===")
    for profile in result.player_profiles:
        base = profile.base_traits
        eff = profile.effective_traits

        print(
            f"{profile.name:20s} "
            f"mode={profile.source_mode:16s} "
            f"arch={profile.archetype:20s} "
            f"C={base.contact:5.1f}->{eff.contact:5.1f} "
            f"P={base.power:5.1f}->{eff.power:5.1f} "
            f"S={base.speed:5.1f}->{eff.speed:5.1f} "
            f"Disc={base.plate_discipline:5.1f}->{eff.plate_discipline:5.1f}"
        )

        if profile.has_adjustment:
            print(f"    adjustment={profile.adjustment}")

        for warning in profile.warnings:
            print(f"    warning={warning}")

    print("\n=== TOP LEADERBOARDS ===")
    for leaderboard in result.leaderboards:
        print(f"\n--- {leaderboard.title} ---")
        for entry in leaderboard.entries[:3]:
            print(
                f"{entry.display_name}: "
                f"mean={entry.metrics.mean_runs:.3f}, "
                f"P(>=target)={entry.metrics.prob_ge_target:.3f}, "
                f"Sortino={entry.metrics.sortino:.3f}, "
                f"lineup={entry.lineup}"
            )

    print("\n=== COACH SUMMARY ===")
    print(f"Optimized lineup scores 4+ runs: {result.coach_summary.optimized_prob_ge_target:.1%}")
    print(f"Original lineup scores 4+ runs:  {result.coach_summary.original_prob_ge_target:.1%}")
    print(f"Improvement: {result.coach_summary.improvement_prob_ge_target:.1%}")

    for bullet in result.coach_summary.bullets:
        print(f"  - {bullet}")

    print("\n=== COMPARISON SET ===")
    for item in result.comparison_set:
        print(
            f"{item.display_name:12s} "
            f"mean={item.metrics.mean_runs:.3f} "
            f"median={item.metrics.median_runs:.3f} "
            f"P(>=target)={item.metrics.prob_ge_target:.3f} "
            f"lineup={item.lineup}"
        )

    print("\n=== CHART PATHS ===")
    for chart in result.charts:
        print(f"{chart.key}: {chart.path}")


if __name__ == "__main__":
    try:
        if DATA_SOURCE in {"gc", "gc_plus_tweaks"}:
            result = run_optimizer_workflow(
                data_source=DATA_SOURCE,
                csv_path=Path("data/uploads/PLBC FOG 11U Fall 2025 Stats.csv"),
                adjustments_path=Path("data/scenarios/coach_adjustments.json"),
                output_dir="output",
                present=(OUTPUT_MODE == "presented"),
            )
        elif DATA_SOURCE == "manual_archetypes":
            result = run_optimizer_workflow(
                data_source=DATA_SOURCE,
                roster_path=Path("data/scenarios/manual_archetype_roster.json"),
                output_dir="output",
                present=(OUTPUT_MODE == "presented"),
            )
        elif DATA_SOURCE == "manual_traits":
            result = run_optimizer_workflow(
                data_source=DATA_SOURCE,
                roster_path=Path("data/scenarios/manual_traits_roster.json"),
                output_dir="output",
                present=(OUTPUT_MODE == "presented"),
            )
        else:
            raise ValueError(f"Unknown DATA_SOURCE: {DATA_SOURCE}")

    except ValueError as e:
        print("\n=== INPUT VALIDATION ERROR ===")
        print(str(e))
        raise SystemExit(1)

    if OUTPUT_MODE == "raw":
        print_imported_team(result.profiles)
        print_results("Top by Mean Runs", result.results["top_mean"])

        print("\n=== COACH SUMMARY ===")
        print(f"Optimized lineup scores 4+ runs: {result.summary['optimized_prob_ge_target']:.1%}")
        print(f"Original lineup scores 4+ runs:  {result.summary['original_prob_ge_target']:.1%}")
        print(f"Improvement: {result.summary['improvement_prob_ge_target']:.1%}")

        print_lineup_summary_table(
            result.comparison_set,
            title="Optimized vs Original vs Random vs Worst-Case",
        )

        print("\n=== CHART PATHS ===")
        for name, path in result.chart_paths.items():
            print(f"{name}: {path}")

    elif OUTPUT_MODE == "presented":
        print_presented_result(result)

    else:
        raise ValueError(f"Unknown OUTPUT_MODE: {OUTPUT_MODE}")