"""CLI entry point.

The CLI is also the data layer for the planned Electron app. Every command
that the GUI will eventually surface (`list`, `inspect`, `review`, `whoami`)
supports a `--json` flag — the GUI shells out and parses stdout. Human-
readable Rich output stays the default for direct CLI use.

Commands:
    login    Store GitHub credentials (`--token <PAT>` or interactive paste).
    logout   Clear stored credentials.
    whoami   Show the currently authenticated GitHub user.
    list     PRs awaiting your review / authored by you / assigned to you.
    inspect  Assemble and print the context bundle. No LLM call, no posting.
    review   Run the full pipeline: assemble → analyze → filter → render/post.

Token resolution cascade (handled by `auth.resolve_token`):
    1. GITHUB_TOKEN / GH_TOKEN env var
    2. `gh auth token` subprocess (zero-friction for `gh` users)
    3. Stored auth from `~/.config/code-review-wizard/auth.json`
    4. AuthMissingError → friendly "run `reviewer login`" message.
"""

from __future__ import annotations

import sys

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .auth import (
    AuthMissingError,
    clear_auth,
    fetch_github_user,
    load_auth,
    login_with_pat,
    resolve_token,
)
from .config import get_settings
from .context.assembler import assemble
from .delivery import github_poster
from .delivery.terminal_renderer import render_report
from .github_client import GitHubClient
from .models import ContextBundle, PRSummary
from .pipeline import new_run_dir, run_review_sync

app = typer.Typer(
    no_args_is_help=True,
    help="Smart code reviewer for GitHub PRs.",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


@app.callback()
def _main() -> None:
    """Subcommands: login, logout, whoami, list, inspect, review."""
    # Empty callback present so Typer keeps the multi-command CLI shape.


# ---------------------------------------------------------------------------
# Auth commands
# ---------------------------------------------------------------------------


@app.command("login")
def login_cmd(
    token: str | None = typer.Option(
        None, "--token", "-t",
        help="GitHub Personal Access Token. If omitted, prompts interactively.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit result as JSON to stdout."),
) -> None:
    """Store GitHub credentials for the CLI (and, later, the GUI)."""
    if not token:
        if sys.stdin.isatty():
            console.print(
                "[dim]Create a PAT at https://github.com/settings/tokens/new "
                "with `repo` and `read:user` scopes.[/dim]"
            )
            token = typer.prompt("Paste your token", hide_input=True)
        else:
            _fail("No --token provided and not a TTY for interactive paste.", json_out)
            raise typer.Exit(2)

    try:
        auth = login_with_pat(token)
    except Exception as e:
        _fail(f"Login failed: {e}", json_out)
        raise typer.Exit(1) from e

    if json_out:
        _emit_json(auth.model_dump(mode="json"))
    else:
        console.print(
            f"[green]Logged in as[/green] [cyan]@{auth.user_login}[/cyan] "
            f"[dim]({auth.token_type})[/dim]"
        )


@app.command("logout")
def logout_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit result as JSON to stdout."),
) -> None:
    """Clear stored credentials."""
    removed = clear_auth()
    if json_out:
        _emit_json({"removed": removed})
    else:
        if removed:
            console.print("[yellow]Logged out.[/yellow] Stored credentials removed.")
        else:
            console.print("[dim]No stored credentials to remove.[/dim]")


