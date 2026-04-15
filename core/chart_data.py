from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import numpy as np


DEFAULT_BUCKETS: list[tuple[str, int | None, int | None]] = [
    ("0–1", 0, 1),
    ("2–3", 2, 3),
    ("4–5", 4, 5),
    ("6–7", 6, 7),
    ("8-9", 8, 9),
    ("10+", 10, None),
]


def _coerce_lineup_entry(item: Any) -> dict[str, Any]:
    """
    Accept either:
    - dict payloads from app_service / custom evaluation
    - LineupEvaluationSchema-like objects from presenters
    """
    if isinstance(item, Mapping):
        display_name = str(item.get("display_name") or "Lineup")
        lineup = [str(x) for x in item.get("lineup", [])]
        metrics = item
        distribution = [int(x) for x in item.get("runs_scored_distribution", []) or []]
        return {
            "display_name": display_name,
            "lineup": lineup,
            "mean_runs": float(metrics["mean_runs"]),
            "median_runs": float(metrics["median_runs"]),
            "std_runs": float(metrics["std_runs"]),
            "prob_ge_target": float(metrics["prob_ge_target"]),
            "sortino": float(metrics["sortino"]),
            "p10_runs": float(metrics["p10_runs"]),
            "p90_runs": float(metrics["p90_runs"]),
            "n_games": int(metrics["n_games"]),
            "target_runs": float(metrics["target_runs"]) if metrics.get("target_runs") is not None else None,
            "runs_scored_distribution": distribution,
        }

    # LineupEvaluationSchema-like object
    display_name = str(getattr(item, "display_name", "Lineup"))
    lineup = [str(x) for x in getattr(item, "lineup", [])]
    metrics = getattr(item, "metrics")
    distribution = [int(x) for x in getattr(item, "runs_scored_distribution", []) or []]

    return {
        "display_name": display_name,
        "lineup": lineup,
        "mean_runs": float(metrics.mean_runs),
        "median_runs": float(metrics.median_runs),
        "std_runs": float(metrics.std_runs),
        "prob_ge_target": float(metrics.prob_ge_target),
        "sortino": float(metrics.sortino),
        "p10_runs": float(metrics.p10_runs),
        "p90_runs": float(metrics.p90_runs),
        "n_games": int(metrics.n_games),
        "target_runs": float(metrics.target_runs) if metrics.target_runs is not None else None,
        "runs_scored_distribution": distribution,
    }


def _coerce_many(items: Sequence[Any]) -> list[dict[str, Any]]:
    coerced = [_coerce_lineup_entry(item) for item in items]
    valid = [item for item in coerced if item["runs_scored_distribution"]]
    if not valid:
        raise ValueError("No lineup entries with runs_scored_distribution were provided.")
    return valid


def _resolve_max_runs(items: Sequence[dict[str, Any]], max_runs: int | None) -> int:
    if max_runs is not None:
        return int(max_runs)
    observed_max = max(max(item["runs_scored_distribution"]) for item in items)
    return max(8, int(observed_max))


def build_survival_curve_chart_data(
    items: Sequence[Any],
    *,
    max_runs: int | None = None,
) -> dict[str, Any]:
    """
    Returns chart data for P(runs >= x).
    """
    lineups = _coerce_many(items)
    resolved_max = _resolve_max_runs(lineups, max_runs)
    x = list(range(0, resolved_max + 1))

    series = []
    for item in lineups:
        values = np.array(item["runs_scored_distribution"])
        y = [float((values >= threshold).mean()) for threshold in x]
        series.append(
            {
                "name": item["display_name"],
                "y": y,
                "lineup": item["lineup"],
            }
        )

    return {
        "chart_key": "survival_curves",
        "title": "Probability of Scoring At Least X Runs",
        "x_label": "Runs scored",
        "y_label": "Probability",
        "x": x,
        "series": series,
    }


def build_bucket_bar_chart_data(
    items: Sequence[Any],
    *,
    buckets: Sequence[tuple[str, int | None, int | None]] | None = None,
) -> dict[str, Any]:
    lineups = _coerce_many(items)
    resolved_buckets = list(buckets or DEFAULT_BUCKETS)

    x = [label for label, _, _ in resolved_buckets]
    series = []

    for item in lineups:
        values = np.array(item["runs_scored_distribution"])
        y: list[float] = []

        for _, low, high in resolved_buckets:
            if low is None and high is None:
                prob = 1.0
            elif high is None:
                prob = float((values >= low).mean())
            elif low is None:
                prob = float((values <= high).mean())
            else:
                prob = float(((values >= low) & (values <= high)).mean())
            y.append(prob)

        series.append(
            {
                "name": item["display_name"],
                "y": y,
                "lineup": item["lineup"],
            }
        )

    return {
        "chart_key": "bucket_bars",
        "title": "Bucketed Outcome Comparison",
        "x_label": "Run buckets",
        "y_label": "Probability",
        "x": x,
        "series": series,
    }


def build_density_chart_data(
    items: Sequence[Any],
    *,
    max_runs: int | None = None,
    bandwidth: float = 0.75,
    n_points: int = 250,
) -> dict[str, Any]:
    lineups = _coerce_many(items)
    resolved_max = _resolve_max_runs(lineups, max_runs)

    x_grid = np.linspace(0, resolved_max, n_points)
    series = []

    for item in lineups:
        values = np.array(item["runs_scored_distribution"], dtype=float)
        density_vals = np.zeros_like(x_grid)

        for v in values:
            density_vals += np.exp(-0.5 * ((x_grid - v) / bandwidth) ** 2)

        density_vals /= (len(values) * bandwidth * np.sqrt(2 * np.pi))

        dx = x_grid[1] - x_grid[0]
        total_area = density_vals.sum() * dx
        if total_area > 0:
            density_vals /= total_area

        series.append(
            {
                "name": item["display_name"],
                "y": [float(v) for v in density_vals],
                "lineup": item["lineup"],
            }
        )

    return {
        "chart_key": "densities",
        "title": "Run Distribution Densities",
        "x_label": "Runs scored",
        "y_label": "Density",
        "x": [float(v) for v in x_grid],
        "series": series,
    }


def build_comparison_table_rows(
    items: Sequence[Any],
) -> list[dict[str, Any]]:
    lineups = _coerce_many(items)

    rows: list[dict[str, Any]] = []
    for item in lineups:
        target_runs = item["target_runs"] if item["target_runs"] is not None else 4.0
        rows.append(
            {
                "lineup": item["display_name"],
                "avg_runs": round(item["mean_runs"], 3),
                "chance_ge_target": round(item["prob_ge_target"], 4),
                "target_runs": round(target_runs, 2),
                "median_runs": round(item["median_runs"], 3),
                "p10_runs": round(item["p10_runs"], 3),
                "p90_runs": round(item["p90_runs"], 3),
                "std_runs": round(item["std_runs"], 3),
                "sortino": round(item["sortino"], 3),
                "batting_order": item["lineup"],
            }
        )

    rows.sort(key=lambda r: (-r["avg_runs"], -r["chance_ge_target"]))
    return rows