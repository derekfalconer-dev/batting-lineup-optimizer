from dataclasses import dataclass
from enum import Enum
from typing import List


class GameStrategy(str, Enum):
    SMALL_BALL = "small_ball"
    BALANCED = "balanced"
    POWER = "power"


class CoachingStyle(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class OpposingPitchingStrength(str, Enum):
    WEAK = "weak"
    AVERAGE = "average"
    STRONG = "strong"
    ELITE = "elite"

    # matchup archetypes
    POWER_ARM = "power_arm"
    CRAFTY = "crafty"
    WILD = "wild"
    BALANCED_ARM = "balanced_arm"


class OpponentLevel(str, Enum):
    WEAK = "weak"
    AVERAGE = "average"
    STRONG = "strong"


@dataclass
class Player:
    name: str

    # Hitting probabilities (must sum to ~1.0)
    p_bb: float
    p_1b: float
    p_2b: float
    p_3b: float
    p_hr: float
    p_so: float
    p_bip_out: float  # ball in play out

    # Baserunning traits (0.0 – 1.0 scale)
    speed: float = 0.5
    aggression: float = 0.5
    steal_skill: float = 0.5
    baserunning_iq: float = 0.5
    sacrifice_ability: float = 0.5
    # Optional offensive traits copied from PlayerProfile.
    # These let opponent pitcher effects vary by batter instead of applying
    # the same global multiplier to every hitter.
    contact_trait: float = 0.5
    power_trait: float = 0.5
    discipline_trait: float = 0.5
    walk_skill_trait: float = 0.5
    strikeout_tendency_trait: float = 0.5
    chase_tendency_trait: float = 0.5

    def normalize(self):
        total = (
            self.p_bb
            + self.p_1b
            + self.p_2b
            + self.p_3b
            + self.p_hr
            + self.p_so
            + self.p_bip_out
        )
        if total == 0:
            raise ValueError(f"{self.name} has zero probability total")

        self.p_bb /= total
        self.p_1b /= total
        self.p_2b /= total
        self.p_3b /= total
        self.p_hr /= total
        self.p_so /= total
        self.p_bip_out /= total


@dataclass
class RulesConfig:
    innings: int = 6
    max_runs_per_inning: int = 5

    steals_allowed: bool = True
    leadoffs_allowed: bool = False
    base_distance_ft: int = 70

    # NEW
    continuous_batting: bool = True
    lineup_size: int = 9

    steal_attempt_multiplier: float = 1.0
    steal_success_multiplier: float = 1.0

    # NEW
    game_strategy: GameStrategy = GameStrategy.BALANCED
    coaching_style: CoachingStyle = CoachingStyle.BALANCED
    opposing_pitching: OpposingPitchingStrength = OpposingPitchingStrength.AVERAGE
    opponent_level: OpponentLevel = OpponentLevel.AVERAGE

    # Imported opponent scouting report context
    use_opponent_scouting: bool = False
    opponent_pitcher_name: str | None = None
    opponent_pitcher_label: str | None = None
    opponent_pitcher_strikeout_multiplier: float = 1.0
    opponent_pitcher_walk_multiplier: float = 1.0
    opponent_pitcher_contact_multiplier: float = 1.0
    opponent_pitcher_power_multiplier: float = 1.0

    # Imported opponent pitcher sample-size metadata
    opponent_pitcher_sample_size: str | None = None
    opponent_pitcher_innings_pitched: float | None = None
    opponent_pitcher_batters_faced: int | None = None

    # Derived modifiers

    # Derived modifiers
    contact_multiplier: float = 1.0
    power_multiplier: float = 1.0
    walk_multiplier: float = 1.0
    strikeout_multiplier: float = 1.0
    advancement_success_multiplier: float = 1.0
    productive_out_env_multiplier: float = 1.0


@dataclass
class LineupResult:
    lineup: List[str]

    mean_runs: float
    median_runs: float
    std_runs: float

    prob_ge_target: float
    target_runs: float

    downside_deviation: float
    sortino: float

    p10_runs: float
    p90_runs: float

    n_games: int
    runs_scored_distribution: List[int]


def compile_rules_context(rules: RulesConfig) -> RulesConfig:
    """
    Return a copy of rules with environment multipliers baked in.
    """
    compiled = RulesConfig(**rules.__dict__)

    compiled.contact_multiplier = 1.0
    compiled.power_multiplier = 1.0
    compiled.walk_multiplier = 1.0
    compiled.strikeout_multiplier = 1.0
    compiled.advancement_success_multiplier = 1.0
    compiled.productive_out_env_multiplier = 1.0

    # preserve any explicit user multipliers
    base_steal_attempt = float(rules.steal_attempt_multiplier)
    base_steal_success = float(rules.steal_success_multiplier)
    compiled.steal_attempt_multiplier = base_steal_attempt
    compiled.steal_success_multiplier = base_steal_success

    # Strategy
    if compiled.game_strategy == GameStrategy.SMALL_BALL:
        compiled.steal_attempt_multiplier *= 1.20
        compiled.advancement_success_multiplier *= 1.06
        compiled.productive_out_env_multiplier *= 1.10
        compiled.power_multiplier *= 0.97
    elif compiled.game_strategy == GameStrategy.POWER:
        compiled.power_multiplier *= 1.10
        compiled.strikeout_multiplier *= 1.05
        compiled.steal_attempt_multiplier *= 0.90
        compiled.productive_out_env_multiplier *= 0.95

    # Coaching style
    if compiled.coaching_style == CoachingStyle.CONSERVATIVE:
        compiled.steal_attempt_multiplier *= 0.82
        compiled.advancement_success_multiplier *= 0.96
    elif compiled.coaching_style == CoachingStyle.AGGRESSIVE:
        compiled.steal_attempt_multiplier *= 1.18
        compiled.advancement_success_multiplier *= 1.04

    # Opposing pitching
    if compiled.opposing_pitching == OpposingPitchingStrength.WEAK:
        compiled.contact_multiplier *= 1.05
        compiled.walk_multiplier *= 1.05
        compiled.strikeout_multiplier *= 0.92
        compiled.power_multiplier *= 1.03
    elif compiled.opposing_pitching == OpposingPitchingStrength.STRONG:
        compiled.contact_multiplier *= 0.95
        compiled.walk_multiplier *= 0.96
        compiled.strikeout_multiplier *= 1.10
        compiled.power_multiplier *= 0.95
    elif compiled.opposing_pitching == OpposingPitchingStrength.ELITE:
        compiled.contact_multiplier *= 0.90
        compiled.walk_multiplier *= 0.93
        compiled.strikeout_multiplier *= 1.18
        compiled.power_multiplier *= 0.90
        compiled.productive_out_env_multiplier *= 1.05
    elif compiled.opposing_pitching == OpposingPitchingStrength.POWER_ARM:
        compiled.contact_multiplier *= 0.85
        compiled.strikeout_multiplier *= 1.25
        compiled.power_multiplier *= 0.96
    elif compiled.opposing_pitching == OpposingPitchingStrength.CRAFTY:
        compiled.contact_multiplier *= 1.15
        compiled.power_multiplier *= 0.85
        compiled.strikeout_multiplier *= 0.95
        compiled.productive_out_env_multiplier *= 1.05
    elif compiled.opposing_pitching == OpposingPitchingStrength.WILD:
        compiled.walk_multiplier *= 1.22
        compiled.strikeout_multiplier *= 1.0
        compiled.contact_multiplier *= 0.97
    elif compiled.opposing_pitching == OpposingPitchingStrength.BALANCED_ARM:
        pass

    # Opponent level / defense
    if compiled.opponent_level == OpponentLevel.WEAK:
        compiled.advancement_success_multiplier *= 1.08
        compiled.productive_out_env_multiplier *= 1.05
    elif compiled.opponent_level == OpponentLevel.STRONG:
        compiled.advancement_success_multiplier *= 0.93
        compiled.productive_out_env_multiplier *= 0.95

    # Specific imported opposing pitcher effects are applied per batter in
    # app_service._apply_environment_to_players(), because batter traits matter.
    # Do not apply those numeric pitcher multipliers globally here.

    return compiled