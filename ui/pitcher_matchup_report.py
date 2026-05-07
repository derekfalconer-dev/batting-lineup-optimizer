from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from core.maxpreps_pdf_parser import parse_maxpreps_pdf, report_to_dict
from core.pitcher_matchups import (
    build_pitcher_matchup_report,
    format_pitcher_matchup_report,
)


def _write_uploaded_pdf(uploaded_file, destination: Path) -> None:
    destination.write_bytes(uploaded_file.getvalue())


def _show_parser_warnings(label: str, report_payload: dict) -> None:
    warnings = list(report_payload.get("parser_warnings") or [])
    if not warnings:
        return

    with st.expander(f"{label} parser warnings", expanded=True):
        for warning in warnings:
            st.warning(str(warning))


def render_pitcher_matchup_report_panel() -> None:
    """
    Dev-only Streamlit panel for generating a plain-text pitching matchup report.

    This intentionally avoids persistence, charts, scoring changes, and any
    existing lineup optimizer workflow changes.
    """
    with st.expander("Experimental: Pitching Matchup Report", expanded=False):
        st.caption(
            "Upload an opponent MaxPreps stats PDF and your team's MaxPreps stats PDF "
            "to generate a pitcher-vs-lineup matchup read."
        )

        opponent_pdf = st.file_uploader(
            "Opponent MaxPreps stats PDF",
            type=["pdf"],
            key="experimental_pitcher_matchup_opponent_pdf",
        )
        own_team_pdf = st.file_uploader(
            "Your team MaxPreps stats PDF",
            type=["pdf"],
            key="experimental_pitcher_matchup_own_team_pdf",
        )

        if not st.button(
            "Generate Pitching Matchup Report",
            key="experimental_generate_pitcher_matchup_report",
        ):
            return

        if opponent_pdf is None or own_team_pdf is None:
            st.warning("Upload both MaxPreps stats PDFs before generating the report.")
            return

        try:
            with st.spinner("Parsing PDFs and building matchup report..."):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_path = Path(tmp_dir)
                    opponent_path = tmp_path / "opponent_maxpreps_stats.pdf"
                    own_team_path = tmp_path / "own_team_maxpreps_stats.pdf"

                    _write_uploaded_pdf(opponent_pdf, opponent_path)
                    _write_uploaded_pdf(own_team_pdf, own_team_path)

                    opponent_report = parse_maxpreps_pdf(opponent_path)
                    own_team_report = parse_maxpreps_pdf(own_team_path)

                opponent_payload = report_to_dict(opponent_report)
                own_team_payload = report_to_dict(own_team_report)

                _show_parser_warnings("Opponent PDF", opponent_payload)
                _show_parser_warnings("Your team PDF", own_team_payload)

                matchup_report = build_pitcher_matchup_report(
                    opponent_batting_rows=list(opponent_payload.get("batters") or []),
                    own_pitching_rows=list(own_team_payload.get("pitchers") or []),
                    lineup_size=9,
                )
                formatted_report = format_pitcher_matchup_report(matchup_report)

            st.text_area(
                "Pitching matchup report",
                value=formatted_report,
                height=700,
                key="experimental_pitcher_matchup_report_output",
            )

        except Exception as exc:
            st.error(f"Could not generate pitching matchup report: {exc}")
