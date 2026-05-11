from __future__ import annotations

from typing import Any, Sequence

from core.evaluator import evaluate_lineup
from core.models import LineupResult, Player, RulesConfig
from core.pitcher_matchups import (
    OpponentHitterProfile,
    PitcherProfile,
    ProjectedLineupSpot,
)


# Conservative high-school-ish baselines used only to translate matchup inputs
# into the existing lineup simulator's Player probability model.
#
# TODO: Calibrate these against the existing standalone pitcher matchup
# simulator before wiring this adapter into any report/UI path.
_BASELINE_EVENT_RATES = {
    "bb": 0.105,
    "so": 0.180,
    "1b": 0.165,
    "2b": 0.045,
    "3b": 0.008,
    "hr": 0.012,
}

_EVENT_BOUNDS = {
    "bb": (0.020, 0.250),
    "so": (0.030, 0.450),
    "1b": (0.050, 0.350),
    "2b": (0.000, 0.120),
    "3b": (0.000, 0.035),
    "hr": (0.000, 0.080),
}


def build_pitcher_adjusted_sim_players(
    projected_lineup: Sequence[ProjectedLineupSpot],
    pitcher: PitcherProfile,
) -> list[Player]:
    """
    Convert a projected opponent lineup into existing simulator Player objects.

    The existing evaluator expects each Player to carry plate-appearance event
    probabilities:
      p_bb, p_1b, p_2b, p_3b, p_hr, p_so, p_bip_out

    This adapter builds those probabilities from a conservative hitter/pitcher
    rate blend, then lets the existing simulator handle all baseball mechanics
    such as base advancement, productive outs, and inning/game flow.

    TODO: Treat this as an adapter skeleton/first pass. The rate translation
    should be validated before replacing the current isolated matchup simulator.
    """
    players: list[Player] = []

    for spot in projected_lineup or []:
        hitter = _projected_spot_hitter(spot)
        if hitter is None:
            continue

        probabilities = _estimate_existing_sim_player_probabilities(hitter, pitcher)

        player = Player(
            name=str(hitter.name or "Unknown hitter"),
            p_bb=probabilities["bb"],
            p_1b=probabilities["1b"],
            p_2b=probabilities["2b"],
            p_3b=probabilities["3b"],
            p_hr=probabilities["hr"],
            p_so=probabilities["so"],
            p_bip_out=probabilities["bip_out"],
            speed=_clamp(float(hitter.speed_score or 0.0) / 100.0),
            aggression=_clamp(
                (0.55 * (float(hitter.speed_score or 0.0) / 100.0))
                + (0.45 * (float(hitter.on_base_score or 0.0) / 100.0))
            ),
            steal_skill=_clamp(float(hitter.speed_score or 0.0) / 100.0),
            baserunning_iq=_clamp(
                (0.50 * (float(hitter.on_base_score or 0.0) / 100.0))
                + (0.50 * (float(hitter.sample_size_score or 0.0) / 100.0))
            ),
            sacrifice_ability=_clamp(1.0 - float(hitter.k_rate or 0.0)),
            contact_trait=_clamp(float(hitter.contact_rate or 0.0)),
            power_trait=_clamp(float(hitter.damage_score or 0.0) / 100.0),
            discipline_trait=_clamp(float(hitter.on_base_score or 0.0) / 100.0),
            walk_skill_trait=_clamp(float(hitter.bb_rate or 0.0) / 0.160),
            strikeout_tendency_trait=_clamp(float(hitter.k_rate or 0.0) / 0.300),
            chase_tendency_trait=_clamp(float(hitter.k_rate or 0.0) / 0.300),
        )
        player.normalize()
        players.append(player)

    return players


def run_existing_simulator_for_pitcher_matchup(
    projected_lineup: Sequence[ProjectedLineupSpot],
    pitcher: PitcherProfile,
    *,
    games: int = 5000,
    innings_per_game: int = 7,
    seed: int | None = 42,
    target_runs: float = 4.0,
    rules: RulesConfig | None = None,
) -> LineupResult:
    """
    Run a pitcher matchup through the existing lineup evaluator.

    This intentionally does not wire into the Pitching Matchup Report UI yet.
    It is an investigation adapter so the separate lightweight simulator can be
    compared against the app's canonical simulation/evaluation path.

    Returns:
        LineupResult from core.evaluator.evaluate_lineup.
    """
    sim_players = build_pitcher_adjusted_sim_players(projected_lineup, pitcher)
    if not sim_players:
        raise ValueError("Cannot run pitcher matchup simulation without projected hitters.")

    n_games = _safe_positive_int(games)
    if n_games <= 0:
        raise ValueError("games must be greater than zero.")

    innings = _safe_positive_int(innings_per_game)
    if innings <= 0:
        raise ValueError("innings_per_game must be greater than zero.")

    matchup_rules = _build_matchup_rules(
        rules=rules,
        innings_per_game=innings,
        lineup_size=len(sim_players),
    )

    return evaluate_lineup(
        sim_players,
        matchup_rules,
        n_games=n_games,
        target_runs=target_runs,
        seed=seed,
    )


