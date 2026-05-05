from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.maxpreps_pdf_parser import MaxPrepsOpponentReport, MaxPrepsPitchingRow
from core.models import OpponentLevel


# Conservative HS baselines.
# These are model calibration anchors, not claims about a specific league.
HS_BASELINE_K_RATE = 0.22
HS_BASELINE_BB_RATE = 0.09
HS_BASELINE_HITS_PER_BF = 0.24
HS_BASELINE_XBH_PER_BF = 0.06
HS_BASELINE_OBP_ALLOWED = 0.330


def _blend_baseline(global_anchor: float, team_rate: float | None, *, team_weight: float = 0.35) -> float:
    """
    Blend a generic HS anchor with the opponent team's actual staff environment.

    This prevents every pitcher on a strong staff from looking extreme versus
    a too-soft generic baseline, while still preserving true ace/weak-arm separation.
    """
    if team_rate is None or team_rate <= 0:
        return global_anchor

    return ((1.0 - team_weight) * global_anchor) + (team_weight * float(team_rate))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _safe_rate(numerator: int | float | None, denominator: int | float | None) -> float:
    if numerator is None or denominator is None:
        return 0.0
    denominator = float(denominator)
    if denominator <= 0:
        return 0.0
    return float(numerator) / denominator


def _sample_weight(*, batters_faced: int, innings_pitched: float) -> float:
    """
    Shrink small-sample pitcher effects toward neutral.

    5 IP / 23 BF should not move the model nearly as much as
    40 IP / 140 BF.
    """
    bf_weight = _clamp(float(batters_faced) / 100.0, 0.0, 1.0)
    ip_weight = _clamp(float(innings_pitched) / 25.0, 0.0, 1.0)
    return max(bf_weight, ip_weight)


def _shrink_multiplier_to_neutral(multiplier: float, *, weight: float) -> float:
    return round(1.0 + ((float(multiplier) - 1.0) * float(weight)), 3)


@dataclass(slots=True)
class OpponentPitcherProfile:
    """
    Numeric profile for one specific opposing pitcher.

    Important:
    - Raw rates preserve what the opposing pitcher actually did.
    - Multipliers are capped so the simulation stays stable.
    - label is coach-facing only; it should not drive the model.
    """

    name: str
    grade: str | None = None

    innings_pitched: float = 0.0
    walks: int = 0
    strikeouts: int = 0
    batters_faced: int = 0
    hits_allowed: int = 0
    earned_runs: int = 0
    runs_allowed: int = 0

    k_rate: float = 0.0
    bb_rate: float = 0.0
    k_per_ip: float = 0.0
    bb_per_ip: float = 0.0
    hits_per_bf: float = 0.0

    strikeout_multiplier: float = 1.0
    walk_multiplier: float = 1.0
    contact_multiplier: float = 1.0
    power_multiplier: float = 1.0

    label: str = "Balanced pitcher"
    confidence: str = "Low"
    coach_summary: str = ""
    scouting_note: str = ""

    source_row: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "grade": self.grade,
            "innings_pitched": self.innings_pitched,
            "walks": self.walks,
            "strikeouts": self.strikeouts,
            "batters_faced": self.batters_faced,
            "hits_allowed": self.hits_allowed,
            "earned_runs": self.earned_runs,
            "runs_allowed": self.runs_allowed,
            "k_rate": self.k_rate,
            "bb_rate": self.bb_rate,
            "k_per_ip": self.k_per_ip,
            "bb_per_ip": self.bb_per_ip,
            "hits_per_bf": self.hits_per_bf,
            "strikeout_multiplier": self.strikeout_multiplier,
            "walk_multiplier": self.walk_multiplier,
            "contact_multiplier": self.contact_multiplier,
            "power_multiplier": self.power_multiplier,
            "label": self.label,
            "confidence": self.confidence,
            "coach_summary": self.coach_summary,
            "source_row": dict(self.source_row),
            "scouting_note": self.scouting_note,
        }


