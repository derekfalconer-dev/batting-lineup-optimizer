from pathlib import Path
import streamlit as st

from core.models import (
    GameStrategy,
    CoachingStyle,
    OpposingPitchingStrength,
    OpponentLevel,
)

from core.schemas import SessionStateSchema

from ui.upload_helpers import save_uploaded_file

from core.api_service import (
    import_opponent_maxpreps_pdf,
    list_opponent_reports,
    get_active_opponent_context,
    select_active_opponent_pitcher,
    delete_opponent_report,
)

from core.opponent_profiles import build_manual_pitcher_profile


def get_saved_rules_for_active_team() -> tuple[str, dict]:
    try:
        from core.session_manager import get_session_manager

        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        return (
            getattr(team, "rules_preset", "High School") or "High School",
            dict(getattr(team, "rules_config", {}) or {}),
        )
    except Exception:
        return "High School", {}


def save_rules_for_active_team(
    *,
    rules_preset: str,
    rules_config: dict,
) -> None:
    try:
        from core.session_manager import get_session_manager

        manager = get_session_manager()
        team = manager.get_workspace_team_for_session(
            st.session_state.optimizer_session_id
        )
        team.rules_preset = str(rules_preset)
        team.rules_config = dict(rules_config)
        manager.mark_workspace_dirty(st.session_state.optimizer_session_id)
    except Exception:
        pass


