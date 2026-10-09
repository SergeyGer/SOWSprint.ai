"""Presentation helpers for the financial observability dashboard.

The PRD requires a *sticky* widget showing real-time compute cost per session.
Chainlit renders Markdown, so the widget is produced here as a Markdown card plus a
compact one-line form suitable for a fixed header.
"""

from __future__ import annotations

from ..models import CostReport

_BAR_FULL = "█"
_BAR_EMPTY = "░"
_BAR_WIDTH = 12


def budget_bar(report: CostReport, width: int = _BAR_WIDTH) -> str:
    """Render a unicode progress bar for budget consumption."""
    pct = report.budget_used_pct
    filled = min(width, round(width * pct / 100.0))
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def _money(value: float) -> str:
    if value == 0:
        return "$0.0000"
    if value < 0.01:
        return f"${value:.6f}"
    return f"${value:.4f}"


def one_liner(report: CostReport) -> str:
    """Single-line summary for a sticky header."""
    cached = (
        f" · {report.cached_tokens:,} cached (−{_money(report.cache_savings_usd)})"
        if report.cached_tokens
        else ""
    )
    return (
        f"💸 {_money(report.cost_usd)} / ${report.budget_usd:.2f} "
        f"· {report.total_tokens:,} tok · {report.calls} calls{cached}"
    )


def render_dashboard(report: CostReport, *, top_n: int = 6) -> str:
    """Render the full sticky dashboard card as Markdown."""
    bar = budget_bar(report)
    lines: list[str] = [
        "### 💸 Session compute cost",
        "",
        f"`{bar}` **{report.budget_used_pct:.1f}%** of ${report.budget_usd:.2f} budget",
        "",
        "| Metric | Value |",
        "| :-- | --: |",
        f"| Total cost | **{_money(report.cost_usd)}** |",
        f"| Remaining | {_money(report.budget_remaining_usd)} |",
        f"| Prompt tokens | {report.prompt_tokens:,} |",
        f"| Completion tokens | {report.completion_tokens:,} |",
        f"| Total tokens | **{report.total_tokens:,}** |",
        f"| LLM calls | {report.calls} |",
        f"| Avg latency | {report.avg_latency_ms:,.0f} ms |",
    ]

    if report.cached_tokens:
        lines.append(
            f"| Prompt cache | {report.cached_tokens:,} tok reused "
            f"(saved {_money(report.cache_savings_usd)}) |"
        )

    if report.by_node:
        lines += ["", "**Cost by agent**", "", "| Agent | Calls | Tokens | Cost |", "| :-- | --: | --: | --: |"]
        ordered = sorted(report.by_node.items(), key=lambda kv: -kv[1].get("cost_usd", 0.0))
        for node, stats in ordered[:top_n]:
            lines.append(
                f"| `{node}` | {int(stats.get('calls', 0))} | "
                f"{int(stats.get('tokens', 0)):,} | {_money(stats.get('cost_usd', 0.0))} |"
            )

    if report.by_model:
        lines += ["", "**Cost by model**", "", "| Model | Calls | Tokens | Cost |", "| :-- | --: | --: | --: |"]
        ordered_models = sorted(report.by_model.items(), key=lambda kv: -kv[1].get("cost_usd", 0.0))
        for model, stats in ordered_models[:top_n]:
            lines.append(
                f"| `{model}` | {int(stats.get('calls', 0))} | "
                f"{int(stats.get('tokens', 0)):,} | {_money(stats.get('cost_usd', 0.0))} |"
            )

    if report.budget_used_pct >= 100.0:
        lines += ["", "> 🛑 **Budget exhausted** — the graph halts before the next call."]
    elif report.budget_used_pct >= 80.0:
        lines += ["", "> ⚠️ **Budget warning** — remaining steps route to the fast tier."]

    return "\n".join(lines)


def render_plain(report: CostReport) -> str:
    """Terminal-friendly rendering used by the headless CLI and CI smoke tests."""
    return "\n".join(
        [
            f"SOWSprint session {report.session_id}",
            f"  calls            : {report.calls}",
            f"  prompt tokens    : {report.prompt_tokens:,}",
            f"  completion tokens: {report.completion_tokens:,}",
            f"  total tokens     : {report.total_tokens:,}",
            f"  cost (USD)       : {_money(report.cost_usd)}",
            f"  prompt cache     : {report.cached_tokens:,} tok reused "
            f"(saved {_money(report.cache_savings_usd)})",
            f"  budget (USD)     : {report.budget_usd:.2f} "
            f"({report.budget_used_pct:.1f}% used)",
            f"  avg latency (ms) : {report.avg_latency_ms:,.0f}",
        ]
    )
