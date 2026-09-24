"""Tests for --json, --quiet, and --max-spend CLI flags (no network, no server dependencies)."""

from __future__ import annotations

import json
import sys
import pytest

# We need to import cli directly from the source to avoid conftest issues
sys.path.insert(0, "/workspace/src")
from treg import cli


@pytest.fixture(autouse=True)
def _isolate_cli_config(tmp_path, monkeypatch):
    """Isolate CLI config for tests."""
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.json")
    # Reset global state between tests
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", False)
    monkeypatch.setattr(cli, "_QUIET_OVERRIDE", False)
    monkeypatch.setattr(cli, "_MAX_SPEND_MICRO", None)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 0)


# ---- Tests for _pop_json_flag ----

def test_pop_json_flag_from_argv():
    """--json flag is popped from argv and returns True."""
    argv = ["--json", "call", "echo"]
    assert cli._pop_json_flag(argv) is True
    assert argv == ["call", "echo"]

    argv = ["call", "echo"]
    assert cli._pop_json_flag(argv) is False
    assert argv == ["call", "echo"]


def test_pop_json_flag_from_env(monkeypatch):
    """TREG_JSON=1 env var sets JSON mode."""
    monkeypatch.setenv("TREG_JSON", "1")
    argv = ["call", "echo"]
    assert cli._pop_json_flag(argv) is True
    assert argv == ["call", "echo"]

    monkeypatch.setenv("TREG_JSON", "true")
    assert cli._pop_json_flag([]) is True

    monkeypatch.setenv("TREG_JSON", "yes")
    assert cli._pop_json_flag([]) is True

    monkeypatch.setenv("TREG_JSON", "0")
    assert cli._pop_json_flag([]) is False


# ---- Tests for _pop_quiet_flag ----

def test_pop_quiet_flag_from_argv():
    """--quiet and -q flags are popped from argv."""
    argv = ["--quiet", "call", "echo"]
    assert cli._pop_quiet_flag(argv) is True
    assert argv == ["call", "echo"]

    argv = ["-q", "call", "echo"]
    assert cli._pop_quiet_flag(argv) is True
    assert argv == ["call", "echo"]

    argv = ["call", "echo"]
    assert cli._pop_quiet_flag(argv) is False


def test_pop_quiet_flag_from_env(monkeypatch):
    """TREG_QUIET=1 env var sets quiet mode."""
    monkeypatch.setenv("TREG_QUIET", "1")
    assert cli._pop_quiet_flag([]) is True

    monkeypatch.setenv("TREG_QUIET", "0")
    assert cli._pop_quiet_flag([]) is False


# ---- Tests for _pop_max_spend_flag ----

def test_pop_max_spend_flag_from_argv():
    """--max-spend <value> is popped and converted to micro-USD."""
    argv = ["--max-spend", "5", "call", "echo"]
    result = cli._pop_max_spend_flag(argv)
    assert result == 5_000_000
    assert argv == ["call", "echo"]

    argv = ["--max-spend=0.50", "call", "echo"]
    result = cli._pop_max_spend_flag(argv)
    assert result == 500_000
    assert argv == ["call", "echo"]

    argv = ["call", "echo"]
    assert cli._pop_max_spend_flag(argv) is None


def test_pop_max_spend_flag_from_env(monkeypatch):
    """TREG_MAX_SPEND env var sets spend cap."""
    monkeypatch.setenv("TREG_MAX_SPEND", "10")
    assert cli._pop_max_spend_flag([]) == 10_000_000

    monkeypatch.setenv("TREG_MAX_SPEND", "0.25")
    assert cli._pop_max_spend_flag([]) == 250_000

    monkeypatch.delenv("TREG_MAX_SPEND", raising=False)
    assert cli._pop_max_spend_flag([]) is None


def test_pop_max_spend_flag_invalid_value():
    """--max-spend with invalid value exits cleanly."""
    argv = ["--max-spend", "abc", "call", "echo"]
    with pytest.raises(SystemExit) as exc_info:
        cli._pop_max_spend_flag(argv)
    assert "must be a number" in str(exc_info.value)


# ---- Tests for _extract_call_metadata ----

def test_extract_call_metadata_extracts_headers():
    """_extract_call_metadata extracts relevant treg headers into a dict."""
    import httpx
    resp = httpx.Response(200, headers={
        "X-Treg-Call-Id": "call123",
        "X-Treg-Cost-Micro": "6667",
        "X-Treg-Cache": "hit",
        "X-Treg-Fetched-At": "2026-09-24T10:00:00Z",
        "X-Treg-Age": "300",
    })
    meta = cli._extract_call_metadata(resp)
    assert meta["call_id"] == "call123"
    assert meta["cost_micro"] == 6667
    assert meta["cost_usd"] == 0.006667
    assert meta["cache_hit"] is True
    assert meta["fetched_at"] == "2026-09-24T10:00:00Z"
    assert meta["age_seconds"] == 300


def test_extract_call_metadata_replay_flag():
    """_extract_call_metadata marks idempotent replays."""
    import httpx
    resp = httpx.Response(200, headers={
        "X-Treg-Idempotent-Replay": "true",
        "X-Treg-Call-Id": "c1",
    })
    meta = cli._extract_call_metadata(resp)
    assert meta["replayed"] is True


# ---- Tests for _show_call_response in JSON mode ----

