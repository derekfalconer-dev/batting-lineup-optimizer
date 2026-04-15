import random
from typing import List, Optional, Tuple

from core.models import Player

BaseState = List[Optional[Player]]  # [first, second, third]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_from_second_on_single(runner: Player, rules) -> float:
    base_prob = (
        0.35
        + 0.30 * runner.speed
        + 0.20 * runner.baserunning_iq
        + 0.10 * runner.aggression
    )

    if getattr(rules, "leadoffs_allowed", False):
        base_prob += 0.08

    if getattr(rules, "base_distance_ft", 70) <= 60:
        base_prob += 0.07
    elif getattr(rules, "base_distance_ft", 70) >= 90:
        base_prob -= 0.05

    base_prob *= getattr(rules, "advancement_success_multiplier", 1.0)

    return clamp(base_prob)


def first_to_third_on_single(runner: Player, rules) -> float:
    base_prob = (
        0.15
        + 0.30 * runner.speed
        + 0.20 * runner.baserunning_iq
        + 0.15 * runner.aggression
    )

    if getattr(rules, "leadoffs_allowed", False):
        base_prob += 0.07

    if getattr(rules, "base_distance_ft", 70) <= 60:
        base_prob += 0.06
    elif getattr(rules, "base_distance_ft", 70) >= 90:
        base_prob -= 0.04

    base_prob *= getattr(rules, "advancement_success_multiplier", 1.0)

    return clamp(base_prob)


def score_from_first_on_double(runner: Player, rules) -> float:
    base_prob = (
        0.45
        + 0.30 * runner.speed
        + 0.15 * runner.baserunning_iq
        + 0.10 * runner.aggression
    )

    if getattr(rules, "leadoffs_allowed", False):
        base_prob += 0.06

    if getattr(rules, "base_distance_ft", 70) <= 60:
        base_prob += 0.05
    elif getattr(rules, "base_distance_ft", 70) >= 90:
        base_prob -= 0.03

    base_prob *= getattr(rules, "advancement_success_multiplier", 1.0)

    return clamp(base_prob)


def steal_second_attempt_prob(runner: Player, rules) -> float:
    base_prob = (
        0.02
        + 0.10 * runner.aggression
        + 0.08 * runner.steal_skill
    )

    if not rules.steals_allowed:
        return 0.0

    if rules.leadoffs_allowed:
        base_prob *= 1.75

    base_prob *= rules.steal_attempt_multiplier

    return clamp(base_prob)


def steal_second_success_prob(runner: Player, rules) -> float:
    base_prob = (
        0.45
        + 0.25 * runner.speed
        + 0.25 * runner.steal_skill
        + 0.05 * runner.baserunning_iq
    )

    if not rules.steals_allowed:
        return 0.0

    # Leadoffs make steals much easier
    if rules.leadoffs_allowed:
        base_prob += 0.18

    # 60 ft bases easier than 70 ft
    if rules.base_distance_ft <= 60:
        base_prob += 0.10
    elif rules.base_distance_ft >= 70:
        base_prob -= 0.05

    base_prob *= rules.steal_success_multiplier

    return clamp(base_prob)


def steal_third_attempt_prob(runner: Player, rules) -> float:
    base_prob = (
        0.01
        + 0.08 * runner.aggression
        + 0.06 * runner.steal_skill
    )

    if not rules.steals_allowed:
        return 0.0

    if rules.leadoffs_allowed:
        base_prob *= 1.5

    base_prob *= rules.steal_attempt_multiplier

    return clamp(base_prob)


def steal_third_success_prob(runner: Player, rules) -> float:
    base_prob = (
        0.40
        + 0.20 * runner.speed
        + 0.30 * runner.steal_skill
        + 0.05 * runner.baserunning_iq
    )

    if not rules.steals_allowed:
        return 0.0

    if rules.leadoffs_allowed:
        base_prob += 0.15

    if rules.base_distance_ft <= 60:
        base_prob += 0.08
    elif rules.base_distance_ft >= 70:
        base_prob -= 0.04

    base_prob *= rules.steal_success_multiplier

    return clamp(base_prob)


def maybe_steal(
    bases: BaseState,
    outs: int,
    rng: random.Random,
    rules,
) -> Tuple[BaseState, int]:
    """
    Try one simple steal event before the plate appearance.

    Priority:
    1. steal 3rd if open
    2. steal 2nd if open

    Returns:
        new_bases, outs_added
    """
    first, second, third = bases

    if outs >= 3:
        return bases, 0

    # Try steal of 3rd first
    if second is not None and third is None:
        if rng.random() < steal_third_attempt_prob(second, rules):
            if rng.random() < steal_third_success_prob(second, rules):
                third = second
                second = None
                return [first, second, third], 0
            else:
                second = None
                return [first, second, third], 1

    # Then try steal of 2nd
    if first is not None and second is None:
        if rng.random() < steal_second_attempt_prob(first, rules):
            if rng.random() < steal_second_success_prob(first, rules):
                second = first
                first = None
                return [first, second, third], 0
            else:
                first = None
                return [first, second, third], 1

    return [first, second, third], 0


