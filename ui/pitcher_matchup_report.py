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
from core.pitcher_matchup_sim_adapter import run_existing_simulator_pitcher_matchup_report
from core.pitcher_matchups import build_pitcher_matchup_report


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


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _run_prevention_score(projected_runs_index) -> float:
    try:
        risk_index = float(projected_runs_index)
    except (TypeError, ValueError):
        risk_index = 100.0

    return _clamp(100.0 - (((risk_index - 80.0) / 80.0) * 100.0))


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


def _short_pitcher_label(name: str) -> str:
    parts = [part for part in str(name).strip().split() if part]
    if len(parts) >= 2:
        return parts[-1]
    return str(name)


def _build_fit_run_risk_chart_data(report: dict, limit: int = 12) -> pd.DataFrame:
    rows = []

    for rank, result in enumerate(list(report.get("pitcher_rankings") or [])[:limit], start=1):
        pitcher = _get_report_value(result, "pitcher")
        pitcher_name = str(_get_report_value(pitcher, "name", "Unknown pitcher"))
        projected_runs_index = _get_report_value(result, "projected_runs_index", 100.0)

        try:
            fit_score = float(_get_report_value(result, "matchup_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue

        run_prevention_score = _run_prevention_score(projected_runs_index)

        rows.append(
            {
                "Rank": rank,
                "Pitcher": pitcher_name,
                "Label": _short_pitcher_label(pitcher_name),
                "Fit Score": round(fit_score, 1),
                "Run Prevention Score": round(run_prevention_score, 1),
                "Run Risk": _run_risk_label(projected_runs_index),
                "Data Confidence": str(_get_report_value(result, "sample_confidence", "Unknown")),
                "K%": _pct(_get_report_value(pitcher, "k_rate", 0.0)),
                "BB%": _pct(_get_report_value(pitcher, "bb_rate", 0.0)),
                "Free-base%": _pct(_get_report_value(pitcher, "free_base_rate", 0.0)),
                "OBA": _decimal(_get_report_value(pitcher, "oba", 0.0)),
                "OBP Allowed": _decimal(_get_report_value(pitcher, "obp_allowed", 0.0)),
                "Matchup Grade": _ui_matchup_grade(result),
            }
        )

    return pd.DataFrame(rows)


def _render_fit_run_risk_chart(chart_data: pd.DataFrame) -> None:
    st.markdown("### Fit vs. Run Prevention")
    st.caption(
        "Fit Score measures how well the pitcher’s profile matches this opponent lineup. "
        "It rewards strikeout ability, command, traffic control, damage suppression, and usable sample."
    )
    st.caption(
        "Run Prevention Score summarizes traffic and damage risk on a 0–100 scale. "
        "Higher is better. It is not projected runs."
    )
    st.caption(
        "These scores are related, so this chart is best used as a quick visual map, "
        "not a fully independent two-axis model."
    )
    st.caption("Best options are higher and farther right. Upper-right is the preferred zone.")

    if chart_data.empty:
        st.info("No pitcher fit/run-prevention chart data is available.")
        return

    sorted_data = chart_data.sort_values(
        ["Fit Score", "Run Prevention Score"],
        ascending=[False, False],
    ).reset_index(drop=True)

    if alt is None:
        st.dataframe(sorted_data, use_container_width=True, hide_index=True)
        st.caption(
            "Guide: upper-right = best statistical options; lower-left = tough matchup. "
            "Because fit and run prevention share inputs, use this as a visual summary "
            "rather than a strict quadrant model."
        )
        return

    base = alt.Chart(sorted_data).encode(
        x=alt.X(
            "Run Prevention Score:Q",
            scale=alt.Scale(domain=[0, 100]),
            title="Run Prevention Score (higher is better)",
        ),
        y=alt.Y(
            "Fit Score:Q",
            scale=alt.Scale(domain=[0, 100]),
            title="Fit Score (higher is better)",
        ),
        tooltip=[
            alt.Tooltip("Pitcher:N", title="Pitcher"),
            alt.Tooltip("Fit Score:Q", title="Fit Score", format=".1f"),
            alt.Tooltip("Run Prevention Score:Q", title="Run Prevention Score", format=".1f"),
            alt.Tooltip("Run Risk:N", title="Run Risk"),
            alt.Tooltip("Data Confidence:N", title="Data Confidence"),
            alt.Tooltip("K%:N", title="K%"),
            alt.Tooltip("BB%:N", title="BB%"),
            alt.Tooltip("Free-base%:N", title="Free-base%"),
            alt.Tooltip("OBA:N", title="OBA"),
            alt.Tooltip("OBP Allowed:N", title="OBP Allowed"),
            alt.Tooltip("Matchup Grade:N", title="Matchup Grade"),
        ],
    )

    points = base.mark_circle(size=90)

    labels = alt.Chart(sorted_data).mark_text(
        align="left",
        baseline="middle",
        dx=8,
        dy=-4,
        fontSize=12,
        color="#E5E7EB",
    ).encode(
        x=alt.X("Run Prevention Score:Q"),
        y=alt.Y("Fit Score:Q"),
        text=alt.Text("Label:N"),
    )

    fit_reference = alt.Chart(pd.DataFrame({"Fit Score": [55.0]})).mark_rule(
        strokeDash=[4, 4],
    ).encode(
        y="Fit Score:Q",
    )

    prevention_reference = alt.Chart(pd.DataFrame({"Run Prevention Score": [50.0]})).mark_rule(
        strokeDash=[4, 4],
    ).encode(
        x="Run Prevention Score:Q",
    )

    chart = alt.layer(
        fit_reference,
        prevention_reference,
        points,
        labels,
    ).properties(height=360)

    st.altair_chart(chart, width="stretch")
    st.caption(
        "Guide: upper-right = best statistical options; lower-left = tough matchup. "
        "Because fit and run prevention share inputs, use this as a visual summary "
        "rather than a strict quadrant model."
    )


def _get_sim_value(item, key: str, default=None):
    if item is None:
        return default

    if isinstance(item, dict):
        return item.get(key, default)

    return getattr(item, key, default)


def _sample_label(reliability: str) -> str:
    return {
        "High": "Large",
        "Medium": "Medium",
        "Low": "Small",
    }.get(str(reliability or "").strip(), "Unknown")


def _sample_phrase(reliability: str) -> str:
    label = _sample_label(reliability).lower()
    if label == "unknown":
        return "unknown pitching sample"
    return f"{label} pitching sample"


def _role_sample_label(role: str, reliability: str) -> str:
    role_mapping = {
        "Established pitching sample": "Large pitching sample",
        "Usable but still developing sample": "Medium pitching sample",
        "Limited pitching sample": "Small pitching sample",
        "Emergency/depth sample": "Small pitching sample",
    }

    cleaned_role = str(role or "").strip()
    if cleaned_role in role_mapping:
        return role_mapping[cleaned_role]

    return _sample_phrase(reliability).capitalize()


def _build_simulation_rows(simulation_results: list) -> list[dict]:
    rows = []

    for result in simulation_results or []:
        raw_runs = float(_get_sim_value(result, "raw_avg_runs_allowed", 0.0) or 0.0)
        adjusted_runs = float(_get_sim_value(result, "adjusted_avg_runs_allowed", raw_runs) or raw_runs)
        hold_le_3 = float(_get_sim_value(result, "hold_le_3_rate", 0.0) or 0.0)
        allow_7_plus = float(_get_sim_value(result, "allow_7_plus_rate", 0.0) or 0.0)
        pitcher_bf = int(_get_sim_value(result, "pitcher_bf", 0) or 0)
        pitcher_ip = float(_get_sim_value(result, "pitcher_ip", 0.0) or 0.0)
        reliability = str(_get_sim_value(result, "reliability", "Unknown") or "Unknown")
        role = str(_get_sim_value(result, "role_caution", "") or "")

        rows.append(
            {
                "Pitcher": str(_get_sim_value(result, "pitcher_name", "Unknown pitcher")),
                "Raw Runs": raw_runs,
                "Adjusted Runs": adjusted_runs,
                "Sample": _sample_label(reliability),
                "Hold ≤3": hold_le_3,
                "7+ Risk": allow_7_plus,
                "BF": pitcher_bf,
                "IP": pitcher_ip,
                "_reliability": reliability,
                "_raw_role": role,
                "_adjustment_gap": adjusted_runs - raw_runs,
            }
        )

    return sorted(rows, key=lambda row: (row["Adjusted Runs"], row["Raw Runs"], -row["BF"]))


def _format_simulation_rows_for_table(rows: list[dict]) -> list[dict]:
    formatted_rows = []

    for row in rows:
        formatted_rows.append(
            {
                "Pitcher": row["Pitcher"],
                "Raw Runs": f"{float(row['Raw Runs']):.2f}",
                "Adjusted Runs": f"{float(row['Adjusted Runs']):.2f}",
                "Sample": row["Sample"],
                "Hold ≤3": f"{float(row['Hold ≤3']):.1%}",
                "7+ Risk": f"{float(row['7+ Risk']):.1%}",
                "BF": int(row["BF"]),
                "IP": f"{float(row['IP']):.1f}",
            }
        )

    return formatted_rows


def _render_simulation_backed_pitching_plan(simulation_results: list) -> None:
    rows = _build_simulation_rows(simulation_results)

    st.markdown("### Projected Runs Allowed vs. This Opponent Lineup")
    st.caption(
        "This ranks your pitching options using the app’s game simulator against the projected opponent lineup. "
        "Lower adjusted runs is better; the adjustment adds caution when the available pitching sample is small."
    )

    if not rows:
        st.info("No simulation-backed pitching plan is available yet.")
        return

    best_usable = next(
        (row for row in rows if row["_reliability"] in {"High", "Medium"}),
        rows[0],
    )
    safest_established = next(
        (row for row in rows if row["_reliability"] == "High"),
        None,
    )
    high_variance = next(
        (
            row
            for row in sorted(rows, key=lambda item: item["Raw Runs"])
            if row["_reliability"] == "Low" and row["_adjustment_gap"] >= 0.75
        ),
        None,
    )
    small_sample_rows = [
        row
        for row in rows
        if row["Sample"] == "Small"
    ]

    chart_data = pd.DataFrame(rows)

    if alt is not None and not chart_data.empty:
        chart = (
            alt.Chart(chart_data)
            .mark_bar()
            .encode(
                y=alt.Y(
                    "Pitcher:N",
                    sort=alt.EncodingSortField(field="Adjusted Runs", order="ascending"),
                    title=None,
                ),
                x=alt.X(
                    "Adjusted Runs:Q",
                    title="Adjusted runs allowed",
                ),
                tooltip=[
                    alt.Tooltip("Pitcher:N"),
                    alt.Tooltip("Raw Runs:Q", title="Raw Runs", format=".2f"),
                    alt.Tooltip("Adjusted Runs:Q", title="Adjusted Runs", format=".2f"),
                    alt.Tooltip("Sample:N"),
                    alt.Tooltip("Hold ≤3:Q", format=".1%"),
                    alt.Tooltip("7+ Risk:Q", format=".1%"),
                ],
            )
            .properties(height=max(180, 30 * len(rows)))
        )

        st.altair_chart(chart, width="stretch")

    card_cols = st.columns(2)

    with card_cols[0]:
        st.markdown("#### Best usable option")
        st.markdown(
            f"**{best_usable['Pitcher']}** — "
            f"{best_usable['Adjusted Runs']:.2f} adjusted runs "
            f"({_sample_phrase(best_usable['_reliability'])})."
        )

        st.markdown("#### Safest established read")
        if safest_established:
            st.markdown(
                f"**{safest_established['Pitcher']}** — "
                f"{safest_established['Adjusted Runs']:.2f} adjusted runs "
                f"over {safest_established['IP']:.1f} IP / {int(safest_established['BF'])} BF."
            )
        else:
            st.caption("No high-reliability pitching sample is available in this report.")

    with card_cols[1]:
        st.markdown("#### High-variance upside")
        if high_variance:
            st.markdown(
                f"**{high_variance['Pitcher']}** had a strong raw simulation result, "
                f"but only has {high_variance['IP']:.1f} IP / {int(high_variance['BF'])} BF in the data, "
                "so the adjusted score adds a small-sample caution."
            )
        else:
            st.caption("No low-reliability upside arm was flagged by the simulation.")

        st.markdown("#### Small-sample caution")
        if small_sample_rows:
            st.markdown(
                ", ".join(row["Pitcher"] for row in small_sample_rows)
                + " have small pitching samples in the data, so treat their simulation results as directional "
                "until coach scouting or more innings confirm them."
            )
        else:
            st.caption("No small pitching sample was flagged.")

    st.dataframe(
        _format_simulation_rows_for_table(rows),
        use_container_width=True,
        hide_index=True,
    )

    st.caption("Raw Runs is the direct simulated average runs allowed.")
    st.caption("Adjusted Runs is the sample-size cautious recommendation score.")
    st.caption("Sample describes the pitcher’s available innings and batters faced in the data.")
    st.caption(
        "Use the top recommendation as a planning input, then layer in real-world coaching context "
        "such as availability, defense, pitch count, and whether the pitcher also plays a key defensive position."
    )


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

                simulation_results = []
                simulation_error = None

                try:
                    simulation_results = run_existing_simulator_pitcher_matchup_report(
                        projected_lineup=list(matchup_report.get("projected_lineup") or []),
                        pitchers=list(matchup_report.get("pitchers") or []),
                        games=3000,
                        innings_per_game=7,
                        seed=42,
                        target_runs=4.0,
                    )
                except Exception as exc:
                    simulation_error = exc

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

            if simulation_error is not None:
                st.warning("Simulation-backed pitcher report could not be generated yet.")
            else:
                _render_simulation_backed_pitching_plan(list(simulation_results))

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

        except Exception as exc:
            st.error(f"Could not generate pitching matchup report: {exc}")
