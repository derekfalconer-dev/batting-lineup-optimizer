from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.gc_loader import load_gamechanger_records


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

COUNTING_STAT_FIELDS: tuple[str, ...] = (
    "PA",
    "AB",
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
)

RATE_FIELDS: tuple[str, ...] = (
    "AVG",
    "OBP",
    "SLG",
    "K_RATE",
    "BB_RATE",
    "H_RATE",
    "XBH_RATE",
    "SB_RATE",
)


# ---------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------

@dataclass(slots=True)
class DuplicateCandidate:
    """
    A coach-review item.

    These are NOT auto-merged.
    They are only surfaced as possible overlaps for later UI review.
    """

    left_name: str
    right_name: str
    reason: str
    left_normalized_name: str
    right_normalized_name: str
    left_sources: list[str] = field(default_factory=list)
    right_sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReconciliationResult:
    """
    Output of the backend multi-GameChanger reconciliation pass.
    """

    input_files: list[str]
    raw_records: list[dict[str, Any]]
    auto_merged_records: list[dict[str, Any]]
    duplicate_candidates: list[DuplicateCandidate]
    auto_merge_groups: list[list[str]] = field(default_factory=list)

    @property
    def raw_record_count(self) -> int:
        return len(self.raw_records)

    @property
    def merged_record_count(self) -> int:
        return len(self.auto_merged_records)


# ---------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------

def reconcile_gamechanger_files(
    csv_paths: Sequence[str | Path],
    *,
    min_pa: int = 5,
    name_format: str = "full",
) -> ReconciliationResult:
    """
    Load multiple GameChanger exports, auto-merge only safe exact normalized
    matches, and surface likely duplicates for coach review.

    MVP policy:
    - auto-merge only exact normalized full-name matches
    - do NOT auto-merge fuzzy matches
    """
    resolved_paths = [str(Path(p)) for p in csv_paths]
    raw_records = load_records_from_many_files(
        resolved_paths,
        min_pa=min_pa,
        name_format=name_format,
    )

    merged_records, auto_merge_groups = auto_merge_exact_name_matches(raw_records)
    duplicate_candidates = find_possible_duplicate_candidates(merged_records)

    return ReconciliationResult(
        input_files=resolved_paths,
        raw_records=raw_records,
        auto_merged_records=merged_records,
        duplicate_candidates=duplicate_candidates,
        auto_merge_groups=auto_merge_groups,
    )


