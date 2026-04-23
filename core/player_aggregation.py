from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.gc_loader import load_gamechanger_records

from core.roster_reconciliation import (
    build_name_compatibility_reason,
    normalize_name_token,
)

from pathlib import Path


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


def _as_int(value: Any) -> int:
    if value in (None, "", "-"):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_div(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return float(num) / float(den)


def normalize_person_name(name: Any) -> str:
    text = str(name or "").strip().lower()
    for ch in [".", ",", "'", '"', "-", "_", "/", "\\", "(", ")", "[", "]"]:
        text = text.replace(ch, " ")
    return " ".join(text.split())


@dataclass(slots=True)
class AggregateBattingStats:
    pa: int = 0
    ab: int = 0
    h: int = 0
    single: int = 0
    double: int = 0
    triple: int = 0
    hr: int = 0
    bb: int = 0
    so: int = 0
    sb: int = 0
    cs: int = 0
    r: int = 0
    rbi: int = 0
    hbp: int = 0
    sf: int = 0
    sac: int = 0
    tb: int = 0
    roe: int = 0

    def add_in_place(self, other: "AggregateBattingStats") -> None:
        self.pa += other.pa
        self.ab += other.ab
        self.h += other.h
        self.single += other.single
        self.double += other.double
        self.triple += other.triple
        self.hr += other.hr
        self.bb += other.bb
        self.so += other.so
        self.sb += other.sb
        self.cs += other.cs
        self.r += other.r
        self.rbi += other.rbi
        self.hbp += other.hbp
        self.sf += other.sf
        self.sac += other.sac
        self.tb += other.tb
        self.roe += other.roe

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_gc_record(cls, record: Mapping[str, Any]) -> "AggregateBattingStats":
        return cls(
            pa=_as_int(record.get("PA")),
            ab=_as_int(record.get("AB")),
            h=_as_int(record.get("H")),
            single=_as_int(record.get("1B")),
            double=_as_int(record.get("2B")),
            triple=_as_int(record.get("3B")),
            hr=_as_int(record.get("HR")),
            bb=_as_int(record.get("BB")),
            so=_as_int(record.get("SO")),
            sb=_as_int(record.get("SB")),
            cs=_as_int(record.get("CS")),
            r=_as_int(record.get("R")),
            rbi=_as_int(record.get("RBI")),
            hbp=_as_int(record.get("HBP")),
            sf=_as_int(record.get("SF")),
            sac=_as_int(record.get("SAC")),
            tb=_as_int(record.get("TB")),
            roe=_as_int(record.get("ROE")),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AggregateBattingStats":
        return cls(
            pa=_as_int(data.get("pa")),
            ab=_as_int(data.get("ab")),
            h=_as_int(data.get("h")),
            single=_as_int(data.get("single")),
            double=_as_int(data.get("double")),
            triple=_as_int(data.get("triple")),
            hr=_as_int(data.get("hr")),
            bb=_as_int(data.get("bb")),
            so=_as_int(data.get("so")),
            sb=_as_int(data.get("sb")),
            cs=_as_int(data.get("cs")),
            r=_as_int(data.get("r")),
            rbi=_as_int(data.get("rbi")),
            hbp=_as_int(data.get("hbp")),
            sf=_as_int(data.get("sf")),
            sac=_as_int(data.get("sac")),
            tb=_as_int(data.get("tb")),
            roe=_as_int(data.get("roe")),
        )


@dataclass(slots=True)
class AggregatePlayerRecord:
    player_id: str
    canonical_name: str
    normalized_name: str

    first_name: str = ""
    last_name: str = ""
    jersey_number: str = ""

    batting: AggregateBattingStats = field(default_factory=AggregateBattingStats)

    source_file_count: int = 0
    source_row_count: int = 0
    source_files: list[str] = field(default_factory=list)
    merged_from_names: list[str] = field(default_factory=list)
    import_events: list[dict[str, Any]] = field(default_factory=list)

    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_record(
        self,
        record: Mapping[str, Any],
        *,
        import_event: Mapping[str, Any] | None = None,
    ) -> None:
        incoming = AggregateBattingStats.from_gc_record(record)
        self.batting.add_in_place(incoming)

        source_file = str(record.get("source_file", "")).strip()
        source_files_from_record = [
            str(x).strip()
            for x in record.get("source_files", [])
            if str(x).strip()
        ]

        resolved_source_files: list[str] = []
        if source_files_from_record:
            resolved_source_files.extend(source_files_from_record)
        elif source_file:
            resolved_source_files.append(source_file)

        for path in resolved_source_files:
            if path not in self.source_files:
                self.source_files.append(path)

        self.source_files.sort()

        player_name = str(record.get("name", "")).strip()
        if player_name and player_name not in self.merged_from_names:
            self.merged_from_names.append(player_name)
            self.merged_from_names.sort()

        self.source_file_count = len(self.source_files)
        self.source_row_count += 1

        if import_event:
            self.import_events.append(dict(import_event))

        first = str(record.get("first", "")).strip()
        last = str(record.get("last", "")).strip()
        number = str(record.get("number", "")).strip()

        if first and len(first) > len(self.first_name):
            self.first_name = first
        if last and len(last) > len(self.last_name):
            self.last_name = last
        if number and not self.jersey_number:
            self.jersey_number = number

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "canonical_name": self.canonical_name,
            "normalized_name": self.normalized_name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "jersey_number": self.jersey_number,
            "batting": self.batting.to_dict(),
            "source_file_count": self.source_file_count,
            "source_row_count": self.source_row_count,
            "source_files": list(self.source_files),
            "merged_from_names": list(self.merged_from_names),
            "import_events": list(self.import_events),
            "active": self.active,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AggregatePlayerRecord":
        return cls(
            player_id=str(data["player_id"]),
            canonical_name=str(data["canonical_name"]),
            normalized_name=str(data.get("normalized_name") or normalize_person_name(data.get("canonical_name", ""))),
            first_name=str(data.get("first_name", "")),
            last_name=str(data.get("last_name", "")),
            jersey_number=str(data.get("jersey_number", "")),
            batting=AggregateBattingStats.from_dict(data.get("batting", {})),
            source_file_count=_as_int(data.get("source_file_count")),
            source_row_count=_as_int(data.get("source_row_count")),
            source_files=[str(x) for x in data.get("source_files", [])],
            merged_from_names=[str(x) for x in data.get("merged_from_names", [])],
            import_events=[dict(x) for x in data.get("import_events", [])],
            active=bool(data.get("active", True)),
            metadata=dict(data.get("metadata", {})),
        )


def gc_record_to_aggregate_player(
    record: Mapping[str, Any],
    *,
    player_id: str | None = None,
    import_event: Mapping[str, Any] | None = None,
) -> AggregatePlayerRecord:
    canonical_name = str(record.get("name", "")).strip() or "Unknown Player"
    player = AggregatePlayerRecord(
        player_id=player_id or uuid4().hex[:12],
        canonical_name=canonical_name,
        normalized_name=normalize_person_name(canonical_name),
        first_name=str(record.get("first", "")).strip(),
        last_name=str(record.get("last", "")).strip(),
        jersey_number=str(record.get("number", "")).strip(),
    )
    player.add_record(record, import_event=import_event)
    return player


def aggregate_player_to_gc_record(player: AggregatePlayerRecord) -> dict[str, Any]:
    batting = player.batting

    avg = round(_safe_div(batting.h, batting.ab), 3)
    obp = round(_safe_div(batting.h + batting.bb + batting.hbp, batting.ab + batting.bb + batting.hbp + batting.sf + batting.sac), 3)
    slg = round(_safe_div(batting.tb, batting.ab), 3)

    k_rate = round(_safe_div(batting.so, batting.pa), 4)
    bb_rate = round(_safe_div(batting.bb, batting.pa), 4)
    h_rate = round(_safe_div(batting.h, batting.pa), 4)
    xbh_rate = round(_safe_div(batting.double + batting.triple + batting.hr, batting.pa), 4)
    sb_rate = round(_safe_div(batting.sb, batting.pa), 4)

    return {
        "name": player.canonical_name,
        "first": player.first_name,
        "last": player.last_name,
        "number": player.jersey_number,
        "PA": batting.pa,
        "AB": batting.ab,
        "H": batting.h,
        "1B": batting.single,
        "2B": batting.double,
        "3B": batting.triple,
        "HR": batting.hr,
        "BB": batting.bb,
        "SO": batting.so,
        "SB": batting.sb,
        "CS": batting.cs,
        "R": batting.r,
        "RBI": batting.rbi,
        "HBP": batting.hbp,
        "SF": batting.sf,
        "SAC": batting.sac,
        "TB": batting.tb,
        "ROE": batting.roe,
        "AVG": avg,
        "OBP": obp,
        "SLG": slg,
        "K_RATE": k_rate,
        "BB_RATE": bb_rate,
        "H_RATE": h_rate,
        "XBH_RATE": xbh_rate,
        "SB_RATE": sb_rate,
        "source_file": player.source_files[0] if len(player.source_files) == 1 else "",
        "source_files": list(player.source_files),
        "source_file_count": len(player.source_files),
        "merged_record_count": player.source_row_count,
        "merged_from_names": list(player.merged_from_names),
        "raw_row": None,
        "raw_rows": [],
    }


def build_aggregate_players_from_gc_records(
    records: Sequence[Mapping[str, Any]],
    *,
    import_event: Mapping[str, Any] | None = None,
) -> tuple[dict[str, AggregatePlayerRecord], dict[str, str]]:
    players_by_id: dict[str, AggregatePlayerRecord] = {}
    aliases: dict[str, str] = {}

    for record in records:
        player = gc_record_to_aggregate_player(record, import_event=import_event)
        players_by_id[player.player_id] = player
        aliases[player.normalized_name] = player.player_id

        for alias_name in player.merged_from_names:
            aliases[normalize_person_name(alias_name)] = player.player_id

    return players_by_id, aliases


def normalize_team_alias_map(player_aliases: Mapping[str, str]) -> dict[str, str]:
    return {
        normalize_person_name(alias): str(player_id)
        for alias, player_id in player_aliases.items()
        if normalize_person_name(alias)
    }


def existing_team_name_index(
    aggregate_player_records: Mapping[str, AggregatePlayerRecord],
) -> dict[str, AggregatePlayerRecord]:
    return {
        str(player_id): record
        for player_id, record in aggregate_player_records.items()
    }


def preview_incoming_gc_records_against_team(
    *,
    incoming_records: Sequence[Mapping[str, Any]],
    aggregate_player_records: Mapping[str, AggregatePlayerRecord],
    player_aliases: Mapping[str, str],
) -> list[dict[str, Any]]:
    """
    Classify incoming records against the current team.

    v1 rules:
    - exact normalized alias/name hit -> matched_existing
    - conservative compatible-name candidate(s) -> ambiguous_match
    - otherwise -> new_player
    """
    alias_map = normalize_team_alias_map(player_aliases)
    team_players = existing_team_name_index(aggregate_player_records)

    team_candidate_records: list[dict[str, Any]] = []
    for player_id, player in team_players.items():
        team_candidate_records.append(
            {
                "player_id": player_id,
                "name": player.canonical_name,
                "first": player.first_name,
                "last": player.last_name,
                "normalized_name": player.normalized_name,
                "normalized_first": normalize_name_token(player.first_name),
                "normalized_last": normalize_name_token(player.last_name),
            }
        )

    preview_rows: list[dict[str, Any]] = []

    for record in incoming_records:
        incoming_name = str(record.get("name", "")).strip()
        normalized_name = normalize_person_name(incoming_name)
        pa = _as_int(record.get("PA"))
        source_file = str(record.get("source_file", "")).strip()

        matched_player_id = alias_map.get(normalized_name)
        if matched_player_id and matched_player_id in team_players:
            matched_player = team_players[matched_player_id]
            preview_rows.append(
                {
                    "incoming_name": incoming_name,
                    "normalized_name": normalized_name,
                    "pa": pa,
                    "source_file": source_file,
                    "classification": "matched_existing",
                    "matched_player_id": matched_player_id,
                    "matched_player_name": matched_player.canonical_name,
                    "suggested_action": "merge_existing",
                    "candidate_player_ids": [matched_player_id],
                    "candidate_player_names": [matched_player.canonical_name],
                    "record": dict(record),
                }
            )
            continue

        # Ambiguous / candidate matching
        candidates: list[tuple[str, str]] = []
        incoming_first = normalize_name_token(record.get("first", ""))
        incoming_last = normalize_name_token(record.get("last", ""))

        for team_record in team_candidate_records:
            reason = build_name_compatibility_reason(
                left_first=incoming_first,
                right_first=team_record["normalized_first"],
                left_last=incoming_last,
                right_last=team_record["normalized_last"],
            )
            if reason is not None:
                candidates.append((team_record["player_id"], team_record["name"]))

        if candidates:
            preview_rows.append(
                {
                    "incoming_name": incoming_name,
                    "normalized_name": normalized_name,
                    "pa": pa,
                    "source_file": source_file,
                    "classification": "ambiguous_match",
                    "matched_player_id": None,
                    "matched_player_name": None,
                    "suggested_action": "skip",
                    "candidate_player_ids": [c[0] for c in candidates],
                    "candidate_player_names": [c[1] for c in candidates],
                    "record": dict(record),
                }
            )
            continue

        preview_rows.append(
            {
                "incoming_name": incoming_name,
                "normalized_name": normalized_name,
                "pa": pa,
                "source_file": source_file,
                "classification": "new_player",
                "matched_player_id": None,
                "matched_player_name": None,
                "suggested_action": "skip",
                "candidate_player_ids": [],
                "candidate_player_names": [],
                "record": dict(record),
            }
        )

    return preview_rows


def apply_gc_preview_decisions_to_team(
    *,
    preview_rows: Sequence[Mapping[str, Any]],
    aggregate_player_records: dict[str, AggregatePlayerRecord],
    player_aliases: dict[str, str],
    import_event: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Apply coach-reviewed import decisions.

    Each preview row must include a chosen_action.
    For ambiguous rows that choose merge_existing, matched_player_id must be set.
    """
    merged_existing_count = 0
    added_new_count = 0
    skipped_count = 0
    plate_appearances_added = 0

    updated_players = dict(aggregate_player_records)
    updated_aliases = normalize_team_alias_map(player_aliases)

    for row in preview_rows:
        chosen_action = str(row.get("chosen_action") or row.get("suggested_action") or "skip")
        record = dict(row.get("record") or {})
        pa = _as_int(record.get("PA"))

        if chosen_action == "skip":
            skipped_count += 1
            continue

        if chosen_action == "merge_existing":
            matched_player_id = str(row.get("matched_player_id") or "").strip()
            if not matched_player_id or matched_player_id not in updated_players:
                raise ValueError(
                    f"Merge decision is missing a valid matched_player_id for incoming player '{row.get('incoming_name')}'."
                )

            updated_players[matched_player_id].add_record(
                record,
                import_event=import_event,
            )

            incoming_name = str(record.get("name", "")).strip()
            normalized_name = normalize_person_name(incoming_name)
            if normalized_name:
                updated_aliases[normalized_name] = matched_player_id

            merged_existing_count += 1
            plate_appearances_added += pa
            continue

        if chosen_action == "add_new":
            new_player = gc_record_to_aggregate_player(
                record,
                import_event=import_event,
            )
            updated_players[new_player.player_id] = new_player
            updated_aliases[new_player.normalized_name] = new_player.player_id

            for alias_name in new_player.merged_from_names:
                normalized_alias = normalize_person_name(alias_name)
                if normalized_alias:
                    updated_aliases[normalized_alias] = new_player.player_id

            added_new_count += 1
            plate_appearances_added += pa
            continue

        raise ValueError(f"Unsupported chosen_action: {chosen_action}")

    return {
        "aggregate_player_records": updated_players,
        "player_aliases": updated_aliases,
        "summary": {
            "merged_existing_count": merged_existing_count,
            "added_new_count": added_new_count,
            "skipped_count": skipped_count,
            "plate_appearances_added": plate_appearances_added,
        },
    }


def load_incoming_gc_records_from_files(
    csv_paths: Sequence[str | Path],
    *,
    min_pa: int = 5,
    name_format: str = "full",
) -> list[dict[str, Any]]:
    loaded_records: list[dict[str, Any]] = []

    for csv_path in csv_paths:
        records = load_gamechanger_records(
            csv_path=csv_path,
            min_pa=min_pa,
            name_format=name_format,
        )
        for record in records:
            enriched = dict(record)
            enriched["normalized_name"] = normalize_person_name(record.get("name", ""))
            enriched["normalized_first"] = normalize_name_token(record.get("first", ""))
            enriched["normalized_last"] = normalize_name_token(record.get("last", ""))
            enriched["source_file"] = str(record.get("source_file", "") or Path(csv_path).name)
            loaded_records.append(enriched)

    return loaded_records