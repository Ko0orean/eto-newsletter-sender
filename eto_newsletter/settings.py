"""Persistent settings, stored in a single JSON file in the project folder.

All settings - API key, sender name, sender email, subject, and the
skip-SSL-verification flag - are written to ``config.json`` next to the
application. This keeps everything together in one place that travels with the
app folder.

Note on the API key: storing it in a plain JSON file is convenient and keeps
the whole configuration in one portable place, but it is less protected than an
OS keychain. The file sits inside the application folder on the office machine;
keep that folder private and do not commit ``config.json`` to shared storage.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

APP_NAME = "ETO Newsletter Sender"

_DEFAULTS: dict = {
    "api_key": "",
    "from_name": "Hong Kong Economic and Trade Office, Tokyo",
    "from_email": "",
    "subject": "",
    "test_emails": "",
    "group_name": "ETO Korea Newsletter Subscribers",
    "social_links": {},
    "skip_ssl_verify": False,
}

# Order and labels of the footer-banner link fields shown in Settings.
SOCIAL_LABELS = ["Website", "Facebook", "X", "Instagram", "YouTube", "LinkedIn"]


def app_dir() -> Path:
    """Return the folder the app lives in, whether run as a script or a
    PyInstaller ``.exe``.

    * Frozen exe  -> the folder containing the executable.
    * Plain script -> the project root (one level above this package).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # this file is <project>/eto_newsletter/settings.py -> project root is parents[1]
    return Path(__file__).resolve().parents[1]


def _config_path() -> Path:
    return app_dir() / "config.json"


def _load_all() -> dict:
    path = _config_path()
    data = dict(_DEFAULTS)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data.update({k: stored.get(k, v) for k, v in _DEFAULTS.items()})
        except (ValueError, OSError):
            pass
    return data


def _save_all(data: dict) -> None:
    merged = dict(_DEFAULTS)
    merged.update(data)
    _config_path().write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---- API key -------------------------------------------------------------

def load_api_key() -> str:
    return _load_all().get("api_key", "")


def save_api_key(key: str) -> None:
    data = _load_all()
    data["api_key"] = key.strip()
    _save_all(data)


# ---- sender / subject / flags --------------------------------------------

def load_sender() -> dict:
    """Return the non-key settings (name, email, subject, skip_ssl_verify)."""
    data = _load_all()
    data.pop("api_key", None)
    return data


def save_sender(
    from_name: str,
    from_email: str,
    skip_ssl_verify: bool = False,
) -> None:
    data = _load_all()
    data["from_name"] = from_name
    data["from_email"] = from_email
    data["skip_ssl_verify"] = skip_ssl_verify
    _save_all(data)


def load_test_emails() -> str:
    """The test-address line exactly as the user last typed it."""
    return _load_all().get("test_emails", "")


def save_test_emails(text: str) -> None:
    data = _load_all()
    data["test_emails"] = text.strip()
    _save_all(data)


def load_group_name() -> str:
    """The MailerLite group the app compares against and keeps in sync."""
    name = (_load_all().get("group_name") or "").strip()
    return name or _DEFAULTS["group_name"]


def save_group_name(name: str) -> None:
    data = _load_all()
    data["group_name"] = name.strip() or _DEFAULTS["group_name"]
    _save_all(data)


def load_social_links() -> dict:
    """Footer-banner links, e.g. {"Facebook": "https://…"}. Empty by default."""
    links = _load_all().get("social_links") or {}
    return links if isinstance(links, dict) else {}


def save_social_links(links: dict) -> None:
    data = _load_all()
    data["social_links"] = {
        str(k): str(v).strip() for k, v in links.items() if str(v).strip()
    }
    _save_all(data)


def load_subject() -> str:
    return _load_all().get("subject", "")


def save_subject(subject: str) -> None:
    data = _load_all()
    data["subject"] = subject.strip()
    _save_all(data)
