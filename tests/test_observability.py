"""Tests for the Langfuse observability layer.

Heavy mocking — no real Langfuse calls. Verifies the lazy-wrapper guarantees
(no-op when credentials missing) and that LiteLLM callbacks register correctly
when credentials are present.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

from minions import observability as obs


def _clear_langfuse_env(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)


def _set_langfuse_env(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")


def test_has_credentials_returns_false_without_env(monkeypatch):
    _clear_langfuse_env(monkeypatch)
    assert obs.has_credentials() is False


def test_has_credentials_true_when_both_set(monkeypatch):
    _set_langfuse_env(monkeypatch)
    assert obs.has_credentials() is True


def test_init_langfuse_returns_false_without_creds(monkeypatch):
    _clear_langfuse_env(monkeypatch)
    assert obs.init_langfuse() is False


def test_init_langfuse_registers_litellm_callbacks(monkeypatch):
    _set_langfuse_env(monkeypatch)
    fake_litellm = types.SimpleNamespace(success_callback=[], failure_callback=[])
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    assert obs.init_langfuse() is True
    assert "langfuse" in fake_litellm.success_callback
    assert "langfuse" in fake_litellm.failure_callback


def test_init_langfuse_idempotent(monkeypatch):
    _set_langfuse_env(monkeypatch)
    fake_litellm = types.SimpleNamespace(success_callback=[], failure_callback=[])
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    obs.init_langfuse()
    obs.init_langfuse()
    assert fake_litellm.success_callback.count("langfuse") == 1
    assert fake_litellm.failure_callback.count("langfuse") == 1


def test_observe_crew_is_noop_without_credentials(monkeypatch):
    _clear_langfuse_env(monkeypatch)

    @obs.observe_crew("planning_crew")
    def add(a: int, b: int) -> int:
        return a + b

    # Decorated function still works; no Langfuse import is attempted.
    assert add(2, 3) == 5


def test_observe_crew_calls_real_observe_when_creds_present(monkeypatch):
    _set_langfuse_env(monkeypatch)
    sentinel = MagicMock(side_effect=lambda *a, **kw: a[0] + a[1] if a else 0)

    # Patch langfuse.observe so the wrapper exercises the real code path.
    fake_observe = MagicMock(return_value=lambda fn: lambda *a, **kw: fn(*a, **kw))
    fake_module = types.SimpleNamespace(observe=fake_observe)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    @obs.observe_crew("planning_crew", as_type="agent")
    def fn(x: int) -> int:
        return x * 2

    assert fn(7) == 14
    fake_observe.assert_called_once_with(name="planning_crew", as_type="agent")


def test_add_metadata_is_noop_without_credentials(monkeypatch):
    _clear_langfuse_env(monkeypatch)
    # Should not raise even though no Langfuse client is around.
    obs.add_metadata(project="x")
    obs.add_metadata({"a": 1}, b=2)


def test_add_metadata_calls_update_current_span_with_creds(monkeypatch):
    _set_langfuse_env(monkeypatch)
    mock_client = MagicMock()
    fake_get_client = MagicMock(return_value=mock_client)
    fake_module = types.SimpleNamespace(get_client=fake_get_client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    obs.add_metadata({"project": "Demo"}, dry_run=True)

    fake_get_client.assert_called_once()
    mock_client.update_current_span.assert_called_once_with(
        metadata={"project": "Demo", "dry_run": True}
    )


def test_add_metadata_swallows_exceptions(monkeypatch):
    _set_langfuse_env(monkeypatch)
    fake_module = types.SimpleNamespace(get_client=MagicMock(side_effect=RuntimeError("boom")))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    # Must NOT raise — observability failures shouldn't break crew runs.
    obs.add_metadata(project="x")


def test_flush_is_noop_without_credentials(monkeypatch):
    _clear_langfuse_env(monkeypatch)
    obs.flush()  # no raise


def test_flush_calls_client_flush_with_creds(monkeypatch):
    _set_langfuse_env(monkeypatch)
    mock_client = MagicMock()
    fake_module = types.SimpleNamespace(get_client=MagicMock(return_value=mock_client))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    obs.flush()
    mock_client.flush.assert_called_once()


def test_host_url_default(monkeypatch):
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    assert obs.host_url() == "https://cloud.langfuse.com"


def test_host_url_override(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.internal")
    assert obs.host_url() == "https://langfuse.internal"


def test_auth_check_fails_without_credentials(monkeypatch):
    _clear_langfuse_env(monkeypatch)
    ok, msg = obs.auth_check()
    assert ok is False
    assert "credentials" in msg.lower()


def test_auth_check_succeeds_when_client_returns_true(monkeypatch):
    _set_langfuse_env(monkeypatch)
    mock_client = MagicMock()
    mock_client.auth_check.return_value = True
    fake_module = types.SimpleNamespace(get_client=MagicMock(return_value=mock_client))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    ok, msg = obs.auth_check()
    assert ok is True
    assert "authenticated" in msg


def test_auth_check_handles_exceptions(monkeypatch):
    _set_langfuse_env(monkeypatch)
    fake_module = types.SimpleNamespace(get_client=MagicMock(side_effect=Exception("network")))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    ok, msg = obs.auth_check()
    assert ok is False
    assert "Exception" in msg
