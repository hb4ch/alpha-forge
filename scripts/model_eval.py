#!/usr/bin/env python
"""Model shopping: evaluate multiple LLMs on alpha-forge pipeline tasks.

Usage:
    python scripts/model_eval.py                  # run all candidates
    python scripts/model_eval.py gemini-2.5-pro   # run one candidate
    python scripts/model_eval.py kimi-k2.5 gemma4-31b-local  # run specific ones

Env vars:
    OPENROUTER_API_KEY  — required for OpenRouter models
    SILICONFLOW_API_KEY — required for DeepSeek via SiliconFlow
    DGX_SPARK_API_KEY   — for local DGX Spark models (default: sk-1234)
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
dotenv.load_dotenv(PROJECT_ROOT / ".env")

from alpha_forge.app.agents.llm_client import LLMClient

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
SILICONFLOW_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
DGX_SPARK_KEY = os.environ.get("DGX_SPARK_API_KEY", "sk-1234")

CANDIDATES = {
    "deepseek-v3.2": {
        "model": "Pro/deepseek-ai/DeepSeek-V3.2",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": SILICONFLOW_KEY,
    },
    "qwen3.5-397b": {
        "model": "Qwen/Qwen3.5-397B-A17B",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": SILICONFLOW_KEY,
    },
    "gemma4-31b-local": {
        "model": "gemma-4-31B-nvfp4",
        "base_url": "http://192.168.3.46:8000/v1",
        "api_key": DGX_SPARK_KEY,
    },
    "gemini-2.5-flash": {
        "model": "google/gemini-2.5-flash",
        "base_url": OPENROUTER_URL,
        "api_key": OPENROUTER_KEY,
    },
    "gemini-2.5-pro": {
        "model": "google/gemini-2.5-pro",
        "base_url": OPENROUTER_URL,
        "api_key": OPENROUTER_KEY,
    },
    "claude-sonnet-4.6": {
        "model": "anthropic/claude-sonnet-4.6",
        "base_url": OPENROUTER_URL,
        "api_key": OPENROUTER_KEY,
    },
    "kimi-k2.5": {
        "model": "moonshotai/kimi-k2.5",
        "base_url": OPENROUTER_URL,
        "api_key": OPENROUTER_KEY,
    },
    "minimax-m2.7": {
        "model": "minimax/minimax-m2.7",
        "base_url": OPENROUTER_URL,
        "api_key": OPENROUTER_KEY,
    },
}

# ── Prompts (same as actual pipeline) ────────────────────────────────────

PLAN_SYSTEM = """You are a crypto alpha researcher. Draft a concrete implementation plan
for the research hypothesis. The plan should specify:
1. What features to compute from OHLCV bars
2. What signal logic to use
3. What configuration parameters to set
4. Expected behavior and falsification criteria

Keep the plan specific and implementable.

## Critical requirements
- All rolling/expanding statistics must use ONLY past data (no future leakage)
- Explicitly state the lookback window for every rolling computation
- Signals: values between -1.0 and 1.0 (fractional sizing)
- No forward-looking operations — .shift(-N) is FORBIDDEN
- MODEL_CONFIG must include "timeframe" and "stop_loss_pct"
"""

PLAN_USER = """## Family
- Hypothesis: Long when buy_volume z-score > 1.5 on 1h bars produces positive 4h forward returns
- Mechanism: Volume-momentum: abnormal buy pressure signals informed flow
- Iteration: 1
- Best score: 0.000

## Seed
- Claim: BTC exhibits short-term momentum after high volume bars
- Horizon: 1h
- Market: crypto

## Constraints
- You may only modify: features.py, labels.py, model_config.py, signal_combiner.py
- Available bar columns: open, high, low, close, volume, buy_volume, vwap, trade_count
- Signals between -1.0 and 1.0

Draft the implementation plan."""

CODE_SYSTEM = """You are a crypto alpha researcher implementing a trading strategy.
Generate Python code for the 4 research files. You MUST respond with valid JSON
where keys are filenames and values are the complete Python file contents.

Response format:
{
  "features.py": "...",
  "model_config.py": "...",
  "signal_combiner.py": "...",
  "labels.py": "..."
}

Rules:
- features.py must export: compute_features(bars: pd.DataFrame) -> pd.DataFrame
- model_config.py must export: MODEL_CONFIG: dict
- signal_combiner.py must export: combine_signals(features: pd.DataFrame, config: dict) -> pd.Series
- labels.py must export: compute_labels(bars: pd.DataFrame) -> pd.Series
- Signals between -1.0 and 1.0, fractional sizing
- NO .shift(-N) — use numpy array slicing for forward returns
- Use only: pandas, numpy
- MODEL_CONFIG must include "timeframe" (str) and "stop_loss_pct" (float)
"""

JUDGE_SYSTEM = """You are a leakage judge for a crypto alpha research pipeline.
Evaluate the code for time leakage, label leakage, and split contamination.

