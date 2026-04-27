import random
from typing import Any, List, Optional, Tuple

from core.models import Player, RulesConfig
from core.baserunning import advance_runners, maybe_steal
from core.simulation_telemetry import SimulationTelemetry


BaseState = List[Optional[Player]]  # [runner_on_1st, runner_on_2nd, runner_on_3rd]


def sample_plate_appearance(player: Player, rng: random.Random) -> str:
    """
    Sample one plate appearance outcome for a player.

    Returns one of:
    'bb', '1b', '2b', '3b', 'hr', 'so', 'bip_out'
    """
    roll = rng.random()
    cumulative = 0.0

    outcomes = [
        ("bb", player.p_bb),
        ("1b", player.p_1b),
        ("2b", player.p_2b),
        ("3b", player.p_3b),
        ("hr", player.p_hr),
        ("so", player.p_so),
        ("bip_out", player.p_bip_out),
    ]

    for outcome, prob in outcomes:
        cumulative += prob
        if roll <= cumulative:
            return outcome

    # In case of floating-point edge cases
    return "bip_out"


def advance_runners_deterministic(
    bases: BaseState,
    batter: Player,
    outcome: str,
) -> Tuple[BaseState, int, int]:
    """
    Advance runners for a simple deterministic v1 model.

    Returns:
        new_bases, runs_scored, outs_added
    """
    first, second, third = bases
    runs = 0
    outs = 0

    if outcome == "bb":
        # Walk: force runners only if needed
        if first is None:
            first = batter
        elif second is None:
            second = first
            first = batter
        elif third is None:
            third = second
            second = first
            first = batter
        else:
            runs += 1
            third = second
            second = first
            first = batter

    elif outcome == "1b":
        # Deterministic simple rule:
        # runner on 3rd scores, others advance one base
        if third is not None:
            runs += 1
        third = second
        second = first
        first = batter

    elif outcome == "2b":
        # runners on 2nd and 3rd score, runner on 1st to 3rd
        if third is not None:
            runs += 1
        if second is not None:
            runs += 1
        new_third = first
        first = None
        second = batter
        third = new_third

    elif outcome == "3b":
        # all runners score
        if first is not None:
            runs += 1
        if second is not None:
            runs += 1
        if third is not None:
            runs += 1
        first = None
        second = None
        third = batter

    elif outcome == "hr":
        # batter plus all runners score
        if first is not None:
            runs += 1
        if second is not None:
            runs += 1
        if third is not None:
            runs += 1
        runs += 1
        first = None
        second = None
        third = None

    elif outcome in ("so", "bip_out"):
        outs = 1

    else:
        raise ValueError(f"Unknown outcome: {outcome}")

    return [first, second, third], runs, outs


def simulate_half_inning(
    lineup: List[Player],
    start_index: int,
    rules: RulesConfig,
    rng: random.Random,
    telemetry: SimulationTelemetry | None = None,
) -> Tuple[int, int]:
    """
    Simulate one half inning.

    Returns:
        runs_scored, next_batter_index
    """
    outs = 0
    runs = 0
    batter_index = start_index
    bases: BaseState = [None, None, None]

    inning_events: list[dict[str, Any]] = []

    while outs < 3 and runs < rules.max_runs_per_inning:
        # optional steal attempt before the pitch / plate appearance
        bases, steal_outs = maybe_steal(bases, outs, rng, rules)
        outs += steal_outs

        if outs >= 3:
            break

        batter = lineup[batter_index]
        lineup_spot = batter_index + 1
        outcome = sample_plate_appearance(batter, rng)

        bases_occupied_before = sum(1 for runner in bases if runner is not None)
        outs_before_play = outs

        bases, play_runs, play_outs = advance_runners(
            bases,
            batter,
            outcome,
            rng,
            rules,
            outs_before_play=outs,
        )

        if telemetry is not None:
            inning_events.append(
                telemetry.record_plate_appearance(
                    player_name=batter.name,
                    lineup_spot=lineup_spot,
                    outcome=outcome,
                    bases_occupied_before=bases_occupied_before,
                    outs_before=outs_before_play,
                    play_runs=play_runs,
                )
            )

        # Respect inning run cap
        runs += play_runs
        if runs > rules.max_runs_per_inning:
            runs = rules.max_runs_per_inning

        outs += play_outs

        batter_index = (batter_index + 1) % len(lineup)

    if telemetry is not None:
        telemetry.finalize_inning(inning_events, inning_runs=runs)

    return runs, batter_index


def simulate_game(
    lineup: List[Player],
    rules: RulesConfig,
    rng: Optional[random.Random] = None,
    telemetry: SimulationTelemetry | None = None,
) -> int:
    """
    Simulate a full game and return total runs scored.
    """
    if rng is None:
        rng = random.Random()

    total_runs = 0
    batter_index = 0

    if telemetry is not None and not telemetry.lineup:
        telemetry.lineup = [p.name for p in lineup]

    for _ in range(rules.innings):
        inning_runs, batter_index = simulate_half_inning(
            lineup=lineup,
            start_index=batter_index,
            rules=rules,
            rng=rng,
            telemetry=telemetry,
        )
        total_runs += inning_runs

    return total_runs