def load_records_from_many_files(
    csv_paths: Sequence[str | Path],
    *,
    min_pa: int = 5,
    name_format: str = "full",
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for csv_path in csv_paths:
        loaded = load_gamechanger_records(
            csv_path=csv_path,
            min_pa=min_pa,
            name_format=name_format,
        )

        for record in loaded:
            enriched = dict(record)
            enriched["normalized_name"] = normalize_person_name(record.get("name", ""))
            enriched["normalized_first"] = normalize_name_token(record.get("first", ""))
            enriched["normalized_last"] = normalize_name_token(record.get("last", ""))
            records.append(enriched)

    return records


def auto_merge_exact_name_matches(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """
    Safe auto-merge:
    - exact normalized full-name match only
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}

    for record in records:
        normalized_name = str(record.get("normalized_name") or normalize_person_name(record.get("name", ""))).strip()
        if not normalized_name:
            # Keep nameless records separate; they should not silently merge.
            normalized_name = f"__unnamed__::{id(record)}"

        grouped.setdefault(normalized_name, []).append(record)

    merged_records: list[dict[str, Any]] = []
    auto_merge_groups: list[list[str]] = []

    for normalized_name, group in grouped.items():
        merged_records.append(merge_gc_record_group(group, normalized_name=normalized_name))

        distinct_names = sorted({str(item.get("name", "")).strip() for item in group if str(item.get("name", "")).strip()})
        if len(group) > 1:
            auto_merge_groups.append(distinct_names or [normalized_name])

    merged_records.sort(key=lambda r: str(r.get("name", "")).lower())
    auto_merge_groups.sort(key=lambda g: [x.lower() for x in g])

    return merged_records, auto_merge_groups


def find_possible_duplicate_candidates(
    merged_records: Sequence[Mapping[str, Any]],
) -> list[DuplicateCandidate]:
    """
    Flag likely overlaps for coach review.

    Conservative candidate rules:
    - compatible first-name tokens + compatible last-name tokens
    - same first initial + compatible last-name tokens

    Important:
    - this only surfaces review candidates
    - it does NOT auto-merge fuzzy matches
    """
    candidates: list[DuplicateCandidate] = []
    seen_keys: set[tuple[str, str]] = set()

    for left, right in combinations(merged_records, 2):
        left_name = str(left.get("name", "")).strip()
        right_name = str(right.get("name", "")).strip()

        if not left_name or not right_name:
            continue
        if left_name == right_name:
            continue

        left_norm = str(left.get("normalized_name") or normalize_person_name(left_name))
        right_norm = str(right.get("normalized_name") or normalize_person_name(right_name))

        if left_norm == right_norm:
            continue

        left_first = str(left.get("normalized_first") or normalize_name_token(left.get("first", "")))
        right_first = str(right.get("normalized_first") or normalize_name_token(right.get("first", "")))
        left_last = str(left.get("normalized_last") or normalize_name_token(left.get("last", "")))
        right_last = str(right.get("normalized_last") or normalize_name_token(right.get("last", "")))

        if not left_first or not right_first or not left_last or not right_last:
            continue

        reason = build_name_compatibility_reason(
            left_first=left_first,
            right_first=right_first,
            left_last=left_last,
            right_last=right_last,
        )

        if reason is None:
            continue

        dedupe_key = tuple(sorted((left_norm, right_norm)))
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        candidates.append(
            DuplicateCandidate(
                left_name=left_name,
                right_name=right_name,
                reason=reason,
                left_normalized_name=left_norm,
                right_normalized_name=right_norm,
                left_sources=sorted(source_files_from_record(left)),
                right_sources=sorted(source_files_from_record(right)),
            )
        )

    candidates.sort(key=lambda c: (c.left_name.lower(), c.right_name.lower()))
    return candidates


def merge_selected_records(
    records: Sequence[Mapping[str, Any]],
    *,
    selected_names: Sequence[str],
) -> dict[str, Any]:
    """
    Future UI helper:
    given a set of selected display names, merge those records into one.
    """
    selected_set = {str(name).strip() for name in selected_names if str(name).strip()}
    group = [record for record in records if str(record.get("name", "")).strip() in selected_set]

    if len(group) < 2:
        raise ValueError("merge_selected_records requires at least two matching records.")

    normalized_name = normalize_person_name(choose_display_name(group))
    return merge_gc_record_group(group, normalized_name=normalized_name)


# ---------------------------------------------------------------------
# Merge mechanics
# ---------------------------------------------------------------------

def merge_gc_record_group(
    group: Sequence[Mapping[str, Any]],
    *,
    normalized_name: str,
) -> dict[str, Any]:
    if not group:
        raise ValueError("Cannot merge an empty GameChanger record group.")

    name = choose_display_name(group)
    first = choose_display_first(group)
    last = choose_display_last(group)
    number = choose_display_number(group)

    merged: dict[str, Any] = {}

    for field in COUNTING_STAT_FIELDS:
        merged[field] = sum(as_int(item.get(field)) for item in group)

    ab = as_int(merged.get("AB"))
    h = as_int(merged.get("H"))
    bb = as_int(merged.get("BB"))
    hbp = as_int(merged.get("HBP"))
    sf = as_int(merged.get("SF"))
    sac = as_int(merged.get("SAC"))
    tb = as_int(merged.get("TB"))

    merged["AVG"] = round(safe_div(h, ab), 3)
    merged["OBP"] = round(safe_div(h + bb + hbp, ab + bb + hbp + sf + sac), 3)
    merged["SLG"] = round(safe_div(tb, ab), 3)

    pa = as_int(merged.get("PA"))
    doubles = as_int(merged.get("2B"))
    triples = as_int(merged.get("3B"))
    homers = as_int(merged.get("HR"))
    steals = as_int(merged.get("SB"))
    strikeouts = as_int(merged.get("SO"))

    merged["K_RATE"] = round(safe_div(strikeouts, pa), 4)
    merged["BB_RATE"] = round(safe_div(bb, pa), 4)
    merged["H_RATE"] = round(safe_div(h, pa), 4)
    merged["XBH_RATE"] = round(safe_div(doubles + triples + homers, pa), 4)
    merged["SB_RATE"] = round(safe_div(steals, pa), 4)

    merged["name"] = name
    merged["first"] = first
    merged["last"] = last
    merged["number"] = number

    merged["normalized_name"] = normalized_name
    merged["normalized_first"] = normalize_name_token(first)
    merged["normalized_last"] = normalize_name_token(last)

    source_files = sorted({str(item.get("source_file", "")).strip() for item in group if str(item.get("source_file", "")).strip()})
    source_names = sorted({str(item.get("name", "")).strip() for item in group if str(item.get("name", "")).strip()})

    merged["source_file"] = source_files[0] if len(source_files) == 1 else ""
    merged["source_files"] = source_files
    merged["source_file_count"] = len(source_files)
    merged["merged_from_names"] = source_names
    merged["merged_record_count"] = len(group)

    merged["raw_row"] = None
    merged["raw_rows"] = [item.get("raw_row") for item in group if item.get("raw_row") is not None]

    return merged


# ---------------------------------------------------------------------
# Display / normalization helpers
# ---------------------------------------------------------------------

def choose_display_name(group: Sequence[Mapping[str, Any]]) -> str:
    candidates = [
        str(item.get("name", "")).strip()
        for item in group
        if str(item.get("name", "")).strip()
    ]
    if not candidates:
        return "Unknown Player"

    # Prefer the most complete-looking display name.
    candidates.sort(key=lambda x: (word_count(x), len(x), x.lower()), reverse=True)
    return candidates[0]


def choose_display_first(group: Sequence[Mapping[str, Any]]) -> str:
    candidates = [
        str(item.get("first", "")).strip()
        for item in group
        if str(item.get("first", "")).strip()
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (len(x), x.lower()), reverse=True)
    return candidates[0]


def choose_display_last(group: Sequence[Mapping[str, Any]]) -> str:
    candidates = [
        str(item.get("last", "")).strip()
        for item in group
        if str(item.get("last", "")).strip()
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (len(x), x.lower()), reverse=True)
    return candidates[0]


def choose_display_number(group: Sequence[Mapping[str, Any]]) -> str:
    candidates = [
        str(item.get("number", "")).strip()
        for item in group
        if str(item.get("number", "")).strip()
    ]
    if not candidates:
        return ""
    # Most frequent non-empty jersey number wins.
    counts: dict[str, int] = {}
    for value in candidates:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)[0][0]


def normalize_person_name(name: Any) -> str:
    text = str(name or "").strip().lower()
    for ch in [".", ",", "'", '"', "-", "_", "/", "\\", "(", ")", "[", "]"]:
        text = text.replace(ch, " ")
    return " ".join(text.split())


def normalize_name_token(value: Any) -> str:
    return normalize_person_name(value).replace(" ", "")


def first_initial(value: str) -> str:
    value = normalize_name_token(value)
    return value[:1] if value else ""


def compatible_name_token(left: str, right: str) -> bool:
    """
    Conservative compatibility check for abbreviated name tokens.

    True when:
    - exact match
    - one side is a single-letter initial matching the other
    - one side is a short prefix of the other (minimum 2 chars)
    """
    left = normalize_name_token(left)
    right = normalize_name_token(right)

    if not left or not right:
        return False

    if left == right:
        return True

    if len(left) == 1 and right.startswith(left):
        return True

    if len(right) == 1 and left.startswith(right):
        return True

    if len(left) >= 2 and right.startswith(left):
        return True

    if len(right) >= 2 and left.startswith(right):
        return True

    return False


def build_name_compatibility_reason(
    left_first: str,
    right_first: str,
    left_last: str,
    right_last: str,
) -> str | None:
    """
    Return a coach-facing reason string when two names look plausibly compatible.

    This is only for candidate surfacing, not auto-merge.
    """
    first_match = compatible_name_token(left_first, right_first)
    last_match = compatible_name_token(left_last, right_last)

    if first_match and last_match:
        if normalize_name_token(left_first) != normalize_name_token(right_first):
            if normalize_name_token(left_last) != normalize_name_token(right_last):
                return "Matching abbreviated first and last name pattern"
            return "Matching first-name abbreviation/prefix and same last name"
        if normalize_name_token(left_last) != normalize_name_token(right_last):
            return "Same first name and matching abbreviated/prefix last name"
        return "Same first and last name pattern"

    if first_initial(left_first) and first_initial(left_first) == first_initial(right_first):
        if compatible_name_token(left_last, right_last):
            return "Same first initial and compatible last name"

    return None


def word_count(value: str) -> int:
    return len([part for part in str(value).split() if part])


def source_files_from_record(record: Mapping[str, Any]) -> set[str]:
    files: set[str] = set()

    source_file = str(record.get("source_file", "")).strip()
    if source_file:
        files.add(source_file)

    for item in record.get("source_files", []) or []:
        text = str(item).strip()
        if text:
            files.add(text)

    return files


# ---------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------

def as_int(value: Any) -> int:
    if value in (None, "", "-", "nan"):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator) / float(denominator)