// ---------------------------------------------------------------------------
// SOWSprint.ai — sticky session cost dashboard
// ---------------------------------------------------------------------------
// Chainlit renders custom elements inline in the message stream. Applying
// `position: sticky` inside the component is what turns it into the persistent
// widget the PRD asks for: the card pins to the top of the transcript while the
// agent trace scrolls underneath it.
//
// The component is intentionally dependency-free (no imports) so it compiles
// under Chainlit's in-browser JSX transform without a bundler step.

const money = (value) => {
  const v = Number(value) || 0;
  if (v === 0) return "$0.0000";
  if (v < 0.01) return "$" + v.toFixed(6);
  return "$" + v.toFixed(4);
};

const compact = (value) => {
  const v = Number(value) || 0;
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "k";
  return String(v);
};

export default function CostDashboard(props) {
  const {
    costUsd = 0,
    budgetUsd = 2.5,
    promptTokens = 0,
    completionTokens = 0,
    totalTokens = 0,
    calls = 0,
    avgLatencyMs = 0,
    byNode = {},
    simulated = true,
    updatedAt = "",
  } = props || {};

  const pct = budgetUsd > 0 ? Math.min(100, (100 * costUsd) / budgetUsd) : 0;

  const barColor =
    pct >= 100 ? "#ef4444" : pct >= 80 ? "#f59e0b" : "#10b981";

  const nodes = Object.entries(byNode || {})
    .map(([name, stats]) => ({
      name,
      calls: Number(stats.calls) || 0,
      tokens: Number(stats.tokens) || 0,
      cost: Number(stats.cost_usd) || 0,
    }))
    .sort((a, b) => b.cost - a.cost)
    .slice(0, 6);

  const maxNodeCost = nodes.length ? Math.max(...nodes.map((n) => n.cost)) : 1;

  const shell = {
    position: "sticky",
    top: "8px",
    zIndex: 20,
    margin: "4px 0 14px 0",
    padding: "12px 14px",
    borderRadius: "12px",
    border: "1px solid rgba(148, 163, 184, 0.28)",
    background: "rgba(15, 23, 42, 0.92)",
    backdropFilter: "blur(8px)",
    color: "#e2e8f0",
    fontFamily:
      "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    fontSize: "13px",
    lineHeight: 1.45,
    boxShadow: "0 6px 20px rgba(0, 0, 0, 0.28)",
  };

  const headerRow = {
    display: "flex",
    alignItems: "baseline",
    justifyContent: "space-between",
    gap: "10px",
    flexWrap: "wrap",
  };

  const label = {
    fontSize: "10px",
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "#94a3b8",
  };

  const big = {
    fontSize: "20px",
    fontWeight: 700,
    color: "#f8fafc",
    fontVariantNumeric: "tabular-nums",
  };

  const metricGrid = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(84px, 1fr))",
    gap: "8px",
    marginTop: "10px",
  };

  const metric = {
    padding: "6px 8px",
    borderRadius: "8px",
    background: "rgba(148, 163, 184, 0.10)",
  };

  const metricValue = {
    fontSize: "14px",
    fontWeight: 600,
    fontVariantNumeric: "tabular-nums",
  };

  return (
    <div style={shell} className="sow-cost-dashboard">
      <div style={headerRow}>
        <div>
          <div style={label}>Session compute cost</div>
          <div style={big}>{money(costUsd)}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={label}>Budget</div>
          <div style={{ fontSize: "14px", fontWeight: 600 }}>
            {pct.toFixed(1)}% of ${Number(budgetUsd).toFixed(2)}
          </div>
        </div>
      </div>

      {/* Budget consumption bar */}
      <div
        style={{
          marginTop: "8px",
          height: "6px",
          borderRadius: "999px",
          background: "rgba(148, 163, 184, 0.22)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: pct + "%",
            height: "100%",
            background: barColor,
            transition: "width 240ms ease",
          }}
        />
      </div>

      <div style={metricGrid}>
        <div style={metric}>
          <div style={label}>Tokens</div>
          <div style={metricValue}>{compact(totalTokens)}</div>
        </div>
        <div style={metric}>
          <div style={label}>Prompt</div>
          <div style={metricValue}>{compact(promptTokens)}</div>
        </div>
        <div style={metric}>
          <div style={label}>Completion</div>
          <div style={metricValue}>{compact(completionTokens)}</div>
        </div>
        <div style={metric}>
          <div style={label}>LLM calls</div>
          <div style={metricValue}>{calls}</div>
        </div>
        <div style={metric}>
          <div style={label}>Avg latency</div>
          <div style={metricValue}>{Math.round(avgLatencyMs)} ms</div>
        </div>
      </div>

      {nodes.length > 0 && (
        <div style={{ marginTop: "10px" }}>
          <div style={label}>Cost by agent</div>
          <div style={{ marginTop: "5px", display: "grid", gap: "4px" }}>
            {nodes.map((node) => (
              <div
                key={node.name}
                style={{
                  display: "grid",
                  gridTemplateColumns: "82px 1fr 62px",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <span
                  style={{
                    color: "#cbd5e1",
                    fontSize: "11px",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {node.name}
                </span>
                <span
                  style={{
                    height: "5px",
                    borderRadius: "999px",
                    background: "rgba(148, 163, 184, 0.18)",
                    overflow: "hidden",
                    display: "block",
                  }}
                >
                  <span
                    style={{
                      display: "block",
                      height: "100%",
                      width:
                        (maxNodeCost > 0 ? (100 * node.cost) / maxNodeCost : 0) + "%",
                      background: "#3b82f6",
                    }}
                  />
                </span>
                <span
                  style={{
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                    fontSize: "11px",
                    color: "#cbd5e1",
                  }}
                >
                  {money(node.cost)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div
        style={{
          marginTop: "9px",
          paddingTop: "7px",
          borderTop: "1px solid rgba(148, 163, 184, 0.18)",
          display: "flex",
          justifyContent: "space-between",
          gap: "8px",
          flexWrap: "wrap",
          fontSize: "10px",
          color: "#94a3b8",
        }}
      >
        <span>
          {simulated
            ? "Simulated pricing — offline engine consumes no billable tokens"
            : "Live provider pricing"}
        </span>
        {updatedAt ? <span>updated {updatedAt}</span> : null}
      </div>
    </div>
  );
}
