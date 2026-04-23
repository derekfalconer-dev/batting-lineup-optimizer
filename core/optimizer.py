import itertools
import random
import time
from typing import Any, Dict, List, Tuple

from core.evaluator import evaluate_lineup
from core.models import Player, RulesConfig


def evaluate_lineup_with_meta(
    lineup: List[Player],
    rules: RulesConfig,
    n_games: int,
    target_runs: float,
    seed: int,
) -> Dict[str, Any]:
    result = evaluate_lineup(
        lineup=lineup,
        rules=rules,
        n_games=n_games,
        target_runs=target_runs,
        seed=seed,
    )

    return {
        "lineup": [p.name for p in lineup],
        "players": lineup[:],
        "mean_runs": result.mean_runs,
        "median_runs": result.median_runs,
        "std_runs": result.std_runs,
        "prob_ge_target": result.prob_ge_target,
        "sortino": result.sortino,
        "p10_runs": result.p10_runs,
        "p90_runs": result.p90_runs,
        "n_games": result.n_games,
        "runs_scored_distribution": result.runs_scored_distribution,
    }

def print_results(title: str, results: List[Dict[str, Any]]):
    print(f"\n===== {title} =====")
    for r in results:
        print("\nLineup:", r["lineup"])
        print(f"Mean runs: {r['mean_runs']:.3f}")
        print(f"Median runs: {r['median_runs']:.3f}")
        print(f"Std dev: {r['std_runs']:.3f}")
        print(f"P(score >= target): {r['prob_ge_target']:.3f}")
        print(f"Sortino: {r['sortino']:.3f}")
        print(f"P10: {r['p10_runs']:.3f}")
        print(f"P90: {r['p90_runs']:.3f}")
        print(f"Simulated games: {r['n_games']}")


def composite_score(result: Dict[str, Any]) -> float:
    """
    Tunable combined ranking score for fast search.
    Mean runs is primary. Sortino and target-clearing probability matter too.
    """
    return (
        0.60 * result["mean_runs"]
        + 0.25 * result["prob_ge_target"]
        + 0.15 * result["sortino"]
    )


def lineup_to_key(lineup: List[Player]) -> Tuple[str, ...]:
    return tuple(p.name for p in lineup)


def generate_swap_neighbors(lineup: List[Player]) -> List[List[Player]]:
    neighbors = []
    n = len(lineup)

    for i in range(n):
        for j in range(i + 1, n):
            neighbor = lineup[:]
            neighbor[i], neighbor[j] = neighbor[j], neighbor[i]
            neighbors.append(neighbor)

    return neighbors


def build_seed_lineups(players: List[Player], rng: random.Random, n_random: int = 12) -> List[List[Player]]:
    """
    Create a diverse set of initial seed lineups.
    """
    seeds = []

    # Original
    seeds.append(players[:])

    # OBP-ish / best hitters first
    seeds.append(
        sorted(
            players,
            key=lambda p: p.p_bb + p.p_1b + p.p_2b + p.p_3b + p.p_hr,
            reverse=True,
        )
    )

    # Speed first
    seeds.append(sorted(players, key=lambda p: p.speed, reverse=True))

    # Studs in middle after table setters (OBP-ish top 2, then best bats)
    obp_sorted = sorted(
        players,
        key=lambda p: p.p_bb + p.p_1b + p.p_2b + p.p_3b + p.p_hr,
        reverse=True,
    )
    if len(obp_sorted) >= 5:
        alt = obp_sorted[:]
        alt = [alt[3], alt[4], alt[0], alt[1], alt[2]] + alt[5:]
        seeds.append(alt)

    # Random seeds
    for _ in range(n_random):
        lineup = players[:]
        rng.shuffle(lineup)
        seeds.append(lineup)

    # Deduplicate
    deduped = []
    seen = set()
    for s in seeds:
        key = lineup_to_key(s)
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    return deduped


