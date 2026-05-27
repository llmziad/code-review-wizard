/**
 * First-launch / signed-out screen — drives the OAuth Device Flow.
 *
 * Stages:
 *   start    → "Sign in with GitHub" button + a one-line pitch
 *   polling  → shows the user_code and verification_uri; calls
 *              api.login.devicePoll which blocks until the user
 *              approves in the browser. login_polling events from
 *              the sidecar update the countdown timer.
 *   error    → friendly message + retry
 *
 * On success the parent (App) re-checks whoami and routes to the inbox.
 */

import { useState } from "react";
import { api } from "@renderer/lib/api";
import type { DeviceFlowChallenge } from "@shared/types";

type Stage =
  | { kind: "start" }
  | { kind: "polling"; challenge: DeviceFlowChallenge; secondsRemaining: number }
  | { kind: "error"; message: string };

export function LoginView({ onSuccess }: { onSuccess: () => void }) {
  const [stage, setStage] = useState<Stage>({ kind: "start" });

  const beginLogin = async () => {
    try {
      const challenge = await api.login.deviceStart();
      setStage({ kind: "polling", challenge, secondsRemaining: challenge.expires_in! });

      await api.login.devicePoll(challenge, (event) => {
        if (event.event === "login_polling" && typeof event["seconds_remaining"] === "number") {
          setStage((cur) =>
            cur.kind === "polling"
              ? { ...cur, secondsRemaining: event["seconds_remaining"] as number }
              : cur,
          );
        }
      });

      onSuccess();
    } catch (err) {
      setStage({ kind: "error", message: (err as Error).message });
    }
  };

  return (
    <div className="flex h-full items-center justify-center px-8">
      <div className="w-full max-w-md">
        {stage.kind === "start" && (
          <StartCard onBegin={beginLogin} />
        )}
        {stage.kind === "polling" && (
          <PollingCard
            challenge={stage.challenge}
            secondsRemaining={stage.secondsRemaining}
          />
        )}
        {stage.kind === "error" && (
          <ErrorCard message={stage.message} onRetry={() => setStage({ kind: "start" })} />
        )}
      </div>
    </div>
  );
}

function StartCard({ onBegin }: { onBegin: () => void }) {
  return (
    <div className="text-center">
      <h2 className="text-2xl font-medium text-zinc-100">Code Review Wizard</h2>
      <p className="mt-2 text-sm text-zinc-400">
        AI code review on GitHub PRs you care about.
      </p>
      <button
        onClick={onBegin}
        className="mt-8 rounded-md bg-zinc-100 px-5 py-2.5 text-sm font-medium text-zinc-900 hover:bg-white transition"
      >
        Sign in with GitHub
      </button>
      <p className="mt-4 text-xs text-zinc-600">
        Opens GitHub in your browser to approve access.
        <br />
        Tokens are stored locally; nothing leaves your machine.
      </p>
    </div>
  );
}

function PollingCard({
  challenge,
  secondsRemaining,
}: {
  challenge: DeviceFlowChallenge;
  secondsRemaining: number;
}) {
  const [copied, setCopied] = useState(false);

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(challenge.user_code!);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard API can fail in some contexts; user can copy manually
    }
  };

  const mins = Math.max(0, Math.floor(secondsRemaining / 60));
  const secs = Math.max(0, secondsRemaining % 60);

  return (
    <div>
      <h2 className="text-center text-xl font-medium text-zinc-100">
        Approve in your browser
      </h2>
      <p className="mt-1 text-center text-sm text-zinc-500">
        We&apos;ll detect it automatically.
      </p>

      <div className="mt-6 rounded-lg border border-zinc-800 bg-zinc-900/40 p-5">
        <p className="text-xs uppercase tracking-wider text-zinc-500">Your code</p>
        <div className="mt-2 flex items-center justify-between">
          <span className="font-mono text-3xl font-medium tracking-widest text-amber-300">
            {challenge.user_code}
          </span>
          <button
            onClick={copyCode}
            className="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>

      <a
        href={challenge.verification_uri}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-3 block rounded-md bg-zinc-100 px-4 py-2.5 text-center text-sm font-medium text-zinc-900 hover:bg-white transition"
      >
        Open GitHub
      </a>

      <p className="mt-4 text-center font-mono text-xs text-zinc-600">
        waiting · {mins}:{String(secs).padStart(2, "0")} remaining
      </p>
    </div>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="text-center">
      <h2 className="text-lg font-medium text-red-400">Login failed</h2>
      <p className="mt-2 font-mono text-xs text-zinc-500 whitespace-pre-wrap">
        {message}
      </p>
      <button
        onClick={onRetry}
        className="mt-4 rounded-md border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
      >
        Try again
      </button>
    </div>
  );
}
