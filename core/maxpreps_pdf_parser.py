from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MaxPrepsBattingRow:
    number: str
    name: str
    grade: str | None = None

    games_played: int | None = None
    avg: float | None = None
    plate_appearances: int | None = None
    at_bats: int | None = None
    runs: int | None = None
    hits: int | None = None
    rbi: int | None = None
    doubles: int | None = None
    triples: int | None = None
    homers: int | None = None

    walks: int | None = None
    strikeouts: int | None = None
    hbp: int | None = None
    roe: int | None = None
    fielder_choice: int | None = None
    lob: int | None = None
    obp: float | None = None
    slg: float | None = None
    ops: float | None = None

    stolen_bases: int | None = None
    stolen_base_attempts: int | None = None


@dataclass(slots=True)
class MaxPrepsPitchingRow:
    number: str
    name: str
    grade: str | None = None

    era: float | None = None
    wins: int | None = None
    losses: int | None = None
    appearances: int | None = None
    games_started: int | None = None

    innings_pitched: float | None = None
    hits_allowed: int | None = None
    runs_allowed: int | None = None
    earned_runs: int | None = None
    walks: int | None = None
    strikeouts: int | None = None
    doubles_allowed: int | None = None
    triples_allowed: int | None = None
    homers_allowed: int | None = None
    batters_faced: int | None = None
    at_bats_against: int | None = None

    opponent_ba: float | None = None
    opponent_obp: float | None = None
    wild_pitches: int | None = None
    hbp: int | None = None
    pitches: int | None = None


@dataclass(slots=True)
class MaxPrepsOpponentReport:
    team_name: str | None = None
    season: str | None = None
    overall_record: str | None = None

    fielding_pct: float | None = None
    fielding_total_chances: int | None = None
    fielding_errors: int | None = None

    team_era: float | None = None
    team_ip: float | None = None
    team_walks: int | None = None
    team_strikeouts: int | None = None
    team_batters_faced: int | None = None
    team_oba: float | None = None
    team_obp_allowed: float | None = None

    batters: list[MaxPrepsBattingRow] = field(default_factory=list)
    pitchers: list[MaxPrepsPitchingRow] = field(default_factory=list)
    raw_text: str = ""

    # Parser health / UI diagnostics. These are intentionally lightweight
    # so imports can succeed with warnings instead of silently failing.
    parser_warnings: list[str] = field(default_factory=list)
    parser_stats: dict[str, Any] = field(default_factory=dict)


def parse_maxpreps_pdf(pdf_path: str | Path) -> MaxPrepsOpponentReport:
    """
    Parse a MaxPreps printable baseball stats PDF into a structured opponent report.

    Phase 3A intentionally does not persist anything or alter simulations.
    It only extracts:
    - team identity
    - team defense summary
    - team pitching totals
    - per-pitcher pitching rows
    """
    path = Path(pdf_path)
    text = _extract_pdf_text(path)

    report = MaxPrepsOpponentReport(raw_text=text)
    report.team_name = _extract_team_name(text)
    report.season = _extract_season(text)
    report.overall_record = _extract_overall_record(text)

    _parse_batting_rows(text, report)
    _parse_fielding_totals(text, report)
    _parse_pitching_totals(text, report)
    _parse_pitching_rows(text, report)

    return report


def report_to_dict(report: MaxPrepsOpponentReport) -> dict[str, Any]:
    return {
        "team_name": report.team_name,
        "season": report.season,
        "overall_record": report.overall_record,
        "fielding_pct": report.fielding_pct,
        "fielding_total_chances": report.fielding_total_chances,
        "fielding_errors": report.fielding_errors,
        "team_era": report.team_era,
        "team_ip": report.team_ip,
        "team_walks": report.team_walks,
        "team_strikeouts": report.team_strikeouts,
        "team_batters_faced": report.team_batters_faced,
        "team_oba": report.team_oba,
        "team_obp_allowed": report.team_obp_allowed,
        "parser_warnings": list(getattr(report, "parser_warnings", []) or []),
        "parser_stats": dict(getattr(report, "parser_stats", {}) or {}),
        "batters": [
            {
                "number": row.number,
                "name": row.name,
                "grade": row.grade,
                "GP": row.games_played,
                "Avg": row.avg,
                "PA": row.plate_appearances,
                "AB": row.at_bats,
                "R": row.runs,
                "H": row.hits,
                "RBI": row.rbi,
                "2B": row.doubles,
                "3B": row.triples,
                "HR": row.homers,
                "BB": row.walks,
                "K": row.strikeouts,
                "HBP": row.hbp,
                "ROE": row.roe,
                "FC": row.fielder_choice,
                "LOB": row.lob,
                "OBP": row.obp,
                "SLG": row.slg,
                "OPS": row.ops,
                "SB": row.stolen_bases,
                "SBA": row.stolen_base_attempts,
                "games_played": row.games_played,
                "avg": row.avg,
                "plate_appearances": row.plate_appearances,
                "at_bats": row.at_bats,
                "runs": row.runs,
                "hits": row.hits,
                "rbi": row.rbi,
                "doubles": row.doubles,
                "triples": row.triples,
                "homers": row.homers,
                "walks": row.walks,
                "strikeouts": row.strikeouts,
                "stolen_bases": row.stolen_bases,
                "stolen_base_attempts": row.stolen_base_attempts,
            }
            for row in report.batters
        ],
        "pitchers": [
            {
                "number": row.number,
                "name": row.name,
                "grade": row.grade,
                "era": row.era,
                "wins": row.wins,
                "losses": row.losses,
                "appearances": row.appearances,
                "games_started": row.games_started,
                "innings_pitched": row.innings_pitched,
                "hits_allowed": row.hits_allowed,
                "runs_allowed": row.runs_allowed,
                "earned_runs": row.earned_runs,
                "walks": row.walks,
                "strikeouts": row.strikeouts,
                "doubles_allowed": row.doubles_allowed,
                "triples_allowed": row.triples_allowed,
                "homers_allowed": row.homers_allowed,
                "batters_faced": row.batters_faced,
                "at_bats_against": row.at_bats_against,
                "opponent_ba": row.opponent_ba,
                "opponent_obp": row.opponent_obp,
                "wild_pitches": row.wild_pitches,
                "hbp": row.hbp,
                "pitches": row.pitches,
            }
            for row in report.pitchers
        ],
    }


def _extract_pdf_text(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError(
            "MaxPreps PDF parsing requires PyMuPDF. Add `PyMuPDF` to requirements.txt."
        ) from exc

    pieces: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            pieces.append(page.get_text("text"))

    return "\n".join(pieces)


def _extract_team_name(text: str) -> str | None:
    match = re.search(r"^\s*(.+?)\s+Baseball Team Season Stats", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _extract_season(text: str) -> str | None:
    match = re.search(r"Baseball Team Season Stats\s*\(([^)]+)\)", text)
    return match.group(1).strip() if match else None


def _extract_overall_record(text: str) -> str | None:
    match = re.search(r"Overall\s+([0-9]+-[0-9]+)", text)
    return match.group(1).strip() if match else None


def _parse_fielding_totals(text: str, report: MaxPrepsOpponentReport) -> None:
    fielding_section = _section_between(text, "Fielding", "Pitching")
    if not fielding_section:
        return

    match = re.search(
        r"Season Totals\s+[0-9]+\s+\.?([0-9]{3})\s+([0-9]+)\s+[0-9]+\s+[0-9]+\s+([0-9]+)",
        fielding_section,
    )
    if not match:
        return

    report.fielding_pct = _parse_decimal(match.group(1))
    report.fielding_total_chances = _safe_int(match.group(2))
    report.fielding_errors = _safe_int(match.group(3))


def _parse_pitching_totals(text: str, report: MaxPrepsOpponentReport) -> None:
    pitching_section = _section_from(text, "Pitching")
    if not pitching_section:
        return

    # First pitching totals line:
    # Season Totals 2.88 13 9 .591 56 22 4 2 3 2 1
    match_summary = re.search(r"Season Totals\s+([0-9.]+)\s+[0-9]+\s+[0-9]+", pitching_section)
    if match_summary:
        report.team_era = _safe_float(match_summary.group(1))

    # IP/H/R/ER/BB/K/2B/3B/HR/BF/AB totals line:
    # Season Totals 146 109 83 60 64 213 28 7 2 658 560
    match_core = re.search(
        r"Season Totals\s+([0-9.]+)\s+[0-9]+\s+[0-9]+\s+[0-9]+\s+([0-9]+)\s+([0-9]+)\s+"
        r"[0-9]+\s+[0-9]+\s+[0-9]+\s+([0-9]+)\s+[0-9]+",
        pitching_section,
    )
    if match_core:
        report.team_ip = _parse_innings(match_core.group(1))
        report.team_walks = _safe_int(match_core.group(2))
        report.team_strikeouts = _safe_int(match_core.group(3))
        report.team_batters_faced = _safe_int(match_core.group(4))

    # Final pitching totals line:
    # Season Totals .195 .305 32 27 4 3 2463 3
    match_rates = re.search(
        r"Season Totals\s+(\.[0-9]+|[0-9.]+)\s+(\.[0-9]+|[0-9.]+)\s+[0-9]+\s+[0-9]+\s+[0-9]+\s+[0-9]+\s+[0-9]+",
        pitching_section,
    )
    if match_rates:
        report.team_oba = _safe_float(match_rates.group(1))
        report.team_obp_allowed = _safe_float(match_rates.group(2))


_NAME_LINE_RE = re.compile(
    r"^[A-Z]\.\s+[A-Za-z'’\-\s]+(?:\([^)]+\))?$"
)


def _line_is_int(value: str) -> bool:
    return re.fullmatch(r"\d+", str(value).strip()) is not None


def _line_looks_like_name(value: str) -> bool:
    return _NAME_LINE_RE.match(str(value).strip()) is not None


def _line_starts_player_row(lines: list[str], idx: int) -> bool:
    if idx >= len(lines):
        return False

    line = lines[idx].strip()

    if line == "N. Player":
        return True

    return (
        _line_is_int(line)
        and idx + 1 < len(lines)
        and _line_looks_like_name(lines[idx + 1])
    )


def _line_starts_pitcher_row(lines: list[str], idx: int) -> bool:
    return _line_starts_player_row(lines, idx)


def _parse_name_and_grade(value: str) -> tuple[str, str | None]:
    cleaned = " ".join(str(value).strip().split())
    match = re.match(
        r"^(?P<name>[A-Z]\.\s+[A-Za-z'’\-\s]+?)(?:\s*\((?P<grade>[^)]+)\))?$",
        cleaned,
    )
    if not match:
        return cleaned, None

    return " ".join(match.group("name").split()), match.group("grade")


def _parse_batting_rows(text: str, report: MaxPrepsOpponentReport) -> None:
    """
    Parse MaxPreps batting and baserunning rows defensively.

    MaxPreps batting is emitted as multiple table-cell streams:
    - GP/Avg/PA/AB/R/H/RBI/2B/3B/HR
    - BB/K/HBP/ROE/FC/LOB/OBP/SLG/OPS
    - SB/SBA in the Baserunning section

    Rows are merged by jersey number + normalized name.
    """
    batting_section = _section_between(text, "Batting", "Baserunning")
    baserunning_section = _section_between(text, "Baserunning", "Fielding")

    merged: dict[str, MaxPrepsBattingRow] = {}
    fragments_seen = 0
    baserunning_rows_merged = 0
    row_shape_counts: dict[str, int] = {}

    if batting_section:
        lines = [line.strip() for line in batting_section.splitlines() if line.strip()]
        active_table: str | None = None
        idx = 0

        while idx < len(lines):
            line = lines[idx]

            if line == "#" and idx + 1 < len(lines) and lines[idx + 1] == "Athlete Name":
                header: list[str] = []
                idx += 2

                while (
                    idx < len(lines)
                    and not _line_starts_player_row(lines, idx)
                    and lines[idx] not in {"#", "Season Totals"}
                ):
                    header.append(lines[idx])
                    idx += 1

                header_set = set(header)

                if "Avg" in header_set and "PA" in header_set and "AB" in header_set:
                    active_table = "batting_summary"
                elif "OBP" in header_set and "SLG" in header_set and "OPS" in header_set:
                    active_table = "batting_rates"
                else:
                    active_table = None

                continue

            if line == "Season Totals":
                idx += 1
                while idx < len(lines) and lines[idx] != "#":
                    idx += 1
                continue

            if active_table and _line_starts_player_row(lines, idx):
                row, idx = _read_player_stat_fragment(lines, idx)
                if row is None:
                    continue

                number, name, grade, stat_tokens = row

                if not stat_tokens:
                    continue

                fragments_seen += 1
                row_shape_counts[active_table] = row_shape_counts.get(active_table, 0) + 1

                batter = _get_or_create_batter(merged, number, name, grade)

                if active_table == "batting_summary":
                    _merge_batting_summary_tokens(batter, stat_tokens)
                elif active_table == "batting_rates":
                    _merge_batting_rates_tokens(batter, stat_tokens)

                continue

            idx += 1
    else:
        report.parser_warnings.append("No Batting section found in MaxPreps PDF.")

    if baserunning_section:
        lines = [line.strip() for line in baserunning_section.splitlines() if line.strip()]
        active_table = False
        idx = 0

        while idx < len(lines):
            line = lines[idx]

            if line == "#" and idx + 1 < len(lines) and lines[idx + 1] == "Athlete Name":
                header: list[str] = []
                idx += 2

                while (
                    idx < len(lines)
                    and not _line_starts_player_row(lines, idx)
                    and lines[idx] not in {"#", "Season Totals"}
                ):
                    header.append(lines[idx])
                    idx += 1

                header_set = set(header)
                active_table = "SB" in header_set and "SBA" in header_set
                continue

            if line == "Season Totals":
                idx += 1
                while idx < len(lines) and lines[idx] != "#":
                    idx += 1
                continue

            if active_table and _line_starts_player_row(lines, idx):
                row, idx = _read_player_stat_fragment(lines, idx)
                if row is None:
                    continue

                number, name, grade, stat_tokens = row
                if not stat_tokens:
                    continue

                batter = _get_or_create_batter(merged, number, name, grade)
                _merge_baserunning_tokens(batter, stat_tokens)
                baserunning_rows_merged += 1
                row_shape_counts["baserunning"] = row_shape_counts.get("baserunning", 0) + 1
                continue

            idx += 1

    batters = []
    skipped_zero_rows = 0

    for row in merged.values():
        if _is_zero_batting_row(row):
            skipped_zero_rows += 1
            continue
        if _has_batting_evidence(row):
            batters.append(row)

    batters.sort(
        key=lambda r: (
            -(r.plate_appearances or 0),
            -(r.at_bats or 0),
            -(r.hits or 0),
            r.name,
        )
    )

    report.batters = batters

    if batting_section and not batters:
        report.parser_warnings.append(
            "No usable batting rows were found. The PDF may use a MaxPreps batting layout this parser does not recognize yet."
        )

    report.parser_stats.update(
        {
            "batting_row_fragments_seen": fragments_seen,
            "batting_rows_merged": len(merged),
            "batters_loaded": len(batters),
            "baserunning_rows_merged": baserunning_rows_merged,
            "batting_row_shape_counts": row_shape_counts,
            "skipped_zero_batting_rows": skipped_zero_rows,
        }
    )


def _read_player_stat_fragment(
    lines: list[str],
    idx: int,
) -> tuple[tuple[str, str, str | None, list[str]] | None, int]:
    number = ""

    if lines[idx] == "N. Player":
        raw_name = "N. Player"
        idx += 1
    else:
        number = lines[idx]
        raw_name = lines[idx + 1]
        idx += 2

    raw_name = " ".join(raw_name.strip().split())
    if raw_name.lower() == "n. player":
        while (
            idx < len(lines)
            and lines[idx] != "#"
            and lines[idx] != "Season Totals"
            and not _line_starts_player_row(lines, idx)
        ):
            idx += 1

        return None, idx

    name, grade = _parse_name_and_grade(raw_name)

    stat_tokens: list[str] = []
    while (
        idx < len(lines)
        and lines[idx] != "#"
        and lines[idx] != "Season Totals"
        and not _line_starts_player_row(lines, idx)
    ):
        if re.fullmatch(r"[0-9.]+", lines[idx]):
            stat_tokens.append(lines[idx])
        idx += 1

    return (number, name, grade, stat_tokens), idx


def _get_or_create_batter(
    merged: dict[str, MaxPrepsBattingRow],
    number: str,
    name: str,
    grade: str | None,
) -> MaxPrepsBattingRow:
    key = _pitcher_key(number, name)
    row = merged.get(key)

    if row is None:
        row = MaxPrepsBattingRow(
            number=number,
            name=name,
            grade=grade,
        )
        merged[key] = row
    elif not row.grade and grade:
        row.grade = grade

    return row


def _merge_batting_summary_tokens(row: MaxPrepsBattingRow, tokens: list[str]) -> None:
    """
    Merge GP/Avg/PA/AB/R/H/RBI/2B/3B/HR from the first batting table.

    This favors the common complete row shape but remains permissive when
    PDF extraction drops trailing zero cells.
    """
    if len(tokens) < 6:
        return

    row.games_played = _safe_int(tokens[0]) if row.games_played is None else row.games_played
    row.avg = _parse_decimal(tokens[1]) if row.avg is None else row.avg
    row.plate_appearances = _safe_int(tokens[2]) if row.plate_appearances is None else row.plate_appearances
    row.at_bats = _safe_int(tokens[3]) if row.at_bats is None else row.at_bats
    row.runs = _safe_int(tokens[4]) if row.runs is None else row.runs
    row.hits = _safe_int(tokens[5]) if row.hits is None else row.hits

    if len(tokens) >= 7:
        row.rbi = _safe_int(tokens[6]) if row.rbi is None else row.rbi
    if len(tokens) >= 8:
        row.doubles = _safe_int(tokens[7]) if row.doubles is None else row.doubles
    if len(tokens) >= 9:
        row.triples = _safe_int(tokens[8]) if row.triples is None else row.triples
    if len(tokens) >= 10:
        row.homers = _safe_int(tokens[9]) if row.homers is None else row.homers


def _merge_batting_rates_tokens(row: MaxPrepsBattingRow, tokens: list[str]) -> None:
    """
    Merge BB/K/HBP/ROE/FC/LOB/OBP/SLG/OPS from the second batting table.

    Header is:
      GP SF SH/B BB K HBP ROE FC LOB OBP SLG OPS

    MaxPreps/PyMuPDF can omit zero-value cells, so OBP/SLG/OPS are anchored
    from the final three decimal/rate-looking tokens instead of fixed indexes.
    """
    if len(tokens) < 4:
        return

    rate_indexes = [
        idx
        for idx, token in enumerate(tokens)
        if _looks_like_batting_rate_token(token)
    ]

    rate_tokens: list[str] = []
    rate_tail_start_idx: int | None = None

    if len(rate_indexes) >= 3:
        final_rate_indexes = rate_indexes[-3:]
        rate_tail_start_idx = final_rate_indexes[0]
        rate_tokens = [tokens[idx] for idx in final_rate_indexes]
    elif len(tokens) >= 3 and all(_looks_like_rate(tok) for tok in tokens[-3:]):
        rate_tail_start_idx = len(tokens) - 3
        rate_tokens = tokens[-3:]

    int_prefix_source = tokens[:rate_tail_start_idx] if rate_tail_start_idx is not None else tokens
    int_prefix = [
        _safe_int(token)
        for token in int_prefix_source
        if re.fullmatch(r"\d+", str(token).strip())
    ]
    int_prefix = [value for value in int_prefix if value is not None]

    if int_prefix:
        row.games_played = int_prefix[0] if row.games_played is None else row.games_played

    stat_ints = int_prefix[1:] if int_prefix else []

    # The matchup model relies most on BB/K/HBP/ROE. MaxPreps emits this table as:
    #   GP SF SH/B BB K HBP ROE FC LOB OBP SLG OPS
    # but PyMuPDF inconsistently keeps or drops zero SF/SH/B/FC/LOB cells. Infer
    # the core offensive counts before filling less-important FC/LOB.
    core_stat_ints = stat_ints

    if len(stat_ints) >= 6 and stat_ints[0] <= 2 and stat_ints[1] <= 2:
        core_stat_ints = stat_ints[2:]
    elif len(stat_ints) >= 5 and stat_ints[0] <= 2:
        core_stat_ints = stat_ints[1:]

    if len(core_stat_ints) >= 1:
        row.walks = core_stat_ints[0] if row.walks is None else row.walks
    if len(core_stat_ints) >= 2:
        row.strikeouts = core_stat_ints[1] if row.strikeouts is None else row.strikeouts
    if len(core_stat_ints) >= 3:
        row.hbp = core_stat_ints[2] if row.hbp is None else row.hbp
    if len(core_stat_ints) >= 4:
        row.roe = core_stat_ints[3] if row.roe is None else row.roe
    if len(core_stat_ints) >= 5:
        row.fielder_choice = core_stat_ints[4] if row.fielder_choice is None else row.fielder_choice
    if len(core_stat_ints) >= 6:
        row.lob = core_stat_ints[5] if row.lob is None else row.lob

    if rate_tokens:
        row.obp = _parse_decimal(rate_tokens[0]) if row.obp is None else row.obp
        row.slg = _parse_decimal(rate_tokens[1]) if row.slg is None else row.slg
        row.ops = _parse_decimal(rate_tokens[2]) if row.ops is None else row.ops


def _merge_baserunning_tokens(row: MaxPrepsBattingRow, tokens: list[str]) -> None:
    """
    Merge GP/SB/SBA from the baserunning table.
    """
    if len(tokens) < 3:
        return

    row.games_played = _safe_int(tokens[0]) if row.games_played is None else row.games_played
    row.stolen_bases = _safe_int(tokens[1]) if row.stolen_bases is None else row.stolen_bases
    row.stolen_base_attempts = _safe_int(tokens[2]) if row.stolen_base_attempts is None else row.stolen_base_attempts


def _has_batting_evidence(row: MaxPrepsBattingRow) -> bool:
    return (
        int(row.plate_appearances or 0) > 0
        or int(row.at_bats or 0) > 0
        or int(row.hits or 0) > 0
        or int(row.walks or 0) > 0
        or int(row.strikeouts or 0) > 0
        or int(row.hbp or 0) > 0
        or float(row.obp or 0.0) > 0.0
        or float(row.slg or 0.0) > 0.0
        or float(row.ops or 0.0) > 0.0
        or int(row.stolen_bases or 0) > 0
        or int(row.stolen_base_attempts or 0) > 0
    )


def _is_zero_batting_row(row: MaxPrepsBattingRow) -> bool:
    return not _has_batting_evidence(row)


def _parse_pitching_rows(text: str, report: MaxPrepsOpponentReport) -> None:
    """
    Parse MaxPreps pitching rows defensively.

    MaxPreps printable PDFs are semi-structured:
    - Pitching can span pages.
    - Each stat family is printed as a separate table.
    - PyMuPDF often extracts the PDF as a stream of table cells, not full rows.
    - PDF text extraction often drops zero-value cells.
    - Some reports include bogus "N. Player" rows.
    - Header blocks can repeat.

    Strategy:
    - Walk the Pitching section line-by-line as a cell stream.
    - Detect table headers and classify the active table family.
    - Read each pitcher row as: jersey number -> name -> numeric cells until next row/header.
    - Merge summary/core/rates fragments by player number + normalized name.
    - Prefer IP/BF/#P/APP as evidence that the player actually pitched.
    """
    pitching_section = _section_from(text, "Pitching")
    if not pitching_section:
        report.pitchers = []
        report.parser_warnings.append("No Pitching section found in MaxPreps PDF.")
        report.parser_stats.update(
            {
                "pitching_row_fragments_seen": 0,
                "pitching_rows_merged": 0,
                "pitchers_loaded": 0,
                "skipped_zero_rows": 0,
                "skipped_placeholder_rows": 0,
                "row_shape_counts": {},
            }
        )
        return

    lines = [line.strip() for line in pitching_section.splitlines() if line.strip()]

    merged: dict[str, MaxPrepsPitchingRow] = {}
    fragments_seen = 0
    skipped_placeholder_rows = 0
    skipped_zero_rows = 0
    row_shape_counts: dict[str, int] = {}

    active_table: str | None = None
    idx = 0

    while idx < len(lines):
        line = lines[idx]

        # Header block:
        # #
        # Athlete Name
        # ERA/W/L... or IP/H/R... or OBA/OBP...
        if line == "#" and idx + 1 < len(lines) and lines[idx + 1] == "Athlete Name":
            header: list[str] = []
            idx += 2

            while (
                idx < len(lines)
                and not _line_starts_pitcher_row(lines, idx)
                and lines[idx] not in {"#", "Season Totals"}
            ):
                header.append(lines[idx])
                idx += 1

            header_set = set(header)

            if "IP" in header_set and "BF" in header_set:
                active_table = "core"
            elif "OBA" in header_set and "#P" in header_set:
                active_table = "rates"
            elif "ERA" in header_set and "APP" in header_set:
                active_table = "summary"
            else:
                active_table = None

            continue

        # Skip season totals for row parsing. Team totals are parsed separately.
        if line == "Season Totals":
            idx += 1
            while idx < len(lines) and lines[idx] != "#":
                idx += 1
            continue

        if active_table and _line_starts_pitcher_row(lines, idx):
            number = ""
            grade = None

            if lines[idx] == "N. Player":
                raw_name = "N. Player"
                idx += 1
            else:
                number = lines[idx]
                raw_name = lines[idx + 1]
                idx += 2

            raw_name = " ".join(raw_name.strip().split())
            if raw_name.lower() == "n. player":
                skipped_placeholder_rows += 1

                # Consume the placeholder row's numeric cells.
                while (
                    idx < len(lines)
                    and lines[idx] != "#"
                    and lines[idx] != "Season Totals"
                    and not _line_starts_pitcher_row(lines, idx)
                ):
                    idx += 1

                continue

            name, grade = _parse_name_and_grade(raw_name)

            stat_tokens: list[str] = []
            while (
                idx < len(lines)
                and lines[idx] != "#"
                and lines[idx] != "Season Totals"
                and not _line_starts_pitcher_row(lines, idx)
            ):
                if re.fullmatch(r"[0-9.]+", lines[idx]):
                    stat_tokens.append(lines[idx])
                idx += 1

            if not stat_tokens:
                continue

            fragments_seen += 1

            # Pitching rows are usually 5-11 numeric cells, but MaxPreps can
            # omit zero cells. Keep this permissive so row type drives parsing.
            if len(stat_tokens) < 2 or len(stat_tokens) > 12:
                continue

            key = _pitcher_key(number, name)
            row = merged.get(key)
            if row is None:
                row = MaxPrepsPitchingRow(
                    number=number,
                    name=name,
                    grade=grade,
                )
                merged[key] = row
            elif not row.grade and grade:
                row.grade = grade

            row_shape_counts[active_table] = row_shape_counts.get(active_table, 0) + 1

            if active_table == "summary":
                _merge_pitching_summary_tokens(row, stat_tokens)
            elif active_table == "core":
                _merge_pitching_core_tokens(row, stat_tokens)
            elif active_table == "rates":
                _merge_pitching_rates_tokens(row, stat_tokens)

            continue

        idx += 1

    candidates = list(merged.values())

    pitchers: list[MaxPrepsPitchingRow] = []
    for row in candidates:
        if _is_zero_pitching_row(row):
            skipped_zero_rows += 1
            continue
        if _has_pitching_evidence(row):
            pitchers.append(row)

    pitchers.sort(
        key=lambda r: (
            -(r.innings_pitched or 0.0),
            -(r.batters_faced or 0),
            -(r.appearances or 0),
            r.name,
        )
    )

    report.pitchers = pitchers

    if not pitchers:
        report.parser_warnings.append(
            "No usable pitcher rows were found. The PDF may use a MaxPreps layout this parser does not recognize yet."
        )

    if row_shape_counts.get("core", 0) == 0:
        report.parser_warnings.append(
            "No IP/H/R/ER/BB/K pitching table was detected. Pitcher profiles may be incomplete."
        )

    report.parser_stats.update(
        {
            "pitching_row_fragments_seen": fragments_seen,
            "pitching_rows_merged": len(merged),
            "pitchers_loaded": len(pitchers),
            "skipped_zero_rows": skipped_zero_rows,
            "skipped_placeholder_rows": skipped_placeholder_rows,
            "row_shape_counts": row_shape_counts,
        }
    )


def _classify_pitching_stat_tokens(tokens: list[str]) -> str:
    """
    Guess which MaxPreps pitching table a row came from.

    Known table families:
    1. Summary:
       ERA W L W% APP GS CG SO SV NH PG
       Often 5-11 tokens due to omitted blank/zero cells.

    2. Core:
       IP H R ER BB K 2B 3B HR BF AB
       Usually has 8-11 tokens. First token may be baseball innings notation.

    3. Rates:
       OBA OBP WP HBP SF SH/B #P BK PO SB
       Usually starts with decimal-looking OBA/OBP or zeros, contains #P near the end.
    """
    if not tokens:
        return "unknown"

    # Rates table usually starts with OBA/OBP decimals like .219 .271,
    # or 0 0 for players with no pitching.
    if len(tokens) >= 6 and (_looks_like_rate(tokens[0]) or tokens[0] == "0") and (_looks_like_rate(tokens[1]) or tokens[1] == "0"):
        # If one of the later tokens is a large pitch count, this is likely the rates/#P table.
        later_ints = [_safe_int(tok) or 0 for tok in tokens[2:]]
        if any(value >= 20 for value in later_ints) or len(tokens) >= 8:
            return "rates"

    # Core table begins with IP, then H/R/ER/BB/K. It often has BF/AB as
    # the last two values, and those are usually larger than early stat cells.
    if len(tokens) >= 8:
        ip = _parse_innings(tokens[0])
        numeric = [_safe_float(tok) for tok in tokens]
        if ip is not None and all(value is not None for value in numeric[:6]):
            last_two = [_safe_int(tokens[-2]) or 0, _safe_int(tokens[-1]) or 0]
            if max(last_two) >= 10:
                return "core"

    # Summary table begins with ERA and then W/L/W%/APP/GS...
    # It is the fallback for shorter pitching fragments.
    if len(tokens) >= 5:
        return "summary"

    return "unknown"


def _merge_pitching_summary_tokens(row: MaxPrepsPitchingRow, tokens: list[str]) -> None:
    """
    Merge ERA/W/L/APP/GS from the summary table.

    MaxPreps can omit W% or trailing zero columns, so this is intentionally
    conservative. APP/GS are useful for scouting, but IP/BF remain the
    authoritative workload signal.
    """
    if not tokens:
        return

    row.era = _safe_float(tokens[0]) if row.era is None else row.era

    if len(tokens) >= 3:
        row.wins = _safe_int(tokens[1]) if row.wins is None else row.wins
        row.losses = _safe_int(tokens[2]) if row.losses is None else row.losses

    # After ERA W L, there may or may not be W%.
    # If token 3 looks like a percentage/rate, APP is token 4.
    # Otherwise APP is token 3.
    app_idx = None
    if len(tokens) >= 5 and _looks_like_rate(tokens[3]):
        app_idx = 4
    elif len(tokens) >= 4:
        app_idx = 3

    if app_idx is not None and app_idx < len(tokens):
        row.appearances = _safe_int(tokens[app_idx]) if row.appearances is None else row.appearances

    gs_idx = app_idx + 1 if app_idx is not None else None
    if gs_idx is not None and gs_idx < len(tokens):
        row.games_started = _safe_int(tokens[gs_idx]) if row.games_started is None else row.games_started


def _merge_pitching_core_tokens(row: MaxPrepsPitchingRow, tokens: list[str]) -> None:
    """
    Merge IP/H/R/ER/BB/K/2B/3B/HR/BF/AB.

    Handles missing zero columns by anchoring BF/AB to the last two tokens.
    """
    if len(tokens) < 8:
        return

    row.innings_pitched = _parse_innings(tokens[0])
    row.hits_allowed = _safe_int(tokens[1])
    row.runs_allowed = _safe_int(tokens[2])
    row.earned_runs = _safe_int(tokens[3])
    row.walks = _safe_int(tokens[4])
    row.strikeouts = _safe_int(tokens[5])

    row.batters_faced = _safe_int(tokens[-2])
    row.at_bats_against = _safe_int(tokens[-1])

    middle = tokens[6:-2]

    # Known full shape: 2B 3B HR BF AB
    # Common omitted-zero shapes:
    #   2B BF AB
    #   2B 3B BF AB
    #   2B 3B HR BF AB
    row.doubles_allowed = _safe_int(middle[0]) if len(middle) >= 1 else 0
    row.triples_allowed = _safe_int(middle[1]) if len(middle) >= 2 else 0
    row.homers_allowed = _safe_int(middle[2]) if len(middle) >= 3 else 0

    if row.doubles_allowed is None:
        row.doubles_allowed = 0
    if row.triples_allowed is None:
        row.triples_allowed = 0
    if row.homers_allowed is None:
        row.homers_allowed = 0


def _merge_pitching_rates_tokens(row: MaxPrepsPitchingRow, tokens: list[str]) -> None:
    """
    Merge OBA/OBP/WP/HBP/#P from the final pitching table.

    Header is:
      OBA OBP WP HBP SF SH/B #P BK PO SB

    PDF extraction sometimes drops trailing zeros, but #P is usually the
    largest later value, so we parse the common positions and fall back
    to the largest plausible pitch-count token.
    """
    if len(tokens) < 2:
        return

    row.opponent_ba = _parse_decimal(tokens[0]) if row.opponent_ba is None else row.opponent_ba
    row.opponent_obp = _parse_decimal(tokens[1]) if row.opponent_obp is None else row.opponent_obp

    if len(tokens) >= 3:
        row.wild_pitches = _safe_int(tokens[2]) if row.wild_pitches is None else row.wild_pitches
    if len(tokens) >= 4:
        row.hbp = _safe_int(tokens[3]) if row.hbp is None else row.hbp

    # #P is usually the largest later value. MaxPreps/PyMuPDF can omit zero
    # cells, so the nominal token position is not reliable.
    later_values = [_safe_int(tok) or 0 for tok in tokens[2:]]
    plausible_pitch_counts = [value for value in later_values if value >= 20]

    pitch_count = max(plausible_pitch_counts) if plausible_pitch_counts else None

    # Fallback for unusually small samples where #P may be under 20.
    if pitch_count is None and len(tokens) >= 7:
        pitch_count = _safe_int(tokens[6])

    if pitch_count is not None:
        row.pitches = pitch_count


def _has_pitching_evidence(row: MaxPrepsPitchingRow) -> bool:
    return (
        float(row.innings_pitched or 0.0) > 0.0
        or int(row.batters_faced or 0) > 0
        or int(row.pitches or 0) > 0
        or int(row.appearances or 0) > 0
    )


def _is_zero_pitching_row(row: MaxPrepsPitchingRow) -> bool:
    return (
        float(row.innings_pitched or 0.0) <= 0.0
        and int(row.batters_faced or 0) <= 0
        and int(row.pitches or 0) <= 0
        and int(row.appearances or 0) <= 0
        and int(row.strikeouts or 0) <= 0
        and int(row.walks or 0) <= 0
    )


def _looks_like_batting_rate_token(value: str) -> bool:
    """
    Return True for batting AVG/OBP/SLG/OPS-style tokens.

    OPS and SLG can exceed 1.000, so this intentionally accepts decimal
    numeric tokens above 1.0. It also accepts compact MaxPreps-style rate
    tokens such as 725 or 1035.
    """
    cleaned = str(value).strip()
    if not cleaned:
        return False

    if cleaned.startswith("."):
        return True

    if "." in cleaned:
        try:
            parsed = float(cleaned)
        except ValueError:
            return False
        return 0.0 <= parsed <= 5.0

    return cleaned.isdigit() and len(cleaned) in {3, 4}


def _looks_like_rate(value: str) -> bool:
    cleaned = str(value).strip()
    if cleaned.startswith("."):
        return True
    try:
        parsed = float(cleaned)
    except ValueError:
        return False
    return 0.0 <= parsed <= 1.0


def _section_from(text: str, heading: str) -> str:
    idx = text.find(heading)
    return text[idx:] if idx >= 0 else ""


def _section_between(text: str, start_heading: str, end_heading: str) -> str:
    start = text.find(start_heading)
    if start < 0:
        return ""
    end = text.find(end_heading, start + len(start_heading))
    if end < 0:
        return text[start:]
    return text[start:end]


def _pitcher_key(number: str, name: str) -> str:
    return f"{str(number).strip()}::{str(name).strip().lower()}"


def _parse_decimal(value: str) -> float | None:
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if cleaned.startswith("."):
        cleaned = "0" + cleaned
    elif len(cleaned) == 3 and cleaned.isdigit():
        cleaned = "0." + cleaned
    elif len(cleaned) == 4 and cleaned.isdigit():
        cleaned = f"{cleaned[0]}.{cleaned[1:]}"
    return _safe_float(cleaned)


def _parse_innings(value: str) -> float | None:
    """
    MaxPreps uses baseball notation:
    39.2 = 39 and 2/3 innings, not 39.2 decimal innings.
    """
    cleaned = str(value).strip()
    if not cleaned:
        return None

    if "." not in cleaned:
        return _safe_float(cleaned)

    whole, frac = cleaned.split(".", 1)
    whole_int = _safe_int(whole) or 0

    if frac == "1":
        return whole_int + (1.0 / 3.0)
    if frac == "2":
        return whole_int + (2.0 / 3.0)

    return _safe_float(cleaned)


def _safe_int(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        cleaned = str(value).strip()
        if cleaned.startswith("."):
            cleaned = "0" + cleaned
        return float(cleaned)
    except (TypeError, ValueError):
        return None
