/**
 * Diff display. v1 is a styled <pre> with +/- coloring — enough to read.
 * Monaco diff viewer lands in a follow-up commit (heavier dep + worker
 * config; ship the smaller version first so the navigation feels solid).
 */

import type { ContextBundle } from "@shared/types";

export function DiffPanel({ bundle }: { bundle: ContextBundle }) {
  return (
    <div className="px-4 py-4">
      <section className="mb-4">
        <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Description
        </h2>
        {bundle.pr.description?.trim() ? (
          <p className="mt-2 whitespace-pre-wrap text-sm text-zinc-300">
            {bundle.pr.description.slice(0, 800)}
            {bundle.pr.description.length > 800 ? "…" : ""}
          </p>
        ) : (
          <p className="mt-2 text-sm italic text-zinc-600">No description.</p>
        )}
      </section>

      <section className="mb-4">
        <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Changed symbols ({bundle.changed_symbols.length})
        </h2>
        <ul className="mt-2 space-y-1">
          {bundle.changed_symbols.map((ctx, i) => (
            <li key={i} className="font-mono text-xs">
              <span className="text-zinc-500">{ctx.symbol.symbol_kind}</span>{" "}
              <span className="text-zinc-200">{ctx.symbol.symbol_name}</span>{" "}
              <span className="text-zinc-600">
                · {ctx.symbol.file_path}:{ctx.symbol.start_line}–{ctx.symbol.end_line}
              </span>{" "}
              <span className="text-zinc-600">· {(ctx.callers ?? []).length} caller(s)</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Diff
        </h2>
        <pre className="mt-2 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-relaxed">
          {(bundle.full_diff ?? "").split("\n").map((line, i) => (
            <div key={i} className={lineColor(line)}>
              {line || " "}
            </div>
          ))}
        </pre>
      </section>
    </div>
  );
}

function lineColor(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "text-zinc-500 font-medium";
  if (line.startsWith("@@")) return "text-sky-400";
  if (line.startsWith("+")) return "text-emerald-300 bg-emerald-950/30";
  if (line.startsWith("-")) return "text-red-300 bg-red-950/30";
  if (line.startsWith("diff --git")) return "text-zinc-500 mt-3 font-medium";
  return "text-zinc-400";
}
