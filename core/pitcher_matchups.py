from __future__ import annotations

from dataclasses import dataclass


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _rate_score(value: float, *, poor: float, excellent: float) -> float:
    """
    Convert a rate where higher is better into a 0-100 score.
    """
    if excellent == poor:
        return 0.0
    return _clamp(100.0 * (value - poor) / (excellent - poor))


def _inverse_rate_score(value: float, *, excellent: float, poor: float) -> float:
    """
    Convert a rate where lower is better into a 0-100 score.
    """
    if poor == excellent:
        return 0.0
    return _clamp(100.0 * (poor - value) / (poor - excellent))


def _format_pct(value: float) -> str:
    return f"{value:.1%}"


@dataclass(slots=True)
class OpponentHitterProfile:
    name: str
    pa: int
    ab: int
    hits: int
    doubles: int
    triples: int
    hr: int
    bb: int
    k: int
    hbp: int
    obp: float
    slg: float
    ops: float
    sb: int = 0
    gp: int = 0

    @property
    def contact_rate(self) -> float:
        return _clamp(_safe_div(self.ab - self.k, self.ab), 0.0, 1.0)

    @property
    def k_rate(self) -> float:
        return _safe_div(self.k, self.pa)

    @property
    def bb_rate(self) -> float:
        return _safe_div(self.bb, self.pa)

    @property
    def xbh(self) -> int:
        return self.doubles + self.triples + self.hr

    @property
    def xbh_rate(self) -> float:
        return _safe_div(self.xbh, self.pa)

    @property
    def damage_score(self) -> float:
        slg_score = _rate_score(self.slg, poor=0.250, excellent=0.650)
        xbh_score = _rate_score(self.xbh_rate, poor=0.025, excellent=0.150)
        hr_score = _rate_score(_safe_div(self.hr, self.pa), poor=0.000, excellent=0.055)
        return _clamp((0.50 * slg_score) + (0.35 * xbh_score) + (0.15 * hr_score))

    @property
    def on_base_score(self) -> float:
        obp_score = _rate_score(self.obp, poor=0.280, excellent=0.500)
        walk_score = _rate_score(self.bb_rate, poor=0.030, excellent=0.160)
        hbp_score = _rate_score(_safe_div(self.hbp, self.pa), poor=0.000, excellent=0.045)
        return _clamp((0.70 * obp_score) + (0.20 * walk_score) + (0.10 * hbp_score))

    @property
    def speed_score(self) -> float:
        sb_per_pa = _safe_div(self.sb, self.pa)
        sb_per_game = _safe_div(self.sb, self.gp)
        return _clamp(
            (0.65 * _rate_score(sb_per_pa, poor=0.000, excellent=0.120))
            + (0.35 * _rate_score(sb_per_game, poor=0.000, excellent=0.900))
        )

    @property
    def sample_size_score(self) -> float:
        return _clamp(100.0 * _safe_div(self.pa, 80.0))


