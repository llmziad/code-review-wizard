/**
 * Settings screen. Account info + logout for v1; model/threshold knobs are
 * read-only readouts because they're env-driven on the sidecar today.
 *
 * Future: writeable sidecar config + OS-keychain token storage (Electron's
 * safeStorage API). The current sidecar file storage at ~/.config with
 * 0600 perms is adequate for a single-user desktop app; the swap is local
 * to auth.py when it lands.
 */

import { useEffect, useState } from "react";
import { useStore } from "@renderer/lib/store";
import { api, type WhoamiResult } from "@renderer/lib/api";

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; whoami: WhoamiResult }
  | { kind: "error"; message: string };

export function SettingsView({ onLoggedOut }: { onLoggedOut: () => void }) {
  const close = useStore((s) => s.closeSettings);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .whoami()
      .then((whoami) => {
        if (!cancelled) setState({ kind: "loaded", whoami });
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onLogout = async () => {
    setLoggingOut(true);
    try {
      await api.logout();
      onLoggedOut(); // parent will re-probe auth and route to login
      close();
    } catch (err) {
      setState({ kind: "error", message: (err as Error).message });
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-2xl px-6 py-8">
        <div className="flex items-baseline justify-between">
          <h1 className="text-lg font-medium text-zinc-100">Settings</h1>
          <button
            onClick={close}
            className="text-xs text-zinc-500 hover:text-zinc-200"
          >
            close
          </button>
        </div>

        <Section title="Account">
          {state.kind === "loading" && (
            <p className="text-sm text-zinc-500">Loading…</p>
          )}
          {state.kind === "error" && (
            <p className="font-mono text-xs text-red-400">{state.message}</p>
          )}
          {state.kind === "loaded" && (
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm font-medium text-zinc-200">
                  @{state.whoami.login}
                  {state.whoami.name && (
                    <span className="ml-2 font-normal text-zinc-500">
                      ({state.whoami.name})
                    </span>
                  )}
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  Token source:{" "}
                  <code className="font-mono text-zinc-400">
                    {state.whoami.token_source}
                  </code>
                </p>
                {state.whoami.html_url && (
                  <a
                    href={state.whoami.html_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 block text-xs text-zinc-500 hover:text-zinc-300"
                  >
                    {state.whoami.html_url}
                  </a>
                )}
              </div>
              <button
                onClick={onLogout}
                disabled={loggingOut}
                className="rounded border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-red-500/50 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50 transition"
              >
                {loggingOut ? "Logging out…" : "Log out"}
              </button>
            </div>
          )}
        </Section>

        <Section title="Sidecar">
          <p className="text-xs text-zinc-400">
            The Python sidecar is spawned automatically when the app launches.
            Configuration (Anthropic model, confidence threshold, severity caps)
            lives in <code className="font-mono text-zinc-300">sidecar/.env</code>.
          </p>
          <ul className="mt-3 space-y-1 font-mono text-xs">
            <Knob label="ANTHROPIC_MODEL" value="claude-sonnet-4-6" defaultNote="(env-driven)" />
            <Knob label="MIN_CONFIDENCE" value="0.7" defaultNote="(default)" />
            <Knob label="MAX_BLOCKING / SUGGESTION / NIT" value="3 / 5 / 3" defaultNote="(default)" />
          </ul>
          <p className="mt-3 text-[11px] text-zinc-600">
            In-app editing of these knobs lands in a follow-up; for now, edit
            <code className="mx-1 font-mono text-zinc-400">sidecar/.env</code>
            and restart the app.
          </p>
        </Section>

        <Section title="Storage">
          <p className="text-xs text-zinc-400">
            Tokens live at{" "}
            <code className="font-mono text-zinc-300">
              ~/.config/code-review-wizard/auth.json
            </code>{" "}
            (0600 perms).
          </p>
          <p className="mt-1 text-xs text-zinc-400">
            Run history (context bundles, raw pass output, drop logs, posted
            reports) lives under{" "}
            <code className="font-mono text-zinc-300">sidecar/cache/runs/</code>.
          </p>
          <p className="mt-3 text-[11px] text-zinc-600">
            OS keychain storage via Electron&apos;s safeStorage API is on the roadmap.
          </p>
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-8 rounded-lg border border-zinc-800 bg-zinc-900/30 p-5">
      <h2 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Knob({
  label,
  value,
  defaultNote,
}: {
  label: string;
  value: string;
  defaultNote: string;
}) {
  return (
    <li className="flex items-baseline justify-between">
      <span className="text-zinc-500">{label}</span>
      <span>
        <span className="text-zinc-200">{value}</span>{" "}
        <span className="text-[10px] text-zinc-600">{defaultNote}</span>
      </span>
    </li>
  );
}
