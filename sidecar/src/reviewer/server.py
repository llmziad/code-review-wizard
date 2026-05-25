"""JSON-RPC sidecar — `reviewer serve` mode.

Long-lived process. Reads line-delimited JSON requests from stdin, dispatches
to async handlers, writes responses and streaming events to stdout. The
desktop app spawns this process at launch and communicates via the same
pipe for the whole session — no HTTP, no ports, no firewall dialogs.

Protocol:
    Request  : {"id": "<str>", "method": "<str>", "params": {...}}
    Response : {"id": "<str>", "result": {...}}
             | {"id": "<str>", "error": {"message": "...", "type": "..."}}
    Event    : {"event": "...", "request_id": "<str>", ...}

Concurrency: each request runs in its own asyncio.Task so a long-running
`review_pr` doesn't block a `list_prs` issued from the renderer's sidebar.
Stdout writes are serialized through an asyncio.Lock so concurrent emitters
don't interleave bytes.

Errors never crash the loop. A handler exception becomes an error response;
malformed JSON becomes an error event; the loop keeps reading until stdin
closes (signaling the parent process is shutting down).
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from .auth import (
    AuthMissingError,
    clear_auth,
    device_flow_poll,
    device_flow_start,
    fetch_github_user,
    load_auth,
    login_with_pat,
    resolve_token,
)
from .config import get_settings
from .context.assembler import assemble
from .github_client import GitHubClient
from .models import PipelineEvent, ReviewReport
from .pipeline import review_pr_stream

# ---------------------------------------------------------------------------
# IO primitives
# ---------------------------------------------------------------------------

_stdout_lock = asyncio.Lock()


async def _emit(payload: dict) -> None:
    """Serialize one JSON object onto stdout as a single line, atomically."""
    line = json.dumps(payload, default=str, ensure_ascii=False)
    async with _stdout_lock:
        sys.stdout.write(line)
        sys.stdout.write("\n")
        sys.stdout.flush()


def _make_emitter(request_id: str) -> Callable[[dict], Awaitable[None]]:
    """Returns an `emit(event_payload)` callback that tags the request_id."""
    async def emit(event_payload: dict) -> None:
        await _emit({"event": event_payload.get("event"), "request_id": request_id, **event_payload})
    return emit


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_whoami(params: dict, emit) -> dict:
    token = resolve_token()
    user = await asyncio.to_thread(fetch_github_user, token)
    stored = load_auth()
    return {
        "login": user["login"],
        "id": user.get("id"),
        "name": user.get("name"),
        "html_url": user.get("html_url"),
        "token_source": "stored" if stored and stored.access_token == token else "env_or_gh",
    }


async def handle_login_pat(params: dict, emit) -> dict:
    token = params.get("token") or ""
    auth = await asyncio.to_thread(login_with_pat, token)
    return auth.model_dump(mode="json")


async def handle_login_device_start(params: dict, emit) -> dict:
    challenge = await asyncio.to_thread(device_flow_start)
    return challenge.model_dump(mode="json")


async def handle_login_device_poll(params: dict, emit) -> dict:
    """Poll until the user completes the device flow. Emits countdown events."""
    from .auth import DeviceFlowChallenge
    challenge = DeviceFlowChallenge(**params["challenge"])

    async def on_poll_callback(seconds_remaining: int) -> None:
        await _emit({
            "event": "login_polling",
            "seconds_remaining": seconds_remaining,
        })

    # device_flow_poll is sync + uses an `on_poll` sync callback. Bridge by
    # scheduling the async emit on whatever loop is current.
    def sync_on_poll(seconds_remaining: int) -> None:
        asyncio.ensure_future(on_poll_callback(seconds_remaining))

    auth = await asyncio.to_thread(device_flow_poll, challenge, sync_on_poll)
    return auth.model_dump(mode="json")


async def handle_logout(params: dict, emit) -> dict:
    removed = clear_auth()
    return {"removed": removed}


async def handle_list_prs(params: dict, emit) -> dict:
    filter_ = params.get("filter", "all")
    limit = int(params.get("limit", 50))
    settings = get_settings()
    groups: dict[str, list[dict]] = {}
    with GitHubClient(None, settings.cache_dir) as gh:
        if filter_ in ("all", "review-requested"):
            groups["review_requested"] = [
                pr.model_dump(mode="json")
                for pr in await asyncio.to_thread(gh.list_review_requested, limit)
            ]
        if filter_ in ("all", "authored"):
            groups["authored"] = [
                pr.model_dump(mode="json")
                for pr in await asyncio.to_thread(gh.list_authored, limit)
            ]
        if filter_ in ("all", "assigned"):
            groups["assigned"] = [
                pr.model_dump(mode="json")
                for pr in await asyncio.to_thread(gh.list_assigned, limit)
            ]
    return groups


async def handle_inspect_pr(params: dict, emit) -> dict:
    url = params["url"]
    settings = get_settings()
    with GitHubClient(None, settings.cache_dir) as gh:
        bundle = await asyncio.to_thread(assemble, url, gh)
    return bundle.model_dump(mode="json")


async def handle_review_pr(params: dict, emit) -> dict:
    """Stream pipeline events; return the final report when done."""
    url = params["url"]
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set on the sidecar.")

    final_report: ReviewReport | None = None
    with GitHubClient(None, settings.cache_dir) as gh:
        async for item in review_pr_stream(url, gh, settings):
            if isinstance(item, PipelineEvent):
                await emit(item.model_dump(mode="json"))
            else:
                final_report = item

    if final_report is None:
        raise RuntimeError("Pipeline ended without yielding a ReviewReport.")
    return {"run_id": final_report.run_id, "report": final_report.model_dump(mode="json")}


# Triage / replay handlers — implemented in task 22 (post_review, dismiss, get_run).
# Schema export — implemented in task 23.


HANDLERS: dict[str, Callable[..., Awaitable[dict]]] = {
    "whoami": handle_whoami,
    "login_pat": handle_login_pat,
    "login_device_start": handle_login_device_start,
    "login_device_poll": handle_login_device_poll,
    "logout": handle_logout,
    "list_prs": handle_list_prs,
    "inspect_pr": handle_inspect_pr,
    "review_pr": handle_review_pr,
}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _dispatch_one(request: dict) -> None:
    req_id = str(request.get("id", ""))
    method = request.get("method")
    params = request.get("params") or {}

    handler = HANDLERS.get(method) if isinstance(method, str) else None
    if handler is None:
        await _emit({
            "id": req_id,
            "error": {
                "type": "UnknownMethod",
                "message": f"Unknown method: {method!r}",
                "available": sorted(HANDLERS.keys()),
            },
        })
        return

    emit = _make_emitter(req_id)
    try:
        result = await handler(params, emit)
        await _emit({"id": req_id, "result": result})
    except AuthMissingError as e:
        await _emit({
            "id": req_id,
            "error": {"type": "AuthMissingError", "message": str(e)},
        })
    except Exception as e:
        await _emit({
            "id": req_id,
            "error": {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            },
        })


async def serve() -> None:
    """Main loop. Reads stdin, spawns a task per request, runs until EOF."""
    pending: set[asyncio.Task[Any]] = set()
    loop = asyncio.get_running_loop()

    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break  # parent closed stdin → shut down
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            await _emit({"error": {"type": "InvalidJSON", "message": str(e)}})
            continue

        if not isinstance(request, dict):
            await _emit({"error": {"type": "BadRequest", "message": "Request must be a JSON object."}})
            continue

        task = asyncio.create_task(_dispatch_one(request))
        pending.add(task)
        task.add_done_callback(pending.discard)

    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def serve_blocking() -> None:
    """Entry point invoked by the `reviewer serve` Typer command."""
    asyncio.run(serve())


__all__ = ["serve", "serve_blocking", "HANDLERS"]
