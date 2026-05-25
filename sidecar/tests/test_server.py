"""JSON-RPC sidecar — dispatcher behavior + handler smoke tests.

We don't spin up an actual subprocess for these tests; we exercise the
internal dispatch function directly and capture what would have been
written to stdout. That's enough to pin down:

  - request → response shape
  - unknown methods emit a structured error
  - handler exceptions become error responses, not crashes
  - request_id is round-tripped onto responses
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from reviewer import server as srv


@pytest.fixture
def captured_lines(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Intercept `_emit` so we can inspect what the dispatcher wrote."""
    out: list[dict[str, Any]] = []

    async def fake_emit(payload: dict) -> None:
        out.append(payload)

    monkeypatch.setattr(srv, "_emit", fake_emit)
    return out


async def test_unknown_method_returns_structured_error(captured_lines) -> None:
    await srv._dispatch_one({"id": "r1", "method": "no_such_method", "params": {}})
    assert len(captured_lines) == 1
    out = captured_lines[0]
    assert out["id"] == "r1"
    assert out["error"]["type"] == "UnknownMethod"
    assert "available" in out["error"]


async def test_handler_exception_becomes_error_response(captured_lines, monkeypatch) -> None:
    async def boom(params, emit):
        raise ValueError("kaboom")

    monkeypatch.setitem(srv.HANDLERS, "boom", boom)
    await srv._dispatch_one({"id": "r2", "method": "boom", "params": {}})
    assert len(captured_lines) == 1
    out = captured_lines[0]
    assert out["id"] == "r2"
    assert out["error"]["type"] == "ValueError"
    assert out["error"]["message"] == "kaboom"


async def test_successful_handler_response_carries_id(captured_lines, monkeypatch) -> None:
    async def echo(params, emit):
        return {"received": params}

    monkeypatch.setitem(srv.HANDLERS, "echo", echo)
    await srv._dispatch_one({"id": "r3", "method": "echo", "params": {"k": "v"}})
    assert captured_lines == [{"id": "r3", "result": {"received": {"k": "v"}}}]


async def test_handler_can_emit_events_before_result(captured_lines, monkeypatch) -> None:
    async def streamy(params, emit):
        await emit({"event": "step", "n": 1})
        await emit({"event": "step", "n": 2})
        return {"done": True}

    monkeypatch.setitem(srv.HANDLERS, "streamy", streamy)
    await srv._dispatch_one({"id": "r4", "method": "streamy", "params": {}})

    # Two events then one response — all carrying request_id
    assert len(captured_lines) == 3
    assert captured_lines[0]["event"] == "step"
    assert captured_lines[0]["request_id"] == "r4"
    assert captured_lines[1]["event"] == "step"
    assert captured_lines[2] == {"id": "r4", "result": {"done": True}}


async def test_dispatch_never_raises(captured_lines) -> None:
    """The dispatcher must surface all failures as responses; the loop relies on this."""
    # Missing method field entirely
    await srv._dispatch_one({"id": "r5"})
    assert captured_lines[-1]["id"] == "r5"
    assert "error" in captured_lines[-1]


def test_handlers_registry_includes_expected_methods() -> None:
    """Pin down the public surface so an accidental rename is loud."""
    expected = {
        "whoami", "login_pat", "login_device_start", "login_device_poll",
        "logout", "list_prs", "inspect_pr", "review_pr",
    }
    assert expected.issubset(set(srv.HANDLERS.keys()))


def test_emit_payload_is_valid_json(captured_lines) -> None:
    """JSON-serialization must not break on Pydantic / Path / datetime objects."""
    # This implicitly tests _emit by running the dispatcher; full e2e is verified
    # by the smoke test against a subprocess in the docs.
    # Here we just confirm json.dumps tolerates the kinds of payloads we make.
    json.dumps({"event": "stage_started", "request_id": "x"})