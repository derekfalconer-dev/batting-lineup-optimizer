from __future__ import annotations

import json
from typing import Any

import psycopg

from core.team_repository import TeamRepository


class PostgresTeamRepository(TeamRepository):
    """
    Postgres-backed TeamRepository implementation.

    Stores the full TeamRecord payload in JSONB while also indexing key
    ownership fields separately for fast owner-scoped queries.
    """

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._conn = None

    def _connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def create_team(self, team_id: str, payload: dict[str, Any]) -> None:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO teams (
                    team_id,
                    owner_user_id,
                    team_name,
                    payload,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    team_id,
                    payload["owner_user_id"],
                    payload["team_name"],
                    json.dumps(payload),
                    payload.get("created_at"),
                    payload.get("updated_at"),
                ),
            )

    def save_team(self, team_id: str, payload: dict[str, Any]) -> None:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE teams
                SET owner_user_id = %s,
                    team_name = %s,
                    payload = %s::jsonb,
                    created_at = %s,
                    updated_at = %s
                WHERE team_id = %s
                """,
                (
                    payload["owner_user_id"],
                    payload["team_name"],
                    json.dumps(payload),
                    payload.get("created_at"),
                    payload.get("updated_at"),
                    team_id,
                ),
            )

    def load_team(self, team_id: str) -> dict[str, Any]:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM teams WHERE team_id = %s",
                (team_id,),
            )
            row = cur.fetchone()

        if not row:
            raise ValueError(f"Team not found: {team_id}")

        payload = row[0]
        return payload if isinstance(payload, dict) else json.loads(payload)

    def list_teams_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload
                FROM teams
                WHERE owner_user_id = %s
                ORDER BY updated_at DESC NULLS LAST
                """,
                (owner_user_id,),
            )
            rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            payload = row[0]
            results.append(payload if isinstance(payload, dict) else json.loads(payload))

        return results

    def list_team_summaries_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT team_id, team_name, updated_at
                FROM teams
                WHERE owner_user_id = %s
                ORDER BY updated_at DESC NULLS LAST
                """,
                (owner_user_id,),
            )
            rows = cur.fetchall()

        return [
            {
                "team_id": str(row[0]),
                "team_name": str(row[1]),
                "updated_at": float(row[2] or 0.0),
            }
            for row in rows
        ]

    def get_team_for_user(self, team_id: str, owner_user_id: str) -> dict[str, Any]:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload
                FROM teams
                WHERE team_id = %s AND owner_user_id = %s
                """,
                (team_id, owner_user_id),
            )
            row = cur.fetchone()

        if not row:
            raise ValueError("Team not found for this user.")

        payload = row[0]
        return payload if isinstance(payload, dict) else json.loads(payload)

    def list_team_ids(self) -> list[str]:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT team_id FROM teams ORDER BY updated_at DESC NULLS LAST"
            )
            rows = cur.fetchall()

        return [str(row[0]) for row in rows]

    def delete_team(self, team_id: str) -> None:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM teams WHERE team_id = %s",
                (team_id,),
            )