IMPORTANT RULES FOR EVALUATING LEAKAGE:
- If features are computed and then uniformly shifted by 1 bar via .shift(1), all feature values
  at bar T reflect data up to bar T-1. This is CORRECT and NOT leakage.
- Rolling windows applied to already-shifted features are backward-looking by construction.
  At bar T, a rolling(60) on shifted features uses values [T-59..T], which map to raw data
  [T-60..T-1]. This is NOT lookahead.
- The only true time leakage is when .shift(-N) with N>0 is used, or when raw (unshifted)
  current-bar data flows into signals.

You MUST respond with valid JSON matching this schema:
{
  "verdict": "approve" or "revise",
  "judge_type": "leakage",
  "reasoning_summary": "...",
  "must_fix": ["list of issues"],
  "time_leakage_risk": "low" or "medium" or "high",
  "label_leakage_risk": "low" or "medium" or "high"
}
"""

JUDGE_CODE_SAMPLE = """## Code
```python
# === features.py ===
import pandas as pd
import numpy as np

def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    features = pd.DataFrame(index=bars.index)
    buy_volume = bars['buy_volume']
    rolling_mean = buy_volume.rolling(window=24, min_periods=24).mean()
    rolling_std = buy_volume.rolling(window=24, min_periods=24).std().clip(lower=1e-8)
    features['buy_volume_zscore'] = (buy_volume - rolling_mean) / rolling_std
    returns = bars['close'].pct_change()
    features['realized_vol'] = returns.rolling(window=24, min_periods=24).std() * np.sqrt(252)
    # Shift all features by 1 bar to avoid lookahead
    features = features.shift(1)
    return features

# === signal_combiner.py ===
import pandas as pd
import numpy as np

def combine_signals(features: pd.DataFrame, config: dict) -> pd.Series:
    zscore = features['buy_volume_zscore']
    realized_vol = features['realized_vol']
    conviction = ((zscore - config['z_score_threshold']) / 2.0).clip(lower=0, upper=1.0)
    vol_scale = (config['target_volatility'] / realized_vol.clip(lower=0.05)).clip(upper=1.0)
    signal = conviction * vol_scale * config['max_position']
    signal = signal.clip(lower=0, upper=config['max_position'])
    signal.iloc[:config['warmup_period']] = np.nan
    return signal

# === labels.py ===
import numpy as np
import pandas as pd

def compute_labels(bars: pd.DataFrame) -> pd.Series:
    close = bars['close'].values
    n = len(close)
    k = 4
    ret = np.full(n, np.nan)
    ret[:n-k] = (close[k:] - close[:n-k]) / close[:n-k]
    return pd.Series(ret, index=bars.index)
```

## Plan
Long when buy_volume z-score > 1.5 with vol targeting and fractional sizing.
Features shifted by 1 bar. Labels use numpy slicing for 4h forward returns.

Evaluate this code for leakage."""


def make_client(name: str) -> LLMClient:
    cfg = CANDIDATES[name]
    return LLMClient(
        model=cfg["model"],
        provider=name,  # anything non-"anthropic" uses OpenAI compat
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
    )


def eval_plan(client: LLMClient) -> dict:
    """Test plan generation quality."""
    t0 = time.time()
    try:
        plan = client.call(PLAN_SYSTEM, PLAN_USER, max_tokens=4096)
        elapsed = time.time() - t0
        checks = {
            "has_content": len(plan) > 200,
            "mentions_features": "features" in plan.lower(),
            "mentions_signal": "signal" in plan.lower(),
            "mentions_lookback_or_rolling": "lookback" in plan.lower() or "rolling" in plan.lower(),
            "mentions_timeframe": "timeframe" in plan.lower() or "1h" in plan,
            "mentions_stop_loss": "stop" in plan.lower(),
            "no_shift_neg": ".shift(-" not in plan,
            "mentions_fractional": any(k in plan.lower() for k in ["fractional", "conviction", "0.5", "sizing"]),
        }
        return {"status": "ok", "time": elapsed, "length": len(plan), "checks": checks,
                "pass_rate": sum(checks.values()) / len(checks), "excerpt": plan[:500]}
    except Exception as e:
        return {"status": "error", "time": time.time() - t0, "error": str(e), "pass_rate": 0}


def eval_code(client: LLMClient, plan: str = "Implement buy_volume z-score momentum strategy with vol targeting on 1h bars.") -> dict:
    """Test code generation quality."""
    user_prompt = f"""## Plan
{plan}

