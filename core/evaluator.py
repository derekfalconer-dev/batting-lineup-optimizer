import random
from statistics import mean, median
from typing import List, Optional

from core.models import LineupResult, Player, RulesConfig
from core.simulator import simulate_game


def compute_std(values: List[float]) -> float:
    if not values:
        return 0.0
    mu = mean(values)
    variance = sum((x - mu) ** 2 for x in values) / len(values)
    return variance ** 0.5


def compute_downside_deviation(values: List[float], target: float) -> float:
    if not values:
        return 0.0

    downside_terms = []
    for x in values:
        shortfall = min(0.0, x - target)
        downside_terms.append(shortfall ** 2)

    downside_variance = sum(downside_terms) / len(values)
    return downside_variance ** 0.5


def percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])

    idx = (len(sorted_values) - 1) * pct
    lower = int(idx)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = idx - lower

    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def evaluate_lineup(
    lineup: List[Player],
    rules: RulesConfig,
    n_games: int = 1000,
    target_runs: float = 4.0,
    seed: Optional[int] = None,
) -> LineupResult:
    rng = random.Random(seed)

    runs_scored = [simulate_game(lineup, rules, rng) for _ in range(n_games)]

    mean_runs = mean(runs_scored)
    median_runs = median(runs_scored)
    std_runs = compute_std(runs_scored)

    downside_dev = compute_downside_deviation(runs_scored, target_runs)
    if downside_dev == 0:
        sortino = float("inf") if mean_runs > target_runs else 0.0
    else:
        sortino = (mean_runs - target_runs) / downside_dev

    prob_ge_target = sum(1 for x in runs_scored if x >= target_runs) / float(n_games)

    sorted_runs = sorted(runs_scored)
    p10_runs = percentile(sorted_runs, 0.10)
    p90_runs = percentile(sorted_runs, 0.90)

    return LineupResult(
        lineup=[p.name for p in lineup],
        mean_runs=mean_runs,
        median_runs=median_runs,
        std_runs=std_runs,
        prob_ge_target=prob_ge_target,
        target_runs=target_runs,
        downside_deviation=downside_dev,
        sortino=sortino,
        p10_runs=p10_runs,
        p90_runs=p90_runs,
        n_games=n_games,
        runs_scored_distribution=runs_scored,
    )