@dataclass(slots=True)
class OpponentTeamProfile:
    """
    Numeric opponent profile derived from a MaxPreps opponent report.

    Defense is intentionally generic because the current simulator only has
    team-level opponent environment knobs.
    """

    team_name: str | None
    season: str | None = None
    overall_record: str | None = None

    fielding_pct: float | None = None
    fielding_total_chances: int | None = None
    fielding_errors: int | None = None
    derived_opponent_level: OpponentLevel = OpponentLevel.AVERAGE

    team_era: float | None = None
    team_ip: float | None = None
    team_walks: int | None = None
    team_strikeouts: int | None = None
    team_batters_faced: int | None = None
    team_k_rate: float = 0.0
    team_bb_rate: float = 0.0

    pitchers: list[OpponentPitcherProfile] = field(default_factory=list)
    parser_warnings: list[str] = field(default_factory=list)
    parser_stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "season": self.season,
            "overall_record": self.overall_record,
            "fielding_pct": self.fielding_pct,
            "fielding_total_chances": self.fielding_total_chances,
            "fielding_errors": self.fielding_errors,
            "derived_opponent_level": self.derived_opponent_level.value,
            "team_era": self.team_era,
            "team_ip": self.team_ip,
            "team_walks": self.team_walks,
            "team_strikeouts": self.team_strikeouts,
            "team_batters_faced": self.team_batters_faced,
            "team_k_rate": self.team_k_rate,
            "team_bb_rate": self.team_bb_rate,
            "parser_warnings": list(self.parser_warnings),
            "parser_stats": dict(self.parser_stats),
            "pitchers": [p.as_dict() for p in self.pitchers],
        }


def build_opponent_team_profile(
    report: MaxPrepsOpponentReport,
) -> OpponentTeamProfile:
    """
    Convert a parsed MaxPreps report into model-ready numeric opponent profiles.

    No persistence.
    No UI.
    No simulator mutation.
    """

    team_bf = int(report.team_batters_faced or 0)
    team_hits_allowed = sum(int(row.hits_allowed or 0) for row in report.pitchers)
    team_xbh_allowed = sum(
        int(row.doubles_allowed or 0)
        + int(row.triples_allowed or 0)
        + int(row.homers_allowed or 0)
        for row in report.pitchers
    )

    team_k_rate = _safe_rate(report.team_strikeouts, team_bf)
    team_bb_rate = _safe_rate(report.team_walks, team_bf)
    team_hits_per_bf = _safe_rate(team_hits_allowed, team_bf)
    team_xbh_per_bf = _safe_rate(team_xbh_allowed, team_bf)

    team_profile = OpponentTeamProfile(
        team_name=report.team_name,
        season=report.season,
        overall_record=report.overall_record,
        fielding_pct=report.fielding_pct,
        fielding_total_chances=report.fielding_total_chances,
        fielding_errors=report.fielding_errors,
        derived_opponent_level=derive_opponent_level_from_fielding(report.fielding_pct),
        team_era=report.team_era,
        team_ip=report.team_ip,
        team_walks=report.team_walks,
        team_strikeouts=report.team_strikeouts,
        team_batters_faced=report.team_batters_faced,
        team_k_rate=team_k_rate,
        team_bb_rate=team_bb_rate,
        parser_warnings=list(getattr(report, "parser_warnings", []) or []),
        parser_stats=dict(getattr(report, "parser_stats", {}) or {}),
        pitchers=[
            build_pitcher_profile(
                row,
                team_k_rate=team_k_rate,
                team_bb_rate=team_bb_rate,
                team_hits_per_bf=team_hits_per_bf,
                team_xbh_per_bf=team_xbh_per_bf,
            )
            for row in report.pitchers
            if should_include_pitcher(row)
        ],
    )

    team_profile.pitchers.sort(
        key=lambda p: (
            -p.innings_pitched,
            -p.batters_faced,
            p.name,
        )
    )

    return team_profile


