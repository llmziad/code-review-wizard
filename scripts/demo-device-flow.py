#!/usr/bin/env python3
"""Demo: drive the OAuth Device Flow through the sidecar's JSON-RPC interface.

This is exactly what the Electron main process will do — spawn `reviewer
serve` once, send a login_device_start, render the challenge to the user,
then call login_device_poll which streams `login_polling` events back as it
waits for the user to approve in the browser.

Usage:
    cd ~/Desktop/gigs/code-review-wizard
    python3 scripts/demo-device-flow.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SIDECAR_DIR = Path(__file__).resolve().parent.parent / "sidecar"


def send(proc: subprocess.Popen, request: dict) -> None:
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()


def recv_until_response(proc: subprocess.Popen, request_id: str) -> dict:
    """Read lines from sidecar stdout, print events, return the matching response."""
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("Sidecar closed stdout unexpectedly")
        msg = json.loads(line)
        if msg.get("id") == request_id:
            return msg
        if "event" in msg and msg.get("request_id") == request_id:
            event = msg["event"]
            if event == "login_polling":
                remaining = msg.get("seconds_remaining", "?")
                print(f"  [polling — {remaining}s remaining]", flush=True)
            else:
                print(f"  [event: {event}] {msg}", flush=True)
        else:
            print(f"  [stray: {msg}]", flush=True)


def main() -> int:
    proc = subprocess.Popen(
        ["uv", "run", "reviewer", "serve"],
        cwd=SIDECAR_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        # Step 1 — get a device code
        print("→ login_device_start")
        send(proc, {"id": "1", "method": "login_device_start", "params": {}})
        challenge_resp = recv_until_response(proc, "1")
        if "error" in challenge_resp:
            print(f"\nError: {challenge_resp['error']}", file=sys.stderr)
            return 1
        challenge = challenge_resp["result"]

        print()
        print(f"  Visit:  {challenge['verification_uri']}")
        print(f"  Enter:  {challenge['user_code']}")
        print(f"  Code expires in {challenge['expires_in']}s")
        print()
        input("Press Enter once you've approved in the browser to start polling...")

        # Step 2 — poll until user approves
        print()
        print("→ login_device_poll")
        send(proc, {"id": "2", "method": "login_device_poll",
                    "params": {"challenge": challenge}})
        poll_resp = recv_until_response(proc, "2")
        if "error" in poll_resp:
            print(f"\nError: {poll_resp['error']}", file=sys.stderr)
            return 1

        auth = poll_resp["result"]
        print()
        print(f"  ✓ Logged in as @{auth['user_login']} ({auth['token_type']})")

        # Step 3 — verify the token is now resolvable via the cascade
        print()
        print("→ whoami (verify)")
        send(proc, {"id": "3", "method": "whoami", "params": {}})
        whoami_resp = recv_until_response(proc, "3")
        user = whoami_resp["result"]
        print(f"  → {user['login']}   token_source={user['token_source']}")
        if user["token_source"] == "stored":
            print()
            print("  ✓ Token persisted to ~/.config/code-review-wizard/auth.json")
        else:
            print(f"  (cascade resolved via {user['token_source']} — gh CLI fallback)")

        return 0
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
