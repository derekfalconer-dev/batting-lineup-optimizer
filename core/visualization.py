from typing import Dict, List

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from matplotlib.ticker import PercentFormatter


def _short_label(r: Dict, idx: int) -> str:
    return f"#{idx} mean={r['mean_runs']:.2f}"


def plot_lineup_histograms(
    results: List[Dict],
    title="How many runs each lineup scores",
    density: bool = True,
    output_path: str = "lineup_histograms.png",
    return_path: bool = False,
):
    """
    Plot smoothed density curves instead of step histograms.
    """
    plt.figure(figsize=(10, 6))

    x_grid = np.linspace(0, 16, 300)

    for idx, r in enumerate(results, start=1):
        values = np.array(r["runs_scored_distribution"])

        bandwidth = 0.75
        density_vals = np.zeros_like(x_grid)

        for v in values:
            density_vals += np.exp(-0.5 * ((x_grid - v) / bandwidth) ** 2)

        density_vals /= (len(values) * bandwidth * np.sqrt(2 * np.pi))

        if density:
            density_vals /= density_vals.sum() * (x_grid[1] - x_grid[0])

        plt.plot(
            x_grid,
            density_vals,
            linewidth=2.5,
            alpha=0.9,
            label=r.get("display_name", _short_label(r, idx)),
        )

    plt.gca().yaxis.set_major_formatter(PercentFormatter(1.0))
    plt.xlabel("Runs Scored")
    plt.ylabel("Likelihood")
    plt.title(title)
    plt.legend(title="Lineups")
    plt.grid(True, alpha=0.25)
    plt.xlim(0, 16)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    print(f"Saved smoothed density plot to: {output_path}")
    if return_path:
        return output_path


def plot_lineup_cdfs(
    results: List[Dict],
    title="Chance of scoring this many runs or less",
    output_path: str = "lineup_cdfs.png",
    return_path: bool = False,
):
    """
    Plot smooth CDFs by integrating the same Gaussian-smoothed density
    used for the histogram/PDF-style chart.
    """
    plt.figure(figsize=(10, 6))

    x_grid = np.linspace(0, 16, 400)
    bandwidth = 0.75

    for idx, r in enumerate(results, start=1):
        values = np.array(r["runs_scored_distribution"])

        density_vals = np.zeros_like(x_grid)

        for v in values:
            density_vals += np.exp(-0.5 * ((x_grid - v) / bandwidth) ** 2)

        density_vals /= (len(values) * bandwidth * np.sqrt(2 * np.pi))

        # Normalize just in case
        dx = x_grid[1] - x_grid[0]
        total_area = density_vals.sum() * dx
        if total_area > 0:
            density_vals /= total_area

        cdf_vals = np.cumsum(density_vals) * dx

        # Force clean ending at 1.0
        if cdf_vals[-1] > 0:
            cdf_vals /= cdf_vals[-1]

        plt.plot(
            x_grid,
            cdf_vals,
            linewidth=2.5,
            alpha=0.90,
            label=r.get("display_name", _short_label(r, idx)),
        )

    plt.gca().yaxis.set_major_formatter(PercentFormatter(1.0))
    plt.xlabel("Runs Scored")
    plt.ylabel("Chance of scoring this many runs or less")
    plt.title(title)
    plt.legend(title="Lineups", loc="lower right")
    plt.grid(True, alpha=0.25)
    plt.xlim(0, 16)
    plt.ylim(0, 1.02)
    plt.xticks(np.arange(0, 17, 1))
    plt.yticks(np.arange(0, 1.05, 0.1))
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    print(f"Saved smooth CDF plot to: {output_path}")
    if return_path:
        return output_path