def build_pitcher_profile(
    row: MaxPrepsPitchingRow,
    *,
    team_k_rate: float | None = None,
    team_bb_rate: float | None = None,
    team_hits_per_bf: float | None = None,
    team_xbh_per_bf: float | None = None,
) -> OpponentPitcherProfile:

    ip = float(row.innings_pitched or 0.0)
    bf = int(row.batters_faced or 0)
    walks = int(row.walks or 0)
    strikeouts = int(row.strikeouts or 0)
    hits = int(row.hits_allowed or 0)
    earned_runs = int(row.earned_runs or 0)
    runs_allowed = int(row.runs_allowed or 0)

    k_rate = _safe_rate(strikeouts, bf)
    bb_rate = _safe_rate(walks, bf)
    k_per_ip = _safe_rate(strikeouts, ip)
    bb_per_ip = _safe_rate(walks, ip)
    hits_per_bf = _safe_rate(hits, bf)

    sample_weight = _sample_weight(
        batters_faced=bf,
        innings_pitched=ip,
    )

    strikeout_multiplier = _shrink_multiplier_to_neutral(
        derive_strikeout_multiplier(
            k_rate,
            baseline_k_rate=_blend_baseline(HS_BASELINE_K_RATE, team_k_rate),
        ),
        weight=sample_weight,
    )

    walk_multiplier = _shrink_multiplier_to_neutral(
        derive_walk_multiplier(
            bb_rate,
            baseline_bb_rate=_blend_baseline(HS_BASELINE_BB_RATE, team_bb_rate),
        ),
        weight=sample_weight,
    )

    contact_multiplier = _shrink_multiplier_to_neutral(
        derive_contact_multiplier(
            k_rate=k_rate,
            hits_per_bf=hits_per_bf,
            baseline_k_rate=_blend_baseline(HS_BASELINE_K_RATE, team_k_rate),
            baseline_hits_per_bf=_blend_baseline(HS_BASELINE_HITS_PER_BF, team_hits_per_bf),
        ),
        weight=sample_weight,
    )

    power_multiplier = _shrink_multiplier_to_neutral(
        derive_power_multiplier(
            row,
            baseline_xbh_per_bf=_blend_baseline(HS_BASELINE_XBH_PER_BF, team_xbh_per_bf),
        ),
        weight=sample_weight,
    )

    label = describe_pitcher(
        k_rate=k_rate,
        bb_rate=bb_rate,
        hits_per_bf=hits_per_bf,
    )

    confidence = pitcher_confidence(
        innings_pitched=ip,
        batters_faced=bf,
    )

    profile = OpponentPitcherProfile(
        name=row.name,
        grade=row.grade,
        innings_pitched=ip,
        walks=walks,
        strikeouts=strikeouts,
        batters_faced=bf,
        hits_allowed=hits,
        earned_runs=earned_runs,
        runs_allowed=runs_allowed,
        k_rate=k_rate,
        bb_rate=bb_rate,
        k_per_ip=k_per_ip,
        bb_per_ip=bb_per_ip,
        hits_per_bf=hits_per_bf,
        strikeout_multiplier=strikeout_multiplier,
        walk_multiplier=walk_multiplier,
        contact_multiplier=contact_multiplier,
        power_multiplier=power_multiplier,
        label=label,
        confidence=confidence,
        source_row=_pitching_row_to_dict(row),
    )

    profile.coach_summary = build_pitcher_coach_summary(profile)
    profile.scouting_note = build_pitcher_scouting_note(profile)
    return profile


def should_include_pitcher(row: MaxPrepsPitchingRow) -> bool:
    """
    Keep pitchers with enough data to be meaningful.
    This can be loosened later for emergency/relief scouting.
    """
    return int(row.batters_faced or 0) >= 10 and float(row.innings_pitched or 0.0) > 0


def derive_strikeout_multiplier(
    k_rate: float,
    *,
    baseline_k_rate: float = HS_BASELINE_K_RATE,
) -> float:
    """
    Convert pitcher K% into a model multiplier.

    Dampened around 1.0 so average-ish arms stay closer to neutral.
    """
    if baseline_k_rate <= 0:
        return 1.0

    raw = k_rate / baseline_k_rate
    damped = 1.0 + (0.85 * (raw - 1.0))
    return round(_clamp(damped, 0.72, 1.85), 3)


def derive_walk_multiplier(
    bb_rate: float,
    *,
    baseline_bb_rate: float = HS_BASELINE_BB_RATE,
) -> float:
    """
    Convert pitcher BB% into a walk environment.

    Low-walk pitchers should suppress walks, but not crush run scoring by
    themselves. Wild pitchers should create real offensive upside.
    """
    if baseline_bb_rate <= 0:
        return 1.0

    raw = bb_rate / baseline_bb_rate

    if raw >= 1.0:
        damped = 1.0 + (0.90 * (raw - 1.0))
        return round(_clamp(damped, 1.0, 1.85), 3)

    damped = 1.0 + (0.65 * (raw - 1.0))
    return round(_clamp(damped, 0.65, 1.0), 3)


