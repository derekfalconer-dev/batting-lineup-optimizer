from pathlib import Path
import streamlit as st

def save_uploaded_file(uploaded_file, target_name: str) -> Path:
    """
    Persist a Streamlit UploadedFile to this session's temp upload directory.
    """
    uploads_dir = Path(st.session_state.uploads_dir)
    target_path = uploads_dir / target_name
    target_path.write_bytes(uploaded_file.getbuffer())
    return target_path


def save_uploaded_files(uploaded_files, *, prefix: str) -> list[Path]:
    """
    Persist multiple Streamlit UploadedFile objects to the session upload dir.
    """
    uploads_dir = Path(st.session_state.uploads_dir)
    saved_paths: list[Path] = []

    for idx, uploaded_file in enumerate(uploaded_files, start=1):
        original_name = Path(uploaded_file.name).name
        safe_name = f"{prefix}_{idx:02d}_{original_name}"
        target_path = uploads_dir / safe_name
        target_path.write_bytes(uploaded_file.getbuffer())
        saved_paths.append(target_path)

    return saved_paths


def reset_multi_gc_ui_state() -> None:
    st.session_state.multi_gc_reconciliation_result = None
    st.session_state.multi_gc_final_records = None
    st.session_state.multi_gc_uploaded_file_names = []
    st.session_state.multi_gc_import_summary = None


def find_backend_additional_preview_row(
    *,
    incoming_name: str,
    pa: int,
    source_file: str,
) -> dict | None:
    from core.session_manager import get_session_manager

    # Streamlit is a thin UI shell.
    # Auth comes from core/auth.py.
    # Durable team access must stay owner-scoped via SessionManager.
    manager = get_session_manager()
    raw_session = manager.get_session(st.session_state.optimizer_session_id)
    preview_rows = raw_session.manual_roster or []

    for item in preview_rows:
        if (
            str(item.get("incoming_name", "")) == str(incoming_name)
            and int(item.get("pa", 0)) == int(pa)
            and str(item.get("source_file", "")) == str(source_file)
        ):
            return item

    return None