def productive_out_multiplier(batter: Player, rules=None) -> float:
    """
    Estimate how likely the hitter is to produce a useful ball-in-play out.

    Intuition:
    - low strikeout hitters are better at putting pressure on the defense
    - better contact profile slightly improves productive-out odds
    - environment can amplify/reduce productive-out value
    """
    contact_proxy = batter.p_1b + batter.p_2b + batter.p_3b
    multiplier = 1.0

    # Lower K hitters get more productive-outs
    multiplier += 0.8 * max(0.0, 0.22 - batter.p_so)

    # Better contact shape helps a little
    multiplier += 0.4 * max(0.0, contact_proxy - 0.18)

    if rules is not None:
        multiplier *= getattr(rules, "productive_out_env_multiplier", 1.0)

    return clamp(multiplier, 0.75, 1.50)


def runner_advancement_bonus(runner: Player) -> float:
    """
    Bonus applied to runner advancement on productive BIP outs.
    """
    bonus = (
        0.10 * runner.speed
        + 0.08 * runner.baserunning_iq
        + 0.05 * runner.aggression
    )
    return clamp(bonus, 0.0, 0.18)


def double_play_prob(
    batter: Player,
    runner_on_first: Player,
    outs_before_play: int,
    rules=None,
) -> float:
    """
    Small youth-baseball DP model.

    Applies only when:
    - runner on 1st
    - fewer than 2 outs

    Intuition:
    - youth DPs are rare
    - faster batter + faster runner reduce DP odds
    - more contact / lower K shape reduces "hard grounder into two" odds a bit
    - leadoffs reduce DP odds sharply because runner often would have been moving
    - bigger diamonds slightly increase DP odds
    """
    if runner_on_first is None or outs_before_play >= 2:
        return 0.0

    contact_shape = batter.p_1b + batter.p_2b + batter.p_3b
    batter_speed = batter.speed
    runner_speed = runner_on_first.speed

    # Base rate on eligible BIP outs: intentionally low for youth baseball
    prob = 0.08

    # Faster batter / runner -> fewer DPs
    prob -= 0.035 * batter_speed
    prob -= 0.030 * runner_speed

    # Slightly reduce DP odds for stronger contact / table-setter style hitters
    prob -= 0.020 * max(0.0, contact_shape - 0.20)

    if rules is not None:
        if getattr(rules, "leadoffs_allowed", False):
            prob *= 0.25
        else:
            prob *= 1.0

        base_distance = getattr(rules, "base_distance_ft", 70)
        if base_distance <= 60:
            prob *= 0.80
        elif base_distance >= 90:
            prob *= 1.25

    return clamp(prob, 0.01, 0.12)


def fielders_choice_prob(
    batter: Player,
    runner_on_first: Player,
    outs_before_play: int,
    rules=None,
) -> float:
    """
    Lightweight FC model:
    - runner on 1st forced at 2nd
    - batter reaches 1st safely
    - one out recorded

    Intuition:
    - a little more likely than DP
    - faster batter makes FC more believable
    - leadoffs reduce this a bit because the runner may not be standing on first
      at contact as often in a real game
    """
    if runner_on_first is None or outs_before_play >= 2:
        return 0.0

    prob = 0.10
    prob += 0.03 * batter.speed
    prob += 0.01 * batter.aggression
    prob -= 0.02 * runner_on_first.speed

    if rules is not None:
        if getattr(rules, "leadoffs_allowed", False):
            prob *= 0.70

        base_distance = getattr(rules, "base_distance_ft", 70)
        if base_distance <= 60:
            prob *= 0.95
        elif base_distance >= 90:
            prob *= 1.10

    return clamp(prob, 0.03, 0.16)