def derive_contact_multiplier(
    *,
    k_rate: float,
    hits_per_bf: float,
    baseline_k_rate: float = HS_BASELINE_K_RATE,
    baseline_hits_per_bf: float = HS_BASELINE_HITS_PER_BF,
) -> float:
    """
    Contact is affected by strikeout pressure and hit suppression, but this
    should be centered tightly around neutral for average pitchers.
    """
    baseline_k_rate = max(float(baseline_k_rate), 0.001)
    baseline_hits_per_bf = max(float(baseline_hits_per_bf), 0.001)

    k_pressure = (k_rate - baseline_k_rate) / baseline_k_rate
    hit_pressure = (baseline_hits_per_bf - hits_per_bf) / baseline_hits_per_bf

    raw = 1.0 - (0.18 * k_pressure) - (0.08 * hit_pressure)
    return round(_clamp(raw, 0.78, 1.16), 3)


def derive_power_multiplier(
    row: MaxPrepsPitchingRow,
    *,
    baseline_xbh_per_bf: float = HS_BASELINE_XBH_PER_BF,
) -> float:
    """
    Use extra-base hits allowed as a light power environment modifier.
    """
    bf = int(row.batters_faced or 0)
    xbh_allowed = (
        int(row.doubles_allowed or 0)
        + int(row.triples_allowed or 0)
        + int(row.homers_allowed or 0)
    )

    if bf <= 0:
        return 1.0

    # Zero XBH allowed in a small sample is not enough evidence to say
    # the pitcher suppresses damage. Keep it neutral.
    if xbh_allowed == 0 and bf < 60:
        return 1.0

    xbh_per_bf = xbh_allowed / bf
    baseline_xbh_per_bf = max(float(baseline_xbh_per_bf), 0.001)

    raw = xbh_per_bf / baseline_xbh_per_bf
    damped = 1.0 + (0.45 * (raw - 1.0))
    return round(_clamp(damped, 0.82, 1.22), 3)


def derive_opponent_level_from_fielding(fielding_pct: float | None) -> OpponentLevel:
    """
    Generic defense only.

    Current model does not know individual defenders, so fielding feeds the
    existing team-level opponent level.
    """
    if fielding_pct is None:
        return OpponentLevel.AVERAGE

    if fielding_pct >= 0.960:
        return OpponentLevel.STRONG

    if fielding_pct <= 0.920:
        return OpponentLevel.WEAK

    return OpponentLevel.AVERAGE


def pitcher_confidence(
    *,
    innings_pitched: float,
    batters_faced: int,
) -> str:
    if batters_faced >= 100 or innings_pitched >= 25:
        return "High"
    if batters_faced >= 40 or innings_pitched >= 10:
        return "Medium"
    return "Low"


def build_pitcher_scouting_note(profile: OpponentPitcherProfile) -> str:
    label = profile.label

    if label == "Elite power arm — fills it up":
        return (
            "Premium swing-and-miss profile with good control. "
            "Expect fewer balls in play and fewer free passes. "
            "Prioritize your best contact bats and avoid stacking too many high-K hitters."
        )

    if label == "Power arm — but wild":
        return (
            "Can miss bats, but will also give away baserunners. "
            "Patient hitters and low-chase bats gain value. "
            "Make him throw strikes before expanding the zone."
        )

    if label == "Power arm":
        return (
            "Above-average strikeout pressure. "
            "Contact skills matter more than usual. "
            "Avoid giving away at-bats with chase-heavy hitters."
        )

    if label == "Fills the zone — hard to square up":
        return (
            "Around the plate and limits hard contact. "
            "Hitters may need to be ready earlier in the count. "
            "Stringing together quality contact matters more than waiting for walks."
        )

    if label == "Wild — will put guys on":
        return (
            "Control is the weakness. "
            "Patient hitters can create traffic without swinging. "
            "Do not help him by chasing early."
        )

    if label == "Throws strikes, not overpowering":
        return (
            "Likely to be around the plate. "
            "Hitters should be ready to attack good pitches early. "
            "Walks may be harder to come by."
        )

    return (
        "Fairly neutral profile. "
        "Use the normal optimized lineup unless you have coach-specific scouting context."
    )


