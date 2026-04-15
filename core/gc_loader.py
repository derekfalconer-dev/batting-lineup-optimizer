from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping, Sequence


SECTION_BREAKS = {"Batting", "Pitching", "Fielding"}


def load_gamechanger_records(
    csv_path: str | Path,
    *,
    min_pa: int = 5,
    include_names: Sequence[str] | None = None,
    exclude_names: Sequence[str] | None = None,
    name_format: str = "full",
) -> list[dict[str, Any]]:
    """
    Parse a GameChanger team stats CSV and return structured batting records.

    This module is intentionally parser-only.
    It does NOT create PlayerProfile or Player objects.
    """
    path = Path(csv_path)
    rows = _read_csv_rows(path)
    if len(rows) < 3:
        raise ValueError(f"CSV appears too short to be a valid GameChanger export: {path}")

    section_row = rows[0]
    stat_row = rows[1]
    columns = _build_columns(section_row, stat_row)
    data_rows = rows[2:]

    include_set = {n.strip().lower() for n in include_names} if include_names else None
    exclude_set = {n.strip().lower() for n in exclude_names} if exclude_names else set()

    records: list[dict[str, Any]] = []

    for raw_row in data_rows:
        if _is_empty_row(raw_row):
            continue
        if _is_glossary_row(raw_row):
            break

        row = _row_to_dict(columns, raw_row)
        name = _build_player_name(row, name_format=name_format)

        if not name:
            continue

        if include_set is not None and name.lower() not in include_set:
            continue
        if name.lower() in exclude_set:
            continue

        pa = _safe_int(row.get("PA"), default=0)
        if pa < min_pa:
            continue

        batting_record = _extract_batting_stats(row)
        batting_record["name"] = name
        batting_record["first"] = str(row.get("First", "")).strip()
        batting_record["last"] = str(row.get("Last", "")).strip()
        batting_record["number"] = str(row.get("Number", "")).strip()
        batting_record["source_file"] = str(path)
        batting_record["raw_row"] = dict(row)

        records.append(batting_record)

    return records


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------

def _read_csv_rows(csv_path: str | Path) -> list[list[str]]:
    path = Path(csv_path)
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return [list(row) for row in csv.reader(f)]


def _build_columns(section_row: Sequence[str], stat_row: Sequence[str]) -> list[str]:
    """
    Build stable column names from the 2-row GameChanger header.
    """
    columns: list[str] = []
    current_section = "meta"

    for i, stat_name in enumerate(stat_row):
        stat_name = (stat_name or "").strip()
        section_name = (section_row[i] if i < len(section_row) else "").strip()

        if section_name in SECTION_BREAKS:
            current_section = section_name.lower()

        if i < 3:
            columns.append(stat_name if stat_name else f"meta_{i}")
            continue

        if not stat_name:
            columns.append(f"{current_section}_{i}")
            continue

        # Keep batting stat names canonical for downstream conversion
        if current_section == "batting":
            columns.append(stat_name)
        else:
            columns.append(f"{current_section}_{stat_name}")

    return columns


def _row_to_dict(columns: Sequence[str], row: Sequence[str]) -> dict[str, str]:
    padded = list(row) + [""] * max(0, len(columns) - len(row))
    return {col: (padded[i].strip() if i < len(padded) else "") for i, col in enumerate(columns)}


def _extract_batting_stats(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    Pull just the batting-facing fields needed by downstream profile conversion.
    """
    wanted = [
        "PA",
        "AB",
        "AVG",
        "OBP",
        "SLG",
        "H",
        "1B",
        "2B",
        "3B",
        "HR",
        "BB",
        "SO",
        "SB",
        "CS",
        "R",
        "RBI",
        "HBP",
        "SF",
        "SAC",
        "TB",
        "ROE",
    ]
    return {k: row.get(k, "") for k in wanted}


def _build_player_name(row: Mapping[str, Any], *, name_format: str = "full") -> str:
    first = str(row.get("First", "")).strip()
    last = str(row.get("Last", "")).strip()

    if name_format == "first":
        return first
    if name_format == "full":
        if first and last:
            return f"{first} {last}"
        return first or last

    raise ValueError(f"Unsupported name_format: {name_format}")


def _is_empty_row(row: Sequence[str]) -> bool:
    return not any(str(cell).strip() for cell in row)


def _is_glossary_row(row: Sequence[str]) -> bool:
    first_cell = str(row[0]).strip().lower() if row else ""
    return first_cell == "glossary"


def _safe_int(value: Any, *, default: int = 0) -> int:
    if value in (None, "", "-"):
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default