def advance_on_bip_out(
    bases: BaseState,
    batter: Player,
    outs_before_play: int,
    rng: random.Random,
    rules=None,
) -> Tuple[BaseState, int]:
    """
    Lightweight productive-out model.

    Assumptions:
    - Only applies with fewer than 2 outs.
    - Runner on 3rd may score on a sac-fly / productive grounder.
    - Runner on 2nd may advance to 3rd.
    - Runner on 1st may advance to 2nd.
    - Probabilities depend modestly on:
        - hitter contact / strikeout shape
        - runner speed / baserunning / aggression
    """
    first, second, third = bases
    runs = 0

    if outs_before_play >= 2:
        return [first, second, third], runs

    batter_mult = productive_out_multiplier(batter, rules)

    # Runner on 3rd scores on some BIP outs
    if third is not None:
        score_prob = 0.20 * batter_mult + runner_advancement_bonus(third)
        if rng.random() < clamp(score_prob, 0.05, 0.45):
            runs += 1
            third = None

    # Runner on 2nd advances to 3rd on some groundouts / productive outs
    if second is not None and third is None:
        adv_prob = 0.50 * batter_mult + runner_advancement_bonus(second)
        if rng.random() < clamp(adv_prob, 0.20, 0.80):
            third = second
            second = None

    # Runner on 1st advances to 2nd on some groundouts
    if first is not None and second is None:
        adv_prob = 0.50 * batter_mult + runner_advancement_bonus(first)
        if rng.random() < clamp(adv_prob, 0.20, 0.80):
            second = first
            first = None

    return [first, second, third], runs


def advance_runners(
    bases: BaseState,
    batter: Player,
    outcome: str,
    rng: random.Random,
    rules,
    outs_before_play: int = 0,
) -> Tuple[BaseState, int, int]:
    first, second, third = bases
    runs = 0
    outs = 0

    if outcome == "bb":
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
        new_first = batter
        new_second = None
        new_third = None

        if third is not None:
            runs += 1

        if second is not None:
            if rng.random() < score_from_second_on_single(second, rules):
                runs += 1
            else:
                new_third = second

        if first is not None:
            if rng.random() < first_to_third_on_single(first, rules):
                if new_third is None:
                    new_third = first
                else:
                    new_second = first
            else:
                new_second = first

        first, second, third = new_first, new_second, new_third

    elif outcome == "2b":
        if third is not None:
            runs += 1
        if second is not None:
            runs += 1

        new_second = batter
        new_third = None

        if first is not None:
            if rng.random() < score_from_first_on_double(first, rules):
                runs += 1
            else:
                new_third = first

        first, second, third = None, new_second, new_third

    elif outcome == "3b":
        if first is not None:
            runs += 1
        if second is not None:
            runs += 1
        if third is not None:
            runs += 1
        first, second, third = None, None, batter

    elif outcome == "hr":
        if first is not None:
            runs += 1
        if second is not None:
            runs += 1
        if third is not None:
            runs += 1
        runs += 1
        first, second, third = None, None, None

    elif outcome == "so":
        outs = 1

    elif outcome == "bip_out":
        # -------------------------------------------------
        # 1) Small chance of double play
        # -------------------------------------------------
        if first is not None and outs_before_play < 2:
            fc_prob = fielders_choice_prob(
                batter=batter,
                runner_on_first=first,
                outs_before_play=outs_before_play,
                rules=rules,
            )
            if rng.random() < fc_prob:
                # Approximate the common youth FC / failed-DP attempt:
                # - runner from 1st is forced out at 2nd
                # - batter reaches 1st safely
                # - other runners advance similarly to a modestly productive BIP
                fc_runs = 0

                new_first = batter
                new_second = None
                new_third = third

                # Runner from 3rd often scores when defense is occupied with the force
                if third is not None:
                    score_prob = 0.55 + 0.10 * third.speed + 0.08 * third.aggression
                    if rng.random() < clamp(score_prob, 0.40, 0.85):
                        fc_runs += 1
                        new_third = None

                # Runner from 2nd often advances to 3rd on the play
                if second is not None:
                    adv_prob = 0.65 + 0.08 * second.speed + 0.05 * second.baserunning_iq
                    if new_third is None and rng.random() < clamp(adv_prob, 0.45, 0.90):
                        new_third = second
                    else:
                        new_second = second

                runs += fc_runs
                outs = 1
                return [new_first, new_second, new_third], runs, outs

        # -------------------------------------------------
        # 2) Small chance of fielder's choice
        # -------------------------------------------------
        if first is not None and outs_before_play < 2:
            fc_prob = fielders_choice_prob(
                batter=batter,
                runner_on_first=first,
                outs_before_play=outs_before_play,
                rules=rules,
            )
            if rng.random() < fc_prob:
                # Common FC approximation:
                # lead force at 2nd, batter safe at 1st, one out recorded
                first = batter
                outs = 1
                return [first, second, third], runs, outs

        # -------------------------------------------------
        # 3) Otherwise standard productive / neutral BIP out
        # -------------------------------------------------
        bases_after, advance_runs = advance_on_bip_out(
            [first, second, third],
            batter=batter,
            outs_before_play=outs_before_play,
            rng=rng,
            rules=rules,
        )
        first, second, third = bases_after
        runs += advance_runs
        outs = 1

    else:
        raise ValueError(f"Unknown outcome: {outcome}")

    return [first, second, third], runs, outs