def _projected_spot_hitter(spot: Any) -> OpponentHitterProfile | None:
    hitter = getattr(spot, "hitter", None)
    if isinstance(hitter, OpponentHitterProfile):
        return hitter
    return None


def _estimate_existing_sim_player_probabilities(
    hitter: OpponentHitterProfile,
    pitcher: PitcherProfile,
) -> dict[str, float]:
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
        "bb": _shrink_rate(
            _safe_div(float((hitter.bb or 0) + (hitter.hbp or 0)), hitter_pa),
            _BASELINE_EVENT_RATES["bb"],
            hitter_pa,
            midpoint=60.0,
        ),
        "so": _shrink_rate(
            _safe_div(float(hitter.k or 0), hitter_pa),
            _BASELINE_EVENT_RATES["so"],
            hitter_pa,
            midpoint=60.0,
        ),
        "1b": _shrink_rate(
            _safe_div(float(hitter_singles), hitter_pa),
            _BASELINE_EVENT_RATES["1b"],
            hitter_pa,
            midpoint=60.0,
        ),
        "2b": _shrink_rate(
            _safe_div(float(hitter.doubles or 0), hitter_pa),
            _BASELINE_EVENT_RATES["2b"],
            hitter_pa,
            midpoint=60.0,
        ),
        "3b": _shrink_rate(
            _safe_div(float(hitter.triples or 0), hitter_pa),
            _BASELINE_EVENT_RATES["3b"],
            hitter_pa,
            midpoint=60.0,
        ),
        "hr": _shrink_rate(
            _safe_div(float(hitter.hr or 0), hitter_pa),
            _BASELINE_EVENT_RATES["hr"],
            hitter_pa,
            midpoint=60.0,
        ),
    }

    pitcher_rates = {
        "bb": _shrink_rate(
            float(pitcher.free_base_rate or 0.0),
            _BASELINE_EVENT_RATES["bb"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "so": _shrink_rate(
            float(pitcher.k_rate or 0.0),
            _BASELINE_EVENT_RATES["so"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "1b": _shrink_rate(
            _safe_div(float(pitcher_singles_allowed), pitcher_bf),
            _BASELINE_EVENT_RATES["1b"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "2b": _shrink_rate(
            _safe_div(float(pitcher.doubles_allowed or 0), pitcher_bf),
            _BASELINE_EVENT_RATES["2b"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "3b": _shrink_rate(
            _safe_div(float(pitcher.triples_allowed or 0), pitcher_bf),
            _BASELINE_EVENT_RATES["3b"],
            pitcher_bf,
            midpoint=120.0,
        ),
        "hr": _shrink_rate(
            _safe_div(float(pitcher.hr_allowed or 0), pitcher_bf),
            _BASELINE_EVENT_RATES["hr"],
            pitcher_bf,
            midpoint=120.0,
        ),
    }

    blended = {
        event: _blend_rates(hitter_rates[event], pitcher_rates[event])
        for event in _BASELINE_EVENT_RATES
    }

    return _normalize_player_probabilities(blended)


def _build_matchup_rules(
    *,
    rules: RulesConfig | None,
    innings_per_game: int,
    lineup_size: int,
) -> RulesConfig:
    if rules is None:
        matchup_rules = RulesConfig()
    else:
        matchup_rules = RulesConfig(**rules.__dict__)

    matchup_rules.innings = innings_per_game
    matchup_rules.continuous_batting = True
    matchup_rules.lineup_size = lineup_size

    # Pitching matchup reports are about run prevention, so avoid a default
    # per-inning scoring cap masking blow-up innings in this isolated adapter.
    matchup_rules.max_runs_per_inning = max(int(matchup_rules.max_runs_per_inning), 99)

    return matchup_rules


def _normalize_player_probabilities(raw_probabilities: dict[str, float]) -> dict[str, float]:
    probabilities: dict[str, float] = {}

    for event, value in raw_probabilities.items():
        low, high = _EVENT_BOUNDS[event]
        probabilities[event] = _clamp(float(value or 0.0), low, high)

    explicit_total = sum(probabilities.values())

    # Keep room for balls-in-play outs; strikeouts are already explicit outs.
    max_explicit_total = 0.92
    if explicit_total > max_explicit_total:
        scale = max_explicit_total / explicit_total
        probabilities = {
            event: value * scale
            for event, value in probabilities.items()
        }
        explicit_total = sum(probabilities.values())

    probabilities["bip_out"] = max(0.0, 1.0 - explicit_total)

    total = sum(probabilities.values())
    if total <= 0:
        fallback = dict(_BASELINE_EVENT_RATES)
        fallback["bip_out"] = 1.0 - sum(_BASELINE_EVENT_RATES.values())
        return fallback

    return {
        "bb": probabilities["bb"] / total,
        "1b": probabilities["1b"] / total,
        "2b": probabilities["2b"] / total,
        "3b": probabilities["3b"] / total,
        "hr": probabilities["hr"] / total,
        "so": probabilities["so"] / total,
        "bip_out": probabilities["bip_out"] / total,
    }


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


__all__ = [
    "build_pitcher_adjusted_sim_players",
    "run_existing_simulator_for_pitcher_matchup",
]
