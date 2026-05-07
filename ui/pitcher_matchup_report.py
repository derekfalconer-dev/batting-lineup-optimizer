from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import altair as alt
except ImportError:
    alt = None

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


def _get_report_value(item, key: str, default=None):
    if item is None:
        return default

    if isinstance(item, dict):
        return item.get(key, default)

    return getattr(item, key, default)


def _run_risk_label(index) -> str:
    try:
        risk_index = float(index)
    except (TypeError, ValueError):
        return "Unknown"

    if risk_index <= 95:
        return "Low"
    if risk_index <= 110:
        return "Moderate"
    if risk_index <= 125:
        return "Elevated"
    if risk_index <= 140:
        return "High"
    if risk_index <= 155:
        return "Very high"
    return "Extreme"


def _pct(value) -> str:
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "—"


def _decimal(value) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "—"


def _score(value) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _ui_matchup_grade(result) -> str:
    pitcher = _get_report_value(result, "pitcher")
    confidence = str(_get_report_value(result, "sample_confidence", "Unknown"))

    try:
        score = float(_get_report_value(result, "matchup_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0

    try:
        k_rate = float(_get_report_value(pitcher, "k_rate", 0.0) or 0.0)
    except (TypeError, ValueError):
        k_rate = 0.0

    try:
        free_base_rate = float(_get_report_value(pitcher, "free_base_rate", 0.0) or 0.0)
    except (TypeError, ValueError):
        free_base_rate = 0.0

    if score >= 76 and confidence != "Low":
        return "Clean matchup"
    if score >= 66:
        return "Strong option"
    if score >= 55:
        return "Usable with caveats"
    if k_rate >= 0.20 and free_base_rate >= 0.18:
        return "High-variance arm"
    if score >= 40:
        return "Difficult matchup"
    return "Emergency / tough look"


def _build_pitcher_rows(report: dict) -> list[dict]:
    rows = []

    for idx, result in enumerate(list(report.get("pitcher_rankings") or []), start=1):
        pitcher = _get_report_value(result, "pitcher")

        rows.append(
            {
                "Rank": idx,
                "Pitcher": str(_get_report_value(pitcher, "name", "Unknown pitcher")),
                "Fit": _score(_get_report_value(result, "matchup_score", 0.0)),
                "Run Risk": _run_risk_label(_get_report_value(result, "projected_runs_index", 100.0)),
                "Confidence": str(_get_report_value(result, "sample_confidence", "Unknown")),
                "K%": _pct(_get_report_value(pitcher, "k_rate", 0.0)),
                "BB%": _pct(_get_report_value(pitcher, "bb_rate", 0.0)),
                "Free-base%": _pct(_get_report_value(pitcher, "free_base_rate", 0.0)),
                "OBA": _decimal(_get_report_value(pitcher, "oba", 0.0)),
                "OBP Allowed": _decimal(_get_report_value(pitcher, "obp_allowed", 0.0)),
                "Grade": _ui_matchup_grade(result),
            }
        )

    return rows


def _build_fit_score_chart_data(report: dict, limit: int = 8) -> pd.DataFrame:
    rows = []

    for result in list(report.get("pitcher_rankings") or [])[:limit]:
        pitcher = _get_report_value(result, "pitcher")

        try:
            fit_score = float(_get_report_value(result, "matchup_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue

        rows.append(
            {
                "Pitcher": str(_get_report_value(pitcher, "name", "Unknown pitcher")),
                "Fit Score": round(fit_score, 1),
            }
        )

    return pd.DataFrame(rows)


def _render_fit_score_chart(chart_data: pd.DataFrame) -> None:
    if chart_data.empty:
        return

    st.markdown("### Pitcher Fit Score")
    st.caption("Higher is better.")

    sorted_data = chart_data.sort_values("Fit Score", ascending=False).reset_index(drop=True)

    if alt is None:
        st.dataframe(sorted_data, use_container_width=True, hide_index=True)
        return

    base = alt.Chart(sorted_data).encode(
        y=alt.Y(
            "Pitcher:N",
            sort=alt.EncodingSortField(field="Fit Score", order="descending"),
            title=None,
        ),
        x=alt.X(
            "Fit Score:Q",
            scale=alt.Scale(domain=[0, 100]),
            title="Fit Score",
        ),
        tooltip=[
            alt.Tooltip("Pitcher:N", title="Pitcher"),
            alt.Tooltip("Fit Score:Q", title="Fit Score", format=".1f"),
        ],
    )

    bars = base.mark_bar()
    labels = base.mark_text(
        align="left",
        baseline="middle",
        dx=4,
    ).encode(
        text=alt.Text("Fit Score:Q", format=".1f"),
    )

    chart = (bars + labels).properties(
        height=max(180, 32 * len(sorted_data)),
    )

    st.altair_chart(chart, width="stretch")


def _build_lineup_rows(report: dict) -> list[dict]:
    rows = []

    for idx, spot in enumerate(list(report.get("projected_lineup") or []), start=1):
        hitter = _get_report_value(spot, "hitter")

        rows.append(
            {
                "Spot": _get_report_value(spot, "spot", idx),
                "Player": str(_get_report_value(hitter, "name", "Unknown hitter")),
                "Role": str(_get_report_value(spot, "role", "Projected hitter")),
                "OBP": _decimal(_get_report_value(hitter, "obp", 0.0)),
                "SLG": _decimal(_get_report_value(hitter, "slg", 0.0)),
                "OPS": _decimal(_get_report_value(hitter, "ops", 0.0)),
                "K%": _pct(_get_report_value(hitter, "k_rate", 0.0)),
                "BB%": _pct(_get_report_value(hitter, "bb_rate", 0.0)),
            }
        )

    return rows


def render_pitcher_matchup_report_panel() -> None:
    """
    Dev-only Streamlit panel for generating a plain-text pitching matchup report.

    This intentionally avoids persistence, charts, scoring changes, and any
    existing lineup optimizer workflow changes.
    """
    with st.expander("Experimental: Pitching Matchup Report", expanded=False):
        upload_cols = st.columns(2)

        with upload_cols[0]:
            st.markdown("#### Opponent Data")
            st.caption("Used to project their batting lineup.")
            opponent_pdf = st.file_uploader(
                "Opponent MaxPreps stats PDF",
                type=["pdf"],
                accept_multiple_files=False,
                key="experimental_pitcher_matchup_opponent_pdf",
            )

        with upload_cols[1]:
            st.markdown("#### Your Data")
            st.caption("Used to evaluate your pitching staff.")
            own_team_pdf = st.file_uploader(
                "Your team MaxPreps stats PDF",
                type=["pdf"],
                accept_multiple_files=False,
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

            rankings = list(matchup_report.get("pitcher_rankings") or [])

            opponent_team_name = str(opponent_payload.get("team_name") or "Unknown opponent team")
            own_team_name = str(own_team_payload.get("team_name") or "Unknown team")

            st.caption(
                f"Source check: opponent hitters = {opponent_team_name} | "
                f"your pitching staff = {own_team_name}"
            )

            if opponent_team_name == own_team_name:
                st.warning(
                    "Both PDFs appear to be from the same team. Confirm the files are in the right roles.",
                    icon="⚠️",
                )

            st.markdown("### Top Recommendation")

            if rankings:
                top_result = rankings[0]
                top_pitcher = _get_report_value(top_result, "pitcher")
                top_pitcher_name = str(_get_report_value(top_pitcher, "name", "Unknown pitcher"))
                top_score = _get_report_value(top_result, "matchup_score", 0.0)
                top_confidence = str(_get_report_value(top_result, "sample_confidence", "Unknown"))
                top_run_risk = _run_risk_label(
                    _get_report_value(top_result, "projected_runs_index", 100.0)
                )

                try:
                    numeric_top_score = float(top_score)
                except (TypeError, ValueError):
                    numeric_top_score = 0.0

                if numeric_top_score < 55:
                    short_read = "Best option in the current data, but not a clean matchup."
                else:
                    short_read = "Usable statistical matchup, subject to coach scouting and availability."

                with st.container(border=True):
                    st.markdown(f"**Best available statistical matchup:** {top_pitcher_name}")
                    st.markdown(f"**Fit score:** {_score(top_score)} / 100")
                    st.markdown(f"**Run risk:** {top_run_risk}")
                    st.markdown(f"**Data confidence:** {top_confidence}")
                    st.markdown(f"**Coach read:** {short_read}")
            else:
                st.info("No pitcher rankings are available yet.")

            fit_score_chart_data = _build_fit_score_chart_data(matchup_report)
            _render_fit_score_chart(fit_score_chart_data)

            st.markdown("### Pitcher Ranking")
            pitcher_rows = _build_pitcher_rows(matchup_report)
            if pitcher_rows:
                table_height = min(340, 38 + (35 * len(pitcher_rows)))
                st.dataframe(
                    pitcher_rows,
                    use_container_width=True,
                    hide_index=True,
                    height=table_height,
                )
            else:
                st.info("No pitcher ranking rows are available.")

            st.markdown("### Projected Opponent Lineup")
            st.caption(
                "Projected from season stats. Use as a starting point until official lineup import is supported."
            )

            lineup_rows = _build_lineup_rows(matchup_report)
            if lineup_rows:
                st.dataframe(lineup_rows, use_container_width=True, hide_index=True)
            else:
                st.info("No projected opponent lineup is available.")

            st.markdown("### Assumptions / What Could Change This")
            assumptions = list(matchup_report.get("assumptions") or [])
            if assumptions:
                for assumption in assumptions:
                    st.write(f"- {assumption}")
            else:
                st.caption("No assumptions were provided with this report.")

            with st.expander("Full text report", expanded=False):
                st.text_area(
                    "Pitching matchup report",
                    value=formatted_report,
                    height=700,
                    key="experimental_pitcher_matchup_report_output",
                )

        except Exception as exc:
            st.error(f"Could not generate pitching matchup report: {exc}")