@dataclass(slots=True)
class PitcherProfile:
    name: str
    ip: float
    bf: int
    ab: int
    hits_allowed: int
    doubles_allowed: int
    triples_allowed: int
    hr_allowed: int
    bb: int
    k: int
    hbp: int
    oba: float
    obp_allowed: float
    pitches: int = 0
    era: float | None = None
    gp: int = 0
    gs: int = 0

    @property
    def k_rate(self) -> float:
        return _safe_div(self.k, self.bf)

    @property
    def bb_rate(self) -> float:
        return _safe_div(self.bb, self.bf)

    @property
    def hbp_rate(self) -> float:
        return _safe_div(self.hbp, self.bf)

    @property
    def free_base_rate(self) -> float:
        return _safe_div(self.bb + self.hbp, self.bf)

    @property
    def hit_rate(self) -> float:
        return _safe_div(self.hits_allowed, self.bf)

    @property
    def damage_rate(self) -> float:
        weighted_xbh = (
            self.doubles_allowed
            + (2 * self.triples_allowed)
            + (3 * self.hr_allowed)
        )
        return _safe_div(weighted_xbh, self.bf)

    @property
    def pitches_per_bf(self) -> float:
        return _safe_div(self.pitches, self.bf)

    @property
    def sample_size_score(self) -> float:
        bf_score = _clamp(100.0 * _safe_div(self.bf, 140.0))
        ip_score = _clamp(100.0 * _safe_div(self.ip, 35.0))
        return max(bf_score, ip_score)

    @property
    def command_score(self) -> float:
        free_base_score = _inverse_rate_score(
            self.free_base_rate,
            excellent=0.045,
            poor=0.180,
        )
        walk_score = _inverse_rate_score(
            self.bb_rate,
            excellent=0.035,
            poor=0.150,
        )
        return _clamp((0.65 * free_base_score) + (0.35 * walk_score))

    @property
    def bat_missing_score(self) -> float:
        return _rate_score(self.k_rate, poor=0.080, excellent=0.360)

    @property
    def damage_suppression_score(self) -> float:
        oba_score = _inverse_rate_score(self.oba, excellent=0.170, poor=0.360)
        obp_score = _inverse_rate_score(self.obp_allowed, excellent=0.230, poor=0.450)
        xbh_score = _inverse_rate_score(self.damage_rate, excellent=0.015, poor=0.135)
        return _clamp((0.35 * oba_score) + (0.30 * obp_score) + (0.35 * xbh_score))


@dataclass(slots=True)
class ProjectedLineupSpot:
    spot: int
    hitter: OpponentHitterProfile
    role: str
    explanation: str


@dataclass(slots=True)
class PitcherMatchupResult:
    pitcher: PitcherProfile
    projected_runs_index: float
    matchup_score: float
    strikeout_advantage: float
    walk_risk: float
    damage_risk: float
    traffic_risk: float
    sample_confidence: str
    recommended_role: str
    explanation: str
    caveats: list[str]


def _hitter_balanced_score(hitter: OpponentHitterProfile) -> float:
    return _clamp(
        (0.30 * hitter.on_base_score)
        + (0.30 * hitter.damage_score)
        + (0.20 * _rate_score(hitter.ops, poor=0.550, excellent=1.050))
        + (0.10 * _rate_score(hitter.contact_rate, poor=0.550, excellent=0.880))
        + (0.10 * hitter.sample_size_score)
    )


def _choose_hitter(
    hitters: list[OpponentHitterProfile],
    used_names: set[str],
    scorer,
) -> OpponentHitterProfile | None:
    available = [hitter for hitter in hitters if hitter.name not in used_names]
    if not available:
        return None
    return max(available, key=scorer)


def _select_for_spot(
    candidates: list[OpponentHitterProfile],
    used_names: set[str],
    scorer,
) -> OpponentHitterProfile | None:
    return _choose_hitter(candidates, used_names, scorer)


def _hitter_hr_rate(hitter: OpponentHitterProfile) -> float:
    return _safe_div(hitter.hr, hitter.pa)


def _leadoff_score(hitter: OpponentHitterProfile) -> float:
    contact_score = _rate_score(hitter.contact_rate, poor=0.550, excellent=0.880)
    elite_damage_penalty = 8.0 * _clamp(
        (hitter.damage_score - 75.0) / 25.0,
        0.0,
        1.0,
    )
    return _clamp(
        (0.46 * hitter.on_base_score)
        + (0.24 * hitter.speed_score)
        + (0.16 * contact_score)
        + (0.14 * hitter.sample_size_score)
        - elite_damage_penalty
    )


def _best_overall_hitter_score(hitter: OpponentHitterProfile) -> float:
    return _clamp(
        (0.34 * _rate_score(hitter.ops, poor=0.550, excellent=1.100))
        + (0.29 * hitter.damage_score)
        + (0.27 * hitter.on_base_score)
        + (0.10 * hitter.sample_size_score)
    )