## Constraints
Generate the 4 research files as JSON. Features shifted by 1 bar, labels use numpy slicing.
"""
    t0 = time.time()
    try:
        result = client.call_json(CODE_SYSTEM, user_prompt, max_tokens=8192)
        elapsed = time.time() - t0
        expected = {"features.py", "model_config.py", "signal_combiner.py", "labels.py"}
        has_all = expected <= set(result.keys())

        checks = {
            "has_all_4_files": has_all,
            "features_has_func": "compute_features" in result.get("features.py", ""),
            "signal_has_func": "combine_signals" in result.get("signal_combiner.py", ""),
            "labels_has_func": "compute_labels" in result.get("labels.py", ""),
            "config_has_dict": "MODEL_CONFIG" in result.get("model_config.py", ""),
            "config_has_timeframe": "timeframe" in result.get("model_config.py", ""),
            "config_has_stop_loss": "stop_loss" in result.get("model_config.py", ""),
            "no_shift_neg": all(".shift(-" not in c for c in result.values()),
            "imports_pd_or_np": any("import pandas" in c or "import numpy" in c for c in result.values()),
        }

        # Syntax check
        syntax_ok = True
        for fname in expected:
            if fname in result:
                try:
                    compile(result[fname], fname, "exec")
                except SyntaxError:
                    syntax_ok = False
                    break
        checks["all_compile"] = syntax_ok

        # Execution check on synthetic data
        exec_ok = False
        non_nan = None
        if has_all and syntax_ok:
            try:
                import pandas as pd
                import numpy as np
                import tempfile
                from pathlib import Path
                import shutil

                tmpdir = Path(tempfile.mkdtemp())
                for f, c in result.items():
                    (tmpdir / f).write_text(c)

                sys.path.insert(0, str(tmpdir))
                for mod in ["features", "labels", "model_config", "signal_combiner"]:
                    if mod in sys.modules:
                        del sys.modules[mod]

                np.random.seed(42)
                n = 300
                close = 30000 + np.cumsum(np.random.randn(n) * 100)
                bars = pd.DataFrame({
                    "open": close + np.random.randn(n) * 30,
                    "high": close + abs(np.random.randn(n) * 50),
                    "low": close - abs(np.random.randn(n) * 50),
                    "close": close,
                    "volume": abs(np.random.randn(n) * 1000 + 5000),
                    "buy_volume": abs(np.random.randn(n) * 500 + 2500),
                    "vwap": close + np.random.randn(n) * 10,
                    "trade_count": abs(np.random.randn(n) * 100 + 500).astype(int),
                })

                from features import compute_features
                from labels import compute_labels
                from model_config import MODEL_CONFIG
                from signal_combiner import combine_signals

                feats = compute_features(bars)
                labels = compute_labels(bars)
                signals = combine_signals(feats, MODEL_CONFIG)

                assert feats.shape[0] == n, f"features rows {feats.shape[0]} != {n}"
                assert labels.shape[0] == n, f"labels rows {labels.shape[0]} != {n}"
                assert signals.shape[0] == n, f"signals rows {signals.shape[0]} != {n}"
                non_nan = signals.dropna()
                if len(non_nan) > 0:
                    assert non_nan.min() >= -1.0 and non_nan.max() <= 1.0, "signals out of range"
                exec_ok = True

                sys.path.remove(str(tmpdir))
                shutil.rmtree(tmpdir)
            except Exception as e:
                exec_ok = False
                checks["exec_error"] = str(e)[:200]

        checks["executes_correctly"] = exec_ok

        # Check fractional signals
        if exec_ok and non_nan is not None and len(non_nan) > 0:
            unique_abs = set(non_nan.abs().round(4).unique())
            checks["fractional_signals"] = not (unique_abs <= {0.0, 1.0})
        else:
            checks["fractional_signals"] = False

        return {"status": "ok", "time": elapsed, "checks": checks,
                "pass_rate": sum(v for k, v in checks.items() if k != "exec_error") /
                             sum(1 for k in checks if k != "exec_error")}
    except Exception as e:
        return {"status": "error", "time": time.time() - t0, "error": str(e)[:300], "pass_rate": 0}


def eval_judge(client: LLMClient) -> dict:
    """Test leakage judge quality — the code is CLEAN, judge should approve."""
    t0 = time.time()
    try:
        result = client.call_json(JUDGE_SYSTEM, JUDGE_CODE_SAMPLE, max_tokens=4096)
        elapsed = time.time() - t0

        verdict = result.get("verdict", "").lower()
        time_risk = result.get("time_leakage_risk", "").lower()
        label_risk = result.get("label_leakage_risk", "").lower()
        must_fix = result.get("must_fix", [])
        reasoning = result.get("reasoning_summary", "")

        checks = {
            "valid_verdict": verdict in ("approve", "revise", "approve_with_constraints"),
            "has_reasoning": len(reasoning) > 20,
            "has_judge_type": result.get("judge_type") == "leakage",
            "valid_time_risk": time_risk in ("low", "medium", "high"),
            "valid_label_risk": label_risk in ("low", "medium", "high"),
            # The code IS clean — correct answer is approve with low time leakage
            "correct_verdict_approve": verdict in ("approve", "approve_with_constraints"),
            "correct_time_risk_low": time_risk == "low",
            "correct_label_risk_low": label_risk == "low",
            "no_false_must_fix": len(must_fix) == 0,
        }

        return {"status": "ok", "time": elapsed, "checks": checks,
                "verdict": verdict, "time_risk": time_risk, "must_fix": must_fix,
                "reasoning": reasoning[:400],
                "pass_rate": sum(checks.values()) / len(checks)}
    except Exception as e:
        return {"status": "error", "time": time.time() - t0, "error": str(e)[:300], "pass_rate": 0}


def run_eval(name: str) -> dict:
    """Run full evaluation for one model."""
    print(f"\n{'=' * 70}")
    print(f"  EVALUATING: {name} ({CANDIDATES[name]['model']})")
    print(f"{'=' * 70}")

    client = make_client(name)

    # 1. Plan
    print("  [1/3] Plan generation...", end="", flush=True)
    plan_result = eval_plan(client)
    print(f" {plan_result['status']} ({plan_result.get('time', 0):.0f}s, pass={plan_result['pass_rate']:.0%})")

    # 2. Code
    print("  [2/3] Code generation...", end="", flush=True)
    plan_text = plan_result.get("excerpt", "Implement buy_volume z-score momentum strategy.")
    code_result = eval_code(client, plan_text)
    print(f" {code_result['status']} ({code_result.get('time', 0):.0f}s, pass={code_result['pass_rate']:.0%})")

    # 3. Judge
    print("  [3/3] Leakage judge...", end="", flush=True)
    judge_result = eval_judge(client)
    print(f" {judge_result['status']} ({judge_result.get('time', 0):.0f}s, pass={judge_result['pass_rate']:.0%})")
    if judge_result.get("verdict"):
        print(f"         verdict={judge_result['verdict']}, time_risk={judge_result.get('time_risk')}, must_fix={len(judge_result.get('must_fix', []))}")

    return {
        "name": name,
        "model": CANDIDATES[name]["model"],
        "plan": plan_result,
        "code": code_result,
        "judge": judge_result,
        "avg_pass_rate": (plan_result["pass_rate"] + code_result["pass_rate"] + judge_result["pass_rate"]) / 3,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        names = sys.argv[1:]
    else:
        names = list(CANDIDATES.keys())

    results = {}
    for name in names:
        if name not in CANDIDATES:
            print(f"Unknown model: {name}")
            continue
        results[name] = run_eval(name)

    # Summary
    print(f"\n\n{'=' * 70}")
    print("  SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Model':<22} {'Plan':>8} {'Code':>8} {'Judge':>8} {'Avg':>8}")
    print("-" * 58)
    for name, r in sorted(results.items(), key=lambda x: -x[1]["avg_pass_rate"]):
        print(f"{name:<22} {r['plan']['pass_rate']:>7.0%} {r['code']['pass_rate']:>7.0%} {r['judge']['pass_rate']:>7.0%} {r['avg_pass_rate']:>7.0%}")

    # Detail: judge verdicts
    print(f"\n{'Model':<22} {'Verdict':<15} {'TimeRisk':<10} {'MustFix':>8}")
    print("-" * 58)
    for name, r in results.items():
        j = r["judge"]
        v = j.get("verdict", j.get("error", "err")[:12])
        tr = j.get("time_risk", "?")
        mf = len(j.get("must_fix", []))
        print(f"{name:<22} {v:<15} {tr:<10} {mf:>8}")

    # Save full results
    out_path = PROJECT_ROOT / "scripts" / "model_eval_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results saved to {out_path}")
