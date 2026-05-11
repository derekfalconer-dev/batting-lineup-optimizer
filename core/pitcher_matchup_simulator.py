from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

from core.pitcher_matchups import (
    OpponentHitterProfile,
    PitcherMatchupResult,
    PitcherProfile,
    ProjectedLineupSpot,
)


_EVENT_ORDER = ("BB/HBP", "SO", "1B", "2B", "3B", "HR", "OUT")

# Conservative high-school-ish baselines used only for shrinkage. This spike is
# not pitch-level scouting; it is a first-pass run environment estimate that
# blends hitter production with pitcher allowed rates.
_BASELINE_EVENT_RATES = {
    "BB/HBP": 0.105,
    "SO": 0.180,
    "1B": 0.165,
    "2B": 0.045,
    "3B": 0.008,
    "HR": 0.012,
}

_EVENT_BOUNDS = {
    "BB/HBP": (0.020, 0.250),
    "SO": (0.030, 0.450),
    "1B": (0.050, 0.350),
    "2B": (0.000, 0.120),
    "3B": (0.000, 0.035),
    "HR": (0.000, 0.080),
}


@dataclass(slots=True)
class PitcherSimulationResult:
    pitcher_name: str
    games_simulated: int
    innings_per_game: int
    avg_runs_allowed: float
    adjusted_avg_runs_allowed: float
    median_runs_allowed: float
    reliability_label: str
    role_caution: str
    pct_hold_to_2_or_less: float
    pct_hold_to_3_or_less: float
    pct_allow_5_plus: float
    pct_allow_7_plus: float
    blowup_inning_rate: float
    run_distribution: dict[int, int]
    notes: list[str]


def simulate_pitcher_vs_projected_lineup(
    pitcher: PitcherProfile,
    projected_lineup: list[ProjectedLineupSpot],
    *,
    games: int = 5000,
    innings_per_game: int = 7,
    seed: int | None = 42,
) -> PitcherSimulationResult:
    """
    Simulate a candidate pitcher against a projected opponent lineup.

    This is intentionally isolated from the main lineup optimizer simulator. It
    uses a lightweight event model and simple base advancement so the pitching
    matchup spike can evolve independently before any product/UI integration.
    """
    games_count = _safe_positive_int(games)
    innings_count = _safe_positive_int(innings_per_game)
    hitters = [spot.hitter for spot in projected_lineup if getattr(spot, "hitter", None)]

    reliability_label = _simulation_reliability_label(pitcher)
    role_caution = _role_caution(pitcher)
    caution_adjustment = _low_sample_risk_adjustment(pitcher)
    notes = _simulation_notes(
        pitcher,
        hitters,
        reliability_label=reliability_label,
        role_caution=role_caution,
        caution_adjustment=caution_adjustment,
    )

    if games_count <= 0 or innings_count <= 0 or not hitters:
        if not hitters:
            notes.append("No projected hitters were available, so no simulated games were run.")
        if games_count <= 0:
            notes.append("Games must be greater than zero.")
        if innings_count <= 0:
            notes.append("Innings per game must be greater than zero.")

        return PitcherSimulationResult(
            pitcher_name=pitcher.name,
            games_simulated=0,
            innings_per_game=max(innings_count, 0),
            avg_runs_allowed=0.0,
            adjusted_avg_runs_allowed=0.0,
            median_runs_allowed=0.0,
            reliability_label=reliability_label,
            role_caution=role_caution,
            pct_hold_to_2_or_less=0.0,
            pct_hold_to_3_or_less=0.0,
            pct_allow_5_plus=0.0,
            pct_allow_7_plus=0.0,
            blowup_inning_rate=0.0,
            run_distribution={},
            notes=notes,
        )

    rng = random.Random(seed)
    probabilities_by_hitter = [
        _estimate_event_probabilities(hitter, pitcher)
        for hitter in hitters
    ]

    game_run_totals: list[int] = []
    run_distribution: dict[int, int] = {}
    blowup_innings = 0

    for _ in range(games_count):
        lineup_index = 0
        game_runs = 0

        for _inning in range(innings_count):
            outs = 0
            bases = (False, False, False)
            inning_runs = 0

            while outs < 3:
                hitter_idx = lineup_index % len(hitters)
                lineup_index += 1

                event = _sample_event(probabilities_by_hitter[hitter_idx], rng)
                bases, outs, runs_scored = _apply_event(event, bases, outs, rng)

                game_runs += runs_scored
                inning_runs += runs_scored

            if inning_runs >= 4:
                blowup_innings += 1

        game_run_totals.append(game_runs)
        run_distribution[game_runs] = run_distribution.get(game_runs, 0) + 1

    total_games = len(game_run_totals)
    total_innings = total_games * innings_count
    avg_runs_allowed = sum(game_run_totals) / total_games

    return PitcherSimulationResult(
        pitcher_name=pitcher.name,
        games_simulated=total_games,
        innings_per_game=innings_count,
        avg_runs_allowed=avg_runs_allowed,
        adjusted_avg_runs_allowed=avg_runs_allowed + caution_adjustment,
        median_runs_allowed=float(statistics.median(game_run_totals)),
        reliability_label=reliability_label,
        role_caution=role_caution,
        pct_hold_to_2_or_less=_pct_count(game_run_totals, lambda runs: runs <= 2),
        pct_hold_to_3_or_less=_pct_count(game_run_totals, lambda runs: runs <= 3),
        pct_allow_5_plus=_pct_count(game_run_totals, lambda runs: runs >= 5),
        pct_allow_7_plus=_pct_count(game_run_totals, lambda runs: runs >= 7),
        blowup_inning_rate=blowup_innings / total_innings if total_innings else 0.0,
        run_distribution=dict(sorted(run_distribution.items())),
        notes=notes,
    )


