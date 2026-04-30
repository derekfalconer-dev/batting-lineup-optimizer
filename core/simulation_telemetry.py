from __future__ import annotations

from dataclasses import dataclass, field
from collections import defaultdict
from typing import Any


REACH_OUTCOMES = {"bb", "1b", "2b", "3b", "hr"}
DAMAGE_OUTCOMES = {"2b", "3b", "hr"}


@dataclass
class PlayerTelemetry:
    player: str
    lineup_spot: int

    plate_appearances: int = 0
    reaches: int = 0
    runs_created_on_play: int = 0

    pressure_events: int = 0
    pressure_points: float = 0.0

    estimated_pitches: int = 0
    pitcher_stress_points: float = 0.0
    walks: int = 0
    deep_count_pas: int = 0

    walks: int = 0
    deep_count_pas: int = 0

    rally_starts: int = 0
    rally_extensions: int = 0
    rally_damage_events: int = 0
    rally_runs: int = 0


@dataclass
class SimulationTelemetry:
    """
    Aggregated simulator telemetry for one evaluated lineup.

    This intentionally stores simple event counts so coach-facing charts can say:
    "This happened across N simulated games."
    """

    lineup_name: str = "Lineup"
    lineup: list[str] = field(default_factory=list)
    n_games: int = 0
    innings: int = 0

    player_rows: dict[str, PlayerTelemetry] = field(default_factory=dict)

    total_plate_appearances: int = 0
    total_reaches: int = 0
    total_pressure_events: int = 0
    total_rally_innings: int = 0
    total_long_innings: int = 0
    total_traffic_innings: int = 0
    total_estimated_pitches: int = 0

    def ensure_player(self, player_name: str, lineup_spot: int) -> PlayerTelemetry:
        if player_name not in self.player_rows:
            self.player_rows[player_name] = PlayerTelemetry(
                player=player_name,
                lineup_spot=int(lineup_spot),
            )
        return self.player_rows[player_name]

    def record_plate_appearance(
        self,
        *,
        player_name: str,
        lineup_spot: int,
        outcome: str,
        bases_occupied_before: int,
        outs_before: int,
        play_runs: int,
    ) -> dict[str, Any]:
        row = self.ensure_player(player_name, lineup_spot)

        reached = outcome in REACH_OUTCOMES
        damage = outcome in DAMAGE_OUTCOMES
        pressure_event = reached or play_runs > 0 or bases_occupied_before > 0

        # Simple pitch-count proxy. This is not claiming exact pitches;
        # it is a stable stress proxy derived from simulated PA outcomes.
        estimated_pitches_by_outcome = {
            "bb": 6,
            "so": 5,
            "1b": 4,
            "2b": 4,
            "3b": 4,
            "hr": 4,
            "bip_out": 3,
        }
        estimated_pitches = int(estimated_pitches_by_outcome.get(outcome, 4))

        pressure_points = 0.0
        if reached:
            pressure_points += 1.0
        if bases_occupied_before > 0:
            pressure_points += 0.5
        if play_runs > 0:
            pressure_points += 1.0 + (0.5 * play_runs)
        if damage:
            pressure_points += 0.75
        if outs_before == 2 and reached:
            pressure_points += 0.75

        pitcher_stress_points = float(estimated_pitches)
        if reached:
            pitcher_stress_points += 2.0
        if bases_occupied_before > 0:
            pitcher_stress_points += 1.0
        if play_runs > 0:
            pitcher_stress_points += 1.5
        if outs_before == 2 and reached:
            pitcher_stress_points += 1.5

        row.plate_appearances += 1
        row.estimated_pitches += estimated_pitches
        row.pitcher_stress_points += pitcher_stress_points
        row.pressure_points += pressure_points

        if outcome == "bb":
            row.walks += 1

        # Proxy for deep count / long PA.
        # We do not simulate exact ball-strike counts yet, so this uses estimated pitch load.
        if estimated_pitches >= 5:
            row.deep_count_pas += 1

        self.total_plate_appearances += 1
        self.total_estimated_pitches += estimated_pitches

        if reached:
            row.reaches += 1
            self.total_reaches += 1

        if play_runs > 0:
            row.runs_created_on_play += int(play_runs)

        if pressure_event:
            row.pressure_events += 1
            self.total_pressure_events += 1

        return {
            "player_name": player_name,
            "lineup_spot": int(lineup_spot),
            "outcome": outcome,
            "reached": reached,
            "damage": damage,
            "pressure_event": pressure_event,
            "bases_occupied_before": int(bases_occupied_before),
            "outs_before": int(outs_before),
            "play_runs": int(play_runs),
            "estimated_pitches": int(estimated_pitches),
            "pressure_points": float(pressure_points),
            "pitcher_stress_points": float(pitcher_stress_points),
        }

    def finalize_inning(self, inning_events: list[dict[str, Any]], inning_runs: int) -> None:
        """
        Define rally innings defensibly:

        Rally inning = 2+ runs OR 3+ reaches in the inning.

        Then attribute:
        - first reach in rally inning = rally start
        - later reaches in rally inning = rally extensions
        - extra-base hits or run-producing plays = rally damage
        """
        if not inning_events:
            return

        reaches = [event for event in inning_events if event["reached"]]
        reach_count = len(reaches)
        pa_count = len(inning_events)

        is_rally_inning = int(inning_runs) >= 2 or reach_count >= 3
        is_traffic_inning = reach_count >= 2
        is_long_inning = pa_count >= 6

        if is_traffic_inning:
            self.total_traffic_innings += 1

        if is_long_inning:
            self.total_long_innings += 1

        if not is_rally_inning:
            return

        self.total_rally_innings += 1

        first_reach_seen = False
        for event in inning_events:
            player = event["player_name"]
            spot = event["lineup_spot"]
            row = self.ensure_player(player, spot)

            if event["reached"]:
                if not first_reach_seen:
                    row.rally_starts += 1
                    first_reach_seen = True
                else:
                    row.rally_extensions += 1

            if event["damage"] or int(event["play_runs"]) > 0:
                row.rally_damage_events += 1

            if int(event["play_runs"]) > 0:
                row.rally_runs += int(event["play_runs"])

    def as_dict(self) -> dict[str, Any]:
        rows = []
        max_pressure = max(
            [row.pressure_points for row in self.player_rows.values()] or [1.0]
        )
        max_stress = max(
            [row.pitcher_stress_points for row in self.player_rows.values()] or [1.0]
        )
        max_ignition = max(
            [
                (row.rally_starts * 2.0) + row.rally_extensions + row.rally_damage_events
                for row in self.player_rows.values()
            ] or [1.0]
        )

        for row in sorted(self.player_rows.values(), key=lambda r: r.lineup_spot):
            ignition_raw = (
                (row.rally_starts * 2.0)
                + row.rally_extensions
                + row.rally_damage_events
            )

            rows.append(
                {
                    "Spot": int(row.lineup_spot),
                    "Player": row.player,

                    "PA": int(row.plate_appearances),
                    "Reaches": int(row.reaches),
                    "Runs Created On Play": int(row.runs_created_on_play),

                    "Pressure Events": int(row.pressure_events),
                    "Pressure Points": float(row.pressure_points),
                    "Pressure Score": round(100.0 * row.pressure_points / max_pressure, 1),

                    "Estimated Pitches": int(row.estimated_pitches),
                    "Estimated Pitches/PA": round(row.estimated_pitches / max(row.plate_appearances, 1), 2),
                    "Walks": int(row.walks),
                    "Walk Rate": round(row.walks / max(row.plate_appearances, 1), 3),
                    "Deep Count PAs": int(row.deep_count_pas),
                    "Deep Count Rate": round(row.deep_count_pas / max(row.plate_appearances, 1), 3),
                    "Pitcher Stress Points": float(row.pitcher_stress_points),
                    "Stress Score": round(100.0 * row.pitcher_stress_points / max_stress, 1),

                    "Rally Starts": int(row.rally_starts),
                    "Rally Starts/100 PA": round(100.0 * row.rally_starts / max(row.plate_appearances, 1), 1),
                    "Rally Extensions": int(row.rally_extensions),
                    "Rally Extensions/100 PA": round(100.0 * row.rally_extensions / max(row.plate_appearances, 1), 1),
                    "Rally Damage Events": int(row.rally_damage_events),
                    "Rally Damage/100 PA": round(100.0 * row.rally_damage_events / max(row.plate_appearances, 1), 1),
                    "Ignition": round(100.0 * ((row.rally_starts * 2.0) / max_ignition), 1),
                    "Extension": round(100.0 * (row.rally_extensions / max_ignition), 1),
                    "Damage": round(100.0 * (row.rally_damage_events / max_ignition), 1),
                }
            )

        return {
            "lineup_name": self.lineup_name,
            "lineup": list(self.lineup),
            "n_games": int(self.n_games),
            "innings": int(self.innings),
            "total_plate_appearances": int(self.total_plate_appearances),
            "total_reaches": int(self.total_reaches),
            "total_pressure_events": int(self.total_pressure_events),
            "total_rally_innings": int(self.total_rally_innings),
            "total_long_innings": int(self.total_long_innings),
            "total_traffic_innings": int(self.total_traffic_innings),
            "total_estimated_pitches": int(self.total_estimated_pitches),
            "player_rows": rows,
        }
