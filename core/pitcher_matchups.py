from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


def _is_blank_value(value: Any) -> bool:
    return value is None or str(value).strip() in {"", "-"}


def _normalized_key(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _as_float(value, default=0.0) -> float:
    if _is_blank_value(value):
        return default

    if isinstance(value, (int, float)):
        return float(value)

    cleaned = str(value).strip().replace(",", "")
    is_percent = cleaned.endswith("%")
    if is_percent:
        cleaned = cleaned[:-1].strip()

    if cleaned.startswith("."):
        cleaned = "0" + cleaned

    try:
        parsed = float(cleaned)
    except (TypeError, ValueError):
        return default

    if is_percent:
        return parsed / 100.0

    return parsed


def _as_int(value, default=0) -> int:
    if _is_blank_value(value):
        return default

    try:
        return int(round(_as_float(value, float(default))))
    except (TypeError, ValueError):
        return default


def _first_present(row: dict, keys: list[str], default=None):
    if row is None:
        return default

    for key in keys:
        if isinstance(row, dict) and key in row and not _is_blank_value(row[key]):
            return row[key]

        if not isinstance(row, dict) and hasattr(row, key):
            value = getattr(row, key)
            if not _is_blank_value(value):
                return value

    if isinstance(row, dict):
        normalized_lookup = {
            _normalized_key(str(existing_key)): value
            for existing_key, value in row.items()
        }

        for key in keys:
            value = normalized_lookup.get(_normalized_key(key))
            if not _is_blank_value(value):
                return value

    return default


def _parse_baseball_ip(value) -> float:
    if _is_blank_value(value):
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    cleaned = str(value).strip().replace(",", "")
    if "." not in cleaned:
        return _as_float(cleaned, 0.0)

    whole, frac = cleaned.split(".", 1)
    whole_int = _as_int(whole, 0)

    if frac == "1":
        return whole_int + (1.0 / 3.0)
    if frac == "2":
        return whole_int + (2.0 / 3.0)

    return _as_float(cleaned, 0.0)


def _as_rate(value, default: float = 0.0) -> float:
    if _is_blank_value(value):
        return default

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.endswith("%"):
            return _as_float(cleaned, default)

        if cleaned.startswith("."):
            return _as_float(cleaned, default)

        if cleaned.isdigit() and len(cleaned) in {3, 4}:
            return float(cleaned) / 1000.0

        parsed = _as_float(cleaned, default)
        if parsed > 2.0 and parsed <= 100.0:
            return parsed / 100.0
        return parsed

    return _as_float(value, default)


def _optional_float(value) -> float | None:
    if _is_blank_value(value):
        return None
    return _as_float(value, 0.0)


def _is_totals_name(name: str) -> bool:
    normalized = " ".join(str(name).strip().lower().split())
    return normalized in {
        "season totals",
        "totals",
        "team totals",
        "overall totals",
    }


def _first_name(row: dict) -> str:
    value = _first_present(
        row,
        ["name", "athlete", "athlete_name", "player", "Athlete Name"],
        default="",
    )
    return " ".join(str(value).strip().split())


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


def build_opponent_hitters_from_rows(rows: list[dict]) -> list[OpponentHitterProfile]:
    hitters: list[OpponentHitterProfile] = []

    for row in rows or []:
        name = _first_name(row)
        if not name or _is_totals_name(name) or name.lower() == "n. player":
            continue

        gp = _as_int(_first_present(row, ["GP", "gp", "games_played"], 0))
        pa = _as_int(_first_present(row, ["PA", "pa", "plate_appearances"], 0))
        ab = _as_int(_first_present(row, ["AB", "ab", "at_bats"], 0))
        hits = _as_int(_first_present(row, ["H", "h", "hits"], 0))
        doubles = _as_int(_first_present(row, ["2B", "doubles"], 0))
        triples = _as_int(_first_present(row, ["3B", "triples"], 0))
        hr = _as_int(_first_present(row, ["HR", "hr", "home_runs", "homers"], 0))
        bb = _as_int(_first_present(row, ["BB", "bb", "walks"], 0))
        k = _as_int(_first_present(row, ["K", "k", "SO", "so", "strikeouts"], 0))
        hbp = _as_int(_first_present(row, ["HBP", "hbp", "hit_by_pitch"], 0))
        sb = _as_int(_first_present(row, ["SB", "sb", "stolen_bases"], 0))

        obp_raw = _first_present(row, ["OBP", "obp", "on_base_percentage"])
        slg_raw = _first_present(row, ["SLG", "slg", "slugging_percentage"])
        ops_raw = _first_present(row, ["OPS", "ops"])

        obp = (
            _as_rate(obp_raw)
            if obp_raw is not None
            else _safe_div(hits + bb + hbp, pa)
        )

        total_bases = hits + doubles + (2 * triples) + (3 * hr)
        slg = (
            _as_rate(slg_raw)
            if slg_raw is not None
            else _safe_div(total_bases, ab)
        )

        ops = _as_rate(ops_raw) if ops_raw is not None else obp + slg

        if not any([pa, ab, hits, doubles, triples, hr, bb, k, hbp, sb, obp, slg, ops]):
            continue

        hitters.append(
            OpponentHitterProfile(
                name=name,
                pa=pa,
                ab=ab,
                hits=hits,
                doubles=doubles,
                triples=triples,
                hr=hr,
                bb=bb,
                k=k,
                hbp=hbp,
                obp=obp,
                slg=slg,
                ops=ops,
                sb=sb,
                gp=gp,
            )
        )

    return hitters


def build_pitchers_from_rows(rows: list[dict]) -> list[PitcherProfile]:
    pitchers: list[PitcherProfile] = []

    for row in rows or []:
        name = _first_name(row)
        if not name or _is_totals_name(name) or name.lower() == "n. player":
            continue

        ip = _parse_baseball_ip(
            _first_present(row, ["IP", "ip", "innings_pitched", "Innings Pitched"], 0)
        )
        bf = _as_int(_first_present(row, ["BF", "bf", "batters_faced", "batters faced"], 0))
        ab = _as_int(_first_present(row, ["AB", "ab", "at_bats_against", "at bats against"], 0))
        hits_allowed = _as_int(_first_present(row, ["H", "h", "hits_allowed", "hits allowed"], 0))
        doubles_allowed = _as_int(_first_present(row, ["2B", "doubles_allowed", "doubles allowed"], 0))
        triples_allowed = _as_int(_first_present(row, ["3B", "triples_allowed", "triples allowed"], 0))
        hr_allowed = _as_int(_first_present(row, ["HR", "hr", "homers_allowed", "home_runs_allowed", "homers allowed"], 0))
        bb = _as_int(_first_present(row, ["BB", "bb", "walks"], 0))
        k = _as_int(_first_present(row, ["K", "k", "SO", "so", "strikeouts"], 0))
        hbp = _as_int(_first_present(row, ["HBP", "hbp", "hit_by_pitch"], 0))
        pitches = _as_int(_first_present(row, ["#P", "Pitches", "pitches", "pitch_count"], 0))

        oba = _as_rate(
            _first_present(row, ["OBA", "oba", "opponent_ba", "opponent_batting_average"], 0)
        )
        obp_allowed = _as_rate(
            _first_present(row, ["OBP", "obp", "opponent_obp", "obp_allowed"], 0)
        )

        if not any([ip, bf, pitches, hits_allowed, bb, k, hbp, oba, obp_allowed]):
            continue

        pitchers.append(
            PitcherProfile(
                name=name,
                ip=ip,
                bf=bf,
                ab=ab,
                hits_allowed=hits_allowed,
                doubles_allowed=doubles_allowed,
                triples_allowed=triples_allowed,
                hr_allowed=hr_allowed,
                bb=bb,
                k=k,
                hbp=hbp,
                oba=oba,
                obp_allowed=obp_allowed,
                pitches=pitches,
                era=_optional_float(_first_present(row, ["ERA", "era"])),
                gp=_as_int(_first_present(row, ["GP", "gp", "APP", "app", "appearances"], 0)),
                gs=_as_int(_first_present(row, ["GS", "gs", "games_started"], 0)),
            )
        )

    return pitchers


def build_pitcher_matchup_report(
    opponent_batting_rows: list[dict],
    own_pitching_rows: list[dict],
    lineup_size: int = 9,
    *,
    include_simulation: bool = False,
    simulation_games: int = 5000,
    simulation_innings: int = 7,
    simulation_seed: int | None = 42,
) -> dict:
    hitters = build_opponent_hitters_from_rows(opponent_batting_rows)
    pitchers = build_pitchers_from_rows(own_pitching_rows)
    projected_lineup = project_opponent_lineup(hitters, lineup_size=lineup_size)
    lineup_summary = summarize_opponent_lineup(projected_lineup)
    pitcher_rankings = rank_pitchers_for_opponent(pitchers, projected_lineup)

    report = {
        "hitters": hitters,
        "pitchers": pitchers,
        "projected_lineup": projected_lineup,
        "lineup_summary": lineup_summary,
        "pitcher_rankings": pitcher_rankings,
        "assumptions": get_pitcher_matchup_assumptions(),
    }

    if not include_simulation:
        return report

    from core.pitcher_matchup_simulator import simulate_pitcher_matchup_report

    report["simulation_results"] = simulate_pitcher_matchup_report(
        pitcher_rankings,
        projected_lineup,
        games=simulation_games,
        innings_per_game=simulation_innings,
        seed=simulation_seed,
    )
    report["simulation_settings"] = {
        "games": simulation_games,
        "innings": simulation_innings,
        "seed": simulation_seed,
    }

    return report


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


def _get_report_value(item: Any, key: str, default: Any = None) -> Any:
    if item is None:
        return default

    if isinstance(item, dict):
        return item.get(key, default)

    return getattr(item, key, default)


def summarize_simulation_results(simulation_results: list) -> list[dict]:
    rows: list[dict] = []

    for result in simulation_results or []:
        avg_runs_allowed = _get_report_value(result, "avg_runs_allowed", 0.0)
        adjusted_avg_runs_allowed = getattr(
            result,
            "adjusted_avg_runs_allowed",
            _get_report_value(result, "adjusted_avg_runs_allowed", avg_runs_allowed),
        )
        reliability_label = getattr(
            result,
            "reliability_label",
            _get_report_value(result, "reliability_label", "Unknown"),
        )
        role_caution = getattr(
            result,
            "role_caution",
            _get_report_value(result, "role_caution", ""),
        )

        rows.append(
            {
                "Pitcher": str(_get_report_value(result, "pitcher_name", "Unknown pitcher")),
                "Avg Runs": _as_float(avg_runs_allowed),
                "Adjusted Avg Runs": _as_float(adjusted_avg_runs_allowed),
                "Reliability": str(reliability_label or "Unknown"),
                "Role Caution": str(role_caution or ""),
                "Median Runs": _as_float(_get_report_value(result, "median_runs_allowed", 0.0)),
                "Hold <= 2": _as_float(_get_report_value(result, "pct_hold_to_2_or_less", 0.0)),
                "Hold <= 3": _as_float(_get_report_value(result, "pct_hold_to_3_or_less", 0.0)),
                "Allow 5+": _as_float(_get_report_value(result, "pct_allow_5_plus", 0.0)),
                "Allow 7+": _as_float(_get_report_value(result, "pct_allow_7_plus", 0.0)),
                "Blowup Inning Rate": _as_float(_get_report_value(result, "blowup_inning_rate", 0.0)),
                "Games": _as_int(_get_report_value(result, "games_simulated", 0)),
                "Innings": _as_int(_get_report_value(result, "innings_per_game", 0)),
            }
        )

    return rows


def build_simulation_comparison_rows(
    pitcher_rankings: list[PitcherMatchupResult],
    simulation_results: list,
) -> list[dict]:
    rows: list[dict] = []

    for heuristic_rank, (ranking, result) in enumerate(
        zip(pitcher_rankings or [], simulation_results or []),
        start=1,
    ):
        pitcher = _get_report_value(ranking, "pitcher")
        avg_runs_allowed = _get_report_value(result, "avg_runs_allowed", 0.0)
        adjusted_avg_runs_allowed = _get_report_value(
            result,
            "adjusted_avg_runs_allowed",
            avg_runs_allowed,
        )

        rows.append(
            {
                "Pitcher": str(
                    _get_report_value(
                        pitcher,
                        "name",
                        _get_report_value(result, "pitcher_name", "Unknown pitcher"),
                    )
                ),
                "Heuristic Rank": heuristic_rank,
                "Fit Score": _as_float(_get_report_value(ranking, "matchup_score", 0.0)),
                "Heuristic Run Risk Index": _as_float(
                    _get_report_value(ranking, "projected_runs_index", 0.0)
                ),
                "Raw Avg Runs": _as_float(avg_runs_allowed),
                "Adjusted Avg Runs": _as_float(adjusted_avg_runs_allowed),
                "Median Runs": _as_float(_get_report_value(result, "median_runs_allowed", 0.0)),
                "Hold <= 3": _as_float(_get_report_value(result, "pct_hold_to_3_or_less", 0.0)),
                "Allow 5+": _as_float(_get_report_value(result, "pct_allow_5_plus", 0.0)),
                "Allow 7+": _as_float(_get_report_value(result, "pct_allow_7_plus", 0.0)),
                "Blowup Inning Rate": _as_float(
                    _get_report_value(result, "blowup_inning_rate", 0.0)
                ),
                "Reliability": str(_get_report_value(result, "reliability_label", "Unknown") or "Unknown"),
                "Role Caution": str(_get_report_value(result, "role_caution", "") or ""),
            }
        )

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            _as_float(row.get("Adjusted Avg Runs"), 0.0),
            _as_float(row.get("Raw Avg Runs"), 0.0),
            _as_int(row.get("Heuristic Rank"), 0),
        ),
    )

    return [
        {
            "Sim Rank": sim_rank,
            **row,
        }
        for sim_rank, row in enumerate(sorted_rows, start=1)
    ]


