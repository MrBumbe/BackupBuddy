"""Onboarding wizard state persistence.

State is saved as JSON to data_dir/onboarding_state.json via atomic writes
(write .tmp, os.replace) so partial writes cannot corrupt the state file.

The SMTP password and webhook URL are NOT persisted here — they live in
app.state during the wizard session and are written to SecretsStore only at
finish time.  If the browser session is lost the user must re-enter them.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_FILENAME = "onboarding_state.json"


@dataclass
class WizardState:
    role: str = ""                      # "new" | "join"
    node_name: str = ""                 # slug used as node_id
    node_display_name: str = ""         # friendly name shown in UI
    storage_paths: list[str] = field(default_factory=list)
    storage_quota_gb: int = 500         # GB quota applied to every storage path
    profile: str = "adaptive"
    notify_smtp_enabled: bool = False
    notify_smtp_host: str = ""
    notify_smtp_port: int = 587
    notify_smtp_user: str = ""
    notify_smtp_to: str = ""
    notify_webhook_enabled: bool = False
    # Join flow
    invite_code: str = ""
    gatekeeper_url: str = ""
    # Set by finish cascade
    first_invite_code: str = ""
    recovery_key_confirmed: bool = False
    completed: bool = False


def load_state(data_dir: Path) -> WizardState:
    path = data_dir / _FILENAME
    if not path.exists():
        return WizardState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = WizardState.__dataclass_fields__
        return WizardState(**{k: v for k, v in raw.items() if k in known})
    except Exception as exc:
        logger.warning("Failed to load wizard state, starting fresh: %s", exc)
        return WizardState()


def save_state(data_dir: Path, state: WizardState) -> None:
    path = data_dir / _FILENAME
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.error("Failed to save wizard state: %s", exc)
        raise


def clear_state(data_dir: Path) -> None:
    (data_dir / _FILENAME).unlink(missing_ok=True)