def simulate_pitcher_matchup_report(
    pitcher_rankings: list[PitcherMatchupResult],
    projected_lineup: list[ProjectedLineupSpot],
    *,
    games: int = 5000,
    innings_per_game: int = 7,
    seed: int | None = 42,
) -> list[PitcherSimulationResult]:
    """
    Simulate every ranked pitcher against the projected lineup.

    Results preserve the existing ranking order. Seeds are offset per pitcher so
    repeated calls are reproducible without giving every pitcher the same random
    stream.
    """
    results: list[PitcherSimulationResult] = []

    for idx, ranking in enumerate(pitcher_rankings):
        pitcher_seed = None if seed is None else seed + (idx * 1009)

        results.append(
            simulate_pitcher_vs_projected_lineup(
                ranking.pitcher,
                projected_lineup,
                games=games,
                innings_per_game=innings_per_game,
                seed=pitcher_seed,
            )
        )

    return results


def _estimate_event_probabilities(
    hitter: OpponentHitterProfile,
    pitcher: PitcherProfile,
) -> dict[str, float]:
    """
    Estimate plate-appearance event probabilities from hitter and pitcher stats.

    This is a transparent first-pass blend:
    - hitter rates are shrunk toward generic baselines based on PA sample
    - pitcher rates are shrunk toward generic baselines based on BF sample
    - the two sides are blended conservatively so tiny samples do not dominate
    - probabilities are clamped and normalized to sum to 1.0
    """
    hitter_pa = max(int(hitter.pa or 0), 0)
    pitcher_bf = max(int(pitcher.bf or 0), 0)

    hitter_singles = max(
        int(hitter.hits or 0)
        - int(hitter.doubles or 0)
        - int(hitter.triples or 0)
        - int(hitter.hr or 0),
        0,
    )
    pitcher_singles_allowed = max(
        int(pitcher.hits_allowed or 0)
        - int(pitcher.doubles_allowed or 0)
        - int(pitcher.triples_allowed or 0)
        - int(pitcher.hr_allowed or 0),
        0,
    )

    hitter_rates = {
        "BB/HBP": _shrink_rate(
            _safe_div(float((hitter.bb or 0) + (hitter.hbp or 0)), hitter_pa),
            _BASELINE_EVENT_RATES["BB/HBP"],
            hitter_pa,
            midpoint=60.0,
        ),
        "SO": _shrink_rate(
            _safe_div(float(hitter.k or 0), hitter_pa),
            _BASELINE_EVENT_RATES["SO"],
            hitter_pa,
            midpoint=60.0,
        ),
        "1B": _shrink_rate(
            _safe_div(float(hitter_singles), hitter_pa),
            _BASELINE_EVENT_RATES["1B"],
            hitter_pa,
            midpoint=60.0,
        ),
        "2B": _shrink_rate(
            _safe_div(float(hitter.doubles or 0), hitter_pa),
            _BASELINE_EVENT_RATES["2B"],
            hitter_pa,
            midpoint=60.0,
        ),
        "3B": _shrink_rate(
            _safe_div(float(hitter.triples or 0), hitter_pa),
            _BASELINE_EVENT_RATES["3B"],
            hitter_pa,
            midpoint=60.0,
        ),
        "HR": _shrink_rate(
            _safe_div(float(hitter.hr or 0), hitter_pa),
            _BASELINE_EVENT_RATES["HR"],
            hitter_pa,
            midpoint=60.0,
        ),
    }

    pitcher_rates = {
        "BB/HBP": _shrink_rate(
            float(pitcher.free_base_rate or 0.0),
            _BASELINE_EVENT_RATES["BB/HBP"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "SO": _shrink_rate(
            float(pitcher.k_rate or 0.0),
            _BASELINE_EVENT_RATES["SO"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "1B": _shrink_rate(
            _safe_div(float(pitcher_singles_allowed), pitcher_bf),
            _BASELINE_EVENT_RATES["1B"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "2B": _shrink_rate(
            _safe_div(float(pitcher.doubles_allowed or 0), pitcher_bf),
            _BASELINE_EVENT_RATES["2B"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "3B": _shrink_rate(
            _safe_div(float(pitcher.triples_allowed or 0), pitcher_bf),
            _BASELINE_EVENT_RATES["3B"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "HR": _shrink_rate(
            _safe_div(float(pitcher.hr_allowed or 0), pitcher_bf),
            _BASELINE_EVENT_RATES["HR"],
            pitcher_bf,
            midpoint=120.0,
        ),
    }

    blended = {
        event: _blend_rates(hitter_rates[event], pitcher_rates[event])
        for event in _BASELINE_EVENT_RATES
    }

    return _normalize_event_probabilities(blended)


def _apply_event(
    event: str,
    bases: tuple[bool, bool, bool],
    outs: int,
    rng: random.Random,
) -> tuple[tuple[bool, bool, bool], int, int]:
    first, second, third = bases
    runs = 0

    if event == "BB/HBP":
        if first and second and third:
            runs += 1

        return (
            (
                True,
                bool(second or first),
                bool(third if not (first and second and third) else False) or bool(first and second),
            ),
            outs,
            runs,
        )

    if event == "SO":
        return bases, outs + 1, 0

    if event == "OUT":
        if outs < 2 and third and rng.random() < 0.25:
            return (first, second, False), outs + 1, 1
        return bases, outs + 1, 0

    if event == "1B":
        runs += int(third) + int(second)
        return (True, first, False), outs, runs

    if event == "2B":
        first_scores = bool(first and rng.random() < 0.60)
        runs += int(third) + int(second) + int(first_scores)
        return (False, True, bool(first and not first_scores)), outs, runs

    if event == "3B":
        runs += int(first) + int(second) + int(third)
        return (False, False, True), outs, runs

    if event == "HR":
        runs += int(first) + int(second) + int(third) + 1
        return (False, False, False), outs, runs

    return bases, outs + 1, 0


def _sample_event(probabilities: dict[str, float], rng: random.Random) -> str:
    draw = rng.random()
    cumulative = 0.0

    for event in _EVENT_ORDER:
        cumulative += float(probabilities.get(event, 0.0) or 0.0)
        if draw <= cumulative:
            return event

    return "OUT"


def _normalize_event_probabilities(raw_probabilities: dict[str, float]) -> dict[str, float]:
    probabilities: dict[str, float] = {}

    for event, value in raw_probabilities.items():
        low, high = _EVENT_BOUNDS[event]
        probabilities[event] = _clamp(float(value or 0.0), low, high)

    explicit_total = sum(probabilities.values())

    # Always leave room for generic balls-in-play outs. Strikeouts are modeled
    # separately as their own out event.
    max_explicit_total = 0.92
    if explicit_total > max_explicit_total:
        scale = max_explicit_total / explicit_total
        probabilities = {
            event: value * scale
            for event, value in probabilities.items()
        }
        explicit_total = sum(probabilities.values())

    probabilities["OUT"] = max(0.0, 1.0 - explicit_total)

    total = sum(probabilities.values())
    if total <= 0:
        return {
            "BB/HBP": _BASELINE_EVENT_RATES["BB/HBP"],
            "SO": _BASELINE_EVENT_RATES["SO"],
            "1B": _BASELINE_EVENT_RATES["1B"],
            "2B": _BASELINE_EVENT_RATES["2B"],
            "3B": _BASELINE_EVENT_RATES["3B"],
            "HR": _BASELINE_EVENT_RATES["HR"],
            "OUT": 1.0 - sum(_BASELINE_EVENT_RATES.values()),
        }

    return {
        event: probabilities.get(event, 0.0) / total
        for event in _EVENT_ORDER
    }


def _simulation_reliability_label(pitcher: PitcherProfile) -> str:
    bf = int(pitcher.bf or 0)

    if bf >= 100:
        return "High"
    if bf >= 50:
        return "Medium"
    return "Low"


def _role_caution(pitcher: PitcherProfile) -> str:
    bf = int(pitcher.bf or 0)

    if bf >= 100:
        return "Established pitching sample"
    if bf >= 50:
        return "Usable but still developing sample"
    if bf >= 25:
        return "Limited pitching sample"
    return "Emergency/depth sample"


def _low_sample_risk_adjustment(pitcher: PitcherProfile) -> float:
    bf = int(pitcher.bf or 0)
    adjustment = 0.0

    if bf < 75:
        adjustment += 0.25
    if bf < 50:
        adjustment += 0.35
    if bf < 25:
        adjustment += 0.40

    free_base_rate = float(pitcher.free_base_rate or 0.0)
    obp_allowed = float(pitcher.obp_allowed or 0.0)
    oba = float(pitcher.oba or 0.0)

    if free_base_rate >= 0.18:
        adjustment += 0.25
    if free_base_rate >= 0.25:
        adjustment += 0.30

    if obp_allowed >= 0.450:
        adjustment += 0.25
    if obp_allowed >= 0.550:
        adjustment += 0.30

    if oba >= 0.350:
        adjustment += 0.20
    if oba >= 0.450:
        adjustment += 0.25

    return _clamp(adjustment, 0.0, 1.75)


def _simulation_notes(
    pitcher: PitcherProfile,
    hitters: list[OpponentHitterProfile],
    *,
    reliability_label: str,
    role_caution: str,
    caution_adjustment: float,
) -> list[str]:
    notes = [
        "First-pass isolated Monte Carlo model; not pitch-level scouting and not wired into the optimizer.",
        "Base advancement is intentionally simple and approximates common high-school run scoring movement.",
    ]

    if reliability_label == "Low":
        notes.append("Low simulation reliability: pitcher sample is small, so adjusted runs include a caution penalty.")

    if caution_adjustment > 0:
        notes.append("Adjusted average runs includes a small-sample/risk caution adjustment.")

    if role_caution in {"Limited pitching sample", "Emergency/depth sample"}:
        notes.append("Limited mound usage may reflect coach trust, defensive needs, or role constraints.")

    low_sample_hitters = sum(1 for hitter in hitters if int(hitter.pa or 0) < 25)
    if low_sample_hitters:
        notes.append(f"{low_sample_hitters} projected hitter(s) have small PA samples; hitter rates are shrunk toward baseline.")

    return notes


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def _safe_positive_int(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _sample_weight(sample_size: int, midpoint: float) -> float:
    sample = max(float(sample_size or 0), 0.0)
    if midpoint <= 0:
        return 1.0
    return _clamp(sample / (sample + midpoint), 0.0, 1.0)


def _shrink_rate(
    observed: float,
    baseline: float,
    sample_size: int,
    *,
    midpoint: float,
) -> float:
    weight = _sample_weight(sample_size, midpoint)
    return (weight * float(observed or 0.0)) + ((1.0 - weight) * baseline)


def _blend_rates(hitter_rate: float, pitcher_rate: float) -> float:
    return (0.52 * hitter_rate) + (0.48 * pitcher_rate)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _pct_count(values: list[int], predicate) -> float:
    if not values:
        return 0.0

    return sum(1 for value in values if predicate(value)) / len(values)


__all__ = [
    "PitcherSimulationResult",
    "simulate_pitcher_vs_projected_lineup",
    "simulate_pitcher_matchup_report",
]
