/**
 * Right-side panel for the PR detail view. Has three modes:
 *   - idle: a single "Run review" button
 *   - running: live progress events from the streaming pipeline
 *   - completed: list of review comments with severity badges
 */

import { useStore } from "@renderer/lib/store";
import { api } from "@renderer/lib/api";
import type { ContextBundle, PipelineEvent, ReviewComment } from "@shared/types";

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

      {progress.status === "idle" && (
        <div className="flex flex-1 flex-col items-center justify-center px-6">
          <p className="text-center text-sm text-zinc-500">
            {bundle.changed_symbols.length} symbol(s) ready.
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
      )}

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

      {progress.status === "completed" && report && (
        <div className="flex-1 overflow-auto px-4 py-3">
          <ReportSummary
            inline={(report.comments ?? []).length}
            orphan={(report.orphan_comments ?? []).length}
            total={report.total_candidates ?? 0}
            cost={report.cost_summary?.total_cost_usd ?? 0}
          />
          <CommentsList
            heading="Inline"
            comments={report.comments ?? []}
            emptyMessage="No inline comments."
          />
          {(report.orphan_comments ?? []).length > 0 && (
            <CommentsList
              heading="General observations"
              comments={report.orphan_comments ?? []}
              emptyMessage="None."
            />
          )}
        </div>
      )}
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

function ReportSummary(props: {
  inline: number;
  orphan: number;
  total: number;
  cost: number;
}) {
  return (
    <div className="mb-4 rounded border border-zinc-800 bg-zinc-900/40 px-3 py-2">
      <p className="text-xs text-zinc-400">
        <span className="text-zinc-200 font-medium">
          {props.inline + props.orphan}
        </span>{" "}
        kept of {props.total} ·{" "}
        <span className="text-emerald-300">${props.cost.toFixed(4)}</span>
      </p>
    </div>
  );
}

function CommentsList({
  heading,
  comments,
  emptyMessage,
}: {
  heading: string;
  comments: ReviewComment[];
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
        <ul className="mt-2 space-y-3">
          {comments.map((c) => (
            <li
              key={c.id}
              className="rounded border border-zinc-800 bg-zinc-900/40 px-3 py-2"
            >
              <div className="flex items-baseline gap-2">
                <SeverityBadge severity={c.severity} />
                <span className="font-mono text-xs text-zinc-500">
                  {c.file_path}:{c.line}
                </span>
              </div>
              <p className="mt-2 text-sm font-medium text-zinc-200">{c.title}</p>
              <p className="mt-1 text-xs text-zinc-400 whitespace-pre-wrap leading-relaxed">
                {c.body}
              </p>
              <p className="mt-2 text-[10px] text-zinc-600">
                confidence {c.confidence.toFixed(2)} · {c.category}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const styles: Record<string, string> = {
    blocking: "bg-red-500/20 text-red-300",
    suggestion: "bg-amber-500/20 text-amber-200",
    nit: "bg-zinc-700/50 text-zinc-400",
  };
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${
        styles[severity] ?? "bg-zinc-700 text-zinc-300"
      }`}
    >
      {severity}
    </span>
  );
}
