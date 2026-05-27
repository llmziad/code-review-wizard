/**
 * Right-side panel for the PR detail view. Has four modes:
 *   - idle:      a single "Run review" button
 *   - running:   live progress events from the streaming pipeline
 *   - error:     the failure with a reset button
 *   - completed: comment list with per-comment triage + a Post action bar
 *
 * The triage state (approve / dismiss / edit per comment) lives in the
 * global store; the action bar reads counts and submits in one call.
 */

import { useStore } from "@renderer/lib/store";
import { api } from "@renderer/lib/api";
import type { ContextBundle, PipelineEvent } from "@shared/types";
import { CommentItem } from "./CommentItem";

export function RunReviewPanel({ bundle }: { bundle: ContextBundle }) {
  const progress = useStore((s) => s.progress);
  const report = useStore((s) => s.report);
  const startReview = useStore((s) => s.startReview);
  const pushEvent = useStore((s) => s.pushEvent);
  const completeReview = useStore((s) => s.completeReview);
  const failReview = useStore((s) => s.failReview);
  const resetReview = useStore((s) => s.resetReview);

  const onRun = async () => {
    startReview();
    try {
      const { report: r } = await api.reviewPr(bundle.pr.url, (event) => {
        pushEvent(event);
      });
      completeReview(r);
    } catch (err) {
      failReview((err as Error).message);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-400">
            Review
          </h2>
          {progress.status !== "idle" && (
            <button
              onClick={resetReview}
              className="text-xs text-zinc-500 hover:text-zinc-300"
            >
              reset
            </button>
          )}
        </div>
      </div>

      {progress.status === "idle" && <IdleState onRun={onRun} symbolCount={bundle.changed_symbols.length} />}

      {progress.status === "running" && (
        <div className="flex-1 overflow-auto px-4 py-3">
          <ProgressList events={progress.events} />
        </div>
      )}

      {progress.status === "error" && (
        <div className="flex-1 overflow-auto px-4 py-3">
          <p className="text-sm font-medium text-red-400">Review failed.</p>
          <p className="mt-2 font-mono text-xs text-zinc-500 whitespace-pre-wrap">
            {progress.error}
          </p>
        </div>
      )}

      {progress.status === "completed" && report && <TriageState />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Idle / progress / triage subviews
// ---------------------------------------------------------------------------

function IdleState({ onRun, symbolCount }: { onRun: () => void; symbolCount: number }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6">
      <p className="text-center text-sm text-zinc-500">
        {symbolCount} symbol(s) ready.
      </p>
      <button
        onClick={onRun}
        className="mt-4 rounded-md bg-amber-500/90 px-4 py-2 text-sm font-medium text-black hover:bg-amber-400 transition"
      >
        Run review
      </button>
      <p className="mt-3 text-center text-xs text-zinc-600">
        Uses your Anthropic API key.<br />
        Expect ~$0.03 + 5-15s.
      </p>
    </div>
  );
}

function ProgressList({ events }: { events: PipelineEvent[] }) {
  return (
    <ul className="space-y-1">
      {events.map((e, i) => (
        <li key={i} className="font-mono text-xs">
          <span className={stageColor(e.stage)}>{e.stage}</span>
          <span className="text-zinc-600"> · </span>
          <span className="text-zinc-400">{e.event}</span>
          {e.detail && (
            <>
              <span className="text-zinc-600"> · </span>
              <span className="text-zinc-300">{e.detail}</span>
            </>
          )}
          {e.tokens_input != null && (
            <>
              <span className="text-zinc-600"> · </span>
              <span className="text-zinc-500">
                {e.tokens_input}→{e.tokens_output}
              </span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

function stageColor(stage: string): string {
  switch (stage) {
    case "context":
      return "text-sky-400";
    case "analysis":
      return "text-amber-300";
    case "filtering":
      return "text-emerald-300";
    default:
      return "text-zinc-400";
  }
}

// ---------------------------------------------------------------------------
// Triage — completed report with per-comment actions + post bar
// ---------------------------------------------------------------------------

function TriageState() {
  const report = useStore((s) => s.report)!;
  const inline = report.comments ?? [];
  const orphan = report.orphan_comments ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-auto px-4 py-3">
        <ReportSummary
          total={report.total_candidates ?? 0}
          kept={inline.length + orphan.length}
          cost={report.cost_summary?.total_cost_usd ?? 0}
        />
        <Section heading="Inline" comments={inline} emptyMessage="No inline comments." />
        {orphan.length > 0 && (
          <Section heading="General observations" comments={orphan} emptyMessage="None." />
        )}
      </div>
      <PostBar />
    </div>
  );
}

function ReportSummary(props: { total: number; kept: number; cost: number }) {
  return (
    <div className="mb-4 rounded border border-zinc-800 bg-zinc-900/40 px-3 py-2">
      <p className="text-xs text-zinc-400">
        <span className="text-zinc-200 font-medium">{props.kept}</span> kept of{" "}
        {props.total} ·{" "}
        <span className="text-emerald-300">${props.cost.toFixed(4)}</span>
      </p>
    </div>
  );
}

function Section({
  heading,
  comments,
  emptyMessage,
}: {
  heading: string;
  comments: Array<Parameters<typeof CommentItem>[0]["comment"]>;
  emptyMessage: string;
}) {
  return (
    <section className="mb-6">
      <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
        {heading} ({comments.length})
      </h3>
      {comments.length === 0 ? (
        <p className="mt-2 text-xs italic text-zinc-600">{emptyMessage}</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {comments.map((c) => (
            <CommentItem key={c.id} comment={c} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PostBar() {
  const report = useStore((s) => s.report);
  const triage = useStore((s) => s.triage);
  const edits = useStore((s) => s.edits);
  const posting = useStore((s) => s.posting);
  const setPosting = useStore((s) => s.setPosting);

  const counts = Object.values(triage).reduce(
    (acc, d) => {
      acc[d] = (acc[d] ?? 0) + 1;
      return acc;
    },
    { pending: 0, approved: 0, dismissed: 0 } as Record<string, number>,
  );

  const approvedIds = Object.entries(triage)
    .filter(([, d]) => d === "approved")
    .map(([id]) => id);
  const dismissedIds = Object.entries(triage)
    .filter(([, d]) => d === "dismissed")
    .map(([id]) => id);

  const onPost = async () => {
    if (!report?.run_id || approvedIds.length === 0) return;
    setPosting({ kind: "posting" });
    try {
      const { review_url } = await api.postReview({
        run_id: report.run_id,
        comment_ids: approvedIds,
        edits,
      });
      setPosting({ kind: "posted", url: review_url });
      // Fire-and-forget dismissal logging — doesn't block the UI.
      if (dismissedIds.length > 0) {
        api
          .dismissComments({
            run_id: report.run_id,
            comment_ids: dismissedIds,
            reason: "user_dismissed_via_app",
          })
          .catch((err) => console.error("dismiss_comments failed:", err));
      }
    } catch (err) {
      setPosting({ kind: "error", message: (err as Error).message });
    }
  };

  return (
    <div className="border-t border-zinc-800 bg-zinc-950 px-4 py-3">
      {posting.kind === "posted" ? (
        <div>
          <p className="text-sm text-emerald-400">✓ Review posted</p>
          <a
            href={posting.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 block font-mono text-[11px] text-zinc-400 hover:text-zinc-200 break-all"
          >
            {posting.url}
          </a>
        </div>
      ) : (
        <>
          <p className="text-xs text-zinc-500">
            <span className="text-emerald-300">{counts.approved}</span> approved ·{" "}
            <span className="text-zinc-500">{counts.dismissed}</span> dismissed ·{" "}
            <span className="text-zinc-500">{counts.pending}</span> pending
          </p>
          <button
            onClick={onPost}
            disabled={approvedIds.length === 0 || posting.kind === "posting"}
            className="mt-2 w-full rounded bg-emerald-500/90 px-3 py-2 text-sm font-medium text-black hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500 transition"
          >
            {posting.kind === "posting"
              ? "Posting…"
              : approvedIds.length === 0
                ? "Approve at least one to post"
                : `Post ${approvedIds.length} to GitHub`}
          </button>
          {posting.kind === "error" && (
            <p className="mt-2 text-xs text-red-400">{posting.message}</p>
          )}
        </>
      )}
    </div>
  );
}
