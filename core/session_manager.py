from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from core.archetypes import PlayerProfile
from core.schemas import WorkflowResponseSchema


# ---------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------

@dataclass
class SavedScenario:
    """
    A saved Coach Lab scenario.

    This is session-scoped for now. Later it can become a persisted DB model.
    """

    scenario_id: str
    name: str

    lineup_names: list[str] = field(default_factory=list)
    adjustments_by_name: dict[str, dict[str, float]] = field(default_factory=dict)

    # Raw evaluation payload for now.
    # Later we can replace this with a formal schema.
    result: Optional[dict[str, Any]] = None

    created_at: float = field(default_factory=lambda: __import__("time").time())
    updated_at: float = field(default_factory=lambda: __import__("time").time())

    def touch(self) -> None:
        self.updated_at = __import__("time").time()


@dataclass
class OptimizerSession:
    """
    Represents a single end-to-end optimizer workflow session.

    This is the backbone for a future web app.
    """

    session_id: str

    # -----------------------------
    # Inputs
    # -----------------------------
    data_source: Optional[str] = None
    csv_path: Optional[Path] = None
    adjustments_path: Optional[Path] = None
    roster_path: Optional[Path] = None

    # Raw loaded config (optional future use)
    adjustments: Optional[dict[str, dict[str, Any]]] = None
    manual_roster: Optional[list[dict[str, Any]]] = None

    # -----------------------------
    # Coach Lab state
    # -----------------------------
    coach_adjustments_by_name: dict[str, dict[str, float]] = field(default_factory=dict)
    custom_lineup_names: list[str] = field(default_factory=list)
    custom_lineup_result: Optional[dict[str, Any]] = None
    saved_scenarios: list[SavedScenario] = field(default_factory=list)

    # -----------------------------
    # Editable roster state
    # -----------------------------
    editable_profiles: list[PlayerProfile] = field(default_factory=list)
    benched_player_names: list[str] = field(default_factory=list)

    # -----------------------------
    # Derived state (pre-run)
    # -----------------------------
    profiles: Optional[list[Any]] = None
    players: Optional[list[Any]] = None

    # -----------------------------
    # Results (post-run)
    # -----------------------------
    result: Optional[WorkflowResponseSchema] = None

    # -----------------------------
    # Metadata
    # -----------------------------
    created_at: float = field(default_factory=lambda: __import__("time").time())
    updated_at: float = field(default_factory=lambda: __import__("time").time())

    def touch(self) -> None:
        """Update last-modified timestamp."""
        self.updated_at = __import__("time").time()

    # -----------------------------
    # Convenience flags
    # -----------------------------
    @property
    def has_inputs(self) -> bool:
        return self.data_source is not None

    @property
    def is_ready_to_run(self) -> bool:
        """
        Readiness means:
        - GC sessions need a CSV
        - Manual sessions need an editable roster with at least one player
        """
        if self.data_source in {"gc", "gc_plus_tweaks"}:
            return self.csv_path is not None

        if self.data_source in {"manual_archetypes", "manual_traits"}:
            return len(self.editable_profiles) > 0

        return False

    @property
    def has_results(self) -> bool:
        return self.result is not None

    @property
    def has_coach_adjustments(self) -> bool:
        return bool(self.coach_adjustments_by_name)

    @property
    def has_custom_lineup(self) -> bool:
        return bool(self.custom_lineup_names)

    @property
    def has_custom_result(self) -> bool:
        return self.custom_lineup_result is not None

    @property
    def has_saved_scenarios(self) -> bool:
        return bool(self.saved_scenarios)

    @property
    def has_editable_roster(self) -> bool:
        return bool(self.editable_profiles)

    @property
    def active_player_names(self) -> list[str]:
        benched = set(self.benched_player_names)
        return [p.name for p in self.editable_profiles if p.name not in benched]


# ---------------------------------------------------------------------
# Session manager (in-memory)
# ---------------------------------------------------------------------

