/**
 * One review comment with per-comment triage controls:
 *   Approve   — commit to posting this to GitHub
 *   Edit      — modify the body inline; saving auto-approves
 *   Dismiss   — exclude from the post; logged to dropped.json as user_dismissed
 *
 * Decision + edits live in the global store so the action bar at the bottom
 * of the panel can read counts and submit them in one shot.
 */

import { useState } from "react";
import { useStore } from "@renderer/lib/store";
import type { ReviewComment } from "@shared/types";

export function CommentItem({ comment }: { comment: ReviewComment }) {
  const decision = useStore((s) => s.triage[comment.id!] ?? "pending");
  const editedBody = useStore((s) => s.edits[comment.id!]);
  const setDecision = useStore((s) => s.setDecision);
  const setEdit = useStore((s) => s.setEdit);
  const clearEdit = useStore((s) => s.clearEdit);

  const [editing, setEditing] = useState(false);

  const dismissed = decision === "dismissed";
  const approved = decision === "approved";
  const edited = editedBody !== undefined;

  const borderClass = approved
    ? "border-emerald-500/40"
    : dismissed
      ? "border-zinc-800 opacity-40"
      : "border-zinc-800";

  return (
    <li className={`rounded border ${borderClass} bg-zinc-900/40 px-3 py-2 transition`}>
      <div className="flex items-baseline gap-2">
        <SeverityBadge severity={comment.severity} />
        <span className="font-mono text-xs text-zinc-500">
          {comment.file_path}:{comment.line}
        </span>
        {edited && (
          <span className="rounded bg-sky-500/20 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-sky-300">
            edited
          </span>
        )}
        {approved && (
          <span className="ml-auto text-xs text-emerald-400">✓ approved</span>
        )}
      </div>

      <p
        className={`mt-2 text-sm font-medium ${
          dismissed ? "line-through text-zinc-500" : "text-zinc-200"
        }`}
      >
        {comment.title}
      </p>

      {editing ? (
        <div className="mt-2">
          <textarea
            value={editedBody ?? comment.body}
            onChange={(e) => setEdit(comment.id!, e.target.value)}
            autoFocus
            rows={5}
            className="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200 focus:border-amber-500 focus:outline-none font-mono leading-relaxed"
          />
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => {
                setEditing(false);
                setDecision(comment.id!, "approved");
              }}
              className="rounded bg-emerald-500/80 px-2 py-1 text-xs font-medium text-black hover:bg-emerald-400"
            >
              Save & approve
            </button>
            <button
              onClick={() => {
                setEditing(false);
                clearEdit(comment.id!);
              }}
              className="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <p
          className={`mt-1 text-xs whitespace-pre-wrap leading-relaxed ${
            dismissed ? "line-through text-zinc-600" : "text-zinc-400"
          }`}
        >
          {editedBody ?? comment.body}
        </p>
      )}

      <div className="mt-3 flex items-center gap-1">
        <span className="text-[10px] text-zinc-600">
          confidence {comment.confidence.toFixed(2)} · {comment.category}
        </span>
        <div className="ml-auto flex gap-1">
          {!editing && (
            <>
              <TriageButton
                active={approved}
                onClick={() =>
                  setDecision(comment.id!, approved ? "pending" : "approved")
                }
                className={
                  approved
                    ? "bg-emerald-500/30 text-emerald-200"
                    : "text-zinc-400 hover:bg-zinc-800"
                }
              >
                {approved ? "✓" : "Approve"}
              </TriageButton>
              {!dismissed && (
                <TriageButton onClick={() => setEditing(true)}>
                  Edit
                </TriageButton>
              )}
              <TriageButton
                active={dismissed}
                onClick={() =>
                  setDecision(comment.id!, dismissed ? "pending" : "dismissed")
                }
                className={
                  dismissed
                    ? "bg-zinc-700 text-zinc-300"
                    : "text-zinc-400 hover:bg-zinc-800"
                }
              >
                {dismissed ? "Undo dismiss" : "Dismiss"}
              </TriageButton>
            </>
          )}
        </div>
      </div>
    </li>
  );
}

function TriageButton(props: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  className?: string;
}) {
  const base = "rounded px-2 py-0.5 text-[11px] transition";
  return (
    <button onClick={props.onClick} className={`${base} ${props.className ?? "text-zinc-400 hover:bg-zinc-800"}`}>
      {props.children}
    </button>
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
