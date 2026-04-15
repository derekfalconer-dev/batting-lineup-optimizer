from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from core.models import Player


# ------------------------------
# Utility helpers
# ------------------------------

def _clamp_0_100(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _clamp_0_1(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _scale(value: float | None, low: float, high: float, *, default: float = 50.0) -> float:
    if value is None:
        return default
    if high <= low:
        raise ValueError("high must be greater than low")
    pct = (value - low) / (high - low)
    return _clamp_0_100(pct * 100.0)


def _invert(value: float) -> float:
    return _clamp_0_100(100.0 - value)


def _blend(weighted_values: list[tuple[float, float]]) -> float:
    total_weight = sum(weight for _, weight in weighted_values)
    if total_weight <= 0:
        return 50.0
    return _clamp_0_100(sum(value * weight for value, weight in weighted_values) / total_weight)


def _get_num(data: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue
    return float(default)


def _get_rate(data: Mapping[str, Any], keys: list[str], fallback: float | None = None) -> float | None:
    for key in keys:
        if key not in data or data[key] in (None, ""):
            continue
        raw = data[key]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 1.0:
            value = value / 100.0
        return value
    return fallback


class Handedness(str, Enum):
    RIGHT = "R"
    LEFT = "L"
    SWITCH = "S"
    UNKNOWN = "U"


class PlayerArchetype(str, Enum):
    ELITE_CONTACT = "elite_contact"
    CONTACT = "contact"
    GAP_TO_GAP = "gap_to_gap"
    POWER = "power"
    THREE_TRUE_OUTCOMES = "three_true_outcomes"
    SPEEDSTER = "speedster"
    TABLE_SETTER = "table_setter"
    BALANCED = "balanced"
    WEAK_HITTER = "weak_hitter"
    UNKNOWN = "unknown"


class SourceMode(str, Enum):
    GC = "gc"
    GC_NUDGED = "gc_nudged"
    MANUAL_ARCHETYPE = "manual_archetype"
    MANUAL_TRAITS = "manual_traits"
    MANUAL_PROFILE = "manual_profile"


@dataclass(slots=True)
class TraitAdjustment:
    """
    Coach-entered tweaks layered on top of imported or preset traits.
    All values are additive nudges on a 0-100 trait scale.
    """

    contact: float = 0.0
    power: float = 0.0
    speed: float = 0.0
    baserunning: float = 0.0
    plate_discipline: float = 0.0
    strikeout_tendency: float = 0.0
    walk_skill: float = 0.0
    chase_tendency: float = 0.0
    aggression: float = 0.0
    clutch: float = 0.0
    sacrifice_ability: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "contact": self.contact,
            "power": self.power,
            "speed": self.speed,
            "baserunning": self.baserunning,
            "plate_discipline": self.plate_discipline,
            "strikeout_tendency": self.strikeout_tendency,
            "walk_skill": self.walk_skill,
            "chase_tendency": self.chase_tendency,
            "aggression": self.aggression,
            "clutch": self.clutch,
            "sacrifice_ability": self.sacrifice_ability,
        }

    def is_zero(self) -> bool:
        return all(value == 0 for value in self.as_dict().values())


@dataclass(slots=True)
class PlayerTraits:
    """
    Normalized player traits on a 0-100 scale.

    Convention:
    - Higher is better for positive tools: contact, power, speed, discipline, walk_skill
    - Higher is worse for negative tendencies: strikeout_tendency, chase_tendency
    """

    contact: float
    power: float
    speed: float
    baserunning: float
    plate_discipline: float
    strikeout_tendency: float
    walk_skill: float
    chase_tendency: float
    aggression: float
    clutch: float = 50.0
    sacrifice_ability: float = 50.0

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            setattr(self, name, _clamp_0_100(value))

    def as_dict(self) -> dict[str, float]:
        return {
            "contact": self.contact,
            "power": self.power,
            "speed": self.speed,
            "baserunning": self.baserunning,
            "plate_discipline": self.plate_discipline,
            "strikeout_tendency": self.strikeout_tendency,
            "walk_skill": self.walk_skill,
            "chase_tendency": self.chase_tendency,
            "aggression": self.aggression,
            "clutch": self.clutch,
            "sacrifice_ability": self.sacrifice_ability,
        }

    def apply_adjustment(self, adjustment: TraitAdjustment | None) -> "PlayerTraits":
        if adjustment is None or adjustment.is_zero():
            return replace(self)

        base = self.as_dict()
        delta = adjustment.as_dict()
        return PlayerTraits(**{k: base[k] + delta.get(k, 0.0) for k in base})

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        defaults: Mapping[str, float] | None = None,
    ) -> "PlayerTraits":
        payload = dict(defaults or {})
        payload.update({k: float(v) for k, v in data.items() if k in cls.__dataclass_fields__})
        missing = [name for name in cls.__dataclass_fields__ if name not in payload]
        if missing:
            raise ValueError(f"Missing trait values: {', '.join(missing)}")
        return cls(**payload)


@dataclass(slots=True)
class ArchetypeDefinition:
    key: PlayerArchetype
    label: str
    description: str
    default_traits: PlayerTraits
    lineup_notes: tuple[str, ...] = ()

    def instantiate(
        self,
        *,
        name: str,
        handedness: Handedness = Handedness.UNKNOWN,
        adjustment: TraitAdjustment | None = None,
        source: str = "manual_archetype",
        metadata: Mapping[str, Any] | None = None,
    ) -> "PlayerProfile":
        return PlayerProfile(
            name=name,
            handedness=handedness,
            archetype=self.key,
            base_traits=self.default_traits,
            adjustment=adjustment or TraitAdjustment(),
            source=source,
            source_mode=SourceMode.MANUAL_ARCHETYPE,
            metadata=dict(metadata or {}),
        )


@dataclass(slots=True)
class PlayerProfile:
    """
    Source-of-truth profile object before conversion to simulator Player.

    Supports:
    1. Preset archetype
    2. GameChanger-derived traits
    3. Manual/scouting-entry traits
    """

    name: str
    handedness: Handedness = Handedness.UNKNOWN
    archetype: PlayerArchetype = PlayerArchetype.UNKNOWN
    base_traits: PlayerTraits = field(
        default_factory=lambda: PlayerTraits(
            contact=50,
            power=50,
            speed=50,
            baserunning=50,
            plate_discipline=50,
            strikeout_tendency=50,
            walk_skill=50,
            chase_tendency=50,
            aggression=50,
            clutch=50,
            sacrifice_ability=50,
        )
    )
    adjustment: TraitAdjustment = field(default_factory=TraitAdjustment)
    source: str = "manual"
    source_mode: SourceMode = SourceMode.MANUAL_PROFILE
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_traits(self) -> PlayerTraits:
        return self.base_traits.apply_adjustment(self.adjustment)

    def with_adjustment(self, **changes: float) -> "PlayerProfile":
        merged = self.adjustment.as_dict()
        for key, value in changes.items():
            if key not in merged:
                raise KeyError(f"Unknown adjustment field: {key}")
            merged[key] = float(value)
        return replace(self, adjustment=TraitAdjustment(**merged))

    def bump(self, **delta: float) -> "PlayerProfile":
        merged = self.adjustment.as_dict()
        for key, value in delta.items():
            if key not in merged:
                raise KeyError(f"Unknown adjustment field: {key}")
            merged[key] += float(value)
        return replace(self, adjustment=TraitAdjustment(**merged))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "handedness": self.handedness.value,
            "archetype": self.archetype.value,
            "base_traits": self.base_traits.as_dict(),
            "adjustment": self.adjustment.as_dict(),
            "effective_traits": self.effective_traits.as_dict(),
            "source": self.source,
            "source_mode": self.source_mode.value,
            "metadata": self.metadata,
        }

    def to_sim_player(self) -> Player:
        """
        Convert the profile into the simulator Player object used by the current codebase.
        """
        return profile_to_player(self)

    @classmethod
    def from_manual_traits(
        cls,
        *,
        name: str,
        traits: Mapping[str, Any],
        handedness: Handedness = Handedness.UNKNOWN,
        archetype: PlayerArchetype = PlayerArchetype.UNKNOWN,
        adjustment: TraitAdjustment | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "PlayerProfile":
        return cls(
            name=name,
            handedness=handedness,
            archetype=archetype,
            base_traits=PlayerTraits.from_mapping(traits),
            adjustment=adjustment or TraitAdjustment(),
            source="manual_traits",
            source_mode=SourceMode.MANUAL_TRAITS,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_gamechanger(
        cls,
        *,
        name: str,
        gc_row: Mapping[str, Any],
        handedness: Handedness = Handedness.UNKNOWN,
        adjustment: TraitAdjustment | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "PlayerProfile":
        base_traits = traits_from_gamechanger(gc_row)
        inferred_archetype = infer_archetype(base_traits)
        merged_meta = dict(metadata or {})
        merged_meta["gc_row"] = dict(gc_row)

        effective_source_mode = (
            SourceMode.GC_NUDGED
            if adjustment is not None and not adjustment.is_zero()
            else SourceMode.GC
        )

        return cls(
            name=name,
            handedness=handedness,
            archetype=inferred_archetype,
            base_traits=base_traits,
            adjustment=adjustment or TraitAdjustment(),
            source="gamechanger",
            source_mode=effective_source_mode,
            metadata=merged_meta,
        )


ARCHETYPES: dict[PlayerArchetype, ArchetypeDefinition] = {
    PlayerArchetype.ELITE_CONTACT: ArchetypeDefinition(
        key=PlayerArchetype.ELITE_CONTACT,
        label="Elite Contact",
        description="Rare bat-to-ball player who sprays liners and keeps innings alive.",
        default_traits=PlayerTraits(
            contact=88,
            power=48,
            speed=55,
            baserunning=55,
            plate_discipline=72,
            strikeout_tendency=18,
            walk_skill=58,
            chase_tendency=28,
            aggression=52,
            clutch=60,
            sacrifice_ability=70,
        ),
        lineup_notes=("Ideal 1-2 hitter", "High on-base floor", "Low swing-and-miss"),
    ),
    PlayerArchetype.CONTACT: ArchetypeDefinition(
        key=PlayerArchetype.CONTACT,
        label="Contact",
        description="Reliable contact bat with modest pop and decent on-base ability.",
        default_traits=PlayerTraits(
            contact=74,
            power=40,
            speed=50,
            baserunning=50,
            plate_discipline=60,
            strikeout_tendency=30,
            walk_skill=48,
            chase_tendency=38,
            aggression=50,
            clutch=52,
            sacrifice_ability=65,
        ),
        lineup_notes=("Fits top or middle third", "Keeps pressure on defense"),
    ),
    PlayerArchetype.GAP_TO_GAP: ArchetypeDefinition(
        key=PlayerArchetype.GAP_TO_GAP,
        label="Gap-to-Gap",
        description="Doubles-oriented hitter with balanced contact and extra-base impact.",
        default_traits=PlayerTraits(
            contact=68,
            power=62,
            speed=54,
            baserunning=53,
            plate_discipline=56,
            strikeout_tendency=38,
            walk_skill=44,
            chase_tendency=42,
            aggression=58,
            clutch=54,
            sacrifice_ability=50,
        ),
        lineup_notes=("Strong 2-5 hitter", "RBI upside without all-or-nothing profile"),
    ),
    PlayerArchetype.POWER: ArchetypeDefinition(
        key=PlayerArchetype.POWER,
        label="Power",
        description="Damage-first bat capable of changing the game with one swing.",
        default_traits=PlayerTraits(
            contact=52,
            power=84,
            speed=45,
            baserunning=42,
            plate_discipline=48,
            strikeout_tendency=58,
            walk_skill=46,
            chase_tendency=50,
            aggression=62,
            clutch=55,
            sacrifice_ability=35,
        ),
        lineup_notes=("Middle-of-order bat", "Protect with contact around him"),
    ),
    PlayerArchetype.THREE_TRUE_OUTCOMES: ArchetypeDefinition(
        key=PlayerArchetype.THREE_TRUE_OUTCOMES,
        label="Three True Outcomes",
        description="Walks, strikeouts, and damage. Volatile but dangerous.",
        default_traits=PlayerTraits(
            contact=42,
            power=86,
            speed=40,
            baserunning=40,
            plate_discipline=62,
            strikeout_tendency=74,
            walk_skill=62,
            chase_tendency=44,
            aggression=56,
            clutch=50,
            sacrifice_ability=20,
        ),
        lineup_notes=("Boom/bust run producer", "Pair with high-OBP bats"),
    ),
    PlayerArchetype.SPEEDSTER: ArchetypeDefinition(
        key=PlayerArchetype.SPEEDSTER,
        label="Speedster",
        description="Pressure player who creates chaos with legs and range.",
        default_traits=PlayerTraits(
            contact=58,
            power=28,
            speed=90,
            baserunning=88,
            plate_discipline=46,
            strikeout_tendency=40,
            walk_skill=36,
            chase_tendency=46,
            aggression=70,
            clutch=53,
            sacrifice_ability=50,
        ),
        lineup_notes=("Can lead off", "Best when on-base skills are passable"),
    ),
    PlayerArchetype.TABLE_SETTER: ArchetypeDefinition(
        key=PlayerArchetype.TABLE_SETTER,
        label="Table Setter",
        description="Gets on, sees pitches, and moves well enough to score from anywhere.",
        default_traits=PlayerTraits(
            contact=70,
            power=34,
            speed=72,
            baserunning=76,
            plate_discipline=70,
            strikeout_tendency=28,
            walk_skill=60,
            chase_tendency=30,
            aggression=52,
            clutch=55,
            sacrifice_ability=65,
        ),
        lineup_notes=("Classic leadoff/2-hole option", "OBP-driven value"),
    ),
    PlayerArchetype.BALANCED: ArchetypeDefinition(
        key=PlayerArchetype.BALANCED,
        label="Balanced",
        description="No glaring hole, no single huge carrying tool.",
        default_traits=PlayerTraits(
            contact=58,
            power=52,
            speed=52,
            baserunning=52,
            plate_discipline=52,
            strikeout_tendency=44,
            walk_skill=44,
            chase_tendency=44,
            aggression=50,
            clutch=50,
            sacrifice_ability=55,
        ),
        lineup_notes=("Flexible roster glue", "Easy to move around lineup"),
    ),
    PlayerArchetype.WEAK_HITTER: ArchetypeDefinition(
        key=PlayerArchetype.WEAK_HITTER,
        label="Weak Hitter",
        description="Bottom-of-order bat who currently projects limited offensive impact.",
        default_traits=PlayerTraits(
            contact=32,
            power=18,
            speed=42,
            baserunning=40,
            plate_discipline=34,
            strikeout_tendency=62,
            walk_skill=24,
            chase_tendency=62,
            aggression=46,
            clutch=44,
            sacrifice_ability=40,
        ),
        lineup_notes=("Hide lower in lineup", "May improve with coach override/scouting"),
    ),
    PlayerArchetype.UNKNOWN: ArchetypeDefinition(
        key=PlayerArchetype.UNKNOWN,
        label="Balanced",
        description="Balanced fallback profile when no single offensive shape strongly stands out.",
        default_traits=PlayerTraits(
            contact=50,
            power=50,
            speed=50,
            baserunning=50,
            plate_discipline=50,
            strikeout_tendency=50,
            walk_skill=50,
            chase_tendency=50,
            aggression=50,
            clutch=50,
            sacrifice_ability=50,
        ),
        lineup_notes=("Flexible profile", "Use manual tweaks or scouting reports if needed"),
    ),
}


def get_archetype_definition(archetype: PlayerArchetype | str) -> ArchetypeDefinition:
    key = PlayerArchetype(archetype)
    return ARCHETYPES[key]


def list_archetypes() -> list[ArchetypeDefinition]:
    return list(ARCHETYPES.values())


def create_player_from_archetype(
    name: str,
    archetype: PlayerArchetype | str,
    *,
    handedness: Handedness = Handedness.UNKNOWN,
    adjustment: TraitAdjustment | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> PlayerProfile:
    definition = get_archetype_definition(archetype)
    return definition.instantiate(
        name=name,
        handedness=handedness,
        adjustment=adjustment,
        metadata=metadata,
    )


def infer_archetype(traits: PlayerTraits) -> PlayerArchetype:
    if traits.contact >= 82 and traits.strikeout_tendency <= 25:
        return PlayerArchetype.ELITE_CONTACT
    if traits.power >= 82 and traits.strikeout_tendency >= 65 and traits.walk_skill >= 50:
        return PlayerArchetype.THREE_TRUE_OUTCOMES
    if traits.power >= 76:
        return PlayerArchetype.POWER
    if traits.speed >= 84 and traits.baserunning >= 80:
        return PlayerArchetype.SPEEDSTER
    if traits.contact >= 66 and traits.plate_discipline >= 65 and traits.speed >= 62:
        return PlayerArchetype.TABLE_SETTER
    if traits.contact >= 68 and traits.power < 55:
        return PlayerArchetype.CONTACT
    if traits.contact >= 60 and traits.power >= 58:
        return PlayerArchetype.GAP_TO_GAP
    if traits.contact <= 38 and traits.power <= 25:
        return PlayerArchetype.WEAK_HITTER
    if all(40 <= value <= 60 for value in traits.as_dict().values()):
        return PlayerArchetype.BALANCED

    # Fallback for mixed / non-extreme profiles.
    # Use BALANCED rather than UNKNOWN so the coach-facing UI does not imply
    # uncertainty or low confidence when the player simply has no dominant shape.
    return PlayerArchetype.BALANCED


# ------------------------------
# Bridge to current simulator
# ------------------------------

def profile_to_player(profile: PlayerProfile) -> Player:
    """
    Convert a PlayerProfile into the simulator's Player object.

    This is the key adapter between:
    - coach-facing archetypes / traits
    - current Monte Carlo simulation engine
    """
    t = profile.effective_traits

    # Convert 0-100 traits to 0-1 scales
    contact = t.contact / 100.0
    power = t.power / 100.0
    discipline = t.plate_discipline / 100.0
    walk_skill = t.walk_skill / 100.0
    k_tendency = t.strikeout_tendency / 100.0
    speed = t.speed / 100.0
    baserunning = t.baserunning / 100.0
    aggression = t.aggression / 100.0
    sacrifice_ability = t.sacrifice_ability / 100.0

    gc_row = profile.metadata.get("gc_row", {}) if isinstance(profile.metadata, dict) else {}
    pa_raw = _get_num(gc_row, "PA", default=0.0)
    roe_raw = _get_num(gc_row, "ROE", default=0.0)
    roe_rate = (roe_raw / pa_raw) if pa_raw > 0 else 0.0

    # Heuristic first-pass mapping to event probabilities.
    # These are intentionally easy to tune later.
    p_bb = 0.04 + 0.10 * walk_skill + 0.03 * discipline
    p_so = 0.08 + 0.22 * k_tendency - 0.05 * contact
    p_hr = 0.002 + 0.04 * power * (0.5 + 0.5 * contact)
    p_3b = 0.003 + 0.015 * speed * (0.3 + 0.7 * contact)
    p_2b = 0.02 + 0.07 * power * (0.4 + 0.6 * contact) + 0.015 * speed * contact
    # Blend a modest share of ROE into the "safe on ball in play" bucket by
    # slightly lifting singles. This is not saying ROE == single; it is a
    # practical youth-baseball approximation while we do not model ROE as its
    # own explicit event type.
    roe_single_bump = min(0.03, 0.50 * roe_rate)
    p_1b = 0.08 + 0.18 * contact + 0.02 * speed + roe_single_bump

    # Clamp individual events before normalization
    p_bb = _clamp_0_1(p_bb)
    p_so = _clamp_0_1(p_so)
    p_hr = _clamp_0_1(p_hr)
    p_3b = _clamp_0_1(p_3b)
    p_2b = _clamp_0_1(p_2b)
    p_1b = _clamp_0_1(p_1b)

    used = p_bb + p_1b + p_2b + p_3b + p_hr + p_so
    p_bip_out = max(1.0 - used, 0.01)

    player = Player(
        name=profile.name,
        p_bb=p_bb,
        p_1b=p_1b,
        p_2b=p_2b,
        p_3b=p_3b,
        p_hr=p_hr,
        p_so=p_so,
        p_bip_out=p_bip_out,
        speed=_clamp_0_1(0.70 * speed + 0.30 * baserunning),
        aggression=_clamp_0_1(aggression),
        steal_skill=_clamp_0_1(0.55 * speed + 0.45 * baserunning),
        baserunning_iq=_clamp_0_1(0.65 * baserunning + 0.35 * discipline),
        sacrifice_ability=_clamp_0_1(sacrifice_ability),
    )
    player.normalize()
    return player


# ------------------------------
# GameChanger mapping helpers
# ------------------------------

def traits_from_gamechanger(gc_row: Mapping[str, Any]) -> PlayerTraits:
    """
    Map raw GameChanger-style counting/rate stats into normalized traits.
    Heuristic by design; easy to tune.
    """

    pa = _get_num(gc_row, "PA", default=0)
    ab = _get_num(gc_row, "AB", default=0)
    hits = _get_num(gc_row, "H", default=0)
    doubles = _get_num(gc_row, "2B", default=0)
    triples = _get_num(gc_row, "3B", default=0)
    hr = _get_num(gc_row, "HR", default=0)
    bb = _get_num(gc_row, "BB", default=0)
    roe = _get_num(gc_row, "ROE", default=0)
    so = _get_num(gc_row, "SO", "K", default=0)
    sb = _get_num(gc_row, "SB", default=0)
    cs = _get_num(gc_row, "CS", default=0)

    avg = _get_rate(gc_row, ["AVG", "BA"], fallback=(hits / ab if ab else None))
    on_base_events = hits + bb + roe
    obp = _get_rate(gc_row, ["OBP"], fallback=(on_base_events / pa if pa else None))
    slg = _get_rate(gc_row, ["SLG"], fallback=None)

    if slg is None and ab:
        singles = max(hits - doubles - triples - hr, 0)
        total_bases = singles + 2 * doubles + 3 * triples + 4 * hr
        slg = total_bases / ab

    k_rate = so / pa if pa else None
    bb_rate = bb / pa if pa else None
    sb_attempts = sb + cs
    sb_rate = sb / sb_attempts if sb_attempts else None
    roe_rate = roe / pa if pa else None

    contact = _scale(avg, 0.150, 0.500, default=50)
    power = _scale(slg, 0.150, 1.000, default=50)
    speed = _blend([
        (_scale(sb, 0, 25, default=50), 0.65),
        (_scale(sb_rate, 0.40, 1.00, default=50), 0.35),
    ])
    baserunning = _blend([
        (speed, 0.70),
        (_scale(sb_rate, 0.35, 1.00, default=50), 0.30),
    ])
    plate_discipline = _blend([
        (_scale(obp, 0.180, 0.650, default=50), 0.40),
        (_scale(bb_rate, 0.00, 0.25, default=50), 0.30),
        (_scale(roe_rate, 0.00, 0.20, default=50), 0.10),
        (_invert(_scale(k_rate, 0.00, 0.50, default=50)), 0.20),
    ])
    strikeout_tendency = _scale(k_rate, 0.00, 0.50, default=50)
    walk_skill = _scale(bb_rate, 0.00, 0.25, default=50)
    chase_tendency = _invert(plate_discipline)
    aggression = _blend([
        (speed, 0.25),
        (power, 0.20),
        (_scale(pa, 1, 80, default=50), 0.15),
        (_scale(sb, 0, 20, default=50), 0.30),
        (_scale(roe_rate, 0.00, 0.20, default=50), 0.10),
    ])
    clutch = 50.0
    sacrifice_ability = _blend([
        (contact, 0.55),
        (plate_discipline, 0.40),
        (speed, 0.05),
    ])

    return PlayerTraits(
        contact=contact,
        power=power,
        speed=speed,
        baserunning=baserunning,
        plate_discipline=plate_discipline,
        strikeout_tendency=strikeout_tendency,
        walk_skill=walk_skill,
        chase_tendency=chase_tendency,
        aggression=aggression,
        clutch=clutch,
        sacrifice_ability=sacrifice_ability,
    )