class SessionManager:
    """
    In-memory session registry.

    Later:
        - swap to Redis / DB
        - add expiration / cleanup
        - support multi-user
    """

    def __init__(self):
        self._sessions: Dict[str, OptimizerSession] = {}

    # -----------------------------
    # Lifecycle
    # -----------------------------
    def create_session(self) -> OptimizerSession:
        session_id = uuid4().hex[:12]
        session = OptimizerSession(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> OptimizerSession:
        if session_id not in self._sessions:
            raise ValueError(f"Session not found: {session_id}")
        return self._sessions[session_id]

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    # -----------------------------
    # Input setters
    # -----------------------------
    def set_data_source(
        self,
        session_id: str,
        *,
        data_source: str,
        csv_path: Path | None = None,
        adjustments_path: Path | None = None,
        roster_path: Path | None = None,
    ) -> OptimizerSession:
        session = self.get_session(session_id)

        session.data_source = data_source
        session.csv_path = csv_path
        session.adjustments_path = adjustments_path
        session.roster_path = roster_path
        session.adjustments = None
        session.manual_roster = None

        # Reset editable roster + Coach Lab state because the roster context changed
        session.editable_profiles = []
        session.benched_player_names = []
        session.coach_adjustments_by_name = {}
        session.custom_lineup_names = []
        session.custom_lineup_result = None
        session.saved_scenarios = []

        session.touch()
        return session

    def set_adjustments(
        self,
        session_id: str,
        adjustments: dict[str, Any],
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.adjustments = adjustments
        session.touch()
        return session

    def set_manual_roster(
        self,
        session_id: str,
        roster: list[dict[str, Any]],
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.manual_roster = list(roster)
        session.touch()
        return session

    def set_editable_roster(
        self,
        session_id: str,
        *,
        profiles: list[PlayerProfile],
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.editable_profiles = list(profiles)
        session.benched_player_names = []
        session.custom_lineup_names = []
        session.custom_lineup_result = None
        session.touch()
        return session

    def get_editable_roster(
        self,
        session_id: str,
    ) -> list[PlayerProfile]:
        session = self.get_session(session_id)
        return list(session.editable_profiles)

    def replace_player_profile(
        self,
        session_id: str,
        *,
        player_name: str,
        profile: PlayerProfile,
    ) -> OptimizerSession:
        session = self.get_session(session_id)

        replaced = False
        updated_profiles: list[PlayerProfile] = []

        for existing in session.editable_profiles:
            if existing.name == player_name:
                updated_profiles.append(profile)
                replaced = True
            else:
                updated_profiles.append(existing)

        if not replaced:
            raise ValueError(f"Editable roster player not found: {player_name}")

        session.editable_profiles = updated_profiles

        if player_name != profile.name:
            if player_name in session.coach_adjustments_by_name:
                session.coach_adjustments_by_name[profile.name] = session.coach_adjustments_by_name.pop(player_name)

            session.custom_lineup_names = [
                profile.name if name == player_name else name
                for name in session.custom_lineup_names
            ]

            session.benched_player_names = [
                profile.name if name == player_name else name
                for name in session.benched_player_names
            ]

        session.custom_lineup_result = None
        session.touch()
        return session

    def add_player_profile(
        self,
        session_id: str,
        *,
        profile: PlayerProfile,
    ) -> OptimizerSession:
        session = self.get_session(session_id)

        if any(p.name == profile.name for p in session.editable_profiles):
            raise ValueError(f"A player named '{profile.name}' already exists in the editable roster.")

        session.editable_profiles.append(profile)
        session.custom_lineup_result = None
        session.touch()
        return session

    def delete_player_profile(
        self,
        session_id: str,
        *,
        player_name: str,
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        before = len(session.editable_profiles)

        session.editable_profiles = [
            p for p in session.editable_profiles
            if p.name != player_name
        ]

        if len(session.editable_profiles) == before:
            raise ValueError(f"Editable roster player not found: {player_name}")

        session.benched_player_names = [
            name for name in session.benched_player_names
            if name != player_name
        ]
        session.coach_adjustments_by_name.pop(player_name, None)
        session.custom_lineup_names = [
            name for name in session.custom_lineup_names
            if name != player_name
        ]
        session.custom_lineup_result = None
        session.touch()
        return session

    def bench_player(
        self,
        session_id: str,
        *,
        player_name: str,
    ) -> OptimizerSession:
        session = self.get_session(session_id)

        valid_names = {p.name for p in session.editable_profiles}
        if player_name not in valid_names:
            raise ValueError(f"Editable roster player not found: {player_name}")

        if player_name not in session.benched_player_names:
            session.benched_player_names.append(player_name)

        session.custom_lineup_names = [
            name for name in session.custom_lineup_names
            if name != player_name
        ]
        session.custom_lineup_result = None
        session.touch()
        return session

    def unbench_player(
        self,
        session_id: str,
        *,
        player_name: str,
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.benched_player_names = [
            name for name in session.benched_player_names
            if name != player_name
        ]
        session.custom_lineup_result = None
        session.touch()
        return session

    def move_player_up(
        self,
        session_id: str,
        *,
        player_name: str,
    ) -> OptimizerSession:
        session = self.get_session(session_id)

        benched = set(session.benched_player_names)
        active_profiles = [p for p in session.editable_profiles if p.name not in benched]
        benched_profiles = [p for p in session.editable_profiles if p.name in benched]

        idx = next((i for i, p in enumerate(active_profiles) if p.name == player_name), None)
        if idx is None:
            raise ValueError(f"Active roster player not found: {player_name}")

        if idx > 0:
            active_profiles[idx - 1], active_profiles[idx] = active_profiles[idx], active_profiles[idx - 1]

        session.editable_profiles = active_profiles + benched_profiles
        session.custom_lineup_result = None
        session.touch()
        return session

    def move_player_down(
        self,
        session_id: str,
        *,
        player_name: str,
    ) -> OptimizerSession:
        session = self.get_session(session_id)

        benched = set(session.benched_player_names)
        active_profiles = [p for p in session.editable_profiles if p.name not in benched]
        benched_profiles = [p for p in session.editable_profiles if p.name in benched]

        idx = next((i for i, p in enumerate(active_profiles) if p.name == player_name), None)
        if idx is None:
            raise ValueError(f"Active roster player not found: {player_name}")

        if idx < len(active_profiles) - 1:
            active_profiles[idx + 1], active_profiles[idx] = active_profiles[idx], active_profiles[idx + 1]

        session.editable_profiles = active_profiles + benched_profiles
        session.custom_lineup_result = None
        session.touch()
        return session

    def set_player_adjustment(
        self,
        session_id: str,
        *,
        player_name: str,
        adjustment: dict[str, float],
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.coach_adjustments_by_name[player_name] = {
            str(k): float(v) for k, v in adjustment.items()
        }
        session.custom_lineup_result = None
        session.touch()
        return session

    def clear_player_adjustment(
        self,
        session_id: str,
        *,
        player_name: str,
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.coach_adjustments_by_name.pop(player_name, None)
        session.custom_lineup_result = None
        session.touch()
        return session

    def clear_all_adjustments(self, session_id: str) -> OptimizerSession:
        session = self.get_session(session_id)
        session.coach_adjustments_by_name = {}
        session.custom_lineup_result = None
        session.touch()
        return session

    def set_custom_lineup(
        self,
        session_id: str,
        lineup_names: list[str],
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.custom_lineup_names = [str(name) for name in lineup_names]
        session.custom_lineup_result = None
        session.touch()
        return session

    def clear_custom_lineup(self, session_id: str) -> OptimizerSession:
        session = self.get_session(session_id)
        session.custom_lineup_names = []
        session.custom_lineup_result = None
        session.touch()
        return session

    def set_custom_lineup_result(
        self,
        session_id: str,
        result: WorkflowResponseSchema,
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.custom_lineup_result = result
        session.touch()
        return session

    def save_scenario(
        self,
        session_id: str,
        *,
        name: str,
        lineup_names: list[str],
        adjustments_by_name: dict[str, dict[str, float]] | None = None,
        result: dict[str, Any] | None = None,
    ) -> SavedScenario:
        session = self.get_session(session_id)

        scenario = SavedScenario(
            scenario_id=uuid4().hex[:12],
            name=str(name),
            lineup_names=[str(x) for x in lineup_names],
            adjustments_by_name={
                str(player): {str(k): float(v) for k, v in values.items()}
                for player, values in (adjustments_by_name or {}).items()
            },
            result=result,
        )

        session.saved_scenarios.append(scenario)
        session.touch()
        return scenario

    def list_saved_scenarios(self, session_id: str) -> list[SavedScenario]:
        session = self.get_session(session_id)
        return list(session.saved_scenarios)

    def get_saved_scenario(
        self,
        session_id: str,
        scenario_id: str,
    ) -> SavedScenario:
        session = self.get_session(session_id)
        for scenario in session.saved_scenarios:
            if scenario.scenario_id == scenario_id:
                return scenario
        raise ValueError(f"Saved scenario not found: {scenario_id}")

    def rename_scenario(
        self,
        session_id: str,
        *,
        scenario_id: str,
        new_name: str,
    ) -> SavedScenario:
        scenario = self.get_saved_scenario(session_id, scenario_id)
        scenario.name = str(new_name)
        scenario.touch()

        session = self.get_session(session_id)
        session.touch()
        return scenario

    def delete_scenario(
        self,
        session_id: str,
        *,
        scenario_id: str,
    ) -> None:
        session = self.get_session(session_id)
        before = len(session.saved_scenarios)
        session.saved_scenarios = [
            s for s in session.saved_scenarios
            if s.scenario_id != scenario_id
        ]
        if len(session.saved_scenarios) == before:
            raise ValueError(f"Saved scenario not found: {scenario_id}")
        session.touch()

    # -----------------------------
    # Results
    # -----------------------------
    def set_result(
        self,
        session_id: str,
        result: WorkflowResponseSchema,
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        session.result = result
        session.touch()
        return session

    def clear_result(self, session_id: str) -> None:
        session = self.get_session(session_id)
        session.result = None
        session.custom_lineup_result = None
        session.touch()


# ---------------------------------------------------------------------
# Singleton (simple global for now)
# ---------------------------------------------------------------------

_default_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = SessionManager()
    return _default_manager