def _cleanup_damage_score(hitter: OpponentHitterProfile) -> float:
    return _clamp(
        (0.40 * hitter.damage_score)
        + (0.25 * _rate_score(hitter.slg, poor=0.300, excellent=0.700))
        + (0.20 * _rate_score(hitter.xbh_rate, poor=0.025, excellent=0.160))
        + (0.10 * _rate_score(_hitter_hr_rate(hitter), poor=0.000, excellent=0.060))
        + (0.05 * hitter.sample_size_score)
    )


def _secondary_run_producer_score(hitter: OpponentHitterProfile) -> float:
    return _clamp(
        (0.36 * hitter.damage_score)
        + (0.32 * _rate_score(hitter.ops, poor=0.550, excellent=1.100))
        + (0.22 * hitter.on_base_score)
        + (0.10 * hitter.sample_size_score)
    )


def _two_hole_table_setter_score(hitter: OpponentHitterProfile) -> float:
    return _clamp(
        (0.38 * _rate_score(hitter.contact_rate, poor=0.550, excellent=0.900))
        + (0.34 * hitter.on_base_score)
        + (0.18 * _inverse_rate_score(hitter.k_rate, excellent=0.050, poor=0.300))
        + (0.10 * hitter.sample_size_score)
    )


def project_opponent_lineup(
    hitters: list[OpponentHitterProfile],
    lineup_size: int = 9,
) -> list[ProjectedLineupSpot]:
    """
    Build an explainable projected opponent batting order from season stats.

    This does not claim to know the opponent's actual order. It creates a
    reasonable scouting estimate so matchup modeling can happen before an
    official lineup is available.
    """
    if not hitters or lineup_size <= 0:
        return []

    target_size = min(lineup_size, len(hitters))
    qualified = [hitter for hitter in hitters if hitter.pa >= 10]

    if len(qualified) < target_size:
        tiny_sample_fillers = sorted(
            [hitter for hitter in hitters if hitter.pa < 10],
            key=_hitter_balanced_score,
            reverse=True,
        )
        candidates = qualified + tiny_sample_fillers
    else:
        candidates = qualified

    candidates = sorted(
        candidates,
        key=lambda hitter: (hitter.sample_size_score, _hitter_balanced_score(hitter)),
        reverse=True,
    )
    candidate_pool_size = min(len(candidates), max(lineup_size + 4, lineup_size))
    candidates = candidates[: max(candidate_pool_size, 1)]

    used_names: set[str] = set()
    selected_by_spot: dict[int, ProjectedLineupSpot] = {}

    def assign_spot(
        desired_spot: int,
        role: str,
        scorer,
        explanation_builder,
    ) -> None:
        hitter = _select_for_spot(candidates, used_names, scorer)
        if hitter is None:
            return

        used_names.add(hitter.name)
        selected_by_spot[desired_spot] = ProjectedLineupSpot(
            spot=desired_spot,
            hitter=hitter,
            role=role,
            explanation=explanation_builder(hitter),
        )

    protected_spot_plans = [
        (
            1,
            "Projected leadoff",
            _leadoff_score,
            lambda h: (
                f"Projected leadoff profile: high OBP ({h.obp:.3f}), speed pressure, "
                f"and usable sample ({h.pa} PA). Elite damage bats get a small "
                f"leadoff penalty so middle-order power is protected."
            ),
        ),
        (
            3,
            "Projected best overall bat",
            _best_overall_hitter_score,
            lambda h: (
                f"Projected three-hole profile: best overall bat with "
                f"{h.ops:.3f} OPS, {h.obp:.3f} OBP, and strong damage indicators."
            ),
        ),
        (
            4,
            "Projected cleanup damage bat",
            _cleanup_damage_score,
            lambda h: (
                f"Projected cleanup profile: primary damage bat with "
                f"{h.slg:.3f} SLG, {h.xbh} extra-base hits, and "
                f"{_format_pct(_hitter_hr_rate(h))} HR rate."
            ),
        ),
        (
            2,
            "Projected contact/OBP table-setter",
            _two_hole_table_setter_score,
            lambda h: (
                f"Projected two-hole table-setter: contact/OBP profile with "
                f"{_format_pct(h.contact_rate)} contact rate, {h.obp:.3f} OBP, "
                f"and {_format_pct(h.k_rate)} K rate."
            ),
        ),
        (
            5,
            "Projected secondary run producer",
            _secondary_run_producer_score,
            lambda h: (
                f"Projected five-hole profile: secondary run producer with "
                f"{h.ops:.3f} OPS and enough damage/on-base ability to protect cleanup."
            ),
        ),
    ]

    for desired_spot, role, scorer, explanation_builder in protected_spot_plans:
        if desired_spot <= target_size:
            assign_spot(desired_spot, role, scorer, explanation_builder)

    for desired_spot in range(6, target_size + 1):
        assign_spot(
            desired_spot,
            "Projected lower-order depth",
            _hitter_balanced_score,
            lambda h: (
                f"Projected lower-order profile: best remaining balanced bat by "
                f"OBP, damage, contact, and sample size ({h.pa} PA)."
            ),
        )

    for desired_spot in range(1, target_size + 1):
        if desired_spot in selected_by_spot:
            continue

        assign_spot(
            desired_spot,
            "Projected lower-order depth",
            _hitter_balanced_score,
            lambda h: (
                f"Projected lower-order profile: best remaining balanced bat by "
                f"OBP, damage, contact, and sample size ({h.pa} PA)."
            ),
        )

    return [
        selected_by_spot[spot]
        for spot in range(1, target_size + 1)
        if spot in selected_by_spot
    ]


