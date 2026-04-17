from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


# ---------------------------------------------------------------------
# Player / roster view models
# ---------------------------------------------------------------------

@dataclass(slots=True)
class TraitSetSchema:
    """
    UI/API-safe representation of a 0-100 trait bundle.
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
    clutch: float
    sacrifice_ability: float

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TraitSetSchema":
        return cls(
            contact=float(data["contact"]),
            power=float(data["power"]),
            speed=float(data["speed"]),
            baserunning=float(data["baserunning"]),
            plate_discipline=float(data["plate_discipline"]),
            strikeout_tendency=float(data["strikeout_tendency"]),
            walk_skill=float(data["walk_skill"]),
            chase_tendency=float(data["chase_tendency"]),
            aggression=float(data["aggression"]),
            clutch=float(data["clutch"]),
            sacrifice_ability=float(data["sacrifice_ability"]),
        )


@dataclass(slots=True)
class PlayerProfileSchema:
    """
    Coach-facing representation of a player profile.

    Mirrors the important parts of core.archetypes.PlayerProfile, but keeps
    the payload clean and serialization-friendly.
    """

    name: str
    handedness: str
    archetype: str
    source: str
    source_mode: str

    base_traits: TraitSetSchema
    adjustment: dict[str, float]
    effective_traits: TraitSetSchema

    plate_appearances: int | None = None
    source_file_count: int | None = None
    confidence: str | None = None
    confidence_badge: str | None = None
    confidence_action: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_adjustment(self) -> bool:
        return any(value != 0 for value in self.adjustment.values())


@dataclass(slots=True)
class RosterSummarySchema:
    """
    High-level roster summary for UI display.
    """

    team_source: str
    player_count: int
    source_mode_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Lineup / evaluation schemas
# ---------------------------------------------------------------------

@dataclass(slots=True)
class LineupSlotSchema:
    """
    One batting-order slot.
    """

    batting_order: int
    player_name: str


@dataclass(slots=True)
class EvaluationMetricsSchema:
    """
    Stable evaluation payload for any lineup.
    """

    mean_runs: float
    median_runs: float
    std_runs: float
    prob_ge_target: float
    sortino: float
    p10_runs: float
    p90_runs: float
    n_games: int
    target_runs: float | None = None


@dataclass(slots=True)
class LineupEvaluationSchema:
    """
    Full lineup evaluation result for coach/UI consumption.
    """

    display_name: str
    lineup: list[str]
    slots: list[LineupSlotSchema]
    metrics: EvaluationMetricsSchema
    runs_scored_distribution: list[int] = field(default_factory=list)


@dataclass(slots=True)
class LeaderboardSchema:
    """
    Named leaderboard such as top_mean / top_sortino / top_prob.
    """

    key: str
    title: str
    entries: list[LineupEvaluationSchema] = field(default_factory=list)


# ---------------------------------------------------------------------
# Charts / summary schemas
# ---------------------------------------------------------------------

@dataclass(slots=True)
class ChartSchema:
    """
    Metadata about a generated chart artifact.
    """

    key: str
    title: str
    path: str


@dataclass(slots=True)
class CoachSummarySchema:
    """
    Human-readable coach-facing summary block.
    """

    optimized_prob_ge_target: float
    original_prob_ge_target: float
    improvement_prob_ge_target: float

    optimized_mean_runs: float
    original_mean_runs: float
    improvement_mean_runs: float

    optimized_lineup: list[str]
    original_lineup: list[str]

    bullets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Scenario schemas
# ---------------------------------------------------------------------

@dataclass(slots=True)
class SavedScenarioSchema:
    """
    Coach Lab saved scenario.

    Represents one saved coaching experiment:
    - lineup choice
    - active player nudges
    - optional simulation result
    """

    scenario_id: str
    name: str

    lineup_names: list[str] = field(default_factory=list)
    adjustments_by_name: dict[str, dict[str, float]] = field(default_factory=dict)

    result: LineupEvaluationSchema | None = None

    created_at: float | None = None
    updated_at: float | None = None


@dataclass(slots=True)
class ScenarioCollectionSchema:
    """
    Container for saved Coach Lab scenarios in the current session.
    """

    scenarios: list[SavedScenarioSchema] = field(default_factory=list)

# ---------------------------------------------------------------------
# Top-level workflow response schema
# ---------------------------------------------------------------------

@dataclass(slots=True)
class WorkflowResponseSchema:
    """
    App-facing response contract for the optimizer workflow.

    This is intended to replace the current ad hoc print-oriented output.
    """

    team_source: str

    roster_summary: RosterSummarySchema
    player_profiles: list[PlayerProfileSchema]

    optimized: LineupEvaluationSchema
    original: LineupEvaluationSchema
    random_lineup: LineupEvaluationSchema
    worst_lineup: LineupEvaluationSchema

    comparison_set: list[LineupEvaluationSchema]
    leaderboards: list[LeaderboardSchema]

    coach_summary: CoachSummarySchema
    charts: list[ChartSchema]

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Optional lightweight session schema
# ---------------------------------------------------------------------

@dataclass(slots=True)
class SessionStateSchema:
    """
    Early session model for future Streamlit / API flow.
    This does not replace session_manager yet, but gives us a clean shape
    to grow into.
    """

    session_id: str
    status: str = "created"

    data_source: str | None = None
    csv_path: str | None = None
    adjustments_path: str | None = None
    roster_path: str | None = None

    workflow_response: WorkflowResponseSchema | None = None

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)