@app.command("whoami")
def whoami_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit result as JSON to stdout."),
) -> None:
    """Print the currently authenticated GitHub user."""
    try:
        token = resolve_token()
    except AuthMissingError as e:
        _fail(str(e), json_out)
        raise typer.Exit(2) from e

    try:
        user = fetch_github_user(token)
    except Exception as e:
        _fail(f"Could not fetch user: {e}", json_out)
        raise typer.Exit(1) from e

    stored = load_auth()
    payload = {
        "login": user["login"],
        "id": user.get("id"),
        "name": user.get("name"),
        "html_url": user.get("html_url"),
        "token_source": "stored" if stored and stored.access_token == token else "env_or_gh",
    }
    if json_out:
        _emit_json(payload)
    else:
        console.print(
            f"[cyan]@{payload['login']}[/cyan]"
            + (f" ({payload['name']})" if payload['name'] else "")
            + f"\n[dim]{payload['html_url']}   token via {payload['token_source']}[/dim]"
        )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd(
    filter_: str = typer.Option(
        "all", "--filter", "-f",
        help="One of: review-requested, authored, assigned, all.",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results per group."),
    json_out: bool = typer.Option(False, "--json", help="Emit result as JSON to stdout."),
) -> None:
    """List PRs across all repos you can see, grouped by relationship."""
    settings = get_settings()

    try:
        with GitHubClient(None, settings.cache_dir) as gh:
            groups: dict[str, list[PRSummary]] = {}
            if filter_ in ("all", "review-requested"):
                groups["review_requested"] = gh.list_review_requested(max_results=limit)
            if filter_ in ("all", "authored"):
                groups["authored"] = gh.list_authored(max_results=limit)
            if filter_ in ("all", "assigned"):
                groups["assigned"] = gh.list_assigned(max_results=limit)
    except AuthMissingError as e:
        _fail(str(e), json_out)
        raise typer.Exit(2) from e

    if json_out:
        _emit_json({
            k: [pr.model_dump(mode="json") for pr in v] for k, v in groups.items()
        })
        return

    _render_pr_groups(groups)


# ---------------------------------------------------------------------------
# Review pipeline commands
# ---------------------------------------------------------------------------


@app.command("inspect")
def inspect_cmd(
    url: str = typer.Argument(..., help="GitHub PR URL"),
    json_out: bool = typer.Option(False, "--json", help="Emit ContextBundle as JSON to stdout."),
) -> None:
    """Assemble and print the context bundle. No LLM call, no posting."""
    settings = get_settings()
    try:
        with GitHubClient(None, settings.cache_dir) as gh:
            if not json_out:
                with console.status("[cyan]Assembling context...[/cyan]"):
                    bundle = assemble(url, gh)
            else:
                bundle = assemble(url, gh)
            run_dir = new_run_dir(settings)
            bundle_path = run_dir / "context.json"
            bundle_path.write_text(bundle.model_dump_json(indent=2))
    except AuthMissingError as e:
        _fail(str(e), json_out)
        raise typer.Exit(2) from e

    if json_out:
        _emit_json(bundle.model_dump(mode="json"))
    else:
        _render_bundle(bundle)
        console.print(f"\n[dim]Bundle written to {bundle_path}[/dim]")


@app.command("review")
def review_cmd(
    url: str = typer.Argument(..., help="GitHub PR URL"),
    post: bool = typer.Option(False, "--post", help="Post the review to GitHub instead of only printing."),
    json_out: bool = typer.Option(False, "--json", help="Emit ReviewReport as JSON to stdout."),
) -> None:
    """Run the full pipeline: assemble → analyze → filter → render (or post)."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        _fail("ANTHROPIC_API_KEY is not set. Add it to .env or your environment.", json_out)
        raise typer.Exit(2)

    try:
        with GitHubClient(None, settings.cache_dir) as gh:
            if not json_out:
                with console.status("[cyan]Reviewing PR...[/cyan]"):
                    report = run_review_sync(url, gh, settings)
            else:
                report = run_review_sync(url, gh, settings)

            if json_out:
                # Emit and (optionally) post; don't render.
                _emit_json(report.model_dump(mode="json"))
            else:
                render_report(report, console)

            if post:
                if not report.comments and not report.orphan_comments:
                    if not json_out:
                        console.print("\n[yellow]Nothing to post — review came back clean.[/yellow]")
                    return
                if not json_out:
                    with console.status("[cyan]Posting to GitHub...[/cyan]"):
                        review_url = github_poster.post(gh, report)
                    console.print(f"\n[green]Review posted:[/green] {review_url}")
                else:
                    github_poster.post(gh, report)
    except AuthMissingError as e:
        _fail(str(e), json_out)
        raise typer.Exit(2) from e


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render_pr_groups(groups: dict[str, list[PRSummary]]) -> None:
    titles = {
        "review_requested": ("▶ Awaiting your review", "yellow"),
        "authored": ("▶ Your open PRs", "green"),
        "assigned": ("▶ Assigned to you", "cyan"),
    }

    any_shown = False
    for key, (heading, color) in titles.items():
        prs = groups.get(key)
        if prs is None:
            continue
        any_shown = True
        console.print(f"\n[{color} bold]{heading}[/{color} bold] [dim]({len(prs)})[/dim]")
        if not prs:
            console.print("  [dim](none)[/dim]")
            continue
        for pr in prs:
            draft_tag = " [magenta][draft][/magenta]" if pr.draft else ""
            console.print(
                f"  [bold]#{pr.number}[/bold] [cyan]{pr.owner}/{pr.repo}[/cyan]{draft_tag} "
                f"— {pr.title[:80]}"
            )
            console.print(
                f"        [dim]by @{pr.author}   updated {pr.updated_at.date()}   {pr.url}[/dim]"
            )
    if not any_shown:
        console.print("[dim]No PRs found for the requested filter.[/dim]")


def _render_bundle(bundle: ContextBundle) -> None:
    pr = bundle.pr
    console.print(
        Panel(
            f"[bold]{pr.title}[/bold]\n"
            f"[dim]{pr.url}[/dim]\n\n"
            f"by [cyan]{pr.author}[/cyan]    "
            f"[yellow]{pr.base_sha[:8]}[/yellow] → "
            f"[green]{pr.head_sha[:8]}[/green]",
            title=f"PR #{pr.number}",
            border_style="blue",
        )
    )

    snippet = bundle.repo_conventions_snippet
    if len(snippet) > 300:
        snippet = snippet[:300].rstrip() + "..."
    console.print(
        Panel(
            f"{bundle.diff_summary}\n\n[dim]Conventions:[/dim]\n{snippet}",
            title="Context summary",
            border_style="dim",
        )
    )

    if not bundle.changed_symbols:
        console.print("[yellow]No changed symbols found.[/yellow]")
        return

    for ctx in bundle.changed_symbols:
        s = ctx.symbol
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=False)
        table.add_column(style="dim", no_wrap=True)
        table.add_column(overflow="fold")
        table.add_row("kind", f"{s.symbol_kind} · {s.language}")
        table.add_row("range", f"L{s.start_line}–L{s.end_line}")
        table.add_row("callers", f"{len(ctx.callers)} shown")
        for c in ctx.callers:
            tag = "[magenta][T][/magenta]" if c.is_test else "   "
            table.add_row(
                "",
                f"{tag} [cyan]{c.file_path}[/cyan]:[yellow]{c.line}[/yellow] · {c.snippet[:70]}",
            )
        if ctx.sibling_symbols:
            shown = ", ".join(ctx.sibling_symbols[:8])
            more = f"  (+{len(ctx.sibling_symbols) - 8})" if len(ctx.sibling_symbols) > 8 else ""
            table.add_row("siblings", f"{shown}{more}")
        if ctx.related_test_files:
            table.add_row("tests", "\n".join(ctx.related_test_files))
        anchored = len(bundle.valid_lines_for(s.file_path))
        table.add_row("anchorable", f"{anchored} lines in diff for this file")

        console.print(
            Panel(
                table,
                title=f"[bold]{s.file_path}[/bold] :: [cyan]{s.symbol_name}[/cyan]",
                border_style="cyan",
            )
        )


# ---------------------------------------------------------------------------
# JSON / error helpers
# ---------------------------------------------------------------------------


def _emit_json(payload: object) -> None:
    import json
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _fail(message: str, json_out: bool) -> None:
    """Print an error. In JSON mode → structured to stderr. Otherwise:
    first line is the headline (bold red); the rest is detail (plain),
    so multi-line guidance with install commands stays readable.
    """
    if json_out:
        import json
        json.dump({"error": message}, sys.stderr, indent=2)
        sys.stderr.write("\n")
        return

    headline, _, detail = message.partition("\n")
    err_console.print(f"[red bold]{headline}[/red bold]")
    if detail:
        err_console.print(detail)


if __name__ == "__main__":
    app()