def build_simulation_coach_read(
    pitcher_rankings: list[PitcherMatchupResult],
    simulation_results: list,
) -> dict:
    combined_rows = _build_simulation_coach_rows(pitcher_rankings, simulation_results)

    if not combined_rows:
        return {
            "best_established_option": None,
            "best_simulated_run_prevention": None,
            "high_variance_options": [],
            "emergency_depth_cautions": [],
            "summary": "No simulation recommendation is available yet.",
        }

    established_candidates: list[dict] = []
    for reliability in ("High", "Medium"):
        established_candidates = [
            row for row in combined_rows
            if str(row.get("Reliability", "")).strip() == reliability
        ]
        if established_candidates:
            break

    if not established_candidates:
        established_candidates = combined_rows

    best_established = min(
        established_candidates,
        key=lambda row: (
            _as_float(row.get("Adjusted Avg Runs"), 0.0),
            _as_float(row.get("Raw Avg Runs"), 0.0),
            _as_int(row.get("Heuristic Rank"), 0),
        ),
    )
    best_simulated = combined_rows[0]

    high_variance_options = _build_high_variance_simulation_options(combined_rows)
    emergency_depth_cautions = _build_emergency_depth_simulation_cautions(combined_rows)

    best_established_name = str(best_established.get("Pitcher", "Unknown pitcher"))
    best_simulated_name = str(best_simulated.get("Pitcher", "Unknown pitcher"))

    if best_established_name == best_simulated_name:
        summary = (
            f"{best_established_name} is both the best established option and "
            "the best adjusted simulation option."
        )
    else:
        summary = (
            f"{best_established_name} is the safest established read, while "
            f"{best_simulated_name} has the best adjusted simulation result."
        )

    if high_variance_options:
        summary += " At least one arm shows simulation upside but carries added sample/role risk."

    return {
        "best_established_option": _compact_simulation_coach_option(
            best_established,
            explanation=_established_option_explanation(best_established),
        ),
        "best_simulated_run_prevention": _compact_simulation_coach_option(
            best_simulated,
            explanation=(
                "Lowest adjusted simulation runs allowed after accounting for "
                "sample and risk caution."
            ),
        ),
        "high_variance_options": high_variance_options,
        "emergency_depth_cautions": emergency_depth_cautions,
        "summary": summary,
    }


