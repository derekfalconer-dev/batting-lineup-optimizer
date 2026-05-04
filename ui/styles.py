import streamlit as st


def inject_custom_styles() -> None:
    st.markdown(
        """
        <style>
        /* ---------- Tabs ---------- */
        div[data-baseweb="tab-list"] {
            gap: 0.5rem;
            border-bottom: 2px solid rgba(250,250,250,0.12);
            padding-bottom: 0.35rem;
            margin-bottom: 1rem;
        }

        div[data-testid="stButton"] button {
            padding: 0.35rem 0.65rem !important;
            min-height: 2.1rem !important;
            white-space: nowrap !important;
        }

        button[data-baseweb="tab"] {
            font-size: 1.05rem !important;
            font-weight: 700 !important;
            padding: 0.6rem 1rem !important;
            border: 1px solid rgba(250,250,250,0.12) !important;
            border-radius: 10px 10px 0 0 !important;
            background: rgba(255,255,255,0.02) !important;
        }

        button[data-baseweb="tab"][aria-selected="true"] {
            background: rgba(255,255,255,0.06) !important;
            border-color: rgba(255,255,255,0.24) !important;
        }

        /* ---------- Lineup card ---------- */
        .lineup-card {
            border: 1px solid rgba(250,250,250,0.14);
            border-radius: 16px;
            padding: 1rem 1rem 0.75rem 1rem;
            margin-bottom: 1rem;
            background: rgba(255,255,255,0.02);
        }

        .lineup-card-title {
            font-size: 1.15rem;
            font-weight: 800;
            margin-bottom: 0.75rem;
        }

        .lineup-slot {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            padding: 0.45rem 0.55rem;
            margin-bottom: 0.35rem;
            border-radius: 10px;
            background: rgba(255,255,255,0.025);
        }

        .lineup-slot.top4 {
            background: rgba(255, 215, 0, 0.10);
            border-left: 4px solid rgba(255, 215, 0, 0.75);
        }

        .lineup-slot-num {
            width: 2rem;
            min-width: 2rem;
            text-align: center;
            font-weight: 800;
            font-size: 1rem;
            opacity: 0.95;
        }

        .lineup-slot-name {
            font-size: 1.02rem;
            font-weight: 600;
        }

        .lineup-subnote {
            font-size: 0.92rem;
            opacity: 0.82;
            margin-top: 0.7rem;
        }

        .lineup-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.7rem;
            margin-bottom: 0.35rem;
        }

        .lineup-chip {
            display: inline-block;
            padding: 0.28rem 0.55rem;
            border-radius: 999px;
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            font-size: 0.88rem;
            font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )