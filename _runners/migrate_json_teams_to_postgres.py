from __future__ import annotations

import os

import streamlit as st

from core.json_team_repository import JsonTeamRepository
from core.postgres_team_repository import PostgresTeamRepository


from pathlib import Path
import os
import tomllib
import streamlit as st

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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

    source_repo = JsonTeamRepository()
    target_repo = PostgresTeamRepository(dsn=dsn)

    migrated = 0
    skipped = 0

    for team_id in source_repo.list_team_ids():
        payload = source_repo.load_team(team_id)

        try:
            target_repo.load_team(team_id)
            skipped += 1
            continue
        except Exception:
            pass

        target_repo.create_team(team_id, payload)
        migrated += 1

    print(f"Migrated {migrated} teams. Skipped {skipped} existing teams.")


if __name__ == "__main__":
    main()