def summarize_opponent_lineup(projected_lineup: list[ProjectedLineupSpot]) -> dict:
    hitters = [spot.hitter for spot in projected_lineup]

    if not hitters:
        return {
            "avg_obp": 0.0,
            "avg_slg": 0.0,
            "avg_ops": 0.0,
            "avg_k_rate": 0.0,
            "avg_bb_rate": 0.0,
            "avg_damage_score": 0.0,
            "lineup_strength_label": "Unknown",
            "explanation": "No projected opponent hitters were provided.",
        }

    avg_obp = _avg([hitter.obp for hitter in hitters])
    avg_slg = _avg([hitter.slg for hitter in hitters])
    avg_ops = _avg([hitter.ops for hitter in hitters])
    avg_k_rate = _avg([hitter.k_rate for hitter in hitters])
    avg_bb_rate = _avg([hitter.bb_rate for hitter in hitters])
    avg_damage_score = _avg([hitter.damage_score for hitter in hitters])

    if avg_ops >= 0.850 or avg_damage_score >= 72:
        strength = "High-scoring threat"
        explanation = "Projected lineup has strong OPS and extra-base damage indicators."
    elif avg_ops >= 0.720 or avg_obp >= 0.370:
        strength = "Solid offensive lineup"
        explanation = "Projected lineup has enough on-base ability or damage to create steady pressure."
    elif avg_ops >= 0.620:
        strength = "Moderate offensive lineup"
        explanation = "Projected lineup grades closer to average, with some pressure spots."
    else:
        strength = "Lower offensive pressure"
        explanation = "Projected lineup has limited OPS and damage indicators in the available stats."

    return {
        "avg_obp": avg_obp,
        "avg_slg": avg_slg,
        "avg_ops": avg_ops,
        "avg_k_rate": avg_k_rate,
        "avg_bb_rate": avg_bb_rate,
        "avg_damage_score": avg_damage_score,
        "lineup_strength_label": strength,
        "explanation": explanation,
    }