def local_beam_search(
    players: List[Player],
    rules: RulesConfig,
    target_runs: float,
    search_games: int,
    refine_games: int,
    beam_width: int,
    max_rounds: int,
    top_n: int,
    seed: int,
) -> Dict[str, List[Dict[str, Any]]]:
    rng = random.Random(seed)
    start_time = time.time()

    seeds = build_seed_lineups(players, rng)
    print(f"Initial seeds: {len(seeds)}")

    cache: Dict[Tuple[str, ...], Dict[str, Any]] = {}

    def eval_cached(lineup: List[Player], n_games: int, eval_seed: int) -> Dict[str, Any]:
        """
        Cache only the search-stage evaluations. Refine-stage always re-runs cleanly.
        """
        key = lineup_to_key(lineup)

        if n_games == search_games and key in cache:
            return cache[key]

        result = evaluate_lineup_with_meta(
            lineup=lineup,
            rules=rules,
            n_games=n_games,
            target_runs=target_runs,
            seed=eval_seed,
        )

        if n_games == search_games:
            cache[key] = result

        return result

    # Evaluate initial beam
    beam = [eval_cached(lineup, search_games, seed) for lineup in seeds]
    beam.sort(key=composite_score, reverse=True)
    beam = beam[:beam_width]

    print(f"Initial beam width: {len(beam)}")

    for round_idx in range(max_rounds):
        round_start = time.time()
        candidates: List[Dict[str, Any]] = []
        seen_keys = set()

        # Include current beam members too
        for b in beam:
            key = tuple(b["lineup"])
            if key not in seen_keys:
                seen_keys.add(key)
                candidates.append(b)

        # Expand one-swap neighbors
        for b in beam:
            lineup_players = b["players"]
            neighbors = generate_swap_neighbors(lineup_players)

            for neighbor in neighbors:
                key = lineup_to_key(neighbor)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                res = eval_cached(
                    lineup=neighbor,
                    n_games=search_games,
                    eval_seed=seed + round_idx + 1,
                )
                candidates.append(res)

        candidates.sort(key=composite_score, reverse=True)
        new_beam = candidates[:beam_width]

        old_best = composite_score(beam[0])
        new_best = composite_score(new_beam[0])

        elapsed = time.time() - round_start
        total_elapsed = time.time() - start_time

        print(
            f"Round {round_idx + 1}: "
            f"{len(candidates)} candidates | "
            f"best score {new_best:.4f} | "
            f"round {elapsed:.1f}s | total {total_elapsed:.1f}s"
        )

        # Stop if beam has converged
        if new_best <= old_best + 1e-9:
            beam = new_beam
            print("No meaningful improvement. Stopping local beam search.")
            break

        beam = new_beam

    # Refine finalists with deeper sims
    finalists = beam[:top_n]
    refined = []
    for idx, f in enumerate(finalists, start=1):
        refined_result = evaluate_lineup_with_meta(
            lineup=f["players"],
            rules=rules,
            n_games=refine_games,
            target_runs=target_runs,
            seed=seed + 1000 + idx,
        )
        refined.append(refined_result)

    # Return leaderboards by different metrics
    top_mean = sorted(refined, key=lambda x: x["mean_runs"], reverse=True)
    top_sortino = sorted(refined, key=lambda x: x["sortino"], reverse=True)
    top_prob = sorted(refined, key=lambda x: x["prob_ge_target"], reverse=True)

    search_evaluations = len(cache)
    refine_evaluations = len(refined)

    return {
        "top_mean": top_mean[:top_n],
        "top_sortino": top_sortino[:top_n],
        "top_prob": top_prob[:top_n],
        "_meta": {
            "mode": "fast",
            "player_count": len(players),
            "seed_count": len(seeds),
            "search_evaluations": int(search_evaluations),
            "search_games_per_evaluation": int(search_games),
            "search_total_games": int(search_evaluations * search_games),
            "refine_evaluations": int(refine_evaluations),
            "refine_games_per_evaluation": int(refine_games),
            "refine_total_games": int(refine_evaluations * refine_games),
            "total_games": int((search_evaluations * search_games) + (refine_evaluations * refine_games)),
        },
    }


def brute_force_search(
    players: List[Player],
    rules: RulesConfig,
    target_runs: float = 4.0,
    search_games: int = 200,
    refine_games: int = 5000,
    top_n: int = 10,
    seed: int = 42,
) -> Dict[str, List[Dict[str, Any]]]:
    all_lineups = list(itertools.permutations(players))
    print(f"Total lineups: {len(all_lineups)}")

    results = []
    start_time = time.time()

    for i, lineup in enumerate(all_lineups):
        if i % 5000 == 0 and i > 0:
            elapsed = time.time() - start_time
            rate = i / elapsed
            remaining = (len(all_lineups) - i) / rate
            print(f"{i}/{len(all_lineups)} | {rate:.1f} lineups/sec | ETA: {remaining/60:.1f} min")

        res = evaluate_lineup_with_meta(
            lineup=list(lineup),
            rules=rules,
            n_games=search_games,
            target_runs=target_runs,
            seed=seed,
        )
        results.append(res)

    top_mean = sorted(results, key=lambda x: x["mean_runs"], reverse=True)[:top_n]
    top_sortino = sorted(results, key=lambda x: x["sortino"], reverse=True)[:top_n]
    top_prob = sorted(results, key=lambda x: x["prob_ge_target"], reverse=True)[:top_n]

    def refine(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        refined = []
        for r in group:
            refined.append(
                evaluate_lineup_with_meta(
                    lineup=r["players"],
                    rules=rules,
                    n_games=refine_games,
                    target_runs=target_runs,
                    seed=seed + 999,
                )
            )
        return refined

    refined_top_mean = refine(top_mean)
    refined_top_sortino = refine(top_sortino)
    refined_top_prob = refine(top_prob)

    search_evaluations = len(results)
    refine_evaluations = len(top_mean) + len(top_sortino) + len(top_prob)

    return {
        "top_mean": refined_top_mean,
        "top_sortino": refined_top_sortino,
        "top_prob": refined_top_prob,
        "_meta": {
            "mode": "brute_force",
            "player_count": len(players),
            "search_evaluations": int(search_evaluations),
            "search_games_per_evaluation": int(search_games),
            "search_total_games": int(search_evaluations * search_games),
            "refine_evaluations": int(refine_evaluations),
            "refine_games_per_evaluation": int(refine_games),
            "refine_total_games": int(refine_evaluations * refine_games),
            "total_games": int((search_evaluations * search_games) + (refine_evaluations * refine_games)),
        },
    }


def find_best_lineups(
    players: List[Player],
    rules: RulesConfig,
    mode: str = "fast",
    target_runs: float = 4.0,
    search_games: int = 75,
    refine_games: int = 3000,
    top_n: int = 5,
    seed: int = 42,
    beam_width: int = 12,
    max_rounds: int = 8,
) -> Dict[str, List[Dict[str, Any]]]:
    if mode == "brute_force":
        return brute_force_search(
            players=players,
            rules=rules,
            target_runs=target_runs,
            search_games=search_games,
            refine_games=refine_games,
            top_n=top_n,
            seed=seed,
        )

    if mode == "fast":
        return local_beam_search(
            players=players,
            rules=rules,
            target_runs=target_runs,
            search_games=search_games,
            refine_games=refine_games,
            beam_width=beam_width,
            max_rounds=max_rounds,
            top_n=top_n,
            seed=seed,
        )

    raise ValueError(f"Unknown optimizer mode: {mode}")