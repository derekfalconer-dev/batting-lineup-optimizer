from __future__ import annotations

from typing import Protocol, Any


class TeamRepository(Protocol):
    """
    Persistence boundary for durable team records.

    SessionManager should depend on this interface rather than on
    JSON files, Postgres, or any specific storage backend.
    """

    def create_team(self, team_id: str, payload: dict[str, Any]) -> None:
        ...

    def save_team(self, team_id: str, payload: dict[str, Any]) -> None:
        ...

    def load_team(self, team_id: str) -> dict[str, Any]:
        ...

    def list_teams_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        ...

    def get_team_for_user(self, team_id: str, owner_user_id: str) -> dict[str, Any]:
        ...

    def list_team_ids(self) -> list[str]:
        ...

    def delete_team(self, team_id: str) -> None:
        ...

    def list_team_summaries_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        ...