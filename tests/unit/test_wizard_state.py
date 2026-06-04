"""Unit tests for gatekeeper/gui/wizard_state.py.

Covers:
  - load_state with no file — returns default WizardState
  - save_state + load_state roundtrip — all fields preserved
  - clear_state — removes the JSON file
  - clear_state on non-existent file — no error
  - load_state with corrupted JSON — returns fresh state, no exception
  - load_state ignores unknown keys (forward-compat)
  - save_state uses atomic write (.tmp then os.replace)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from gatekeeper.gui.wizard_state import WizardState, clear_state, load_state, save_state

_FILENAME = "onboarding_state.json"


@pytest.fixture()
def tmp(tmp_path: Path) -> Path:
    return tmp_path


def test_load_state_no_file_returns_defaults(tmp: Path) -> None:
    state = load_state(tmp)
    assert isinstance(state, WizardState)
    assert state.role == ""
    assert state.node_name == ""
    assert state.storage_paths == []
    assert state.storage_quota_gb == 500
    assert state.profile == "adaptive"
    assert state.recovery_key_confirmed is False
    assert state.completed is False


def test_save_and_load_roundtrip(tmp: Path) -> None:
    state = WizardState(
        role="new",
        node_name="my-node",
        node_display_name="My Node",
        storage_paths=["/mnt/pool1", "/mnt/pool2"],
        storage_quota_gb=250,
        profile="secure",
        notify_smtp_enabled=True,
        notify_smtp_host="smtp.example.com",
        notify_smtp_port=465,
        notify_smtp_user="user@example.com",
        notify_smtp_to="alerts@example.com",
        notify_webhook_enabled=False,
        invite_code="",
        gatekeeper_url="",
        first_invite_code="apple-banana-1",
        recovery_key_confirmed=True,
        completed=False,
    )
    save_state(tmp, state)
    loaded = load_state(tmp)

    assert loaded.role == "new"
    assert loaded.node_name == "my-node"
    assert loaded.node_display_name == "My Node"
    assert loaded.storage_paths == ["/mnt/pool1", "/mnt/pool2"]
    assert loaded.storage_quota_gb == 250
    assert loaded.profile == "secure"
    assert loaded.notify_smtp_enabled is True
    assert loaded.notify_smtp_host == "smtp.example.com"
    assert loaded.notify_smtp_port == 465
    assert loaded.notify_smtp_user == "user@example.com"
    assert loaded.notify_smtp_to == "alerts@example.com"
    assert loaded.notify_webhook_enabled is False
    assert loaded.first_invite_code == "apple-banana-1"
    assert loaded.recovery_key_confirmed is True
    assert loaded.completed is False


def test_save_state_writes_json_file(tmp: Path) -> None:
    state = WizardState(role="join", node_name="test-node")
    save_state(tmp, state)
    path = tmp / _FILENAME
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["role"] == "join"
    assert raw["node_name"] == "test-node"


def test_save_state_no_tmp_file_left_behind(tmp: Path) -> None:
    state = WizardState(role="new")
    save_state(tmp, state)
    tmp_file = (tmp / _FILENAME).with_suffix(".tmp")
    assert not tmp_file.exists()


def test_clear_state_removes_file(tmp: Path) -> None:
    save_state(tmp, WizardState(role="new"))
    assert (tmp / _FILENAME).exists()
    clear_state(tmp)
    assert not (tmp / _FILENAME).exists()


def test_clear_state_missing_file_no_error(tmp: Path) -> None:
    # Should not raise even if the file doesn't exist
    clear_state(tmp)


def test_load_state_corrupted_json_returns_defaults(tmp: Path) -> None:
    path = tmp / _FILENAME
    path.write_text("{not valid json{{", encoding="utf-8")
    state = load_state(tmp)
    assert isinstance(state, WizardState)
    assert state.role == ""
    assert state.node_name == ""


def test_load_state_partially_valid_json_returns_known_fields(tmp: Path) -> None:
    path = tmp / _FILENAME
    path.write_text(
        json.dumps({"role": "new", "node_name": "partial-node"}),
        encoding="utf-8",
    )
    state = load_state(tmp)
    assert state.role == "new"
    assert state.node_name == "partial-node"
    assert state.profile == "adaptive"  # default set by task 1.18.4


def test_load_state_ignores_unknown_keys(tmp: Path) -> None:
    path = tmp / _FILENAME
    path.write_text(
        json.dumps({"role": "new", "future_field": "future_value"}),
        encoding="utf-8",
    )
    # Should not raise; unknown key is silently dropped
    state = load_state(tmp)
    assert state.role == "new"
    assert not hasattr(state, "future_field")


def test_join_flow_fields_roundtrip(tmp: Path) -> None:
    state = WizardState(
        role="join",
        invite_code="apple-mango-3",
        gatekeeper_url="http://100.64.0.2:8080",
    )
    save_state(tmp, state)
    loaded = load_state(tmp)
    assert loaded.role == "join"
    assert loaded.invite_code == "apple-mango-3"
    assert loaded.gatekeeper_url == "http://100.64.0.2:8080"
