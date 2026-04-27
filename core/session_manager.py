from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from core.archetypes import PlayerProfile
from core.schemas import WorkflowResponseSchema

from core.team_repository import TeamRepository
from core.json_team_repository import JsonTeamRepository

from core.player_aggregation import (
    AggregatePlayerRecord,
    build_aggregate_players_from_gc_records,
)

import time

import os
import streamlit as st


@dataclass
class TeamRecord:
    """
    Durable team state.

    This is the product object coaches come back to:
    roster, bench state, coach adjustments, and saved scenarios.
    """

    team_id: str
    team_name: str
    owner_user_id: str

    editable_profiles: list[PlayerProfile] = field(default_factory=list)
    benched_player_names: list[str] = field(default_factory=list)
    coach_adjustments_by_name: dict[str, dict[str, float]] = field(default_factory=dict)

    saved_scenarios: list["SavedScenario"] = field(default_factory=list)

    data_source: Optional[str] = None
    csv_path: Optional[str] = None
    adjustments_path: Optional[str] = None
    roster_path: Optional[str] = None

    import_history: list[dict[str, Any]] = field(default_factory=list)

    rules_preset: str = "High School"
    rules_config: dict[str, Any] = field(default_factory=dict)

    aggregate_player_records: dict[str, AggregatePlayerRecord] = field(default_factory=dict)
    player_aliases: dict[str, str] = field(default_factory=dict)

    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())

    def touch(self) -> None:
        self.updated_at = time.time()


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
    team_id: Optional[str] = None

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

    custom_lineup_names: list[str] = field(default_factory=list)
    custom_lineup_result: Optional[dict[str, Any]] = None

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
    # In-memory workspace
    # -----------------------------
    workspace_team: Optional["TeamRecord"] = None
    workspace_loaded_team_id: Optional[str] = None
    workspace_dirty: bool = False
    workspace_last_flushed_at: Optional[float] = None
    workspace_last_mutation_at: Optional[float] = None
    workspace_mutation_count: int = 0

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
        if self.data_source in {"gc", "gc_plus_tweaks"}:
            return self.csv_path is not None

        if self.data_source in {"manual_archetypes", "manual_traits", "gc_merged"}:
            return self.team_id is not None

        return False

    @property
    def has_results(self) -> bool:
        return self.result is not None

    @property
    def has_custom_lineup(self) -> bool:
        return bool(self.custom_lineup_names)

    @property
    def has_custom_result(self) -> bool:
        return self.custom_lineup_result is not None


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

    def __init__(self, team_repository: TeamRepository | None = None):
        self._sessions: Dict[str, OptimizerSession] = {}
        self._team_repository: TeamRepository = team_repository or JsonTeamRepository()

    # -----------------------------
    # Lifecycle
    # -----------------------------
    def create_session(self) -> OptimizerSession:
        session_id = uuid4().hex[:12]
        session = OptimizerSession(session_id=session_id)
        self._sessions[session_id] = session

        # Do not auto-attach to a global team here.
        # Team attachment should happen later, after we know the current user.
        session.team_id = None

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
    # Team persistence helpers
    # -----------------------------
    def _teams_dir(self) -> Path:
        path = Path("data") / "teams"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _team_path(self, team_id: str) -> Path:
        return self._teams_dir() / f"{team_id}.json"

    def load_team(self, team_id: str) -> TeamRecord:
        payload = self._team_repository.load_team(team_id)
        return self._team_from_dict(payload)

    def _scenario_to_dict(self, s: SavedScenario) -> dict[str, Any]:
        safe_result = None

        if isinstance(s.result, dict):
            safe_result = {
                k: v for k, v in s.result.items()
                if k not in {"players", "profiles"}
            }

        return {
            "scenario_id": s.scenario_id,
            "name": s.name,
            "lineup_names": list(s.lineup_names),
            "adjustments_by_name": s.adjustments_by_name,
            "result": safe_result,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }

    def _scenario_from_dict(self, data: dict[str, Any]) -> SavedScenario:
        return SavedScenario(
            scenario_id=str(data["scenario_id"]),
            name=str(data["name"]),
            lineup_names=list(data.get("lineup_names", [])),
            adjustments_by_name=dict(data.get("adjustments_by_name", {})),
            result=data.get("result"),
            created_at=float(data.get("created_at", __import__("time").time())),
            updated_at=float(data.get("updated_at", __import__("time").time())),
        )

    def _profile_to_dict(self, profile: PlayerProfile) -> dict[str, Any]:
        return {
            "name": profile.name,
            "handedness": profile.handedness.value,
            "archetype": profile.archetype.value,
            "base_traits": profile.base_traits.as_dict(),
            "adjustment": profile.adjustment.as_dict(),
            "source": profile.source,
            "source_mode": profile.source_mode.value,
            "metadata": dict(profile.metadata),
        }

    def _profile_from_dict(self, data: dict[str, Any]) -> PlayerProfile:
        from core.archetypes import (
            Handedness,
            PlayerArchetype,
            PlayerTraits,
            SourceMode,
            TraitAdjustment,
        )

        return PlayerProfile(
            name=str(data["name"]),
            handedness=Handedness(data.get("handedness", "U")),
            archetype=PlayerArchetype(data.get("archetype", "unknown")),
            base_traits=PlayerTraits.from_mapping(data["base_traits"]),
            adjustment=TraitAdjustment(**data.get("adjustment", {})),
            source=str(data.get("source", "manual")),
            source_mode=SourceMode(data.get("source_mode", "manual_profile")),
            metadata=dict(data.get("metadata", {})),
        )

    def _team_to_dict(self, team: TeamRecord) -> dict[str, Any]:
        return {
            "team_id": team.team_id,
            "team_name": team.team_name,
            "editable_profiles": [self._profile_to_dict(p) for p in team.editable_profiles],
            "benched_player_names": list(team.benched_player_names),
            "coach_adjustments_by_name": team.coach_adjustments_by_name,
            "saved_scenarios": [self._scenario_to_dict(s) for s in team.saved_scenarios],
            "data_source": team.data_source,
            "csv_path": team.csv_path,
            "adjustments_path": team.adjustments_path,
            "roster_path": team.roster_path,
            "import_history": list(team.import_history),
            "rules_preset": team.rules_preset,
            "rules_config": dict(team.rules_config),
            "aggregate_player_records": {
                player_id: record.to_dict()
                for player_id, record in team.aggregate_player_records.items()
            },
            "player_aliases": dict(team.player_aliases),
            "created_at": team.created_at,
            "updated_at": team.updated_at,
            "owner_user_id": team.owner_user_id,
        }

    def _team_from_dict(self, data: dict[str, Any]) -> TeamRecord:
        return TeamRecord(
            team_id=str(data["team_id"]),
            team_name=str(data["team_name"]),
            editable_profiles=[self._profile_from_dict(p) for p in data.get("editable_profiles", [])],
            benched_player_names=list(data.get("benched_player_names", [])),
            coach_adjustments_by_name=dict(data.get("coach_adjustments_by_name", {})),
            saved_scenarios=[self._scenario_from_dict(s) for s in data.get("saved_scenarios", [])],
            data_source=data.get("data_source"),
            csv_path=data.get("csv_path"),
            adjustments_path=data.get("adjustments_path"),
            roster_path=data.get("roster_path"),
            import_history=list(data.get("import_history", [])),
            rules_preset=str(data.get("rules_preset", "High School")),
            rules_config=dict(data.get("rules_config", {})),
            aggregate_player_records={
                str(player_id): AggregatePlayerRecord.from_dict(record_payload)
                for player_id, record_payload in data.get("aggregate_player_records", {}).items()
            },
            player_aliases={
                str(alias): str(player_id)
                for alias, player_id in data.get("player_aliases", {}).items()
            },
            created_at=float(data.get("created_at", __import__("time").time())),
            updated_at=float(data.get("updated_at", __import__("time").time())),
            owner_user_id=str(data.get("owner_user_id", "")),
        )

    # -----------------------------
    # Team lifecycle
    # -----------------------------
    def create_team(
            self,
            *,
            owner_user_id: str,
            team_name: str,
    ) -> TeamRecord:
        cleaned_owner = str(owner_user_id).strip()
        if not cleaned_owner:
            raise ValueError("owner_user_id is required.")

        cleaned_name = str(team_name).strip()
        if not cleaned_name:
            raise ValueError("Team name cannot be blank.")

        team = TeamRecord(
            team_id=uuid4().hex[:12],
            team_name=cleaned_name,
            owner_user_id=cleaned_owner,
            data_source=None,
            csv_path=None,
            adjustments_path=None,
            roster_path=None,
            editable_profiles=[],
            benched_player_names=[],
            coach_adjustments_by_name={},
            saved_scenarios=[],
            import_history=[],
            rules_preset="High School",
            rules_config={},
            aggregate_player_records={},
            player_aliases={},
        )
        self._save_team(team)
        return team

    def list_teams_for_user(self, owner_user_id: str) -> list[TeamRecord]:
        cleaned_owner = str(owner_user_id).strip()
        if not cleaned_owner:
            return []

        payloads = self._team_repository.list_teams_for_user(cleaned_owner)
        return [self._team_from_dict(payload) for payload in payloads]

    def list_team_summaries_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        cleaned_owner = str(owner_user_id).strip()
        if not cleaned_owner:
            return []

        return self._team_repository.list_team_summaries_for_user(cleaned_owner)

    def get_team_for_user(self, team_id: str, owner_user_id: str) -> TeamRecord:
        payload = self._team_repository.get_team_for_user(team_id, owner_user_id)
        return self._team_from_dict(payload)

    def get_team(self, team_id: str) -> TeamRecord:
        """
        Temporary compatibility shim.

        Prefer:
        - get_team_for_user(...) in UI / auth-facing flows
        - get_team_for_session(...) in session-bound service flows
        """
        return self.load_team(team_id)

    def delete_team_for_user(self, team_id: str, owner_user_id: str) -> None:
        _ = self.get_team_for_user(team_id, owner_user_id)
        self._team_repository.delete_team(team_id)

    def rename_team_for_user(
            self,
            team_id: str,
            *,
            owner_user_id: str,
            new_name: str,
    ) -> TeamRecord:
        team = self.get_team_for_user(team_id, owner_user_id)
        cleaned = str(new_name).strip()
        if not cleaned:
            raise ValueError("Team name cannot be blank.")

        team.team_name = cleaned
        self._save_team(team)

        # Keep any attached in-memory workspace copies in sync so the UI reflects
        # the rename immediately without requiring a team reattach.
        for session in self._sessions.values():
            if session.team_id == team_id and session.workspace_team is not None:
                session.workspace_team.team_name = cleaned
                session.workspace_team.touch()
                session.touch()

        return team

    def attach_session_to_team(self, session_id: str, *, team_id: str) -> OptimizerSession:
        session = self.get_session(session_id)

        if session.workspace_dirty and session.team_id:
            self.flush_workspace_team(session_id)

        team = self.load_team(team_id)  # validate exists

        session.team_id = team_id

        # Sync session-level source pointers to the selected team so downstream
        # roster initialization uses the active team's source, not stale state from
        # the previously selected team.
        session.data_source = team.data_source
        session.csv_path = Path(team.csv_path) if team.csv_path else None
        session.adjustments_path = Path(team.adjustments_path) if team.adjustments_path else None
        session.roster_path = Path(team.roster_path) if team.roster_path else None

        # These are session-scoped raw inputs and should not bleed across teams.
        session.adjustments = None
        session.manual_roster = None

        # Clear transient runtime state when switching teams.
        session.profiles = None
        session.players = None
        session.custom_lineup_names = []
        session.custom_lineup_result = None
        session.result = None

        # Load a fresh in-memory workspace for the selected team.
        session.workspace_team = self._clone_team(team)
        session.workspace_loaded_team_id = team_id
        session.workspace_dirty = False
        session.workspace_mutation_count = 0
        session.workspace_last_flushed_at = time.time()
        session.workspace_last_mutation_at = None

        session.touch()
        return session

    def _require_team(self, session_id: str) -> TeamRecord:
        session = self.get_session(session_id)
        if not session.team_id:
            raise ValueError(f"Session {session_id} is not attached to a team.")
        return self.get_team_for_session(session_id)

    def get_team_for_session(self, session_id: str) -> TeamRecord:
        session = self.get_session(session_id)

        if not session.team_id:
            raise ValueError(f"Session {session_id} is not attached to a team.")

        return self.load_team(session.team_id)

    def _require_session_team(self, session_id: str) -> TeamRecord:
        """
        Return the team currently attached to this session.

        This is the safe internal path for service-layer operations that already
        flow through an attached session rather than a direct user/team lookup.
        """
        session = self.get_session(session_id)

        if not session.team_id:
            raise ValueError(f"Session {session_id} is not attached to a team.")

        return self.load_team(session.team_id)

    def save_team(self, team: TeamRecord) -> None:
        self._save_team(team)

    def _clone_team(self, team: TeamRecord) -> TeamRecord:
        """
        Deep-ish clone through existing serialization helpers so the session
        workspace is isolated from the durable repository copy.
        """
        return self._team_from_dict(self._team_to_dict(team))

    def ensure_workspace_team(self, session_id: str) -> TeamRecord:
        session = self.get_session(session_id)

        if not session.team_id:
            raise ValueError(f"Session {session_id} is not attached to a team.")

        if (
            session.workspace_team is not None
            and session.workspace_loaded_team_id == session.team_id
        ):
            return session.workspace_team

        durable_team = self.load_team(session.team_id)
        session.workspace_team = self._clone_team(durable_team)
        session.workspace_loaded_team_id = durable_team.team_id
        session.workspace_dirty = False
        session.workspace_mutation_count = 0
        session.workspace_last_flushed_at = time.time()
        session.workspace_last_mutation_at = None
        session.touch()
        return session.workspace_team

    def get_workspace_team_for_session(self, session_id: str) -> TeamRecord:
        return self.ensure_workspace_team(session_id)

    def mark_workspace_dirty(self, session_id: str) -> None:
        session = self.get_session(session_id)
        session.workspace_dirty = True
        session.workspace_mutation_count += 1
        session.workspace_last_mutation_at = time.time()
        session.touch()

    def flush_workspace_team(self, session_id: str) -> TeamRecord | None:
        """
        Persist the active in-memory workspace to the repository.

        This is the Phase 2 durable checkpoint method.
        """
        session = self.get_session(session_id)

        if not session.team_id:
            return None

        if session.workspace_team is None:
            durable_team = self.load_team(session.team_id)
            session.workspace_team = self._clone_team(durable_team)
            session.workspace_loaded_team_id = durable_team.team_id

        if session.workspace_loaded_team_id != session.team_id:
            raise ValueError("Workspace team does not match attached session team.")

        self._save_team(session.workspace_team)
        session.workspace_dirty = False
        session.workspace_mutation_count = 0
        session.workspace_last_flushed_at = time.time()
        session.touch()
        return session.workspace_team

    def flush_session_team(self, session_id: str) -> TeamRecord | None:
        """
        Backward-compatible alias used by app.py from Phase 1.
        """
        return self.flush_workspace_team(session_id)

    def refresh_workspace_team(self, session_id: str) -> TeamRecord | None:
        """
        Replace the current in-memory workspace with a fresh clone of the durable
        repository copy for the attached team.

        Use this after durable import/configuration flows that intentionally write
        through to the repository and should become the new working copy.
        """
        session = self.get_session(session_id)

        if not session.team_id:
            return None

        durable_team = self.load_team(session.team_id)
        session.workspace_team = self._clone_team(durable_team)
        session.workspace_loaded_team_id = durable_team.team_id
        session.workspace_dirty = False
        session.workspace_mutation_count = 0
        session.workspace_last_flushed_at = time.time()
        session.workspace_last_mutation_at = None
        session.touch()
        return session.workspace_team

    def _save_team(self, team: TeamRecord) -> None:
        team.touch()
        payload = self._team_to_dict(team)

        existing_ids = set(self._team_repository.list_team_ids())
        if team.team_id in existing_ids:
            self._team_repository.save_team(team.team_id, payload)
        else:
            self._team_repository.create_team(team.team_id, payload)

    def set_team_aggregate_players(
        self,
        team_id: str,
        *,
        aggregate_player_records: dict[str, AggregatePlayerRecord],
        player_aliases: dict[str, str],
    ) -> TeamRecord:
        team = self.load_team(team_id)
        team.aggregate_player_records = dict(aggregate_player_records)
        team.player_aliases = dict(player_aliases)
        self._save_team(team)
        return team

    def clear_team_aggregate_players(self, team_id: str) -> TeamRecord:
        team = self.load_team(team_id)
        team.aggregate_player_records = {}
        team.player_aliases = {}
        self._save_team(team)
        return team

    def seed_team_aggregates_from_gc_records(
        self,
        team_id: str,
        *,
        records: list[dict[str, Any]],
        import_event: dict[str, Any] | None = None,
    ) -> TeamRecord:
        team = self.load_team(team_id)
        aggregate_player_records, player_aliases = build_aggregate_players_from_gc_records(
            records,
            import_event=import_event,
        )
        team.aggregate_player_records = dict(aggregate_player_records)
        team.player_aliases = dict(player_aliases)
        self._save_team(team)
        return team

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
        session.custom_lineup_names = []
        session.custom_lineup_result = None

        # We do NOT automatically clear aggregate players here.
        # A future "replace team source" vs "add game data" UX will decide that explicitly.

        if session.team_id:
            team = self.get_team_for_session(session_id)
            team.data_source = data_source
            team.csv_path = str(csv_path) if csv_path else None
            team.adjustments_path = str(adjustments_path) if adjustments_path else None
            team.roster_path = str(roster_path) if roster_path else None
            self._save_team(team)

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
        team = self.get_workspace_team_for_session(session_id)

        team.editable_profiles = list(profiles)
        team.benched_player_names = []
        team.touch()
        self.mark_workspace_dirty(session_id)

        # Clear session-level transient state only
        session.custom_lineup_names = []
        session.custom_lineup_result = None
        session.touch()

        return session

    def get_editable_roster(
            self,
            session_id: str,
    ) -> list[PlayerProfile]:
        team = self.get_workspace_team_for_session(session_id)
        return list(team.editable_profiles)

    def replace_player_profile(
        self,
        session_id: str,
        *,
        player_name: str,
        profile: PlayerProfile,
    ) -> OptimizerSession:
        session = self.get_session(session_id)
        team = self.get_workspace_team_for_session(session_id)

        replaced = False
        updated_profiles: list[PlayerProfile] = []

        for existing in team.editable_profiles:
            if existing.name == player_name:
                updated_profiles.append(profile)
                replaced = True
            else:
                updated_profiles.append(existing)

        if not replaced:
            raise ValueError(f"Editable roster player not found: {player_name}")

        team.editable_profiles = updated_profiles

        if player_name != profile.name:
            if player_name in team.coach_adjustments_by_name:
                team.coach_adjustments_by_name[profile.name] = team.coach_adjustments_by_name.pop(player_name)

            session.custom_lineup_names = [
                profile.name if name == player_name else name
                for name in session.custom_lineup_names
            ]

            team.benched_player_names = [
                profile.name if name == player_name else name
                for name in team.benched_player_names
            ]

        team.touch()
        self.mark_workspace_dirty(session_id)
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
        team = self.get_workspace_team_for_session(session_id)

        if any(p.name == profile.name for p in team.editable_profiles):
            raise ValueError(f"A player named '{profile.name}' already exists in the editable roster.")

        team.editable_profiles.append(profile)
        team.touch()
        self.mark_workspace_dirty(session_id)

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
        team = self.get_workspace_team_for_session(session_id)
        before = len(team.editable_profiles)

        team.editable_profiles = [
            p for p in team.editable_profiles
            if p.name != player_name
        ]

        if len(team.editable_profiles) == before:
            raise ValueError(f"Editable roster player not found: {player_name}")

        team.benched_player_names = [
            name for name in team.benched_player_names
            if name != player_name
        ]
        team.coach_adjustments_by_name.pop(player_name, None)
        session.custom_lineup_names = [
            name for name in session.custom_lineup_names
            if name != player_name
        ]

        team.touch()
        self.mark_workspace_dirty(session_id)
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
        team = self.get_workspace_team_for_session(session_id)

        valid_names = {p.name for p in team.editable_profiles}
        if player_name not in valid_names:
            raise ValueError(f"Editable roster player not found: {player_name}")

        if player_name not in team.benched_player_names:
            team.benched_player_names.append(player_name)

        session.custom_lineup_names = [
            name for name in session.custom_lineup_names
            if name != player_name
        ]

        team.touch()
        self.mark_workspace_dirty(session_id)
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
        team = self.get_workspace_team_for_session(session_id)

        team.benched_player_names = [
            name for name in team.benched_player_names
            if name != player_name
        ]

        team.touch()
        self.mark_workspace_dirty(session_id)
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
        team = self.get_workspace_team_for_session(session_id)

        benched = set(team.benched_player_names)
        active_profiles = [p for p in team.editable_profiles if p.name not in benched]
        benched_profiles = [p for p in team.editable_profiles if p.name in benched]

        idx = next((i for i, p in enumerate(active_profiles) if p.name == player_name), None)
        if idx is None:
            raise ValueError(f"Active roster player not found: {player_name}")

        if idx > 0:
            active_profiles[idx - 1], active_profiles[idx] = active_profiles[idx], active_profiles[idx - 1]

        team.editable_profiles = active_profiles + benched_profiles
        team.touch()
        self.mark_workspace_dirty(session_id)

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
        team = self.get_workspace_team_for_session(session_id)

        benched = set(team.benched_player_names)
        active_profiles = [p for p in team.editable_profiles if p.name not in benched]
        benched_profiles = [p for p in team.editable_profiles if p.name in benched]

        idx = next((i for i, p in enumerate(active_profiles) if p.name == player_name), None)
        if idx is None:
            raise ValueError(f"Active roster player not found: {player_name}")

        if idx < len(active_profiles) - 1:
            active_profiles[idx + 1], active_profiles[idx] = active_profiles[idx], active_profiles[idx + 1]

        team.editable_profiles = active_profiles + benched_profiles
        team.touch()
        self.mark_workspace_dirty(session_id)

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
        team = self.get_workspace_team_for_session(session_id)

        team.coach_adjustments_by_name[player_name] = {
            str(k): float(v) for k, v in adjustment.items()
        }

        team.touch()
        self.mark_workspace_dirty(session_id)
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
        team = self.get_workspace_team_for_session(session_id)

        team.coach_adjustments_by_name.pop(player_name, None)

        team.touch()
        self.mark_workspace_dirty(session_id)
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
        team = self.get_workspace_team_for_session(session_id)

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

        team.saved_scenarios.append(scenario)
        team.touch()
        self.mark_workspace_dirty(session_id)

        session.touch()
        return scenario

    def list_saved_scenarios(self, session_id: str) -> list[SavedScenario]:
        team = self.get_workspace_team_for_session(session_id)
        return list(team.saved_scenarios)

    def get_saved_scenario(
            self,
            session_id: str,
            scenario_id: str,
    ) -> SavedScenario:
        team = self.get_workspace_team_for_session(session_id)

        for scenario in team.saved_scenarios:
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

        team = self.get_workspace_team_for_session(session_id)
        team.touch()
        self.mark_workspace_dirty(session_id)

        session = self.get_session(session_id)
        session.touch()
        return scenario

    def delete_scenario(
            self,
            session_id: str,
            *,
            scenario_id: str,
    ) -> None:
        team = self.get_workspace_team_for_session(session_id)

        before = len(team.saved_scenarios)
        team.saved_scenarios = [
            s for s in team.saved_scenarios
            if s.scenario_id != scenario_id
        ]

        if len(team.saved_scenarios) == before:
            raise ValueError(f"Saved scenario not found: {scenario_id}")

        team.touch()
        self.mark_workspace_dirty(session_id)

        session = self.get_session(session_id)
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


_SESSION_MANAGER: SessionManager | None = None


def _build_team_repository():
    postgres_dsn = None

    try:
        postgres_dsn = st.secrets["postgres"]["dsn"]
    except Exception:
        postgres_dsn = os.environ.get("TEAM_DB_DSN")

    if postgres_dsn:
        try:
            print("Using PostgresTeamRepository")
            from core.postgres_team_repository import PostgresTeamRepository
            return PostgresTeamRepository(dsn=postgres_dsn)
        except Exception:
            print("Using JsonTeamRepository")
            # Fall back to JSON repository if Postgres support is not installed
            # or not configured correctly yet.
            return JsonTeamRepository()

    return JsonTeamRepository()


def get_session_manager() -> SessionManager:
    global _SESSION_MANAGER
    if _SESSION_MANAGER is None:
        _SESSION_MANAGER = SessionManager(team_repository=_build_team_repository())
    return _SESSION_MANAGER