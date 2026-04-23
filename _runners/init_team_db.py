from __future__ import annotations

import os

import psycopg
import streamlit as st

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DDL = """
CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    team_name TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at DOUBLE PRECISION,
    updated_at DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_teams_owner_user_id
    ON teams (owner_user_id);

CREATE INDEX IF NOT EXISTS idx_teams_updated_at
    ON teams (updated_at DESC);
"""


from pathlib import Path
import os
import tomllib
import streamlit as st


def _resolve_dsn() -> str:
    try:
        return st.secrets["postgres"]["dsn"]
    except Exception:
        pass

    dsn = os.environ.get("TEAM_DB_DSN")
    if dsn:
        return dsn

    project_root = Path(__file__).resolve().parents[1]
    secrets_path = project_root / ".streamlit" / "secrets.toml"

    if secrets_path.exists():
        with secrets_path.open("rb") as f:
            secrets = tomllib.load(f)
        dsn = secrets.get("postgres", {}).get("dsn")
        if dsn:
            return dsn

    raise ValueError(
        f"No Postgres DSN found in st.secrets, TEAM_DB_DSN, or {secrets_path}"
    )


def main() -> None:
    dsn = _resolve_dsn()

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(DDL)

    print("Initialized teams table.")


if __name__ == "__main__":
    main()