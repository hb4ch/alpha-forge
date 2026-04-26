#!/usr/bin/env python3
"""Generate HTML visualization of multi-factor ablation results.

Produces equity curves for all variants vs buy-and-hold baseline,
organized by symbol and period.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "crypto-pegasus"))

import numpy as np
import pandas as pd
import yaml

from pegasus.config import BacktestConfig
from pegasus.data.provider import DataProvider
from pegasus.engine.backtest import BacktestEngine
from pegasus.strategy.base import Strategy

# Reuse feature/signal logic from ablation_multifactor
from ablation_multifactor import (
    compute_all_features, zscore_normalize, make_signal_fn,
    MultiFactorStrategy, VARIANTS, SPLITS, SYMBOLS, PERIODS,
)


def build_equity_data(bars_cache: dict[str, pd.DataFrame]) -> dict:
    """Run all variants and collect equity curves + buy-and-hold.

    Also generates leverage-adjusted versions of key strategies,
    scaled to match buy-and-hold annualized volatility.
    """
    all_data = {}
    # Collect raw returns for leverage calculation
    raw_returns: dict[str, dict[str, pd.Series]] = {}

    for name, spec in VARIANTS.items():
        signal_fn = make_signal_fn(
            spec["features"],
            spec.get("weights"),
            spec.get("regime_weights"),
            spec.get("long_only", True),
        )
        config = BacktestConfig(
            stop_loss_pct=spec.get("stop_loss_pct", 0.10),
            trailing_stop_pct=spec.get("trailing_stop_pct"),
            timeframe="1d",
        )
        strategy = MultiFactorStrategy(signal_fn)
        engine = BacktestEngine(strategy, config)

        all_data[name] = {}
        raw_returns[name] = {}
        for sym in SYMBOLS:
            full_start = SPLITS["train"]["start"]
            full_end = SPLITS["holdout"]["end"]
            bars = bars_cache[sym].loc[full_start:full_end]
            if len(bars) < 80:
                continue

            result = engine.run_on_bars(bars, symbol=sym, timeframe="1d")
            eq = result.equity_curve
            equity_norm = eq["equity"] / eq["equity"].iloc[0]

            all_data[name][sym] = {
                "dates": [str(d.date()) for d in equity_norm.index],
                "equity": equity_norm.values.tolist(),
            }
            raw_returns[name][sym] = eq["returns"]

    # Buy-and-hold baseline
    all_data["Buy_and_Hold"] = {}
    raw_returns["Buy_and_Hold"] = {}
    bh_vols = []
    for sym in SYMBOLS:
        full_start = SPLITS["train"]["start"]
        full_end = SPLITS["holdout"]["end"]
        bars = bars_cache[sym].loc[full_start:full_end]
        if len(bars) < 80:
            continue
        bh = bars["close"] / bars["close"].iloc[0]
        bh_ret = bars["close"].pct_change()
        all_data["Buy_and_Hold"][sym] = {
            "dates": [str(d.date()) for d in bh.index],
            "equity": bh.values.tolist(),
        }
        raw_returns["Buy_and_Hold"][sym] = bh_ret
        bh_vols.append(bh_ret.std() * np.sqrt(252))

    # Target vol = average buy-and-hold annualized vol
    target_vol = np.mean(bh_vols)

    # Generate leverage-adjusted versions of key strategies
    leverage_candidates = ["A_stable_eq_LO", "G_regime_stable_LO", "J_rsi_only_LO"]
    for name in leverage_candidates:
        if name not in raw_returns:
            continue
        lev_name = f"{name}_LEV"
        all_data[lev_name] = {}
        for sym in SYMBOLS:
            if sym not in raw_returns[name]:
                continue
            strat_ret = raw_returns[name][sym]
            strat_vol = strat_ret.dropna().std() * np.sqrt(252)
            if strat_vol < 1e-6:
                continue
            leverage = target_vol / strat_vol
            lev_ret = strat_ret * leverage
            lev_equity = (1 + lev_ret.fillna(0)).cumprod()
            all_data[lev_name][sym] = {
                "dates": [str(d.date()) for d in lev_equity.index],
                "equity": lev_equity.values.tolist(),
                "leverage": round(leverage, 2),
            }

    return all_data


def generate_html(all_data: dict, output_path: Path) -> None:
    """Generate self-contained HTML with Plotly charts."""

    # Period boundaries for vertical lines
    period_bounds = {
        "Train End": SPLITS["train"]["end"],
        "Val End": SPLITS["validation"]["end"],
    }

    # Select key variants to highlight (not all 10)
    key_variants = [
        "Buy_and_Hold",
        "G_regime_stable_LO",
        "J_rsi_only_LO",
        "G_regime_stable_LO_LEV",
        "J_rsi_only_LO_LEV",
    ]

    # Colors
    colors = {
        "Buy_and_Hold": "#888888",
        "A_stable_eq_LO": "#2196F3",
        "B_stable_eq_LS": "#9C27B0",
        "C_stable_icw_LO": "#03A9F4",
        "D_stable_plus_LO": "#00BCD4",
        "E_with_activity_LO": "#009688",
        "F_activity_only": "#FF5722",
        "G_regime_stable_LO": "#4CAF50",
        "H_all_eq_LO": "#FFC107",
        "I_all_regime_LO": "#FF9800",
        "J_rsi_only_LO": "#E91E63",
        "A_stable_eq_LO_LEV": "#64B5F6",
        "G_regime_stable_LO_LEV": "#81C784",
        "J_rsi_only_LO_LEV": "#F48FB1",
    }

    variant_names = list(all_data.keys())

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Alpha Forge - Multi-Factor Ablation</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            margin: 0;
            padding: 20px;
        }}
        h1 {{
            text-align: center;
            color: #4CAF50;
            margin-bottom: 5px;
        }}
        .subtitle {{
            text-align: center;
            color: #888;
            margin-bottom: 20px;
        }}
        .controls {{
            text-align: center;
            margin: 15px 0;
            padding: 10px;
            background: #16213e;
            border-radius: 8px;
        }}
        .controls label {{
            margin: 0 8px;
            cursor: pointer;
        }}
        .controls input[type=checkbox] {{
            margin-right: 4px;
        }}
        .chart-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-bottom: 20px;
        }}
        .chart-container {{
            background: #16213e;
            border-radius: 8px;
            padding: 10px;
        }}
        .full-width {{
            grid-column: 1 / -1;
        }}
        .summary-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background: #16213e;
            border-radius: 8px;
            overflow: hidden;
        }}
        .summary-table th {{
            background: #0f3460;
            padding: 10px 12px;
            text-align: right;
            font-size: 13px;
        }}
        .summary-table th:first-child {{
            text-align: left;
        }}
        .summary-table td {{
            padding: 8px 12px;
            text-align: right;
            font-size: 13px;
            border-bottom: 1px solid #1a1a2e;
        }}
        .summary-table td:first-child {{
            text-align: left;
            font-weight: bold;
        }}
        .positive {{ color: #4CAF50; }}
        .negative {{ color: #f44336; }}
        .legend-dot {{
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 6px;
            vertical-align: middle;
        }}
    </style>
</head>
<body>
    <h1>Multi-Factor Ablation Study</h1>
    <p class="subtitle">Equity curves vs Buy-and-Hold | Train / Validation / Holdout periods<br>
    <em>_LEV variants are leverage-adjusted to match Buy-and-Hold annualized volatility</em></p>

    <div class="controls" id="variant-controls">
        <strong>Variants:</strong>
"""

    # Checkboxes for each variant
    for vname in variant_names:
        checked = "checked" if vname in key_variants else ""
        color = colors.get(vname, "#ffffff")
        html += f"""        <label>
            <input type="checkbox" value="{vname}" {checked} onchange="updateCharts()">
            <span class="legend-dot" style="background:{color}"></span>{vname}
        </label>\n"""

    html += """    </div>

    <div class="chart-grid">
"""

    # One chart per symbol + one combined
    chart_configs = []
    for sym in SYMBOLS:
        chart_configs.append({"id": f"chart_{sym}", "title": sym, "symbols": [sym]})
    chart_configs.append({"id": "chart_combined", "title": "Equal-Weight Portfolio (avg)", "symbols": SYMBOLS, "class": "full-width"})

    for cfg in chart_configs:
        cls = cfg.get("class", "")
        html += f"""        <div class="chart-container {cls}"><div id="{cfg['id']}" style="height:400px"></div></div>\n"""

    html += """    </div>\n"""

    # Summary table
    html += """    <h2 style="text-align:center">Summary: Returns by Period</h2>
    <table class="summary-table">
        <tr>
            <th>Variant</th>
            <th>Train Return</th>
            <th>Train Sharpe</th>
            <th>Val Return</th>
            <th>Val Sharpe</th>
            <th>Hold Return</th>
            <th>Hold Sharpe</th>
            <th>All 3 &gt; 0?</th>
        </tr>
"""

    # Compute summary stats inline
    for vname in variant_names:
        html += f"        <tr><td><span class='legend-dot' style='background:{colors.get(vname, '#fff')}'></span>{vname}</td>"
        period_rets = []
        for pn, (pstart, pend) in PERIODS.items():
            rets = []
            for sym in SYMBOLS:
                if sym not in all_data[vname]:
                    continue
                d = all_data[vname][sym]
                dates = d["dates"]
                equity = d["equity"]
                # Find indices within period
                start_idx = next((i for i, dt in enumerate(dates) if dt >= pstart), None)
                end_idx = next((i for i, dt in enumerate(dates) if dt >= pend), len(dates))
                if start_idx is not None and end_idx > start_idx:
                    ret = equity[end_idx - 1] / equity[start_idx] - 1
                    rets.append(ret)
            avg_ret = np.mean(rets) if rets else 0
            period_rets.append(avg_ret)
            cls = "positive" if avg_ret > 0 else "negative"
            html += f"<td class='{cls}'>{avg_ret:+.1%}</td><td>-</td>"

        all_pos = all(r > 0 for r in period_rets)
        html += f"<td>{'YES' if all_pos else 'NO'}</td></tr>\n"

    html += """    </table>\n"""

    # JavaScript with Plotly
    html += f"""
    <script>
    const allData = {json.dumps(all_data)};
    const symbols = {json.dumps(SYMBOLS)};
    const colors = {json.dumps(colors)};
    const periodBounds = {json.dumps(period_bounds)};

    function getCheckedVariants() {{
        const checks = document.querySelectorAll('#variant-controls input[type=checkbox]:checked');
        return Array.from(checks).map(c => c.value);
    }}

    function makeTraces(variantNames, targetSymbols, combined) {{
        const traces = [];
        for (const vname of variantNames) {{
            const vdata = allData[vname];
            if (!vdata) continue;

            if (combined && targetSymbols.length > 1) {{
                // Average equity across symbols
                let refDates = null;
                const equities = [];
                for (const sym of targetSymbols) {{
                    if (!vdata[sym]) continue;
                    if (!refDates) refDates = vdata[sym].dates;
                    equities.push(vdata[sym].equity);
                }}
                if (equities.length === 0) continue;
                const avgEquity = refDates.map((_, i) =>
                    equities.reduce((s, eq) => s + (eq[i] || 1), 0) / equities.length
                );
                traces.push({{
                    x: refDates,
                    y: avgEquity,
                    name: vname,
                    type: 'scatter',
                    mode: 'lines',
                    line: {{ color: colors[vname] || '#ffffff', width: vname === 'Buy_and_Hold' ? 3 : 1.5 }},
                }});
            }} else {{
                const sym = targetSymbols[0];
                if (!vdata[sym]) continue;
                traces.push({{
                    x: vdata[sym].dates,
                    y: vdata[sym].equity,
                    name: vname,
                    type: 'scatter',
                    mode: 'lines',
                    line: {{ color: colors[vname] || '#ffffff', width: vname === 'Buy_and_Hold' ? 3 : 1.5 }},
                }});
            }}
        }}
        return traces;
    }}

    function makeLayout(title) {{
        const shapes = [];
        for (const [label, date] of Object.entries(periodBounds)) {{
            shapes.push({{
                type: 'line',
                x0: date, x1: date,
                y0: 0, y1: 1,
                yref: 'paper',
                line: {{ color: '#555', width: 1, dash: 'dash' }},
            }});
        }}
        return {{
            title: {{ text: title, font: {{ color: '#e0e0e0', size: 16 }} }},
            paper_bgcolor: '#16213e',
            plot_bgcolor: '#0f3460',
            font: {{ color: '#e0e0e0' }},
            xaxis: {{ gridcolor: '#1a1a2e', title: '' }},
            yaxis: {{ gridcolor: '#1a1a2e', title: 'Normalized Equity', hoverformat: '.3f' }},
            shapes: shapes,
            annotations: Object.entries(periodBounds).map(([label, date]) => ({{
                x: date, y: 1.02, yref: 'paper',
                text: label, showarrow: false,
                font: {{ color: '#888', size: 10 }},
            }})),
            legend: {{ bgcolor: 'rgba(0,0,0,0.3)', font: {{ size: 11 }} }},
            margin: {{ t: 50, b: 40, l: 60, r: 20 }},
            hovermode: 'x unified',
        }};
    }}

    function updateCharts() {{
        const variants = getCheckedVariants();
"""

    for cfg in chart_configs:
        syms_json = json.dumps(cfg["symbols"])
        combined = "true" if len(cfg["symbols"]) > 1 else "false"
        html += f"""        Plotly.react('{cfg["id"]}',
            makeTraces(variants, {syms_json}, {combined}),
            makeLayout('{cfg["title"]}'),
            {{responsive: true}}
        );\n"""

    html += """    }

    // Initial render
    updateCharts();
    </script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)


def main():
    print("Loading cached bars...")
    config = BacktestConfig()
    provider = DataProvider(config)
    bars_cache = {}
    full_start = SPLITS["train"]["start"]
    full_end = SPLITS["holdout"]["end"]
    for sym in SYMBOLS:
        bars_cache[sym] = provider.get_bars(sym, full_start, full_end, "1d")
        print(f"  {sym}: {len(bars_cache[sym])} bars")
    provider.close()

    print("Building equity curves...")
    all_data = build_equity_data(bars_cache)

    output = Path("alpha_research/reports/ablation_multifactor.html")
    generate_html(all_data, output)
    print(f"HTML written to {output}")


if __name__ == "__main__":
    main()
