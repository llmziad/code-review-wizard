/**
 * PR detail view. Loads the context bundle on mount, shows the diff + a
 * "Run review" panel that streams progress events as the LLM works.
 */

import { useEffect } from "react";
import { useStore } from "@renderer/lib/store";
import { api } from "@renderer/lib/api";
import { DiffPanel } from "./DiffPanel";
import { RunReviewPanel } from "./RunReviewPanel";

export function PRDetailView() {
  const url = useStore((s) => s.activePrUrl);
  const bundle = useStore((s) => s.bundle);
  const bundleLoading = useStore((s) => s.bundleLoading);
  const setBundle = useStore((s) => s.setBundle);
  const setBundleLoading = useStore((s) => s.setBundleLoading);
  const back = useStore((s) => s.backToInbox);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    setBundleLoading(true);
    api
      .inspectPr(url)
      .then((b) => {
        if (!cancelled) setBundle(b);
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setBundle(null);
          console.error("inspect_pr failed:", err);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [url, setBundle, setBundleLoading]);

  if (!url) return null;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-zinc-800 px-4">
        <button
          onClick={back}
          className="text-xs text-zinc-400 hover:text-zinc-200 transition no-drag"
        >
          ← inbox
        </button>
        <span className="text-zinc-700">·</span>
        {bundle ? (
          <>
            <span className="font-mono text-xs text-zinc-500">
              #{bundle.pr.number}
            </span>
            <span className="font-mono text-xs text-zinc-400">
              {bundle.pr.owner}/{bundle.pr.repo}
            </span>
            <span className="text-zinc-700">·</span>
            <span className="text-sm text-zinc-200 truncate">{bundle.pr.title}</span>
          </>
        ) : (
          <span className="text-xs text-zinc-500">
            {bundleLoading ? "Loading context…" : "Failed to load"}
          </span>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div className="w-2/3 overflow-auto border-r border-zinc-800">
          {bundleLoading && <DetailSkeleton />}
          {bundle && <DiffPanel bundle={bundle} />}
          {!bundle && !bundleLoading && (
            <div className="flex h-full items-center justify-center px-8">
              <p className="text-sm text-zinc-500">
                Couldn&apos;t assemble context. Check the main-process terminal.
              </p>
            </div>
          )}
        </div>
        <div className="w-1/3 overflow-auto">
          {bundle && <RunReviewPanel bundle={bundle} />}
        </div>
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="p-6">
      <div className="h-4 w-32 animate-pulse rounded bg-zinc-800/60" />
      <div className="mt-3 h-4 w-64 animate-pulse rounded bg-zinc-800/60" />
      <div className="mt-6 space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <div
            key={i}
            className="h-3 animate-pulse rounded bg-zinc-800/60"
            style={{ width: `${60 + Math.random() * 40}%` }}
          />
        ))}
      </div>
    </div>
  );
}
