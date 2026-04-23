from __future__ import annotations

import json
import os
from typing import Any

import streamlit as st


class AnalyticsLogger:
    def log_event(
        self,
        *,
        event_type: str,
        user_id: str | None = None,
        user_email: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError


class NullAnalyticsLogger(AnalyticsLogger):
    def log_event(
        self,
        *,
        event_type: str,
        user_id: str | None = None,
        user_email: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return


class PostgresAnalyticsLogger(AnalyticsLogger):
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._conn = None

    def _connect(self):
        import psycopg

        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def log_event(
        self,
        *,
        event_type: str,
        user_id: str | None = None,
        user_email: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into app_events (
                    event_type,
                    user_id,
                    user_email,
                    session_id,
                    team_id,
                    metadata
                )
                values (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    str(event_type),
                    user_id,
                    user_email,
                    session_id,
                    team_id,
                    json.dumps(metadata or {}),
                ),
            )


_ANALYTICS_LOGGER: AnalyticsLogger | None = None


def _build_analytics_logger() -> AnalyticsLogger:
    postgres_dsn = None

    try:
        postgres_dsn = st.secrets["postgres"]["dsn"]
    except Exception:
        postgres_dsn = os.environ.get("TEAM_DB_DSN")

    if not postgres_dsn:
        return NullAnalyticsLogger()

    try:
        return PostgresAnalyticsLogger(postgres_dsn)
    except Exception:
        return NullAnalyticsLogger()


def get_analytics_logger() -> AnalyticsLogger:
    global _ANALYTICS_LOGGER
    if _ANALYTICS_LOGGER is None:
        _ANALYTICS_LOGGER = _build_analytics_logger()
    return _ANALYTICS_LOGGER


def safe_log_event(
    *,
    event_type: str,
    user_id: str | None = None,
    user_email: str | None = None,
    session_id: str | None = None,
    team_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        get_analytics_logger().log_event(
            event_type=event_type,
            user_id=user_id,
            user_email=user_email,
            session_id=session_id,
            team_id=team_id,
            metadata=metadata,
        )
    except Exception as exc:
        print(f"[analytics] failed to log event {event_type!r}: {exc}")