def _sample_confidence(pitcher: PitcherProfile, projected_lineup: list[ProjectedLineupSpot]) -> str:
    pitcher_score = pitcher.sample_size_score
    hitter_score = _avg([spot.hitter.sample_size_score for spot in projected_lineup])

    combined = (0.65 * pitcher_score) + (0.35 * hitter_score)

    if combined >= 70:
        return "High"
    if combined >= 40:
        return "Medium"
    return "Low"


def _recommended_role(matchup_score: float, sample_confidence: str) -> str:
    if matchup_score >= 76 and sample_confidence != "Low":
        return "Primary matchup recommendation"
    if matchup_score >= 66:
        return "Strong statistical option"
    if matchup_score >= 55:
        return "Usable matchup with caveats"
    return "Avoid unless roster context requires it"


def _pitcher_caveats(
    pitcher: PitcherProfile,
    summary: dict,
    sample_confidence: str,
) -> list[str]:
    caveats: list[str] = []

    if sample_confidence == "Low":
        caveats.append("Low sample confidence: treat this as directional until more innings or plate appearances are available.")

    if pitcher.bf < 40:
        caveats.append("Pitcher has a small batters-faced sample, so rates can swing quickly.")

    if pitcher.free_base_rate >= 0.130:
        caveats.append("Walk/HBP risk is elevated; free baserunners could drive matchup volatility.")

    if pitcher.damage_rate >= 0.090:
        caveats.append("Extra-base damage allowed is a concern against stronger bats.")

    if summary["avg_obp"] >= 0.390:
        caveats.append("Opponent profile has high on-base pressure, so command and traffic control matter more.")

    if summary["avg_damage_score"] >= 70:
        caveats.append("Opponent profile has meaningful damage potential; avoid mistakes in hitter-friendly counts.")

    if not caveats:
        caveats.append("No major statistical caveat flagged, but coach scouting and pitcher availability still matter.")

    return caveats