def plot_lineup_survival_curves(
    results: List[Dict],
    max_runs: int = 12,
    title="Chance of scoring at least X runs (THIS is what wins games)",
    output_path: str = "lineup_survival_curves.png",
    return_path: bool = False,
):
    """
    Plot P(score >= X) for X = 0..max_runs.
    """
    plt.figure(figsize=(10, 6))

    thresholds = np.arange(0, max_runs + 1)

    for idx, r in enumerate(results, start=1):
        values = np.array(r["runs_scored_distribution"])
        probs = [(values >= x).mean() for x in thresholds]

        plt.plot(
            thresholds,
            probs,
            marker="o",
            linewidth=2.0,
            alpha=0.80,
            markersize=5,
            label=r.get("display_name", _short_label(r, idx)),
        )

    for x, label in [(3, "Close game"), (4, "Competitive"), (5, "Strong offense")]:
        plt.axvline(x=x, linestyle="--", alpha=0.25)
        plt.text(x + 0.1, 0.85, label, fontsize=9, alpha=0.7)

    plt.gca().yaxis.set_major_formatter(PercentFormatter(1.0))
    plt.xlabel("Number of Runs Scored")
    plt.ylabel("Chance of scoring at least this many runs")
    plt.title(title)
    plt.legend(title="Lineups")
    plt.grid(True, alpha=0.25)
    plt.xticks(np.arange(0, max_runs + 1, 1))
    plt.yticks(np.arange(0, 1.05, 0.1))
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    print(f"Saved survival curve plot to: {output_path}")
    if return_path:
        return output_path


def plot_lineup_bucket_bars(
    results: List[Dict],
    title="Game outcomes: how often each lineup scores",
    output_path: str = "lineup_bucket_bars.png",
    return_path: bool = False,
):
    """
    Plot bucketed outcome probabilities:
      0-1 runs, 2-3 runs, 4-5 runs, 6-7 runs, 8+ runs
    """
    bucket_labels = [
        "0–1 runs\n(struggle)",
        "2–3 runs\n(average)",
        "4–5 runs\n(good)",
        "6–7 runs\n(solid)",
        "8–9 runs\n(strong)",
        "10+ runs\n(explosive)",
    ]
    x = np.arange(len(bucket_labels))
    width = 0.13

    plt.figure(figsize=(10, 6))

    for idx, r in enumerate(results):
        values = np.array(r["runs_scored_distribution"])
        probs = [
            ((values >= 0) & (values <= 1)).mean(),
            ((values >= 2) & (values <= 3)).mean(),
            ((values >= 4) & (values <= 5)).mean(),
            ((values >= 6) & (values <= 7)).mean(),
            ((values >= 8) & (values <= 9)).mean(),
            (values >= 10).mean(),
        ]

        offset = (idx - (len(results) - 1) / 2) * width
        plt.bar(
            x + offset,
            probs,
            width=width,
            alpha=0.75,
            label=r.get("display_name", f"Lineup {idx+1}"),
        )

    plt.xticks(x, bucket_labels)
    plt.ylabel("How often this happens")
    plt.gca().yaxis.set_major_formatter(PercentFormatter(1.0))
    plt.xlabel("Runs Scored")
    plt.title(title)
    plt.legend(title="Lineups")
    plt.grid(True, axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    print(f"Saved bucket bar plot to: {output_path}")
    if return_path:
        return output_path


def print_lineup_summary_table(results: List[Dict], title: str = "Lineup Summary"):
    print(f"\n===== {title} =====")
    for i, r in enumerate(results, start=1):
        print(f"\n#{i}")
        print("Lineup:", r["lineup"])
        print(f"Mean runs: {r['mean_runs']:.3f}")
        print(f"Median runs: {r['median_runs']:.3f}")
        print(f"Std dev: {r['std_runs']:.3f}")
        print(f"P(score >= target): {r['prob_ge_target']:.3f}")
        print(f"Sortino: {r['sortino']:.3f}")
        print(f"P10: {r['p10_runs']:.3f}")
        print(f"P90: {r['p90_runs']:.3f}")
        print(f"Simulated games: {r['n_games']}")