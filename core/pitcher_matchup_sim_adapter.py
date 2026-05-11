from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(slots=True)
class ExistingSimulatorPitcherMatchupResult:
    pitcher_name: str
    pitcher_bf: int
    pitcher_ip: float
    raw_avg_runs_allowed: float
    adjusted_avg_runs_allowed: float
    median_runs_allowed: float
    p10_runs_allowed: float
    p90_runs_allowed: float
    hold_le_2_rate: float
    hold_le_3_rate: float
    allow_7_plus_rate: float
    std_runs_allowed: float
    reliability: str
    role_caution: str


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


def run_existing_simulator_pitcher_matchup_report(
    projected_lineup: Sequence[ProjectedLineupSpot],
    pitchers: Sequence[PitcherProfile],
    *,
    games: int = 5000,
    innings_per_game: int = 7,
    seed: int | None = 42,
    target_runs: float = 4.0,
    rules: RulesConfig | None = None,
) -> list[ExistingSimulatorPitcherMatchupResult]:
    """
    Run the existing evaluator-backed simulation for multiple candidate pitchers.

    The returned rows are sorted for coach/report consumption: lowest adjusted
    runs allowed first, then lowest raw average runs allowed, then larger
    pitcher samples.
    """
    rows: list[ExistingSimulatorPitcherMatchupResult] = []

    for idx, pitcher in enumerate(pitchers or []):
        pitcher_seed = None if seed is None else seed + (idx * 1009)

        sim_result = run_existing_simulator_for_pitcher_matchup(
            projected_lineup,
            pitcher,
            games=games,
            innings_per_game=innings_per_game,
            seed=pitcher_seed,
            target_runs=target_runs,
            rules=rules,
        )

        runs_allowed = [
            float(value)
            for value in (getattr(sim_result, "runs_scored_distribution", []) or [])
        ]
        raw_avg_runs = float(getattr(sim_result, "mean_runs", 0.0) or 0.0)
        pitcher_bf = _pitcher_bf(pitcher)
        pitcher_ip = _pitcher_ip(pitcher)

        rows.append(
            ExistingSimulatorPitcherMatchupResult(
                pitcher_name=str(getattr(pitcher, "name", "Unknown pitcher")),
                pitcher_bf=pitcher_bf,
                pitcher_ip=pitcher_ip,
                raw_avg_runs_allowed=raw_avg_runs,
                adjusted_avg_runs_allowed=_adjusted_runs_allowed(
                    raw_avg_runs,
                    pitcher_bf,
                    pitcher_ip,
                ),
                median_runs_allowed=float(getattr(sim_result, "median_runs", 0.0) or 0.0),
                p10_runs_allowed=float(getattr(sim_result, "p10_runs", 0.0) or 0.0),
                p90_runs_allowed=float(getattr(sim_result, "p90_runs", 0.0) or 0.0),
                hold_le_2_rate=_pct_le(runs_allowed, 2.0),
                hold_le_3_rate=_pct_le(runs_allowed, 3.0),
                allow_7_plus_rate=_pct_ge(runs_allowed, 7.0),
                std_runs_allowed=float(getattr(sim_result, "std_runs", 0.0) or 0.0),
                reliability=_reliability_label(pitcher_bf, pitcher_ip),
                role_caution=_role_caution(pitcher_bf, pitcher_ip),
            )
        )

    return sorted(
        rows,
        key=lambda row: (
            row.adjusted_avg_runs_allowed,
            row.raw_avg_runs_allowed,
            -row.pitcher_bf,
        ),
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


def _pct_le(values: Sequence[float], threshold: float) -> float:
    if not values:
        return 0.0

    return sum(1 for value in values if float(value) <= threshold) / len(values)


def _pct_ge(values: Sequence[float], threshold: float) -> float:
    if not values:
        return 0.0

    return sum(1 for value in values if float(value) >= threshold) / len(values)


def _pitcher_ip(pitcher: PitcherProfile) -> float:
    try:
        return max(0.0, float(getattr(pitcher, "ip", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _pitcher_bf(pitcher: PitcherProfile) -> int:
    try:
        return max(0, int(getattr(pitcher, "bf", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _reliability_label(bf: int, ip: float) -> str:
    if bf >= 100 or ip >= 20:
        return "High"
    if bf >= 50 or ip >= 10:
        return "Medium"
    return "Low"


def _role_caution(bf: int, ip: float) -> str:
    reliability = _reliability_label(bf, ip)

    if reliability == "High":
        return "Established pitching sample"
    if reliability == "Medium":
        return "Usable but still developing sample"
    if bf < 25 or ip < 3:
        return "Emergency/depth sample"
    return "Limited pitching sample"


def _adjusted_runs_allowed(raw_avg_runs: float, bf: int, ip: float) -> float:
    reliability = _reliability_label(bf, ip)
    adjustment = 0.0

    if reliability == "Medium":
        adjustment += 0.35
    elif reliability == "Low":
        adjustment += 1.15

    if bf < 25 or ip < 3:
        adjustment += 0.40

    return float(raw_avg_runs or 0.0) + adjustment


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
    "ExistingSimulatorPitcherMatchupResult",
    "build_pitcher_adjusted_sim_players",
    "run_existing_simulator_for_pitcher_matchup",
    "run_existing_simulator_pitcher_matchup_report",
]
