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
    pitching_section = _section_from(text, "Pitching")
    if not pitching_section:
        return

    rows: list[MaxPrepsPitchingRow] = []

    # ONLY parse the reliable "core stat" table (page 5)
    # Parse the reliable "core stat" table:
    # # Athlete Name IP H R ER BB K 2B 3B HR BF AB
    #
    # MaxPreps sometimes omits zero-value trailing middle columns in PDF text
    # extraction. Example:
    #   D. Maya ... 12.1 30 33 21 12 7 2 89 71
    # means:
    #   IP=12.1 H=30 R=33 ER=21 BB=12 K=7 2B=2 3B=0 HR=0 BF=89 AB=71
    #
    # So we parse the row line and tolerate 9, 10, or 11 numeric stat tokens.
    row_pattern = re.compile(
        r"^\s*(?P<num>\d+)\s+"
        r"(?P<name>[A-Z]\.\s+[A-Za-z'’\-\s]+?)\s+\((?P<grade>[^)]+)\)\s+"
        r"(?P<stats>[0-9.\s]+)$",
        re.MULTILINE,
    )

    for match in row_pattern.finditer(pitching_section):
        stat_tokens = match.group("stats").split()

        # Need at least: IP H R ER BB K BF AB
        if len(stat_tokens) < 8:
            continue

        ip = stat_tokens[0]
        h = stat_tokens[1]
        r = stat_tokens[2]
        er = stat_tokens[3]
        bb = stat_tokens[4]
        k = stat_tokens[5]

        # Remaining columns are some form of:
        # full:      2B 3B HR BF AB
        # common:    2B BF AB              (3B/HR omitted as zero)
        # possible:  2B 3B BF AB           (HR omitted as zero)
        remaining = stat_tokens[6:]

        doubles_allowed = 0
        triples_allowed = 0
        homers_allowed = 0
        bf = None
        ab = None

        if len(remaining) >= 5:
            doubles_allowed = _safe_int(remaining[0]) or 0
            triples_allowed = _safe_int(remaining[1]) or 0
            homers_allowed = _safe_int(remaining[2]) or 0
            bf = remaining[3]
            ab = remaining[4]
        elif len(remaining) == 4:
            doubles_allowed = _safe_int(remaining[0]) or 0
            triples_allowed = _safe_int(remaining[1]) or 0
            homers_allowed = 0
            bf = remaining[2]
            ab = remaining[3]
        elif len(remaining) == 3:
            doubles_allowed = _safe_int(remaining[0]) or 0
            triples_allowed = 0
            homers_allowed = 0
            bf = remaining[1]
            ab = remaining[2]
        else:
            continue

        row = MaxPrepsPitchingRow(
            number=match.group("num"),
            name=" ".join(match.group("name").strip().split()),
            grade=match.group("grade").strip(),
            innings_pitched=_parse_innings(ip),
            hits_allowed=_safe_int(h),
            runs_allowed=_safe_int(r),
            earned_runs=_safe_int(er),
            walks=_safe_int(bb),
            strikeouts=_safe_int(k),
            doubles_allowed=doubles_allowed,
            triples_allowed=triples_allowed,
            homers_allowed=homers_allowed,
            batters_faced=_safe_int(bf),
            at_bats_against=_safe_int(ab),
        )
        rows.append(row)
    # Filter garbage rows (no IP or BF)
    report.pitchers = [
        r for r in rows
        if (r.innings_pitched or 0) > 0 and (r.batters_faced or 0) > 0
    ]

    # Sort by workload (best proxy for likely starter)
    report.pitchers.sort(
        key=lambda r: -(r.innings_pitched or 0)
    )


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