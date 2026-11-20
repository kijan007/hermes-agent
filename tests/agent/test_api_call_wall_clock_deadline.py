"""Per-call wall-clock deadline on the chat-completions route (t_7bb26ba4).

Run 61896 died because ONE API call blocked 3885s while chunks kept trickling:
the httpx read timeout only bounds idle gaps, and the configured
``request_timeout_seconds: 600`` never even resolved — workers run with the
runtime provider id ``custom`` while the config lives under ``providers.or``.

Two fixes are covered here:

1. ``hermes_cli.timeouts`` resolves ``custom`` / ``custom:<name>`` onto the
   configured provider name (and the ``custom_providers`` entry itself).
2. ``_iter_provider_stream_chunks`` enforces a ``wall_clock_deadline`` that
   fires on the first chunk after the ceiling EVEN WITH CHUNK FLOW, and
   ``_StreamingCall._api_call_deadline_seconds`` resolves that ceiling.
"""

import time

import pytest

from agent.chat_completion_helpers import _iter_provider_stream_chunks
from hermes_cli.timeouts import get_provider_request_timeout


def _patch_config(monkeypatch, cfg):
    import hermes_cli.config as config_mod

    monkeypatch.setattr(config_mod, "load_config_readonly", lambda: cfg)
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)


# ---------------------------------------------------------------- resolution

def _config_with_or():
    return {
        "providers": {"or": {"request_timeout_seconds": 600}},
        "custom_providers": [{"name": "or", "base_url": "https://or.example/v1"}],
    }


def test_custom_id_resolves_single_configured_entry(monkeypatch):
    _patch_config(monkeypatch, _config_with_or())
    assert get_provider_request_timeout("custom", "auto") == 600.0


def test_custom_prefixed_id_resolves_named_provider(monkeypatch):
    _patch_config(monkeypatch, _config_with_or())
    assert get_provider_request_timeout("custom:or", "auto") == 600.0


def test_custom_entry_own_request_timeout(monkeypatch):
    cfg = {"custom_providers": [{"name": "or", "request_timeout_seconds": 600}]}
    _patch_config(monkeypatch, cfg)
    assert get_provider_request_timeout("custom:or", "auto") == 600.0


def test_stale_timeout_resolves_for_custom(monkeypatch):
    cfg = {
        "providers": {"or": {"stale_timeout_seconds": 240}},
        "custom_providers": [{"name": "or"}],
    }
    _patch_config(monkeypatch, cfg)
    assert get_provider_request_timeout("custom", "auto") is None  # key not set
    from hermes_cli.timeouts import get_provider_stale_timeout
    assert get_provider_stale_timeout("custom", "auto") == 240.0


def test_ambiguous_bare_custom_stays_unresolved(monkeypatch):
    cfg = {
        "providers": {"or": {"request_timeout_seconds": 600},
                      "aimax": {"request_timeout_seconds": 300}},
        "custom_providers": [{"name": "or"}, {"name": "aimax"}],
    }
    _patch_config(monkeypatch, cfg)
    assert get_provider_request_timeout("custom", "auto") is None
    # naming the entry still resolves exactly
    assert get_provider_request_timeout("custom:aimax", "auto") == 300.0


def test_bare_custom_resolves_by_unique_model_match(monkeypatch):
    cfg = {
        "providers": {"or": {"request_timeout_seconds": 600},
                      "aimax": {"request_timeout_seconds": 300}},
        "custom_providers": [
            {"name": "or", "models": {"glm-4.6": {}}},
            {"name": "aimax", "models": {"other-model": {}}},
        ],
    }
    _patch_config(monkeypatch, cfg)
    assert get_provider_request_timeout("custom", "glm-4.6") == 600.0


def test_non_custom_provider_lookup_unchanged(monkeypatch):
    _patch_config(monkeypatch, _config_with_or())
    assert get_provider_request_timeout("openrouter", "auto") is None
    assert get_provider_request_timeout("or", "auto") == 600.0


# ------------------------------------------------------------ wall-clock cut

class _FakeChunk:
    def __init__(self, n):
        self.n = n


def test_deadline_cuts_stream_that_keeps_trickling():
    """The run-61896 scenario at 1/6000 scale: chunks flow forever, so no idle
    timeout could ever fire — the wall-clock deadline must."""
    consumed = []

    def trickling_stream():
        for i in range(100_000):
            time.sleep(0.005)
            chunk = _FakeChunk(i)
            consumed.append(i)
            yield chunk

    deadline = time.monotonic() + 0.05
    with pytest.raises(TimeoutError, match="wall-clock deadline"):
        for _ in _iter_provider_stream_chunks(
                trickling_stream(), wall_clock_deadline=deadline, deadline_seconds=0.05):
            pass
    assert len(consumed) < 100, f"stream ran on: {len(consumed)} chunks consumed"


def test_no_deadline_yields_everything():
    got = list(_iter_provider_stream_chunks(iter([_FakeChunk(i) for i in range(5)])))
    assert [c.n for c in got] == [0, 1, 2, 3, 4]


def test_deadline_not_yet_reached_yields_everything():
    deadline = time.monotonic() + 30
    got = list(_iter_provider_stream_chunks(
        iter([_FakeChunk(i) for i in range(5)]), wall_clock_deadline=deadline,
        deadline_seconds=30))
    assert [c.n for c in got] == [0, 1, 2, 3, 4]


# --------------------------------------------------- deadline value resolution

class _StubAgent:
    provider = "custom"
    model = "auto"


class _StubCall:
    agent = _StubAgent()


def _deadline(**env):
    from agent.chat_completion_helpers import _StreamingCall
    import os
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        return _StreamingCall._api_call_deadline_seconds(_StubCall())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_deadline_uses_resolved_custom_timeout(monkeypatch):
    _patch_config(monkeypatch, _config_with_or())
    assert _deadline(HERMES_API_CALL_DEADLINE=None, HERMES_API_REQUEST_TIMEOUT=None) == 600.0


def test_deadline_env_override(monkeypatch):
    _patch_config(monkeypatch, _config_with_or())
    assert _deadline(HERMES_API_CALL_DEADLINE="42") == 42.0


def test_deadline_none_without_any_config(monkeypatch):
    _patch_config(monkeypatch, {})
    assert _deadline(HERMES_API_CALL_DEADLINE=None, HERMES_API_REQUEST_TIMEOUT=None) is None