def _build_simulation_coach_rows(
    pitcher_rankings: list[PitcherMatchupResult],
    simulation_results: list,
) -> list[dict]:
    rows: list[dict] = []

    for heuristic_rank, (ranking, result) in enumerate(
        zip(pitcher_rankings or [], simulation_results or []),
        start=1,
    ):
        pitcher = _get_report_value(ranking, "pitcher")
        raw_avg_runs = _get_report_value(result, "avg_runs_allowed", 0.0)
        adjusted_avg_runs = getattr(
            result,
            "adjusted_avg_runs_allowed",
            _get_report_value(result, "adjusted_avg_runs_allowed", raw_avg_runs),
        )
        reliability = getattr(
            result,
            "reliability_label",
            _get_report_value(result, "reliability_label", "Unknown"),
        )
        role_caution = getattr(
            result,
            "role_caution",
            _get_report_value(result, "role_caution", ""),
        )

        rows.append(
            {
                "Pitcher": str(
                    _get_report_value(
                        pitcher,
                        "name",
                        _get_report_value(result, "pitcher_name", "Unknown pitcher"),
                    )
                ),
                "Heuristic Rank": heuristic_rank,
                "Fit Score": _as_float(_get_report_value(ranking, "matchup_score", 0.0)),
                "Heuristic Run Risk Index": _as_float(
                    _get_report_value(ranking, "projected_runs_index", 0.0)
                ),
                "Raw Avg Runs": _as_float(raw_avg_runs),
                "Adjusted Avg Runs": _as_float(adjusted_avg_runs),
                "Median Runs": _as_float(_get_report_value(result, "median_runs_allowed", 0.0)),
                "Hold <= 3": _as_float(_get_report_value(result, "pct_hold_to_3_or_less", 0.0)),
                "Allow 5+": _as_float(_get_report_value(result, "pct_allow_5_plus", 0.0)),
                "Allow 7+": _as_float(_get_report_value(result, "pct_allow_7_plus", 0.0)),
                "Blowup Inning Rate": _as_float(
                    _get_report_value(result, "blowup_inning_rate", 0.0)
                ),
                "Reliability": str(reliability or "Unknown"),
                "Role Caution": str(role_caution or ""),
                "_k_rate": _as_float(_get_report_value(pitcher, "k_rate", 0.0)),
                "_free_base_rate": _as_float(_get_report_value(pitcher, "free_base_rate", 0.0)),
            }
        )

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            _as_float(row.get("Adjusted Avg Runs"), 0.0),
            _as_float(row.get("Raw Avg Runs"), 0.0),
            _as_int(row.get("Heuristic Rank"), 0),
        ),
    )

    for sim_rank, row in enumerate(sorted_rows, start=1):
        row["Sim Rank"] = sim_rank

    return sorted_rows


