/**
 * Inbox view. Calls listPrs() on mount, groups by relationship, renders cards.
 *
 * State machine is intentionally tiny: loading → data | error. No router yet
 * (clicking a PR will navigate to the detail view in a follow-up commit).
 */

import { useEffect, useState } from "react";
import { api, type ListPrsResult } from "@renderer/lib/api";
import { useStore } from "@renderer/lib/store";
import type { PRSummary } from "@shared/types";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "data"; result: ListPrsResult };

export function InboxView() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .listPrs({ filter: "all", limit: 10 })
      .then((result) => {
        if (!cancelled) setState({ kind: "data", result });
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-zinc-500">Loading PRs…</p>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="flex h-full items-center justify-center px-8">
        <div className="max-w-md text-center">
          <p className="text-sm font-medium text-red-400">Couldn&apos;t load PRs</p>
          <p className="mt-2 text-xs text-zinc-500 font-mono">{state.message}</p>
          <p className="mt-4 text-xs text-zinc-600">
            The sidecar may not be running, or you may need to log in. Run{" "}
            <code className="text-zinc-400">reviewer login --device</code> in your terminal.
          </p>
        </div>
      </div>
    );
  }

  const { review_requested = [], authored = [], assigned = [] } = state.result;

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-4xl px-6 py-8">
        <Section
          title="Awaiting your review"
          accent="text-amber-300"
          dotColor="bg-amber-400"
          prs={review_requested}
          emptyMessage="Nothing on your plate right now."
        />
        <Section
          title="Your open PRs"
          accent="text-emerald-300"
          dotColor="bg-emerald-400"
          prs={authored}
          emptyMessage="You don't have any open PRs."
        />
        <Section
          title="Assigned to you"
          accent="text-sky-300"
          dotColor="bg-sky-400"
          prs={assigned}
          emptyMessage="No assignments."
        />
      </div>
    </div>
  );
}

function Section(props: {
  title: string;
  accent: string;
  dotColor: string;
  prs: PRSummary[];
  emptyMessage: string;
}) {
  const { title, accent, dotColor, prs, emptyMessage } = props;
  return (
    <section className="mb-10">
      <header className="mb-3 flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} />
        <h2 className={`text-xs font-medium uppercase tracking-wider ${accent}`}>
          {title}
        </h2>
        <span className="text-xs text-zinc-600">{prs.length}</span>
      </header>
      {prs.length === 0 ? (
        <p className="text-sm text-zinc-600 italic">{emptyMessage}</p>
      ) : (
        <ul className="space-y-1">
          {prs.map((pr) => (
            <PRCard key={pr.url} pr={pr} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PRCard({ pr }: { pr: PRSummary }) {
  const openPr = useStore((s) => s.openPr);
  return (
    <li>
      <button
        onClick={() => openPr(pr.url)}
        className="block w-full rounded-md border border-transparent px-3 py-3 text-left transition hover:border-zinc-800 hover:bg-zinc-900/50"
      >
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-xs text-zinc-500">#{pr.number}</span>
          <span className="font-mono text-xs text-zinc-400">{pr.owner}/{pr.repo}</span>
          {pr.draft && (
            <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-zinc-400">
              draft
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-zinc-200 line-clamp-1">{pr.title}</p>
        <p className="mt-1 text-xs text-zinc-500">
          by <span className="text-zinc-400">@{pr.author}</span> · updated{" "}
          {formatRelative(pr.updated_at)}
        </p>
      </button>
    </li>
  );
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const sec = Math.round((now - then) / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
}
