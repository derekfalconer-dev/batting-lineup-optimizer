from __future__ import annotations

from dataclasses import dataclass
import hashlib

import streamlit as st


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: str
    email: str
    display_name: str


def _stable_user_id_from_email(email: str) -> str:
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def get_current_user() -> CurrentUser:
    """
    Return the authenticated Streamlit OIDC user.

    Requires Streamlit auth to be configured in .streamlit/secrets.toml.
    """
    if not getattr(st.user, "is_logged_in", False):
        raise ValueError("No authenticated user is available.")

    email = str(getattr(st.user, "email", "") or "").strip().lower()
    if not email:
        raise ValueError("Authenticated user is missing an email claim.")

    display_name = (
        str(getattr(st.user, "name", "") or "").strip()
        or str(getattr(st.user, "given_name", "") or "").strip()
        or email.split("@")[0]
    )

    return CurrentUser(
        user_id=_stable_user_id_from_email(email),
        email=email,
        display_name=display_name,
    )