def _compact_simulation_coach_option(row: dict, *, explanation: str) -> dict:
    return {
        "pitcher": str(row.get("Pitcher", "Unknown pitcher")),
        "heuristic_rank": _as_int(row.get("Heuristic Rank", 0)),
        "sim_rank": _as_int(row.get("Sim Rank", 0)),
        "fit_score": round(_as_float(row.get("Fit Score", 0.0)), 1),
        "adjusted_avg_runs": round(_as_float(row.get("Adjusted Avg Runs", 0.0)), 2),
        "raw_avg_runs": round(_as_float(row.get("Raw Avg Runs", 0.0)), 2),
        "reliability": str(row.get("Reliability", "Unknown") or "Unknown"),
        "role_caution": str(row.get("Role Caution", "") or ""),
        "explanation": explanation,
    }


def _established_option_explanation(row: dict) -> str:
    reliability = str(row.get("Reliability", "Unknown") or "Unknown")

    if reliability in {"High", "Medium"}:
        return (
            f"Best adjusted run-prevention result among {reliability.lower()}-reliability "
            "pitching samples."
        )

    return (
        "No high- or medium-reliability option was available; this is the best "
        "adjusted simulation result among available arms."
    )


def _build_high_variance_simulation_options(rows: list[dict]) -> list[dict]:
    options: list[dict] = []

    for row in rows:
        raw_avg_runs = _as_float(row.get("Raw Avg Runs", 0.0))
        adjusted_avg_runs = _as_float(row.get("Adjusted Avg Runs", raw_avg_runs))
        reliability = str(row.get("Reliability", "Unknown") or "Unknown")
        adjustment_gap = adjusted_avg_runs - raw_avg_runs

        has_sample_role_volatility = reliability != "High" and adjustment_gap >= 0.75
        has_stuff_command_volatility = (
            _as_float(row.get("_k_rate", 0.0)) >= 0.200
            and _as_float(row.get("_free_base_rate", 0.0)) >= 0.180
        )

        if not (has_sample_role_volatility or has_stuff_command_volatility):
            continue

        if has_sample_role_volatility and has_stuff_command_volatility:
            explanation = "Raw simulation shows upside, but limited sample and free-base risk create volatility."
        elif has_sample_role_volatility:
            explanation = "Raw simulation shows upside, but limited mound sample or role history adds caution."
        else:
            explanation = "Strikeout upside is present, but free-base risk creates volatility."

        options.append(
            {
                "pitcher": str(row.get("Pitcher", "Unknown pitcher")),
                "raw_avg_runs": round(raw_avg_runs, 2),
                "adjusted_avg_runs": round(adjusted_avg_runs, 2),
                "reliability": reliability,
                "role_caution": str(row.get("Role Caution", "") or ""),
                "explanation": explanation,
            }
        )

    return options


