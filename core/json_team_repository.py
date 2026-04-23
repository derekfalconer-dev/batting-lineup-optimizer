from __future__ import annotations

from typing import Any

from core.team_repository import TeamRepository
from core.team_store import (
    create_team_file,
    save_team as save_team_file,
    load_team as load_team_file,
    list_team_ids as list_team_ids_from_store,
    delete_team as delete_team_file,
)


class JsonTeamRepository(TeamRepository):
    """
    JSON-backed repository for TeamRecord payloads.

    This preserves current prototype behavior while giving the app a
    swappable persistence seam for future Postgres migration.
    """

    def create_team(self, team_id: str, payload: dict[str, Any]) -> None:
        create_team_file(team_id, payload)

    def save_team(self, team_id: str, payload: dict[str, Any]) -> None:
        save_team_file(team_id, payload)

    def load_team(self, team_id: str) -> dict[str, Any]:
        return load_team_file(team_id)

    def list_teams_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for team_id in self.list_team_ids():
            payload = self.load_team(team_id)
            if str(payload.get("owner_user_id", "")) == str(owner_user_id):
                results.append(payload)

        results.sort(
            key=lambda t: (
                str(t.get("team_name", "")).strip().lower() == "untitled team",
                str(t.get("team_name", "")).lower(),
                -float(t.get("updated_at", 0.0) or 0.0),
            )
        )
        return results

    def list_team_summaries_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for team_id in self.list_team_ids():
            payload = self.load_team(team_id)
            if str(payload.get("owner_user_id", "")) == str(owner_user_id):
                results.append(
                    {
                        "team_id": str(payload.get("team_id", "")),
                        "team_name": str(payload.get("team_name", "")),
                        "updated_at": float(payload.get("updated_at", 0.0) or 0.0),
                    }
                )

        results.sort(
            key=lambda t: (
                str(t.get("team_name", "")).strip().lower() == "untitled team",
                str(t.get("team_name", "")).lower(),
                -float(t.get("updated_at", 0.0) or 0.0),
            )
        )
        return results

    def get_team_for_user(self, team_id: str, owner_user_id: str) -> dict[str, Any]:
        payload = self.load_team(team_id)
        if str(payload.get("owner_user_id", "")) != str(owner_user_id):
            raise ValueError("Team not found for this user.")
        return payload

    def list_team_ids(self) -> list[str]:
        return list_team_ids_from_store()

    def delete_team(self, team_id: str) -> None:
        delete_team_file(team_id)