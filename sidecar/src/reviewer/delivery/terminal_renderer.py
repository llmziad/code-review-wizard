"""Rich-based renderer — what the interviewer sees on `reviewer review`.

The renderer is the demo surface. Inline-anchored comments group by file with
severity-coded panels; orphan comments (couldn't anchor in-diff) land in a
"General observations" section. A footer shows tokens + cost.
"""

from __future__ import annotations

from collections import defaultdict

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from ..models import ReviewComment, ReviewReport

_SEVERITY_STYLE = {
    "blocking": ("red", "BLOCKING"),
    "suggestion": ("yellow", "SUGGESTION"),
    "nit": ("dim", "nit"),
}


def render_report(report: ReviewReport, console: Console) -> None:
    console.print(_header_panel(report))
    console.print(_summary_panel(report))

    if not report.comments and not report.orphan_comments:
        console.print(
            Panel(
                "[green]No comments to surface. The reviewer ran clean.[/green]",
                border_style="green",
            )
        )
    else:
        for panel in _file_panels(report.comments):
            console.print(panel)
        if report.orphan_comments:
            console.print(_orphans_panel(report.orphan_comments))

    console.print(_footer(report))


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------


def _header_panel(report: ReviewReport) -> Panel:
    pr = report.pr
    return Panel(
        Text.from_markup(
            f"[bold]{_escape(pr.title)}[/bold]\n"
            f"[dim]{pr.url}[/dim]\n\n"
            f"by [cyan]{pr.author}[/cyan]    "
            f"[yellow]{pr.base_sha[:8]}[/yellow] → "
            f"[green]{pr.head_sha[:8]}[/green]"
        ),
        title=f"PR #{pr.number}",
        border_style="blue",
    )


def _summary_panel(report: ReviewReport) -> Panel:
    by_sev = _count_by_severity(report.comments)
    by_sev_orphan = _count_by_severity(report.orphan_comments)
    line1 = (
        f"Passes: [cyan]{', '.join(report.passes_run) or '(none)'}[/cyan]"
        + (f"   [dim](skipped: {', '.join(report.passes_skipped)})[/dim]" if report.passes_skipped else "")
    )
    inline = ", ".join(f"{n} {sev}" for sev, n in by_sev.items()) or "0 inline"
    orphan = sum(by_sev_orphan.values())
    line2 = (
        f"{report.total_posted}/{report.total_candidates} surfaced after filtering"
        f"    inline: {inline}"
        + (f"    orphan: {orphan}" if orphan else "")
    )
    cs = report.cost_summary
    line3 = (
        f"Tokens: [cyan]{cs.total_input_tokens:,}[/cyan] in / "
        f"[cyan]{cs.total_output_tokens:,}[/cyan] out    "
        f"est. cost: [green]${cs.total_cost_usd:.4f}[/green]"
    )
    return Panel(
        Text.from_markup(f"{line1}\n{line2}\n{line3}"),
        title="Run summary",
        border_style="dim",
    )


def _file_panels(comments: list[ReviewComment]) -> list[Panel]:
    by_file: dict[str, list[ReviewComment]] = defaultdict(list)
    for c in comments:
        by_file[c.file_path].append(c)

    panels = []
    for path, group in by_file.items():
        # Already prioritized; preserve order
        rendered = [_render_one_comment(c) for c in group]
        panels.append(
            Panel(
                Group(*_interleave(rendered)),
                title=f"[bold]{_escape(path)}[/bold]",
                border_style="cyan",
            )
        )
    return panels


def _render_one_comment(c: ReviewComment) -> Group:
    color, label = _SEVERITY_STYLE[c.severity]
    header = Text.from_markup(
        f"[{color} bold]{label}[/{color} bold]  "
        f"[dim]L{c.line} · {c.category} · confidence {c.confidence:.2f} · "
        f"via {c.pass_name}[/dim]"
    )
    title = Text.from_markup(f"[bold]{_escape(c.title)}[/bold]")
    body = Markdown(c.body)
    items = [header, title, body]
    if c.suggested_fix:
        items.append(Text("Suggested fix:", style="dim"))
        items.append(_suggestion_block(c.suggested_fix))
    return Group(*items)


def _suggestion_block(code: str) -> Syntax:
    # Try Python lexing first; fall back to generic if it doesn't matter.
    try:
        return Syntax(code, "python", theme="monokai", line_numbers=False, word_wrap=True)
    except Exception:
        return Syntax(code, "text", line_numbers=False, word_wrap=True)


def _orphans_panel(orphans: list[ReviewComment]) -> Panel:
    rendered = [_render_one_comment(c) for c in orphans]
    return Panel(
        Group(*_interleave(rendered)),
        title="[bold]General observations[/bold] [dim](could not anchor in-diff)[/dim]",
        border_style="magenta",
    )


def _footer(report: ReviewReport) -> Text:
    return Text.from_markup(
        f"[dim]{report.total_candidates - report.total_posted} comment(s) filtered. "
        f"Run log under cache/runs/. Re-run with --post to send this review to GitHub.[/dim]"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_by_severity(comments: list[ReviewComment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in comments:
        counts[c.severity] = counts.get(c.severity, 0) + 1
    return counts


def _interleave(items: list, separator: Text | None = None) -> list:
    sep = separator if separator is not None else Text("")
    out: list = []
    for i, it in enumerate(items):
        if i > 0:
            out.append(sep)
        out.append(it)
    return out


def _escape(s: str) -> str:
    return s.replace("[", r"\[")