def rank_pitchers_for_opponent(
    pitchers: list[PitcherProfile],
    projected_lineup: list[ProjectedLineupSpot],
) -> list[PitcherMatchupResult]:
    """
    Rank pitchers with a transparent heuristic matchup score.

    Higher matchup_score is better. Lower projected_runs_index is better.
    The index is relative, not a literal run projection.
    """
    if not pitchers:
        return []

    summary = summarize_opponent_lineup(projected_lineup)

    avg_k_rate = float(summary["avg_k_rate"])
    avg_bb_rate = float(summary["avg_bb_rate"])
    avg_obp = float(summary["avg_obp"])
    avg_slg = float(summary["avg_slg"])
    avg_damage_score = float(summary["avg_damage_score"])

    strikeout_weight = 0.22 + (0.10 * _clamp(avg_k_rate / 0.280, 0.0, 1.0))
    command_weight = 0.22 + (0.08 * _clamp(avg_bb_rate / 0.140, 0.0, 1.0))
    traffic_weight = 0.22 + (0.10 * _clamp((avg_obp - 0.320) / 0.140, 0.0, 1.0))
    damage_weight = 0.24 + (0.12 * max(
        _clamp((avg_slg - 0.360) / 0.220, 0.0, 1.0),
        _clamp(avg_damage_score / 100.0, 0.0, 1.0),
    ))
    sample_weight = 0.10

    total_weight = (
        strikeout_weight
        + command_weight
        + traffic_weight
        + damage_weight
        + sample_weight
    )

    strikeout_weight /= total_weight
    command_weight /= total_weight
    traffic_weight /= total_weight
    damage_weight /= total_weight
    sample_weight /= total_weight

    results: list[PitcherMatchupResult] = []

    for pitcher in pitchers:
        strikeout_advantage = _clamp(
            pitcher.bat_missing_score
            * (0.85 + (0.30 * _clamp(avg_k_rate / 0.280, 0.0, 1.0)))
        )

        walk_risk = _clamp(
            100.0
            * (
                (0.70 * _safe_div(pitcher.free_base_rate, 0.170))
                + (0.30 * _safe_div(avg_bb_rate, 0.140))
            )
        )

        damage_risk = _clamp(
            100.0
            * (
                (0.55 * _safe_div(pitcher.damage_rate, 0.130))
                + (0.25 * _safe_div(max(avg_slg - 0.300, 0.0), 0.350))
                + (0.20 * _safe_div(avg_damage_score, 100.0))
            )
        )

        traffic_risk = _clamp(
            100.0
            * (
                (0.45 * _safe_div(pitcher.obp_allowed, 0.440))
                + (0.25 * _safe_div(pitcher.oba, 0.360))
                + (0.20 * _safe_div(pitcher.free_base_rate, 0.170))
                + (0.10 * _safe_div(avg_obp, 0.450))
            )
        )

        traffic_suppression_score = _clamp(
            (0.55 * _inverse_rate_score(pitcher.obp_allowed, excellent=0.230, poor=0.440))
            + (0.30 * _inverse_rate_score(pitcher.oba, excellent=0.170, poor=0.360))
            + (0.15 * pitcher.command_score)
        )

        matchup_score = _clamp(
            (strikeout_weight * strikeout_advantage)
            + (command_weight * pitcher.command_score)
            + (traffic_weight * traffic_suppression_score)
            + (damage_weight * pitcher.damage_suppression_score)
            + (sample_weight * pitcher.sample_size_score)
        )

        projected_runs_index = _clamp(
            100.0
            + (0.30 * traffic_risk)
            + (0.25 * damage_risk)
            + (0.20 * walk_risk)
            - (0.25 * strikeout_advantage)
            - (0.10 * pitcher.sample_size_score),
            40.0,
            180.0,
        )

        sample_confidence = _sample_confidence(pitcher, projected_lineup)
        recommended_role = _recommended_role(matchup_score, sample_confidence)

        explanation = (
            f"{pitcher.name} matchup score {matchup_score:.1f}/100. "
            f"K rate {_format_pct(pitcher.k_rate)}, free-base rate {_format_pct(pitcher.free_base_rate)}, "
            f"OBA allowed {pitcher.oba:.3f}, OBP allowed {pitcher.obp_allowed:.3f}, "
            f"damage rate {_format_pct(pitcher.damage_rate)}. "
            f"Opponent projection: {summary['lineup_strength_label']} "
            f"({summary['avg_ops']:.3f} OPS, {_format_pct(summary['avg_k_rate'])} K rate)."
        )

        results.append(
            PitcherMatchupResult(
                pitcher=pitcher,
                projected_runs_index=projected_runs_index,
                matchup_score=matchup_score,
                strikeout_advantage=strikeout_advantage,
                walk_risk=walk_risk,
                damage_risk=damage_risk,
                traffic_risk=traffic_risk,
                sample_confidence=sample_confidence,
                recommended_role=recommended_role,
                explanation=explanation,
                caveats=_pitcher_caveats(pitcher, summary, sample_confidence),
            )
        )

    return sorted(
        results,
        key=lambda result: (
            -result.matchup_score,
            result.projected_runs_index,
            -result.pitcher.sample_size_score,
            result.pitcher.name,
        ),
    )


def get_pitcher_matchup_assumptions() -> list[str]:
    return [
        "No official opponent batting order was provided.",
        "The lineup is projected from season stats.",
        "Recommendations are statistical matchup estimates, not guarantees.",
        "Coach scouting, pitcher rest, handedness, pitch count limits, and defensive context can change the recommendation.",
    ]


__all__ = [
    "OpponentHitterProfile",
    "PitcherProfile",
    "ProjectedLineupSpot",
    "PitcherMatchupResult",
    "project_opponent_lineup",
    "rank_pitchers_for_opponent",
    "summarize_opponent_lineup",
    "get_pitcher_matchup_assumptions",
]