def _build_emergency_depth_simulation_cautions(rows: list[dict]) -> list[dict]:
    cautions: list[dict] = []

    for row in rows:
        adjusted_avg_runs = _as_float(row.get("Adjusted Avg Runs", 0.0))
        reliability = str(row.get("Reliability", "Unknown") or "Unknown")
        role_caution = str(row.get("Role Caution", "") or "")

        if "emergency" not in role_caution.lower() and not (
            reliability == "Low" and adjusted_avg_runs >= 6.50
        ):
            continue

        cautions.append(
            {
                "pitcher": str(row.get("Pitcher", "Unknown pitcher")),
                "adjusted_avg_runs": adjusted_avg_runs,
                "reliability": reliability,
                "role_caution": role_caution,
                "explanation": (
                    "Treat as depth context rather than a clean statistical recommendation; "
                    "sample size or role history adds caution."
                ),
            }
        )

    return cautions


def _format_rate_decimal(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "—"


def _format_rate_percent(value: Any) -> str:
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "—"


def _format_report_score(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _run_risk_label(projected_runs_index: float) -> str:
    try:
        risk_index = float(projected_runs_index)
    except (TypeError, ValueError):
        return "Unknown"

    if risk_index <= 85:
        return "Low"
    if risk_index <= 100:
        return "Below average"
    if risk_index <= 115:
        return "Moderate"
    if risk_index <= 130:
        return "High"
    return "Very high"


def _format_ip(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _pitcher_rank_label(rank: int) -> str:
    if rank == 1:
        return "Top option in current data"
    if rank in {2, 3}:
        return "Secondary option"
    return "Depth / emergency option"


def _matchup_grade(result: PitcherMatchupResult) -> str:
    pitcher = _get_report_value(result, "pitcher")
    score = float(_get_report_value(result, "matchup_score", 0.0) or 0.0)
    confidence = str(_get_report_value(result, "sample_confidence", "Unknown"))
    k_rate = float(_get_report_value(pitcher, "k_rate", 0.0) or 0.0)
    free_base_rate = float(_get_report_value(pitcher, "free_base_rate", 0.0) or 0.0)

    if k_rate >= 0.200 and free_base_rate >= 0.180:
        return "High-variance option: strikeout upside with elevated free-base risk"

    if score >= 76 and confidence != "Low":
        return "Clean statistical matchup"
    if score >= 66:
        return "Strong statistical option"
    if score >= 55:
        return "Usable matchup with caveats"
    if score >= 40 and free_base_rate >= 0.180:
        return "High-variance option"
    if score >= 40:
        return "Difficult matchup"
    return "Difficult matchup / not a clean look"


def format_pitcher_matchup_report(report: dict, max_pitchers: int = 5) -> str:
    """
    Format a pitching matchup report as plain coach-facing text.

    This is intentionally pure Python so matchup-report copy can be reviewed
    before adding any Streamlit UI.
    """
    report = report if isinstance(report, dict) else {}

    try:
        pitcher_limit = max(0, int(max_pitchers))
    except (TypeError, ValueError):
        pitcher_limit = 5

    summary = report.get("lineup_summary") or {}
    projected_lineup = list(report.get("projected_lineup") or [])
    rankings = list(report.get("pitcher_rankings") or [])
    assumptions = list(report.get("assumptions") or [])

    lines: list[str] = [
        "Pitching Matchup Report",
        "",
        "Opponent Lineup Summary",
    ]

    if summary:
        strength = str(_get_report_value(summary, "lineup_strength_label", "Unknown"))
        explanation = _one_line(_get_report_value(summary, "explanation", ""))

        lines.extend(
            [
                f"This projected lineup profiles as a {strength}.",
                "The opponent order below is a stats-based projection. If you have the official lineup, treat this as a starting point until lineup import is supported.",
                (
                    "Projected averages: "
                    f"OBP {_format_rate_decimal(_get_report_value(summary, 'avg_obp', 0.0))}, "
                    f"SLG {_format_rate_decimal(_get_report_value(summary, 'avg_slg', 0.0))}, "
                    f"OPS {_format_rate_decimal(_get_report_value(summary, 'avg_ops', 0.0))}, "
                    f"K rate {_format_rate_percent(_get_report_value(summary, 'avg_k_rate', 0.0))}, "
                    f"BB rate {_format_rate_percent(_get_report_value(summary, 'avg_bb_rate', 0.0))}."
                ),
            ]
        )

        if explanation:
            lines.append(explanation)
    else:
        lines.append("No opponent lineup summary is available yet.")

    lines.extend(["", "Assumed Opponent Lineup"])

    if projected_lineup:
        for idx, spot in enumerate(projected_lineup, start=1):
            hitter = _get_report_value(spot, "hitter")
            spot_number = _get_report_value(spot, "spot", idx)
            role = str(_get_report_value(spot, "role", "Projected hitter"))
            explanation = _one_line(_get_report_value(spot, "explanation", ""))

            lines.append(
                f"{spot_number}. {str(_get_report_value(hitter, 'name', 'Unknown hitter'))} — {role}"
            )
            lines.append(
                "   "
                f"OBP {_format_rate_decimal(_get_report_value(hitter, 'obp', 0.0))} / "
                f"SLG {_format_rate_decimal(_get_report_value(hitter, 'slg', 0.0))} / "
                f"OPS {_format_rate_decimal(_get_report_value(hitter, 'ops', 0.0))} / "
                f"K% {_format_rate_percent(_get_report_value(hitter, 'k_rate', 0.0))} / "
                f"BB% {_format_rate_percent(_get_report_value(hitter, 'bb_rate', 0.0))}"
            )
            if explanation:
                lines.append(f"   {explanation}")
    else:
        lines.append("No projected opponent lineup is available yet.")

    lines.extend(
        [
            "",
            "Recommended Pitcher Ranking",
            "",
            "How to read this:",
            "- Fit score: 0–100. Higher is better.",
            "- Run risk: Lower is better. It summarizes expected traffic, free bases, and damage risk.",
            "- Data confidence: How much usable stat sample supports the read. High confidence does not mean a good matchup.",
        ]
    )

    ranked_subset = rankings[:pitcher_limit] if pitcher_limit else []

    if ranked_subset:
        for idx, result in enumerate(ranked_subset, start=1):
            pitcher = _get_report_value(result, "pitcher")
            caveats = list(_get_report_value(result, "caveats", []) or [])
            explanation = _one_line(_get_report_value(result, "explanation", ""))

            lines.append(
                f"{idx}. {str(_get_report_value(pitcher, 'name', 'Unknown pitcher'))} — "
                f"{_pitcher_rank_label(idx)}"
            )
            lines.append(f"   Matchup grade: {_matchup_grade(result)}")
            lines.append(
                f"   Fit score: {_format_report_score(_get_report_value(result, 'matchup_score', 0.0))} / 100"
            )
            lines.append(
                f"   Run risk: {_run_risk_label(float(_get_report_value(result, 'projected_runs_index', 100.0) or 100.0))}"
            )
            lines.append(
                f"   Data confidence: {str(_get_report_value(result, 'sample_confidence', 'Unknown'))}"
            )
            lines.append(
                "   "
                f"Key line: IP {_format_ip(_get_report_value(pitcher, 'ip', 0.0))}, "
                f"BF {int(_get_report_value(pitcher, 'bf', 0) or 0)}, "
                f"K% {_format_rate_percent(_get_report_value(pitcher, 'k_rate', 0.0))}, "
                f"BB% {_format_rate_percent(_get_report_value(pitcher, 'bb_rate', 0.0))}, "
                f"free-base% {_format_rate_percent(_get_report_value(pitcher, 'free_base_rate', 0.0))}, "
                f"OBA {_format_rate_decimal(_get_report_value(pitcher, 'oba', 0.0))}, "
                f"OBP allowed {_format_rate_decimal(_get_report_value(pitcher, 'obp_allowed', 0.0))}, "
                f"damage rate {_format_rate_percent(_get_report_value(pitcher, 'damage_rate', 0.0))}."
            )
            if explanation:
                lines.append(f"   Read: {explanation}")
            if caveats:
                lines.append("   Caveats:")
                for caveat in caveats:
                    lines.append(f"   - {_one_line(caveat)}")
            lines.append("")
    else:
        lines.append("No pitcher rankings are available yet.")

    lines.extend(["Best Read / Coach Takeaway"])

    if rankings:
        top = rankings[0]
        top_pitcher = _get_report_value(top, "pitcher")
        top_name = str(_get_report_value(top_pitcher, "name", "Unknown pitcher"))
        top_score = float(_get_report_value(top, "matchup_score", 0.0) or 0.0)

        lines.append(
            f"{top_name} is the best statistical matchup in the current data "
            f"with a matchup score of {top_score:.1f}/100."
        )

        if top_score < 55:
            lines.append("This is the best option in the current data, not a clean matchup.")
        else:
            lines.append("This grades as a usable matchup, subject to coach scouting and availability.")

        top_group = rankings[: min(3, len(rankings))]
        if len(top_group) >= 2:
            group_scores = [
                float(_get_report_value(item, "matchup_score", 0.0) or 0.0)
                for item in top_group
            ]
            score_gap = max(group_scores) - min(group_scores)

            if score_gap <= 5.0:
                lines.append(
                    f"The top {len(top_group)} options are tightly grouped; "
                    f"only {score_gap:.1f} matchup-score points separate them."
                )

        has_high_variance_arm = False
        for result in rankings[:5]:
            pitcher = _get_report_value(result, "pitcher")
            k_rate = float(_get_report_value(pitcher, "k_rate", 0.0) or 0.0)
            free_base_rate = float(_get_report_value(pitcher, "free_base_rate", 0.0) or 0.0)
            if k_rate >= 0.200 and free_base_rate >= 0.180:
                has_high_variance_arm = True
                break

        if has_high_variance_arm:
            lines.append(
                "There is at least one high-variance arm in the mix: "
                "strikeout upside, but walks/HBP can create blow-up innings."
            )
    else:
        lines.append("No pitcher ranking is available, so there is no matchup recommendation yet.")

    lines.extend(["", "Assumptions / What Could Change This"])

    if assumptions:
        for assumption in assumptions:
            lines.append(f"- {_one_line(assumption)}")
    else:
        lines.append("- No assumptions were provided with this report.")

    return "\n".join(lines).rstrip() + "\n"


def get_pitcher_matchup_assumptions() -> list[str]:
    return [
        "The model projects a likely opponent lineup from season stats.",
        "Defensive context matters. If the best statistical pitching matchup is also your shortstop, catcher, or best defender, the staff choice may change.",
        "Coach scouting, pitcher rest, handedness, pitch count limits, can change the recommendation.",
        "Recommendations are statistical matchup estimates, not guarantees.",
    ]


__all__ = [
    "OpponentHitterProfile",
    "PitcherProfile",
    "ProjectedLineupSpot",
    "PitcherMatchupResult",
    "build_opponent_hitters_from_rows",
    "build_pitchers_from_rows",
    "build_pitcher_matchup_report",
    "format_pitcher_matchup_report",
    "project_opponent_lineup",
    "rank_pitchers_for_opponent",
    "summarize_opponent_lineup",
    "summarize_simulation_results",
    "build_simulation_comparison_rows",
    "build_simulation_coach_read",
    "get_pitcher_matchup_assumptions",
]