def _pitcher_payload_number(pitcher: dict, field: str) -> float:
    source_row = pitcher.get("source_row") if isinstance(pitcher, dict) else None

    raw_value = pitcher.get(field, 0) if isinstance(pitcher, dict) else getattr(pitcher, field, 0)
    if raw_value in (None, "", 0, 0.0) and isinstance(source_row, dict):
        raw_value = source_row.get(field, 0)

    try:
        return float(raw_value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_usable_opponent_pitcher_payload(pitcher: dict) -> bool:
    """
    UI dropdown filter for serialized MaxPreps pitcher profiles.

    The parsed MaxPreps row is the source of truth. Some parser fields are
    serialized inside source_row, so check both profile-level and source-row
    values before deciding whether a pitcher belongs in the selector.
    """
    name = str(pitcher.get("name", "") or "").strip().lower()
    if not name or name == "n. player":
        return False

    return (
        _pitcher_payload_number(pitcher, "innings_pitched") > 0
        or _pitcher_payload_number(pitcher, "batters_faced") > 0
        or _pitcher_payload_number(pitcher, "pitches") > 0
        or _pitcher_payload_number(pitcher, "strikeouts") > 0
        or _pitcher_payload_number(pitcher, "walks") > 0
    )


def _usable_opponent_pitcher_payloads(pitchers: list[dict]) -> list[dict]:
    usable_pitchers = [
        pitcher
        for pitcher in list(pitchers or [])
        if _is_usable_opponent_pitcher_payload(pitcher)
    ]

    usable_pitchers.sort(
        key=lambda pitcher: (
            -_pitcher_payload_number(pitcher, "innings_pitched"),
            -_pitcher_payload_number(pitcher, "batters_faced"),
            -_pitcher_payload_number(pitcher, "pitches"),
            str(pitcher.get("name", "")),
        )
    )

    return usable_pitchers


def render_opponent_defense_level_control(default_label: str = "Average") -> str:
    defense_options = ["Weak", "Average", "Strong"]
    if default_label not in defense_options:
        default_label = "Average"

    return st.sidebar.selectbox(
        "Opponent Defense Level",
        defense_options,
        index=defense_options.index(default_label),
        help=(
            "Models the opponent's team defense and fielding context. "
            "Pitcher quality is handled by MaxPreps or Manual Pitcher scouting."
        ),
    )


def _has_present_diagnostic_value(value: object) -> bool:
    return value not in (None, "", "—")


def _imported_pitcher_model_read(pitcher: dict) -> str:
    kx = float(pitcher.get("strikeout_multiplier", 1.0) or 1.0)
    bbx = float(pitcher.get("walk_multiplier", 1.0) or 1.0)
    cx = float(pitcher.get("contact_multiplier", 1.0) or 1.0)
    px = float(pitcher.get("power_multiplier", 1.0) or 1.0)

    k_rate = float(pitcher.get("k_rate", 0.0) or 0.0)
    bb_rate = float(pitcher.get("bb_rate", 0.0) or 0.0)

    read_parts = []

    if kx >= 1.15 or k_rate >= 0.24:
        read_parts.append("creates more swing-and-miss than average")
    elif kx <= 0.90 or k_rate <= 0.12:
        read_parts.append("allows more balls in play than average")

    if bbx <= 0.90 or bb_rate <= 0.07:
        read_parts.append("limits free passes")
    elif bbx >= 1.15 or bb_rate >= 0.13:
        read_parts.append("can give away baserunners")

    if cx <= 0.92:
        read_parts.append("puts extra pressure on contact quality")
    elif cx >= 1.08:
        read_parts.append("is more hittable on contact")

    if px >= 1.10:
        read_parts.append("allows more damage when hitters connect")
    elif px <= 0.92:
        read_parts.append("mutes power impact")

    if read_parts:
        pitcher_read = "Imported MaxPreps data suggests this pitcher " + ", ".join(read_parts) + "."
    else:
        pitcher_read = "Imported MaxPreps data suggests this pitcher is close to a balanced profile."

    coaching_read = (
        "The optimizer will slightly reward contact stability, avoid stacking too many high-K bats, "
        "and value hitters who can extend innings."
    )

    if bbx >= 1.15 or bb_rate >= 0.13:
        coaching_read = (
            "The optimizer will value patient hitters who can force deep counts and turn wildness "
            "into traffic."
        )
    elif cx >= 1.08 or px >= 1.10:
        coaching_read = (
            "The optimizer may give more credit to damage bats because this profile allows more "
            "productive contact."
        )
    elif kx <= 0.90 or k_rate <= 0.12:
        coaching_read = (
            "The optimizer can lean more normally on the full lineup because this profile creates "
            "less strikeout pressure."
        )

    return f"{pitcher_read} {coaching_read}"


def render_opponent_scouting_panel() -> str:
    st.sidebar.markdown("## 🧢 Opponent Scouting")
    st.sidebar.caption(
        "Choose how to model tonight’s opposing pitcher: imported MaxPreps report, "
        "manual scouting, or a generic baseline."
    )

    team_key = str(st.session_state.get("selected_team_id", "no_team"))
    active_context = get_active_opponent_context(st.session_state.optimizer_session_id)
    active_report = (active_context or {}).get("report") or {}
    active_pitcher = (active_context or {}).get("pitcher") or {}
    active_report_pitchers = list(active_report.get("pitchers", []) or [])
    has_usable_maxpreps_context = bool(
        active_report.get("opponent_report_id")
        and any(_is_usable_opponent_pitcher_payload(p) for p in active_report_pitchers)
    )

    opponent_source_options = ["MaxPreps Report", "Manual Pitcher", "Generic"]
    default_opponent_source = "MaxPreps Report" if has_usable_maxpreps_context else "Generic"

    opponent_source = st.sidebar.radio(
        "Opponent Source",
        opponent_source_options,
        index=opponent_source_options.index(default_opponent_source),
        key=f"opponent_source_{team_key}",
        help=(
            "Use a MaxPreps report when available, create a manual pitcher profile "
            "when you only have scouting notes, or fall back to generic opponent settings."
        ),
    )

    st.session_state[f"use_opponent_context_{team_key}"] = (
        opponent_source == "MaxPreps Report" and has_usable_maxpreps_context
    )

    if opponent_source == "Generic":
        st.sidebar.info("Using generic opponent settings below.")
        return render_opponent_defense_level_control()

    if opponent_source == "Manual Pitcher":
        st.sidebar.markdown("### 🎯 Manual Pitcher")

        manual_name = st.sidebar.text_input(
            "Pitcher Name",
            value="Manual Pitcher",
            key=f"manual_pitcher_name_{team_key}",
        )

        manual_hand = st.sidebar.selectbox(
            "Throws",
            ["R", "L", "Unknown"],
            index=0,
            key=f"manual_pitcher_hand_{team_key}",
        )

        manual_velo = st.sidebar.selectbox(
            "Velocity",
            ["Soft", "Average", "Hard", "Very Hard"],
            index=1,
            key=f"manual_pitcher_velo_{team_key}",
        )

        manual_k_rate = st.sidebar.selectbox(
            "Strikeout Ability",
            ["Low", "Average", "High", "Elite"],
            index=1,
            key=f"manual_pitcher_k_rate_{team_key}",
        )

        manual_bb_rate = st.sidebar.selectbox(
            "Control / Walk Rate",
            ["Low", "Average", "High", "Wild"],
            index=1,
            key=f"manual_pitcher_bb_rate_{team_key}",
        )

        manual_contact = st.sidebar.selectbox(
            "Contact Allowed",
            ["Weak", "Average", "Hard"],
            index=1,
            key=f"manual_pitcher_contact_{team_key}",
        )

        manual_profile = build_manual_pitcher_profile(
            name=manual_name,
            hand=None if manual_hand == "Unknown" else manual_hand,
            velo=manual_velo,
            k_rate=manual_k_rate,
            bb_rate=manual_bb_rate,
            contact=manual_contact,
        )

        st.session_state[f"manual_opponent_pitcher_profile_{team_key}"] = manual_profile.as_dict()

        st.sidebar.markdown(
            f"""
            <div style="
                border: 1px solid rgba(128,128,128,.25);
                border-radius: .65rem;
                padding: .65rem .75rem;
                margin: .5rem 0 .75rem 0;
                background: rgba(128,128,128,.06);
            ">
                <div style="font-size:.78rem; opacity:.72; font-weight:700;">
                    Manual Pitcher
                </div>
                <div style="font-size:1rem; font-weight:800; margin-top:.1rem;">
                    {manual_profile.label}
                </div>
                <div style="font-size:.82rem; margin-top:.35rem; opacity:.85;">
                    {manual_profile.scouting_note}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.sidebar.expander("Manual pitcher model impact", expanded=False):
            st.write(f"Strikeouts: x{manual_profile.strikeout_multiplier:.2f}")
            st.write(f"Walks: x{manual_profile.walk_multiplier:.2f}")
            st.write(f"Contact: x{manual_profile.contact_multiplier:.2f}")
            st.write(f"Power: x{manual_profile.power_multiplier:.2f}")

        return render_opponent_defense_level_control()

    # MaxPreps Report mode continues below.
    reports = list_opponent_reports(st.session_state.optimizer_session_id)

    if not reports:
        st.sidebar.info(
            "No opponent report imported yet. Upload a MaxPreps PDF to enable pitcher scouting, "
            "or continue with generic opponent settings below."
        )

        uploaded_pdf = st.sidebar.file_uploader(
            "MaxPreps PDF",
            type=["pdf"],
            key=f"opponent_maxpreps_pdf_{st.session_state.get('selected_team_id', 'no_team')}",
            help="Upload the printable MaxPreps baseball stats PDF for the opposing team.",
        )

        if uploaded_pdf is not None:
            if st.sidebar.button(
                "Import Opponent Report",
                use_container_width=True,
                key=f"import_opponent_report_{st.session_state.get('selected_team_id', 'no_team')}",
            ):
                try:
                    pdf_path = save_uploaded_file(
                        uploaded_pdf,
                        target_name=f"opponent_{Path(uploaded_pdf.name).name}",
                    )

                    payload = import_opponent_maxpreps_pdf(
                        st.session_state.optimizer_session_id,
                        pdf_path=pdf_path,
                        source_file_name=Path(uploaded_pdf.name).name,
                    )

                    st.sidebar.success(
                        f"Imported {payload.get('team_name', 'opponent')} "
                        f"with {len(payload.get('pitchers', []) or [])} pitcher profiles."
                    )

                    parser_warnings = list(payload.get("parser_warnings", []) or [])
                    parser_stats = dict(payload.get("parser_stats", {}) or {})

                    if parser_warnings:
                        st.sidebar.warning("Imported with parser warnings: " + " ".join(str(w) for w in parser_warnings))

                    if parser_stats:
                        with st.sidebar.expander("Parser details", expanded=False):
                            st.json(parser_stats)

                    st.rerun()

                except Exception as exc:
                    st.sidebar.error(f"Could not import opponent report: {exc}")

        return render_opponent_defense_level_control()

    report_labels = []
    report_by_label = {}

    for report in reports:
        label = str(report.get("team_name") or "Opponent")
        season = report.get("season")
        source_name = report.get("source_file_name")

        bits = [label]
        if season:
            bits.append(str(season))
        if source_name:
            bits.append(str(source_name))

        display_label = " • ".join(bits)

        # Avoid accidental duplicate selectbox labels.
        suffix = 2
        unique_label = display_label
        while unique_label in report_by_label:
            suffix += 1
            unique_label = f"{display_label} ({suffix})"

        report_labels.append(unique_label)
        report_by_label[unique_label] = report

    active_context = get_active_opponent_context(st.session_state.optimizer_session_id)
    use_opponent_context = bool(
        st.session_state.get(f"use_opponent_context_{team_key}", False)
    )

    if use_opponent_context and active_context is None and reports:
        latest_report = reports[-1]
        latest_pitchers = _usable_opponent_pitcher_payloads(
            list(latest_report.get("pitchers", []) or [])
        )

        if latest_pitchers:
            try:
                select_active_opponent_pitcher(
                    st.session_state.optimizer_session_id,
                    opponent_report_id=str(latest_report["opponent_report_id"]),
                    pitcher_name=str(latest_pitchers[0]["name"]),
                )
                st.rerun()
            except Exception as exc:
                st.sidebar.error(f"Could not enable opponent scouting report: {exc}")

    active_report_id = None
    active_pitcher_name = None

    if active_context:
        active_report = active_context.get("report") or {}
        active_pitcher = active_context.get("pitcher") or {}

        active_report_id = active_report.get("opponent_report_id")
        active_pitcher_name = active_pitcher.get("name")

    # If the saved active report exists but the selected pitcher is missing
    # or stale, select the first available pitcher from that report.
    if active_report_id and not active_pitcher_name:
        matching_report = next(
            (
                report for report in reports
                if str(report.get("opponent_report_id")) == str(active_report_id)
            ),
            None,
        )

        matching_pitchers = _usable_opponent_pitcher_payloads(
            list((matching_report or {}).get("pitchers", []) or [])
        )

        if matching_pitchers:
            try:
                select_active_opponent_pitcher(
                    st.session_state.optimizer_session_id,
                    opponent_report_id=str(active_report_id),
                    pitcher_name=str(matching_pitchers[0]["name"]),
                )
                st.rerun()
            except Exception as exc:
                st.sidebar.error(f"Could not repair opponent pitcher selection: {exc}")

    default_report_index = 0
    for idx, label in enumerate(report_labels):
        if report_by_label[label].get("opponent_report_id") == active_report_id:
            default_report_index = idx
            break

    selected_report_label = st.sidebar.selectbox(
        "Opponent Report",
        options=report_labels,
        index=default_report_index,
        key=f"opponent_report_select_{st.session_state.get('selected_team_id', 'no_team')}",
    )

    selected_report = report_by_label[selected_report_label]
    pitchers = _usable_opponent_pitcher_payloads(
        list(selected_report.get("pitchers", []) or [])
    )

    parser_warnings = list(selected_report.get("parser_warnings", []) or [])
    parser_stats = dict(selected_report.get("parser_stats", {}) or {})

    if not pitchers:
        st.session_state[f"use_opponent_context_{team_key}"] = False
        st.sidebar.warning(
            "This opponent report has no usable pitcher profiles with real pitching workload. "
            "Using generic opponent settings instead."
        )
        return render_opponent_defense_level_control()

    pitcher_names = [str(p.get("name", "Unknown pitcher")) for p in pitchers]

    default_pitcher_index = 0
    if active_pitcher_name in pitcher_names:
        default_pitcher_index = pitcher_names.index(active_pitcher_name)

    selected_pitcher_name = st.sidebar.selectbox(
        "Expected Opposing Pitcher",
        options=pitcher_names,
        index=default_pitcher_index,
        key=f"opponent_pitcher_select_{selected_report.get('opponent_report_id', 'report')}",
    )

    selected_pitcher = next(
        p for p in pitchers if str(p.get("name", "")) == selected_pitcher_name
    )

    if (
        selected_report.get("opponent_report_id") != active_report_id
        or selected_pitcher_name != active_pitcher_name
    ):
        try:
            select_active_opponent_pitcher(
                st.session_state.optimizer_session_id,
                opponent_report_id=str(selected_report["opponent_report_id"]),
                pitcher_name=selected_pitcher_name,
            )
            st.rerun()
        except Exception as exc:
            st.sidebar.error(f"Could not select opponent pitcher: {exc}")

    st.sidebar.markdown(
        f"""
        <div style="
            border: 1px solid rgba(255,255,255,.22);
            border-radius: .7rem;
            padding: .65rem .75rem;
            margin: .45rem 0 .55rem 0;
            background: rgba(255,255,255,.06);
        ">
            <div style="font-size:.72rem; opacity:.72; font-weight:700; text-transform:uppercase;">
                Pitcher profile
            </div>
            <div style="font-size:1.05rem; font-weight:850; line-height:1.25; margin-top:.15rem;">
                {selected_pitcher.get('label', 'Pitcher profile')}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    k_rate_text = f"{float(selected_pitcher.get('k_rate', 0.0)):.1%}"
    bb_rate_text = f"{float(selected_pitcher.get('bb_rate', 0.0)):.1%}"
    sample_size = str(selected_pitcher.get("confidence", "—"))

    if sample_size == "High":
        sample_text = "Large"
    elif sample_size == "Medium":
        sample_text = "Medium"
    elif sample_size == "Low":
        sample_text = "Small"
    else:
        sample_text = sample_size

    st.sidebar.markdown(
        f"""
        <div style="
            display:grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: .5rem;
            margin: .55rem 0 .75rem 0;
        ">
            <div>
                <div style="font-size:.72rem; opacity:.72; font-weight:700;">K%</div>
                <div style="font-size:1.15rem; font-weight:800;">{k_rate_text}</div>
            </div>
            <div>
                <div style="font-size:.72rem; opacity:.72; font-weight:700;">BB%</div>
                <div style="font-size:1.15rem; font-weight:800;">{bb_rate_text}</div>
            </div>
            <div>
                <div style="font-size:.72rem; opacity:.72; font-weight:700;">Sample</div>
                <div style="font-size:1.15rem; font-weight:800;">{sample_text}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    ip = float(selected_pitcher.get("innings_pitched", 0.0) or 0.0)
    bf = int(selected_pitcher.get("batters_faced", 0) or 0)

    if sample_size == "Low":
        st.sidebar.warning(
            f"{sample_text} data sample: {ip:.1f} IP, {bf} batters faced. "
            "Use this as a scouting hint and combine it with coach judgment."
        )
    elif sample_size == "Medium":
        st.sidebar.caption(
            f"Medium sample: {ip:.1f} IP / {bf} BF — good directional read; "
            "confirm with coach judgment."
        )

    scouting_note = selected_pitcher.get("scouting_note")
    if scouting_note:
        st.sidebar.markdown(
            f"""
            <div style="
                border: 1px solid rgba(128,128,128,.22);
                border-radius: .6rem;
                padding: .55rem .65rem;
                margin: .45rem 0 .65rem 0;
                background: rgba(128,128,128,.045);
            ">
                <div style="font-size:.72rem; opacity:.68; font-weight:700;">
                    Coach read:
                </div>
                <div style="font-size:.82rem; opacity:.86; line-height:1.35; margin-top:.18rem;">
                    {str(scouting_note)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    defense_level = selected_report.get("derived_opponent_level", "average")
    fielding_pct = selected_report.get("fielding_pct")

    if fielding_pct is not None:
        st.sidebar.caption(
            f"Opponent defense: {float(fielding_pct):.3f} FP → {str(defense_level).title()} level"
        )

    opponent_level_label = render_opponent_defense_level_control(
        str(defense_level).title()
    )

    pitching_rows_parsed = (
        parser_stats.get("pitching_rows_parsed")
        or parser_stats.get("pitching_rows")
        or parser_stats.get("pitching_rows_seen")
    )
    pitching_rows_skipped = (
        parser_stats.get("pitching_rows_skipped")
        or parser_stats.get("skipped_pitching_rows")
        or parser_stats.get("pitching_rows_ignored")
    )
    parser_status = "Imported with parser notes" if parser_warnings else (
        "Imported successfully" if parser_stats or pitchers else None
    )

    diagnostic_rows = []
    if pitchers:
        diagnostic_rows.append(f"- Pitchers loaded: **{len(pitchers)}**")
    if _has_present_diagnostic_value(pitching_rows_parsed):
        diagnostic_rows.append(f"- Pitching rows parsed: **{pitching_rows_parsed}**")
    if _has_present_diagnostic_value(pitching_rows_skipped):
        diagnostic_rows.append(f"- Pitching rows skipped: **{pitching_rows_skipped}**")
    if _has_present_diagnostic_value(parser_status):
        diagnostic_rows.append(f"- Source/parser status: **{parser_status}**")

    with st.sidebar.expander("Scouting details", expanded=False):
        st.markdown("**Model impact**")

        st.markdown(
            f"""
            <div style="
                border: 1px solid rgba(128,128,128,.22);
                border-radius: .6rem;
                padding: .55rem .65rem;
                margin: .35rem 0 .65rem 0;
                background: rgba(128,128,128,.045);
            ">
                <div style="font-size:.72rem; opacity:.68; font-weight:700;">
                    {selected_pitcher.get('label', 'Pitcher profile')}
                </div>
                <div style="font-size:.82rem; margin-top:.25rem;">
                    K% <b>{k_rate_text}</b> &nbsp; BB% <b>{bb_rate_text}</b> &nbsp; Sample <b>{sample_text}</b>
                </div>
                <div style="font-size:.82rem; line-height:1.35; margin-top:.45rem;">
                    Strikeouts: <b>x{float(selected_pitcher.get('strikeout_multiplier', 1.0) or 1.0):.2f}</b><br>
                    Walks: <b>x{float(selected_pitcher.get('walk_multiplier', 1.0) or 1.0):.2f}</b><br>
                    Contact: <b>x{float(selected_pitcher.get('contact_multiplier', 1.0) or 1.0):.2f}</b><br>
                    Power: <b>x{float(selected_pitcher.get('power_multiplier', 1.0) or 1.0):.2f}</b>
                </div>
                <div style="font-size:.72rem; opacity:.68; font-weight:700; margin-top:.55rem;">
                    Coach read:
                </div>
                <div style="font-size:.82rem; opacity:.86; line-height:1.35; margin-top:.18rem;">
                    {_imported_pitcher_model_read(selected_pitcher)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if diagnostic_rows or parser_warnings:
            st.caption("Import summary")
            if diagnostic_rows:
                st.markdown("\n".join(diagnostic_rows))

            if parser_warnings:
                st.caption("Parser notes:")
                for warning in parser_warnings:
                    st.caption(f"- {warning}")

    with st.sidebar.expander("Change report", expanded=False):
        uploaded_pdf = st.file_uploader(
            "MaxPreps PDF",
            type=["pdf"],
            key=f"opponent_maxpreps_pdf_{st.session_state.get('selected_team_id', 'no_team')}",
            help="Upload the printable MaxPreps baseball stats PDF for the opposing team.",
        )

        if uploaded_pdf is not None:
            if st.button(
                "Import Opponent Report",
                use_container_width=True,
                key=f"import_opponent_report_{st.session_state.get('selected_team_id', 'no_team')}",
            ):
                try:
                    pdf_path = save_uploaded_file(
                        uploaded_pdf,
                        target_name=f"opponent_{Path(uploaded_pdf.name).name}",
                    )

                    payload = import_opponent_maxpreps_pdf(
                        st.session_state.optimizer_session_id,
                        pdf_path=pdf_path,
                        source_file_name=Path(uploaded_pdf.name).name,
                    )

                    st.success(
                        f"Imported {payload.get('team_name', 'opponent')} "
                        f"with {len(payload.get('pitchers', []) or [])} pitcher profiles."
                    )

                    parser_warnings = list(payload.get("parser_warnings", []) or [])
                    parser_stats = dict(payload.get("parser_stats", {}) or {})

                    if parser_warnings:
                        st.warning("Imported with parser warnings: " + " ".join(str(w) for w in parser_warnings))

                    if parser_stats:
                        with st.expander("Parser details", expanded=False):
                            st.json(parser_stats)

                    st.rerun()

                except Exception as exc:
                    st.error(f"Could not import opponent report: {exc}")

    with st.sidebar.expander("Manage reports", expanded=False):
        st.caption(
            "Deleting removes this scouting report from the current team. "
            "This cannot be undone."
        )

        confirm_delete = st.checkbox(
            "Yes, delete this opponent report",
            key=f"confirm_delete_opponent_report_{selected_report.get('opponent_report_id', 'report')}",
        )

        if st.button(
                "Delete Opponent Report",
                use_container_width=True,
                disabled=not confirm_delete,
                key=f"delete_opponent_report_{selected_report.get('opponent_report_id', 'report')}",
        ):
            try:
                delete_opponent_report(
                    st.session_state.optimizer_session_id,
                    opponent_report_id=str(selected_report["opponent_report_id"]),
                )
                st.success("Opponent report deleted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete opponent report: {exc}")

    return opponent_level_label


def render_sidebar(session_state: SessionStateSchema) -> dict:

    saved_rules_preset, saved_rules_config = get_saved_rules_for_active_team()
    saved_rules_config = dict(saved_rules_config or {})

    open_game_rules = bool(st.session_state.pop("open_game_rules_on_new_team", False))

    with st.sidebar.expander("⚾ Game Rules", expanded=open_game_rules):
        st.caption("Choose a preset, then tweak if needed.")

        preset_options = [
            "Little League",
            "Intermediate",
            "High School",
            "College",
            "Manual",
        ]

        default_preset = (
            saved_rules_preset
            if saved_rules_preset in preset_options
            else "High School"
        )

        rules_preset = st.selectbox(
            "Rules preset",
            options=preset_options,
            index=preset_options.index(default_preset),
            key=f"rules_preset_{st.session_state.get('selected_team_id', 'no_team')}",
        )

        preset_changed = rules_preset != saved_rules_preset
        if preset_changed:
            saved_rules_config = {}

        preset_defaults = {
            "Little League": {
                "innings": 6,
                "diamond": "46/60",
                "leadoffs": False,
                "run_limit": False,
                "continuous_batting": True,
            },
            "Intermediate": {
                "innings": 7,
                "diamond": "50/70",
                "leadoffs": True,
                "run_limit": False,
                "continuous_batting": True,
            },
            "High School": {
                "innings": 7,
                "diamond": "60/90",
                "leadoffs": True,
                "run_limit": False,
                "continuous_batting": False,
            },
            "College": {
                "innings": 9,
                "diamond": "60/90",
                "leadoffs": True,
                "run_limit": False,
                "continuous_batting": False,
            },
            "Manual": {
                "innings": int(saved_rules_config.get("innings", 7)),
                "diamond": str(saved_rules_config.get("diamond_size", "60/90")),
                "leadoffs": bool(saved_rules_config.get("leadoffs_allowed", True)),
                "run_limit": bool(saved_rules_config.get("use_inning_run_limit", False)),
                "continuous_batting": bool(saved_rules_config.get("continuous_batting", False)),
            },
        }

        preset = preset_defaults[rules_preset]

        if rules_preset == "Manual":
            st.info("Manual mode: use the controls below to create any custom rule set.")

        team_key = str(st.session_state.get("selected_team_id", "no_team"))
        preset_key = f"{team_key}_{rules_preset.lower().replace(' ', '_').replace('/', '_')}"

        innings_per_game = st.slider(
            "Innings / Game",
            3,
            9,
            int(saved_rules_config.get("innings", preset["innings"])),
            key=f"innings_per_game_{preset_key}",
        )

        continuous_batting = st.checkbox(
            "Continuous Batting",
            value=bool(saved_rules_config.get("continuous_batting", preset["continuous_batting"])),
            key=f"continuous_batting_{preset_key}",
        )

        use_inning_run_limit = st.checkbox(
            "Inning Run Limit",
            value=bool(saved_rules_config.get("use_inning_run_limit", preset["run_limit"])),
            key=f"use_inning_run_limit_{preset_key}",
        )

        inning_run_limit = None
        if use_inning_run_limit:
            inning_run_limit = st.number_input(
                "Max runs per inning",
                min_value=1,
                max_value=20,
                value=int(saved_rules_config.get("inning_run_limit") or 5),
                key=f"inning_run_limit_{preset_key}",
            )

        diamond_options = ["46/60", "50/70", "60/90"]
        saved_diamond = str(saved_rules_config.get("diamond_size", preset["diamond"]))
        if saved_diamond not in diamond_options:
            saved_diamond = preset["diamond"]

        diamond_size = st.selectbox(
            "Diamond Size",
            diamond_options,
            index=diamond_options.index(saved_diamond),
            key=f"diamond_size_{preset_key}",
        )

        leadoffs_allowed = st.checkbox(
            "Leadoffs Allowed",
            value=bool(saved_rules_config.get("leadoffs_allowed", preset["leadoffs"])),
            key=f"leadoffs_allowed_{preset_key}",
        )

    opponent_level_label = render_opponent_scouting_panel()

    active_opponent_context = get_active_opponent_context(
        st.session_state.optimizer_session_id
    )

    team_key = str(st.session_state.get("selected_team_id", "no_team"))
    opponent_source = st.session_state.get(
        f"opponent_source_{team_key}",
        "Generic",
    )

    manual_pitcher_profile = dict(
        st.session_state.get(f"manual_opponent_pitcher_profile_{team_key}", {}) or {}
    )
    use_opponent_context = bool(
        st.session_state.get(
            f"use_opponent_context_{team_key}",
            False,
        )
    )

    if opponent_source != "MaxPreps Report" or not use_opponent_context:
        active_opponent_context = None

    if not active_opponent_context and opponent_source != "Manual Pitcher":
        opposing_pitching_label = "Balanced"

    elif active_opponent_context:
        opposing_pitching_label = "Balanced"

    else:
        opposing_pitching_label = "Balanced"

    st.sidebar.markdown("---")

    st.sidebar.markdown("## 🎯 Strategy")

    strategy_label = st.sidebar.selectbox(
        "Game Strategy",
        ["Small Ball", "Balanced", "Power"],
        index=1,
        help="Small Ball leans toward pressure and runner movement. Power leans toward damage and extra-base impact. Balanced stays in the middle.",
    )

    coaching_style_label = st.sidebar.selectbox(
        "Coaching Style",
        ["Conservative", "Balanced", "Aggressive"],
        index=1,
    )

    st.sidebar.markdown("---")

    st.sidebar.markdown("## 🎯 Scoring Goal")
    target_runs = st.sidebar.slider(
        "Goal Runs Per Game",
        min_value=1.0,
        max_value=12.0,
        value=float(st.session_state.get("target_runs_sidebar", 4.0)),
        step=1.0,
        key="target_runs_sidebar",
        help="Used for charts that show the chance of scoring at least this many runs.",
    )
    st.sidebar.caption(f"Charts will show chance of scoring {target_runs:.1f}+ runs.")

    with st.sidebar.expander("Advanced Settings"):
        simulation_detail = st.selectbox(
            "Simulation Detail",
            options=["Quick", "Standard", "Deep"],
            index=1,
            help="Quick runs faster. Deep runs more simulations and may give steadier results.",
        )

        if simulation_detail == "Quick":
            mode = "fast"
            search_games = 40
            refine_games = 1500
            top_n = 5
            beam_width = 8
            max_rounds = 6
        elif simulation_detail == "Standard":
            mode = "fast"
            search_games = 75
            refine_games = 3000
            top_n = 5
            beam_width = 12
            max_rounds = 8
        else:  # Deep
            mode = "fast"
            search_games = 150
            refine_games = 6000
            top_n = 7
            beam_width = 16
            max_rounds = 10

        seed = st.number_input(
            "Random Seed",
            min_value=0,
            max_value=999999,
            value=42,
            step=1,
            help="Keeps results repeatable while testing.",
        )

        with st.expander("Show internal optimizer settings"):
            st.write(f"Mode: `{mode}`")
            st.write(f"Search games: `{search_games}`")
            st.write(f"Refine games: `{refine_games}`")
            st.write(f"Top N lineups: `{top_n}`")
            st.write(f"Beam width: `{beam_width}`")
            st.write(f"Max rounds: `{max_rounds}`")

    with st.sidebar.expander("Session Info"):
        st.code(session_state.session_id)
        st.write(f"Status: **{session_state.status}**")
        st.write(f"Data source: **{session_state.data_source or 'Not set'}**")



    base_distance_lookup = {
        "46/60": 60,
        "50/70": 70,
        "60/90": 90,
    }

    strategy_lookup = {
        "Balanced": GameStrategy.BALANCED.value,
        "Small Ball": GameStrategy.SMALL_BALL.value,
        "Power": GameStrategy.POWER.value,
    }

    coaching_style_lookup = {
        "Conservative": CoachingStyle.CONSERVATIVE.value,
        "Balanced": CoachingStyle.BALANCED.value,
        "Aggressive": CoachingStyle.AGGRESSIVE.value,
    }

    opposing_pitching_lookup = {
        "Balanced": OpposingPitchingStrength.BALANCED_ARM.value,
        "Power Arm": OpposingPitchingStrength.POWER_ARM.value,
        "Crafty": OpposingPitchingStrength.CRAFTY.value,
        "Wild": OpposingPitchingStrength.WILD.value,
    }

    opponent_level_lookup = {
        "Weak": OpponentLevel.WEAK.value,
        "Average": OpponentLevel.AVERAGE.value,
        "Strong": OpponentLevel.STRONG.value,
    }

    rules_config = {
        "innings": int(innings_per_game),
        "max_runs_per_inning": int(inning_run_limit) if use_inning_run_limit else 999,
        "steals_allowed": True,
        "leadoffs_allowed": bool(leadoffs_allowed),
        "base_distance_ft": int(base_distance_lookup[diamond_size]),
        "continuous_batting": bool(continuous_batting),
        "lineup_size": 9,
        "steal_attempt_multiplier": 1.0,
        "steal_success_multiplier": 1.0,
        "game_strategy": strategy_lookup[strategy_label],
        "coaching_style": coaching_style_lookup[coaching_style_label],
        "opposing_pitching": opposing_pitching_lookup[opposing_pitching_label],
        "opponent_level": opponent_level_lookup[opponent_level_label],
        "use_opponent_scouting": bool(active_opponent_context),
        "use_manual_opponent_pitcher": bool(opponent_source == "Manual Pitcher" and manual_pitcher_profile),
        "manual_pitcher_name": None,
        "manual_pitcher_hand": None,
        "manual_pitcher_strikeout_multiplier": 1.0,
        "manual_pitcher_walk_multiplier": 1.0,
        "manual_pitcher_contact_multiplier": 1.0,
        "manual_pitcher_power_multiplier": 1.0,
        "opponent_pitcher_name": None,
        "opponent_pitcher_label": None,
        "opponent_pitcher_strikeout_multiplier": 1.0,
        "opponent_pitcher_walk_multiplier": 1.0,
        "opponent_pitcher_contact_multiplier": 1.0,
        "opponent_pitcher_power_multiplier": 1.0,
        "opponent_pitcher_sample_size": None,
        "opponent_pitcher_innings_pitched": None,
        "opponent_pitcher_batters_faced": None,
    }

    if active_opponent_context:
        active_report = active_opponent_context.get("report") or {}
        active_pitcher = active_opponent_context.get("pitcher") or {}

        rules_config["use_opponent_scouting"] = True
        rules_config["opponent_pitcher_name"] = active_pitcher.get("name")
        rules_config["opponent_pitcher_label"] = active_pitcher.get("label")
        rules_config["opponent_pitcher_strikeout_multiplier"] = float(
            active_pitcher.get("strikeout_multiplier", 1.0) or 1.0
        )
        rules_config["opponent_pitcher_walk_multiplier"] = float(
            active_pitcher.get("walk_multiplier", 1.0) or 1.0
        )
        rules_config["opponent_pitcher_contact_multiplier"] = float(
            active_pitcher.get("contact_multiplier", 1.0) or 1.0
        )
        rules_config["opponent_pitcher_power_multiplier"] = float(
            active_pitcher.get("power_multiplier", 1.0) or 1.0
        )
        rules_config["opponent_pitcher_sample_size"] = active_pitcher.get("confidence")
        rules_config["opponent_pitcher_innings_pitched"] = active_pitcher.get("innings_pitched")
        rules_config["opponent_pitcher_batters_faced"] = active_pitcher.get("batters_faced")

    elif opponent_source == "Manual Pitcher" and manual_pitcher_profile:
        rules_config["use_opponent_scouting"] = False
        rules_config["use_manual_opponent_pitcher"] = True

        rules_config["manual_pitcher_name"] = manual_pitcher_profile.get("name")
        rules_config["manual_pitcher_hand"] = manual_pitcher_profile.get("hand")

        rules_config["manual_pitcher_strikeout_multiplier"] = float(
            manual_pitcher_profile.get("strikeout_multiplier", 1.0) or 1.0
        )
        rules_config["manual_pitcher_walk_multiplier"] = float(
            manual_pitcher_profile.get("walk_multiplier", 1.0) or 1.0
        )
        rules_config["manual_pitcher_contact_multiplier"] = float(
            manual_pitcher_profile.get("contact_multiplier", 1.0) or 1.0
        )
        rules_config["manual_pitcher_power_multiplier"] = float(
            manual_pitcher_profile.get("power_multiplier", 1.0) or 1.0
        )

        # Reuse the existing simulator-facing imported pitcher fields.
        rules_config["opponent_pitcher_name"] = manual_pitcher_profile.get("name")
        rules_config["opponent_pitcher_label"] = manual_pitcher_profile.get("label")
        rules_config["opponent_pitcher_strikeout_multiplier"] = rules_config["manual_pitcher_strikeout_multiplier"]
        rules_config["opponent_pitcher_walk_multiplier"] = rules_config["manual_pitcher_walk_multiplier"]
        rules_config["opponent_pitcher_contact_multiplier"] = rules_config["manual_pitcher_contact_multiplier"]
        rules_config["opponent_pitcher_power_multiplier"] = rules_config["manual_pitcher_power_multiplier"]
        rules_config["opponent_pitcher_sample_size"] = "Manual"

    saved_team_rules_config = {
        "innings": int(innings_per_game),
        "diamond_size": str(diamond_size),
        "leadoffs_allowed": bool(leadoffs_allowed),
        "continuous_batting": bool(continuous_batting),
        "use_inning_run_limit": bool(use_inning_run_limit),
        "inning_run_limit": int(inning_run_limit) if use_inning_run_limit else None,
    }

    run_limit_summary = (
        f"{int(inning_run_limit)} run limit"
        if use_inning_run_limit and inning_run_limit is not None
        else "no run limit"
    )
    rules_summary = (
        f"{rules_preset} — {int(innings_per_game)} innings, "
        f"{'continuous batting' if continuous_batting else 'standard lineup'}, "
        f"{'leadoffs on' if leadoffs_allowed else 'leadoffs off'}, "
        f"{diamond_size}, {run_limit_summary}"
    )

    defense_summary = str(opponent_level_label)
    if active_opponent_context:
        active_report_for_setup = active_opponent_context.get("report") or {}
        fielding_pct = active_report_for_setup.get("fielding_pct")
        if fielding_pct is not None:
            defense_summary = f"{defense_summary} ({float(fielding_pct):.3f} FP)"

    if active_opponent_context:
        active_report_for_setup = active_opponent_context.get("report") or {}
        active_pitcher_for_setup = active_opponent_context.get("pitcher") or {}
        report_name = str(active_report_for_setup.get("team_name") or "Opponent report")
        pitcher_name = str(active_pitcher_for_setup.get("name") or "Selected pitcher")
        pitcher_label = str(active_pitcher_for_setup.get("label") or "Pitcher profile")

        scenario_context = {
            "opponent_source": "MaxPreps Report",
            "opponent_summary": f"MaxPreps — {report_name}, {pitcher_name}",
            "pitcher_summary": pitcher_label,
            "pitcher_details": {
                "report_name": report_name,
                "source_file_name": active_report_for_setup.get("source_file_name"),
                "pitcher_name": pitcher_name,
                "profile": pitcher_label,
                "k_rate": active_pitcher_for_setup.get("k_rate"),
                "bb_rate": active_pitcher_for_setup.get("bb_rate"),
                "sample_size": active_pitcher_for_setup.get("confidence"),
                "innings_pitched": active_pitcher_for_setup.get("innings_pitched"),
                "batters_faced": active_pitcher_for_setup.get("batters_faced"),
            },
        }
    elif opponent_source == "Manual Pitcher" and manual_pitcher_profile:
        manual_hand = manual_pitcher_profile.get("hand")
        hand_label = f"{manual_hand}HP" if manual_hand in {"R", "L"} else "Unknown hand"

        scenario_context = {
            "opponent_source": "Manual Pitcher",
            "opponent_summary": f"Manual Pitcher — {hand_label}",
            "pitcher_summary": str(manual_pitcher_profile.get("label") or "Manual pitcher profile"),
            "pitcher_details": {
                "name": manual_pitcher_profile.get("name"),
                "hand": manual_hand,
                "velocity": st.session_state.get(f"manual_pitcher_velo_{team_key}"),
                "strikeout_ability": st.session_state.get(f"manual_pitcher_k_rate_{team_key}"),
                "control_walk_rate": st.session_state.get(f"manual_pitcher_bb_rate_{team_key}"),
                "contact_allowed": st.session_state.get(f"manual_pitcher_contact_{team_key}"),
            },
        }
    else:
        scenario_context = {
            "opponent_source": "Generic",
            "opponent_summary": "Generic baseline",
            "pitcher_summary": "Generic baseline",
            "pitcher_details": {},
        }

    scenario_context.update(
        {
            "defense_level": str(opponent_level_label),
            "defense_summary": defense_summary,
            "rules_preset": str(rules_preset),
            "rules_summary": rules_summary,
            "rules_details": {
                "innings": int(innings_per_game),
                "continuous_batting": bool(continuous_batting),
                "leadoffs_allowed": bool(leadoffs_allowed),
                "diamond_size": str(diamond_size),
                "use_inning_run_limit": bool(use_inning_run_limit),
                "inning_run_limit": int(inning_run_limit) if use_inning_run_limit else None,
            },
        }
    )

    save_rules_for_active_team(
        rules_preset=rules_preset,
        rules_config=saved_team_rules_config,
    )

    saved_rules_preset = rules_preset

    return {
        "target_runs": float(target_runs),
        "strategy": strategy_label,
        "coaching_style": coaching_style_label,
        "opposing_pitching": opposing_pitching_label,
        "opponent_level": opponent_level_label,
        "rules_config": rules_config,
        "scenario_context": scenario_context,
        "optimizer_config": {
            "mode": mode,
            "search_games": int(search_games),
            "refine_games": int(refine_games),
            "top_n": int(top_n),
            "seed": int(seed),
            "beam_width": int(beam_width),
            "max_rounds": int(max_rounds),
            "target_runs": float(target_runs),
        },
    }
