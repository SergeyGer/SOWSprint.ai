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


def _compact(value: int) -> str:
    """1234567 -> '1.23M', for tiles too narrow for a full number."""
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _escape(text: object) -> str:
    """Escape text interpolated into the dashboard's HTML."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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


def render_dashboard_html(report: CostReport, *, simulated: bool = False) -> str:
    """Render the cost dashboard as a self-contained HTML card.

    This replaced a Chainlit ``CustomElement``. Chainlit 2.12 fetches
    ``/public/elements/<Name>.jsx`` verbatim and evaluates it in the browser, but
    nothing in the wheel or the runtime image compiles JSX — there is no esbuild in
    the Python package and no Node in the container — so the element never mounted and
    the dashboard was invisible in a real browser while the socket payload looked
    correct. Raw HTML in a message renders reliably and is verifiable from the DOM.

    Requires ``unsafe_allow_html = true`` in ``.chainlit/config.toml``.
    """
    pct = min(100.0, report.budget_used_pct)
    bar = "#ef4444" if pct >= 100 else "#f59e0b" if pct >= 80 else "#10b981"

    def tile(name: str, value: str, colour: str | None = None) -> str:
        style = f"color:{colour}" if colour else ""
        return (
            '<div style="padding:6px 8px;border-radius:8px;'
            'background:rgba(148,163,184,0.10)">'
            f'<div style="font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
            f'color:#94a3b8">{name}</div>'
            f'<div style="font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;'
            f'{style}">{value}</div></div>'
        )

    tiles = [
        tile("Tokens", _compact(report.total_tokens)),
        tile("Prompt", _compact(report.prompt_tokens)),
        tile("Completion", _compact(report.completion_tokens)),
        tile("LLM calls", str(report.calls)),
        tile("Avg latency", f"{report.avg_latency_ms:,.0f} ms"),
    ]
    if report.cached_tokens:
        tiles.append(
            tile(
                "Cache hit",
                f"{_compact(report.cached_tokens)} &minus;{_money(report.cache_savings_usd)}",
                "#34d399",
            )
        )

    node_rows = ""
    if report.by_node:
        top = sorted(
            report.by_node.items(),
            key=lambda kv: float(kv[1].get("cost_usd", 0)),
            reverse=True,
        )[:6]
        peak = max((float(v.get("cost_usd", 0)) for _, v in top), default=1.0) or 1.0
        rows = []
        for name, stats in top:
            cost = float(stats.get("cost_usd", 0))
            width = 100.0 * cost / peak
            rows.append(
                '<div style="display:grid;grid-template-columns:82px 1fr 62px;'
                'align-items:center;gap:8px">'
                f'<span style="color:#cbd5e1;font-size:11px;overflow:hidden;'
                f'text-overflow:ellipsis;white-space:nowrap">{_escape(name)}</span>'
                '<span style="height:5px;border-radius:999px;'
                'background:rgba(148,163,184,0.18);overflow:hidden;display:block">'
                f'<span style="display:block;height:100%;width:{width:.1f}%;'
                'background:#3b82f6"></span></span>'
                '<span style="text-align:right;font-variant-numeric:tabular-nums;'
                f'font-size:11px;color:#cbd5e1">{_money(cost)}</span></div>'
            )
        node_rows = (
            '<div style="margin-top:10px">'
            '<div style="font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
            'color:#94a3b8">Cost by agent</div>'
            '<div style="margin-top:5px;display:grid;gap:4px">'
            + "".join(rows)
            + "</div></div>"
        )

    footnote = (
        "Simulated pricing &mdash; offline engine consumes no billable tokens"
        if simulated
        else "Live provider pricing"
    )
    return (
        '<div class="sow-cost-dashboard" style="margin:4px 0 14px;padding:12px 14px;'
        'border-radius:12px;border:1px solid rgba(148,163,184,0.28);'
        'background:rgba(15,23,42,0.92);color:#e2e8f0;'
        "font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        'sans-serif;font-size:13px;line-height:1.45">'
        '<div style="display:flex;align-items:baseline;justify-content:space-between;'
        'gap:10px;flex-wrap:wrap">'
        "<div>"
        '<div style="font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
        'color:#94a3b8">Session compute cost</div>'
        f'<div style="font-size:20px;font-weight:700;color:#f8fafc;'
        f'font-variant-numeric:tabular-nums">{_money(report.cost_usd)}</div>'
        "</div>"
        '<div style="text-align:right">'
        '<div style="font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
        'color:#94a3b8">Budget</div>'
        f'<div style="font-size:14px;font-weight:600">{pct:.1f}% of '
        f"${report.budget_usd:.2f}</div>"
        "</div></div>"
        '<div style="margin-top:8px;height:6px;border-radius:999px;'
        'background:rgba(148,163,184,0.22);overflow:hidden">'
        f'<div style="width:{pct:.1f}%;height:100%;background:{bar}"></div></div>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(84px,1fr));'
        f'gap:8px;margin-top:10px">{"".join(tiles)}</div>'
        f"{node_rows}"
        '<div style="margin-top:9px;padding-top:7px;'
        'border-top:1px solid rgba(148,163,184,0.18);display:flex;'
        'justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:10px;'
        f'color:#94a3b8"><span>{footnote}</span></div>'
        "</div>"
    )
