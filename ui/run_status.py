import streamlit as st


def format_int_compact(value: int) -> str:
    return f"{int(value):,}"


def build_direct_simulation_summary(
    *,
    label: str,
    n_games: int,
    innings_per_game: int,
) -> dict:
    total_innings = int(n_games) * int(innings_per_game)
    return {
        "label": label,
        "games": int(n_games),
        "innings": int(total_innings),
        "detail": (
            f"{label} complete — simulated {format_int_compact(n_games)} games "
            f"and {format_int_compact(total_innings)} innings."
        ),
    }


def build_optimizer_simulation_summary(
    *,
    label: str,
    innings_per_game: int,
    optimizer_meta: dict | None = None,
    refine_games: int | None = None,
) -> dict:
    optimizer_meta = dict(optimizer_meta or {})

    total_games = optimizer_meta.get("total_games")
    search_total_games = optimizer_meta.get("search_total_games")
    refine_total_games = optimizer_meta.get("refine_total_games")

    if total_games is not None:
        total_games = int(total_games)
        total_innings = total_games * int(innings_per_game)

        detail_parts = [
            f"{label} complete — simulated {format_int_compact(total_games)} total games "
            f"and {format_int_compact(total_innings)} total innings."
        ]

        if search_total_games is not None:
            search_total_games = int(search_total_games)
            detail_parts.append(
                f"Search stage: {format_int_compact(search_total_games)} games "
                f"({format_int_compact(search_total_games * int(innings_per_game))} innings)."
            )

        if refine_total_games is not None:
            refine_total_games = int(refine_total_games)
            detail_parts.append(
                f"Final comparison stage: {format_int_compact(refine_total_games)} games "
                f"({format_int_compact(refine_total_games * int(innings_per_game))} innings)."
            )

        return {
            "label": label,
            "games": total_games,
            "innings": total_innings,
            "detail": " ".join(detail_parts),
        }

    # Fallback if optimizer meta is not available yet.
    fallback_refine_games = int(refine_games or 3000) * 4
    fallback_innings = fallback_refine_games * int(innings_per_game)

    return {
        "label": label,
        "games": fallback_refine_games,
        "innings": fallback_innings,
        "detail": (
            f"{label} complete — simulated at least {format_int_compact(fallback_refine_games)} games "
            f"and {format_int_compact(fallback_innings)} innings in the final comparison stage."
        ),
    }


def clear_run_status_tile() -> None:
    st.session_state.run_status_tile = None


def set_run_status_tile(
    *,
    kind: str,
    title: str,
    detail: str,
) -> None:
    st.session_state.run_status_tile = {
        "kind": str(kind),
        "title": str(title),
        "detail": str(detail),
    }


def render_run_status_tile() -> None:
    tile = st.session_state.get("run_status_tile")
    if not tile:
        return

    with st.container(border=True):
        st.markdown(f"#### {tile['title']}")

        if tile["kind"] == "success":
            st.success(tile["detail"])
        elif tile["kind"] == "error":
            st.error(tile["detail"])
        else:
            st.info(tile["detail"])