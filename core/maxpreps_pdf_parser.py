from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


def _parse_pitching_rows(text: str, report: MaxPrepsOpponentReport) -> None:
    """
    Parse MaxPreps pitching rows defensively.

    MaxPreps printable PDFs are only semi-structured:
    - Pitching can span pages.
    - Each stat family is printed as a separate table.
    - PDF text extraction often drops zero-value cells.
    - Some reports include bogus "N. Player" rows.
    - Header blocks can repeat.

    Strategy:
    - Parse all row-like lines after the Pitching heading.
    - Classify each row by numeric token shape.
    - Merge fragments by player number + normalized name.
    - Prefer IP/BF/#P/APP as evidence that the player actually pitched.
    """
    pitching_section = _section_from(text, "Pitching")
    if not pitching_section:
        report.pitchers = []
        report.parser_warnings.append("No Pitching section found in MaxPreps PDF.")
        report.parser_stats = {
            "pitching_row_fragments_seen": 0,
            "pitching_rows_merged": 0,
            "pitchers_loaded": 0,
            "skipped_zero_rows": 0,
            "skipped_placeholder_rows": 0,
        }
        return

    merged: dict[str, MaxPrepsPitchingRow] = {}
    fragments_seen = 0
    skipped_placeholder_rows = 0
    skipped_zero_rows = 0
    row_shape_counts: dict[str, int] = {}

    row_pattern = re.compile(
        r"^\s*(?P<num>\d+)?\s*"
        r"(?P<name>[A-Z]\.\s+[A-Za-z'’\-\s]+?|N\.\s+Player)\s*"
        r"(?:\((?P<grade>[^)]+)\))?\s+"
        r"(?P<stats>[0-9.\s]+)$",
        re.MULTILINE,
    )

    for match in row_pattern.finditer(pitching_section):
        raw_name = " ".join(match.group("name").strip().split())
        if raw_name.lower() == "n. player":
            skipped_placeholder_rows += 1
            continue

        number = str(match.group("num") or "").strip()
        grade = match.group("grade").strip() if match.group("grade") else None
        stat_tokens = match.group("stats").split()

        if not stat_tokens:
            continue

        fragments_seen += 1

        # Ignore obvious non-pitching tables that can appear before/after Pitching.
        # Pitching rows are usually 5-11 numeric tokens after name/grade.
        if len(stat_tokens) < 5 or len(stat_tokens) > 11:
            continue

        key = _pitcher_key(number, raw_name)
        row = merged.get(key)
        if row is None:
            row = MaxPrepsPitchingRow(
                number=number,
                name=raw_name,
                grade=grade,
            )
            merged[key] = row
        elif not row.grade and grade:
            row.grade = grade

        shape = _classify_pitching_stat_tokens(stat_tokens)
        row_shape_counts[shape] = row_shape_counts.get(shape, 0) + 1

        if shape == "summary":
            _merge_pitching_summary_tokens(row, stat_tokens)
        elif shape == "core":
            _merge_pitching_core_tokens(row, stat_tokens)
        elif shape == "rates":
            _merge_pitching_rates_tokens(row, stat_tokens)
        else:
            # Unknown fragments are expected occasionally; do not fail import.
            continue

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

    report.parser_stats = {
        "pitching_row_fragments_seen": fragments_seen,
        "pitching_rows_merged": len(merged),
        "pitchers_loaded": len(pitchers),
        "skipped_zero_rows": skipped_zero_rows,
        "skipped_placeholder_rows": skipped_placeholder_rows,
        "row_shape_counts": row_shape_counts,
    }


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

    # Normal position is token 6: OBA OBP WP HBP SF SH/B #P ...
    pitch_count = None
    if len(tokens) >= 7:
        pitch_count = _safe_int(tokens[6])

    if pitch_count is None or pitch_count <= 0:
        later_values = [_safe_int(tok) or 0 for tok in tokens[2:]]
        plausible = [value for value in later_values if value >= 20]
        if plausible:
            pitch_count = max(plausible)

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