def test_show_call_response_json_mode_emits_structured_output(monkeypatch, capsys):
    """In --json mode, _show_call_response emits ONE JSON doc with result + _treg metadata."""
    import httpx
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", True)
    monkeypatch.setattr(cli, "_QUIET_OVERRIDE", False)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 0)

    resp = httpx.Response(200, content=b'{"results":[{"id":1}]}', headers={
        "content-type": "application/json",
        "X-Treg-Cost-Micro": "1000",
        "X-Treg-Call-Id": "abc123",
    })
    cli._show_call_response(resp)
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["result"] == {"results": [{"id": 1}]}
    assert data["_treg"]["call_id"] == "abc123"
    assert data["_treg"]["cost_usd"] == 0.001
    assert data["_treg"]["http_status"] == 200
    assert err == ""


def test_show_call_response_json_mode_handles_binary(monkeypatch, capsys):
    """In --json mode, binary content is base64-encoded in the result."""
    import httpx
    import base64
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", True)
    monkeypatch.setattr(cli, "_QUIET_OVERRIDE", False)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 0)

    resp = httpx.Response(200, content=b"\x89PNG\r\n", headers={
        "content-type": "image/png",
    })
    cli._show_call_response(resp)
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["result"]["_binary"] is True
    assert base64.b64decode(data["result"]["_base64"]) == b"\x89PNG\r\n"


# ---- Tests for cumulative spend tracking ----

def test_show_charge_line_tracks_cumulative_spend(monkeypatch, capsys):
    """_show_charge_line increments _CUMULATIVE_SPEND_MICRO (except for replays)."""
    import httpx
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", False)
    monkeypatch.setattr(cli, "_QUIET_OVERRIDE", False)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 0)

    resp = httpx.Response(200, headers={"X-Treg-Cost-Micro": "5000"})
    cli._show_charge_line(resp)
    assert cli._CUMULATIVE_SPEND_MICRO == 5000

    resp2 = httpx.Response(200, headers={"X-Treg-Cost-Micro": "3000"})
    cli._show_charge_line(resp2)
    assert cli._CUMULATIVE_SPEND_MICRO == 8000

    replay = httpx.Response(200, headers={
        "X-Treg-Cost-Micro": "5000",
        "X-Treg-Idempotent-Replay": "true",
    })
    cli._show_charge_line(replay)
    assert cli._CUMULATIVE_SPEND_MICRO == 8000  # unchanged for replay


def test_show_charge_line_silent_in_json_mode(monkeypatch, capsys):
    """_show_charge_line prints nothing to stderr in --json mode."""
    import httpx
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", True)
    monkeypatch.setattr(cli, "_QUIET_OVERRIDE", False)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 0)

    resp = httpx.Response(200, headers={"X-Treg-Cost-Micro": "5000", "X-Treg-Call-Id": "c1"})
    cli._show_charge_line(resp)
    _, err = capsys.readouterr()
    assert err == ""
    assert cli._CUMULATIVE_SPEND_MICRO == 5000  # but still tracked


# ---- Tests for _check_spend_cap ----

def test_check_spend_cap_allows_below_cap(monkeypatch):
    """_check_spend_cap does nothing when spend is below cap."""
    monkeypatch.setattr(cli, "_MAX_SPEND_MICRO", 10_000_000)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 5_000_000)
    cli._check_spend_cap()  # should not raise


def test_check_spend_cap_exits_at_cap(monkeypatch, capsys):
    """_check_spend_cap exits when cumulative spend reaches the cap."""
    monkeypatch.setattr(cli, "_MAX_SPEND_MICRO", 5_000_000)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 5_000_000)
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", False)

    with pytest.raises(SystemExit) as exc_info:
        cli._check_spend_cap()
    assert exc_info.value.code == 1
    _, err = capsys.readouterr()
    assert "spend cap reached" in err


def test_check_spend_cap_json_mode_output(monkeypatch, capsys):
    """_check_spend_cap emits JSON error in --json mode."""
    monkeypatch.setattr(cli, "_MAX_SPEND_MICRO", 5_000_000)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 5_000_000)
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", True)

    with pytest.raises(SystemExit):
        cli._check_spend_cap()
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["error"] == "spend_cap_reached"
    assert data["spent_usd"] == 5.0
    assert data["cap_usd"] == 5.0


def test_check_spend_cap_no_cap_set(monkeypatch):
    """_check_spend_cap does nothing when no cap is set."""
    monkeypatch.setattr(cli, "_MAX_SPEND_MICRO", None)
    monkeypatch.setattr(cli, "_CUMULATIVE_SPEND_MICRO", 1_000_000_000)
    cli._check_spend_cap()  # should not raise


# ---- Tests for _show_hint_line suppression ----

def test_show_hint_line_silent_in_quiet_mode(monkeypatch, capsys):
    """_show_hint_line prints nothing in --quiet mode."""
    import httpx
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", False)
    monkeypatch.setattr(cli, "_QUIET_OVERRIDE", True)

    resp = httpx.Response(200, headers={
        "X-Treg-Call-Id": "c1",
        "X-Treg-Hint": "review",
    })
    cli._show_hint_line(resp)
    _, err = capsys.readouterr()
    assert err == ""


def test_show_hint_line_silent_in_json_mode(monkeypatch, capsys):
    """_show_hint_line prints nothing in --json mode."""
    import httpx
    monkeypatch.setattr(cli, "_JSON_OVERRIDE", True)
    monkeypatch.setattr(cli, "_QUIET_OVERRIDE", False)

    resp = httpx.Response(200, headers={
        "X-Treg-Call-Id": "c1",
        "X-Treg-Hint": "feedback",
    })
    cli._show_hint_line(resp)
    _, err = capsys.readouterr()
    assert err == ""