def describe_pitcher(
    *,
    k_rate: float,
    bb_rate: float,
    hits_per_bf: float,
) -> str:
    high_k = k_rate >= 0.28
    extreme_k = k_rate >= 0.42
    low_walk = bb_rate <= 0.07
    high_walk = bb_rate >= 0.12
    low_hits = hits_per_bf <= 0.16

    if extreme_k and low_walk:
        return "Elite power arm — fills it up"

    if high_k and high_walk:
        return "Power arm — but wild"

    if high_k:
        return "Power arm"

    if low_walk and low_hits:
        return "Fills the zone — hard to square up"

    if high_walk:
        return "Wild — will put guys on"

    if low_walk:
        return "Throws strikes, not overpowering"

    return "Average arm"


def build_pitcher_coach_summary(profile: OpponentPitcherProfile) -> str:
    return (
        f"{profile.name}: {profile.label}. "
        f"K rate {profile.k_rate:.1%} "
        f"({profile.strikeouts} K / {profile.batters_faced} BF), "
        f"walk rate {profile.bb_rate:.1%} "
        f"({profile.walks} BB / {profile.batters_faced} BF). "
        f"Model multipliers: strikeouts x{profile.strikeout_multiplier:.2f}, "
        f"walks x{profile.walk_multiplier:.2f}, "
        f"contact x{profile.contact_multiplier:.2f}."
    )


def _pitching_row_to_dict(row: MaxPrepsPitchingRow) -> dict[str, Any]:
    return {
        "number": row.number,
        "name": row.name,
        "grade": row.grade,
        "era": row.era,
        "wins": row.wins,
        "losses": row.losses,
        "appearances": row.appearances,
        "games_started": row.games_started,
        "innings_pitched": row.innings_pitched,
        "hits_allowed": row.hits_allowed,
        "runs_allowed": row.runs_allowed,
        "earned_runs": row.earned_runs,
        "walks": row.walks,
        "strikeouts": row.strikeouts,
        "doubles_allowed": row.doubles_allowed,
        "triples_allowed": row.triples_allowed,
        "homers_allowed": row.homers_allowed,
        "batters_faced": row.batters_faced,
        "at_bats_against": row.at_bats_against,
        "opponent_ba": row.opponent_ba,
        "opponent_obp": row.opponent_obp,
        "wild_pitches": row.wild_pitches,
        "hbp": row.hbp,
        "pitches": row.pitches,
    }


def build_manual_pitcher_profile(
    *,
    name: str,
    hand: str | None,
    velo: str,
    k_rate: str,
    bb_rate: str,
    contact: str,
) -> OpponentPitcherProfile:
    """
    Convert simple coach inputs into model multipliers.
    """

    k_map = {
        "Low": 0.78,
        "Average": 1.0,
        "High": 1.32,
        "Elite": 1.58,
    }

    bb_map = {
        "Low": 0.72,
        "Average": 1.0,
        "High": 1.32,
        "Wild": 1.65,
    }

    contact_map = {
        "Weak": (0.84, 0.86),
        "Average": (1.0, 1.0),
        "Hard": (1.14, 1.22),
    }

    # --- Velo nudges (small effect) ---
    velo_adj = {
        "Soft": -0.1,
        "Average": 0.0,
        "Hard": 0.1,
        "Very Hard": 0.2,
    }

    strikeout_multiplier = k_map.get(k_rate, 1.0) + velo_adj.get(velo, 0.0)
    walk_multiplier = bb_map.get(bb_rate, 1.0)

    contact_multiplier, power_multiplier = contact_map.get(contact, (1.0, 1.0))

    strikeout_multiplier = round(_clamp(strikeout_multiplier, 0.70, 1.85), 3)
    walk_multiplier = round(_clamp(walk_multiplier, 0.65, 1.85), 3)
    contact_multiplier = round(_clamp(contact_multiplier, 0.76, 1.22), 3)
    power_multiplier = round(_clamp(power_multiplier, 0.78, 1.30), 3)

    label = f"{velo} {hand or ''}HP | {k_rate} K | {bb_rate} BB"

    profile = OpponentPitcherProfile(
        name=name or "Manual Pitcher",
        strikeout_multiplier=strikeout_multiplier,
        walk_multiplier=walk_multiplier,
        contact_multiplier=contact_multiplier,
        power_multiplier=power_multiplier,
        label=label,
        confidence="Manual",
    )

    profile.scouting_note = (
        f"{velo} velo, {k_rate} strikeout profile, {bb_rate} control, "
        f"{contact.lower()} contact allowed."
    )

    return profile