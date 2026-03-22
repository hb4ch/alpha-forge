# Alpha Forge TUI — Design Spec

## Context

Alpha Forge runs as CLI scripts (`run_loop.py`, `run_iteration.py`, etc.) with no visibility into LLM conversations, judge reasoning, or backtest results as they happen. The only way to observe the loop is by reading file artifacts after the fact. This makes it impossible to intervene at the right moment or understand why a family is struggling.

This TUI replaces the CLI scripts as the primary way to run Alpha Forge, providing a live dashboard with real-time streaming of all LLM interactions, judge verdicts, metrics, and code — with the ability to switch from autopilot to manual control at any point.

## Design Decisions

### Framework: Textual
- Modern async-native Python TUI framework built on Rich
- CSS-like styling via `.tcss` files
- Rich widget library (DataTable, RichLog, TabbedContent, Tree, Static)
- `run_worker()` for background threads that don't block the UI

### Integration: Direct import (replace CLI scripts)
- The TUI directly imports and drives `Orchestrator`, `FamilyFlow`, `ResearcherAgent`, judges, and storage
- No subprocess wrapping or stdout parsing
- A new `EventBus` provides real-time communication between orchestrator and TUI

### Process isolation: Subprocess workers for heavy compute
- Backtest and robustness runs execute in separate child processes
- Protects the TUI/orchestrator from DuckDB OOM or crashes
- Configurable memory limits, timeouts, and DuckDB thread counts

## Architecture

```
TUI Process (main)
├── Textual App (async event loop)
│   ├── Widgets: sidebar, conversation, tabbed panels, status bar
│   └── EventBus subscriber → updates widgets on events
├── Orchestrator Worker Thread (via Textual run_worker)
│   ├── EventBus publisher → emits stage transitions, verdicts, etc.
│   ├── LLM calls (in-process, HTTP to Anthropic API)
│   │   └── Streaming callback → tokens flow to conversation widget
│   ├── Judge calls (in-process, HTTP)
│   ├── Guard checks (in-process, fast)
│   └── Backtest/Robustness → subprocess via multiprocessing
│       └── Child process with resource limits (memory, timeout, threads)
└── EventBus (asyncio.Queue-based pub/sub, bridges thread↔async)
```

### EventBus

Simple pub/sub with thread-safe bridging. Core API:

```python
class EventBus:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._gate: threading.Event | None = None  # For semi-auto blocking

    async def emit(event: str, data: dict) -> None:
        """Emit from async context (TUI side)."""

    def emit_sync(event: str, data: dict) -> None:
        """Emit from worker thread. Uses loop.call_soon_threadsafe() to
        schedule callbacks on the Textual event loop. This is the critical
        thread→async bridge — asyncio.Queue alone is NOT thread-safe."""

    def subscribe(event: str, callback: Callable) -> None

    def gate_for_override(self) -> dict:
        """Called by worker thread at verdict points. Sets self._gate = threading.Event(),
        emits 'verdict_awaiting_override', then blocks on gate.wait().
        Returns the override decision set by the TUI via release_gate()."""

    def release_gate(self, decision: dict) -> None:
        """Called by TUI (async side) when user makes override choice.
        Sets the decision and calls gate.set() to unblock the worker thread."""
```

Events emitted by orchestrator/family_flow:
- `stage_changed` — iteration stage transitions. Covers all `IterationStage` values: DRAFT_PLAN, PLAN_JUDGED, CODE_WRITE, CODE_JUDGED, RUN_GUARDS, RUN_BACKTEST, RUN_ROBUSTNESS, RESULT_JUDGED, ITERATION_SUCCESS, ITERATION_FAILED
- `family_state_changed` — family state transitions. Covers all `FamilyState` values including intermediate states: NEW, QUEUED, PLAN_IN_REVIEW, PLAN_APPROVED, CODING, CODE_IN_REVIEW, CODE_APPROVED, GUARDS_RUNNING, BACKTEST_RUNNING, RESULTS_IN_REVIEW, ITERATE, PROMOTE_TO_HOLDOUT, HUMAN_REVIEW, etc.
- `llm_token` — streaming token from researcher or judge
- `llm_complete` — full LLM response completed
- `verdict_received` — judge verdict with full JudgeOutput
- `verdict_awaiting_override` — semi-auto mode: worker is blocked, waiting for user decision
- `guards_complete` — guard results (pass/fail per guard). Guards: edit_surface, time_integrity, split_isolation, config_immutability, reproducibility
- `backtest_complete` — backtest metrics per symbol
- `robustness_complete` — robustness test results (cost_perturbation, slippage_perturbation, sub_period_stability, leave_one_out, shuffle_placebo)
- `score_computed` — composite score calculated
- `strike_added` — strike event (only for overfitting loops, not individual revisions)
- `iteration_complete` — iteration finished (ITERATION_SUCCESS or ITERATION_FAILED stage)
- `infrastructure_failure` — subprocess OOM/timeout (no strike)

### Subprocess Runner

`alpha_forge/tui/workers/subprocess_runner.py`

Wraps backtest and robustness calls in child processes:
- **Memory limit**: DuckDB's own `PRAGMA memory_limit='4GB'` (preferred over `RLIMIT_AS` which interferes with mmap). Set via environment variable `DUCKDB_MEMORY_LIMIT` read by the child process before any DuckDB call. Fallback: `resource.setrlimit(RLIMIT_DATA)` as a hard ceiling.
- Timeout via `process.join(timeout=N)`, SIGKILL if exceeded
- DuckDB thread control via `PRAGMA threads=N` in child process
- Results serialized via `multiprocessing.Queue`
- Failure classified as `infrastructure` (OOM/timeout/signal) or `research` (bad results)
- Infrastructure failures: no strike, TUI shows warning, option to retry with adjusted settings via `/retry` or `/threads N`
- Research failures: flow through normal verdict/strike path

Configuration in `guardrails.yaml`:
```yaml
resource_limits:
  backtest_max_memory_mb: 4096
  backtest_timeout_seconds: 300
  duckdb_threads: 2
  robustness_max_memory_mb: 4096
  robustness_timeout_seconds: 600
```

### LLM Streaming

`LLMClient.call()` gains an optional `stream_callback: Callable[[str], None]` parameter:
- When provided: uses Anthropic streaming API, calls `stream_callback(token)` for each token
- When None: existing batch behavior, no change
- `call_json()` also supports streaming (streams tokens, then parses complete response)

## Layout: IDE-style

```
╭─ State ──────────────╮╭─ LLM Conversation ─────────────────────────────────╮
│                      ││                                                     │
│  ▶ momentum-rsi-01   ││  ── Iteration 3 ─────────────────────────────────   │
│  Iteration: 3/10     ││                                                     │
│  Seed: rsi-momentum  ││  🔬 Researcher drafting plan...                     │
│                      ││  I'll refine the RSI divergence signal by adding    │
│  Pipeline:           ││  volume confirmation...█                            │
│  ✓ DRAFT_PLAN        ││                                                     │
│  ✓ PLAN_JUDGED       ││  ⚖ Leakage Judge APPROVE                           │
│  ▸ CODE_WRITE        ││  No forward-looking operations detected.            │
│  ○ CODE_JUDGED       ││                                                     │
│  ○ RUN_GUARDS        ││  ⚖ Overfit Judge REVISE                             │
│  ○ RUN_BACKTEST      ││  Too many free parameters (7).                      │
│  ○ RUN_ROBUSTNESS    │├─────────────────────────────────────────────────────┤
│  ○ RESULT_JUDGED     ││ [Metrics] [Code] [Verdicts] [Guards] [Log]          │
│                      ││                                                     │
│  Strikes: ●○○        ││  Symbol    Sharpe  Sortino  MaxDD   Return  Trades  │
│  Best Score: 0.72    ││  ETHUSDT    1.42    2.10   -12%    +34%     212    │
│  Budget: H:1 V:2     ││  BTCUSDT    0.98    1.45   -18%    +21%     189    │
│                      ││  SOLUSDT    1.67    2.89    -8%    +45%     267    │
│  ────────────        ││  BNBUSDT    0.31    0.42   -28%     +5%     179    │
│  Families:           ││                                                     │
│  ▶ momentum-rsi      ││  Composite: 0.68  Robustness: 4/5 pass             │
│    mean-revert-01    ││                                                     │
│    vol-regime-02     ││                                                     │
╰──────────────────────╯╰─────────────────────────────────────────────────────╯
 AUTOPILOT  Shift+Tab: Semi-auto │ Tab: Focus panel │ /: Command │ q: Quit
```

### Left Sidebar — State Widget

- **Family info**: name, iteration count, seed, best score, mutation budget remaining
- **Pipeline steps**: iteration stages as a vertical list with ✓/▸/○ status icons
- **Strikes**: visual dots (●○○), color-coded
- **Family selector**: list of all families, arrow keys to switch, Enter to select

### Main Panel — LLM Conversation Stream

- Auto-scrolling RichLog widget
- Researcher messages prefixed with 🔬, judge messages with ⚖
- Streaming tokens appear character-by-character
- Judge verdicts color-coded: APPROVE=green, APPROVE_WITH_CONSTRAINTS=yellow, REVISE=orange, REJECT=red
- Judge reasoning and must_fix items shown inline
- Iteration boundaries marked with separator lines

### Bottom Tabbed Panel

**Tab 1 — Metrics**: DataTable with per-symbol backtest results. Color-coded against guardrails.yaml thresholds. Composite score and robustness summary.

**Tab 2 — Code**: Syntax-highlighted view of current research files (features.py, signal_combiner.py, model_config.py, labels.py). Toggle between full file and diff-from-previous-iteration. Diffs are generated against the previous iteration's code files, which are already preserved in the ledger entries and can be reconstructed from git history of the research/ directory (each `apply_code()` call overwrites the files, but git tracks the changes). For the TUI, we snapshot research files to `ArtifactStore` at each iteration start: `save_code_snapshot(family_id, iteration, files_dict)`.

**Tab 3 — Verdicts**: All judge outputs for current iteration. Each judge as a collapsible section: verdict badge, risk levels (low=green, medium=yellow, high=red), must_fix items, reasoning summary.

**Tab 4 — Guards**: 5 guard checks with pass/fail badges: Edit Surface (red strike on violation), Time Integrity (forward-looking detection), Split Isolation (holdout access control), Config Immutability (SHA-256 hash check), Reproducibility (random seed validation). Violation details inline. Red strike violations highlighted prominently.

**Tab 5 — Log**: Raw Python logging output captured via custom logging handler. Filterable by module name. Auto-scroll with pause toggle.

### Status Bar

Bottom line showing:
- Current mode badge: `AUTOPILOT` (blue) or `SEMI-AUTO` (orange)
- Key keybinding hints
- Elapsed time for current iteration

## Interaction Modes

### Autopilot (default)

The loop runs autonomously. All verdicts auto-advance. You watch the stream and can:
- Switch tabs to inspect metrics/code/verdicts
- Switch families
- Pause the loop (`p`)
- Toggle to semi-auto at any time (`Shift+Tab`)

### Pause (`p` key)

Pause operates at **iteration boundaries only** — it finishes the current iteration, then stops before starting the next one. It does NOT interrupt a running subprocess or LLM call mid-flight. In semi-auto mode, pause is implicit (the loop is already stopped at each verdict gate).

### Semi-Auto Override Mechanism

**How `run_iteration()` pauses without refactoring into coroutines:**

`FamilyFlow.run_iteration()` remains a synchronous method running in the worker thread. At each verdict point (after tier-1, tier-2, tier-3 judges), it calls `bus.gate_for_override()` if the bus is in semi-auto mode:

```python
# In family_flow.py, after tier-1 verdict aggregation:
tier1_verdict = aggregate_verdict(tier1_outputs)
if self.bus and self.bus.semi_auto:
    override = self.bus.gate_for_override()  # blocks worker thread
    if override["action"] == "override":
        tier1_verdict = Verdict(override["verdict"])
    if override.get("feedback"):
        prior_feedback.append(override["feedback"])
# ...proceed with (possibly overridden) verdict
```

`gate_for_override()` sets a `threading.Event`, emits `verdict_awaiting_override` to the TUI (via `call_soon_threadsafe`), then calls `event.wait()` — blocking the worker thread. The TUI shows the override prompt. When the user picks an action, the TUI calls `bus.release_gate(decision)` which sets the decision dict and calls `event.set()`, unblocking the worker.

This approach requires no coroutine refactoring — `run_iteration()` stays synchronous, the blocking happens naturally in the worker thread, and the TUI event loop stays responsive.

### Semi-Auto (Shift+Tab to toggle)

The loop pauses at every verdict. An override prompt appears in the conversation panel:

```
⚖ Overfit Judge verdict: REVISE
Too many free parameters (7). must_fix: ["reduce lookback params"]

 SEMI-AUTO  Override this verdict?

  [a] Accept verdict (REVISE) — researcher revises
  [o] Override → APPROVE — skip this concern, proceed
  [r] Override → REJECT — force rejection
  [v] Override → REVISE with custom feedback
  [s] Skip to autopilot — resume auto for rest of this iteration
```

**Option [v] — Custom revision feedback:**

Opens a multi-line text input area. Your guidance gets injected into `prior_feedback` for the researcher agent, alongside the judge's must_fix items. The researcher sees exactly what you want changed.

```
Enter revision guidance (Ctrl+Enter to submit):
┌──────────────────────────────────────────────────┐
│ Keep the RSI lookback at 14 — mechanistically    │
│ motivated. Remove the volume threshold param,    │
│ hardcode at 1.5x average volume.                 │
└──────────────────────────────────────────────────┘
```

## Revised Strike Policy

**Principle: strikes only prevent overfitting death loops, not normal iteration.**

### Current behavior (what actually changes)

In the current code, `add_strike()` is called in one place: tier-1 plan rejection (`family_flow.py:131`). Tier-2 code rejection and tier-3 result rejection transition state but do NOT add strikes. The changes below primarily affect:
1. Replacing the tier-1 plan rejection strike with pattern-based detection
2. Adding new pattern-based strike triggers
3. Changing `CANCELLED_3_STRIKES` to route to `HUMAN_REVIEW` instead of being terminal

### New strike rules

| Event | Strike? | Type | Rationale |
|-------|---------|------|-----------|
| Judge says REVISE (plan or code) | No | — | Normal iteration |
| Judge says REJECT | No | — | Single bad attempt |
| Same overfit flag raised 3+ times consecutively | Yes | Yellow | Researcher looping on same pattern |
| Composite score decreasing 3 iterations in a row | Yes | Yellow | Death spiral |
| Edit surface violation (guard) | Yes | Red | Hard integrity constraint |
| Config immutability violation (guard) | Yes | Red | Hard integrity constraint |
| 3 yellow strikes | Pause for human review | — | You decide: continue, archive, or fork |

### Data model changes for pattern detection

New fields on `IdeaFamily` (Pydantic model in `models.py`):

```python
# Track overfit flag history for consecutive detection
overfit_flag_history: list[str] = []  # last N overfit taxonomy_tags from judges
# Track score trend for death spiral detection
score_history: list[float] = []  # last N composite scores
```

New functions in `strikes.py` (this is effectively a rewrite of the 46-line file):

```python
def detect_overfit_loop(family: IdeaFamily, latest_tags: list[str]) -> bool:
    """Check if the same overfit flag appears in 3+ consecutive iterations."""

def detect_death_spiral(family: IdeaFamily, latest_score: float) -> bool:
    """Check if composite score has decreased 3 iterations in a row."""

def should_pause_for_review(family: IdeaFamily) -> bool:
    """Replaces should_cancel(). Returns True if 3 yellow or 2 red strikes.
    Instead of CANCELLED_3_STRIKES, family transitions to HUMAN_REVIEW."""
```

### State transition change

`CANCELLED_3_STRIKES` is **renamed to `PAUSED_FOR_REVIEW`** and is no longer terminal. From `PAUSED_FOR_REVIEW`, the user can:
- Continue (transition back to `QUEUED` with strikes reset)
- Archive (transition to `ARCHIVED_REJECTED`)
- Fork (create a new family from current state)

The `WAITING_STATES` set in orchestrator is updated to include `PAUSED_FOR_REVIEW` instead of `CANCELLED_3_STRIKES`.

### `/strike reset` command

Resets `strike_count` and `red_strike_count` to 0. Also clears `overfit_flag_history` and `score_history` so pattern detection starts fresh. Does NOT clear `strike_history` (audit trail preserved).

### `/seed <text>` command

Runs the full intake pipeline: `ingest_seed() → distill_seed() → screen_seed()`. If the seed judge accepts, `create_family()` is called and the new family appears in the sidebar family list. This runs as a background task in the worker thread — does NOT block the active family's iteration.

### LLM API failure handling

Network errors (429, 500, connection failures) during LLM calls:
- **No strike** — infrastructure issue, not research quality
- **3 retries with exponential backoff** (1s, 4s, 16s) — added to `LLMClient`
- **After 3 retries**: emit `infrastructure_failure` event, TUI shows error with `/retry` option
- **Mid-stream failure**: partial tokens already displayed; TUI appends "[connection lost — retrying...]"

## Keybindings

| Key | Action |
|-----|--------|
| `Shift+Tab` | Toggle autopilot ↔ semi-auto mode |
| `Tab` | Cycle focus: sidebar → conversation → bottom tabs |
| `1`-`5` | Switch bottom tab (Metrics/Code/Verdicts/Guards/Log) |
| `/` | Open command palette |
| `f` | Family selector popup |
| `p` | Pause/resume loop |
| `q` | Quit |
| `Enter` | Semi-auto: accept current verdict |
| `a/o/r/v/s` | Semi-auto override actions |

### Command Palette (`/`)

| Command | Action |
|---------|--------|
| `/seed <text>` | Inject a new seed idea |
| `/strike reset` | Manually reset strikes |
| `/override <verdict>` | Force verdict on current stage |
| `/family <id>` | Switch to family |
| `/config` | View guardrails/universe/costs |
| `/history` | Show iteration history |
| `/export` | Export iteration data as JSON |
| `/retry` | Retry failed subprocess (after infra failure) |
| `/threads <N>` | Adjust DuckDB thread count for next run |

## File Structure

```
alpha_forge/
├── app/
│   ├── events.py                  # NEW — EventBus (asyncio.Queue pub/sub)
│   ├── workflow/
│   │   ├── orchestrator.py        # MODIFIED — emit events, accept EventBus
│   │   └── family_flow.py         # MODIFIED — emit events at stage transitions
│   ├── agents/
│   │   └── llm_client.py          # MODIFIED — add stream_callback parameter
│   └── domain/
│       └── strikes.py             # MODIFIED — pattern-based strike policy
├── tui/                            # NEW — entire package
│   ├── __init__.py
│   ├── app.py                     # Textual App subclass, screen composition
│   ├── screens/
│   │   └── main_screen.py         # Main IDE-style screen layout
│   ├── widgets/
│   │   ├── state_sidebar.py       # Pipeline steps, family info, family selector
│   │   ├── conversation.py        # Streaming LLM conversation log
│   │   ├── metrics_panel.py       # Backtest results table, scores
│   │   ├── code_panel.py          # Syntax-highlighted code + diff view
│   │   ├── verdicts_panel.py      # Judge output cards
│   │   ├── guards_panel.py        # Guard pass/fail status
│   │   ├── log_panel.py           # Filtered Python log viewer
│   │   ├── override_modal.py      # Semi-auto verdict override + text input
│   │   └── command_palette.py     # Slash command input
│   ├── styles/
│   │   └── theme.tcss             # Textual CSS theme
│   └── workers/
│       ├── loop_worker.py         # Background worker driving orchestrator
│       └── subprocess_runner.py   # Process-isolated backtest/robustness
├── scripts/
│   └── run_tui.py                 # NEW — CLI entry point
```

### Changes to Existing Files (Summary)

| File | Change | Scope |
|------|--------|-------|
| `app/events.py` | New file: EventBus with thread-safe bridging and gate mechanism | ~80 lines |
| `app/workflow/orchestrator.py` | Accept optional `bus` param, emit events, update WAITING_STATES | ~25 lines added |
| `app/workflow/family_flow.py` | Emit events at each stage, call `gate_for_override()` at verdict points, snapshot code to artifact store | ~50 lines added |
| `app/agents/llm_client.py` | Add `stream_callback` param, streaming API support, retry with backoff | ~60 lines added |
| `app/domain/strikes.py` | Rewrite: pattern-based detection (overfit loop, death spiral), `should_pause_for_review()` replaces `should_cancel()` | ~90 lines (rewrite of 46-line file) |
| `app/domain/models.py` | Add `overfit_flag_history`, `score_history` fields to `IdeaFamily` | ~4 lines added |
| `app/domain/states.py` | Rename `CANCELLED_3_STRIKES` → `PAUSED_FOR_REVIEW`, add transitions from it | ~5 lines modified |
| `app/storage/artifact_store.py` | Add `save_code_snapshot()` / `load_code_snapshot()` for diff support | ~20 lines added |
| `configs/guardrails.yaml` | Add `resource_limits` section | ~6 lines added |

All changes are additive — existing CLI scripts continue to work. `bus=None` means no events emitted, no gating. The renamed `PAUSED_FOR_REVIEW` state affects both TUI and CLI paths (CLI scripts will also pause instead of cancelling).

## Verification Plan

1. **Unit tests**: EventBus pub/sub, subprocess runner isolation, revised strike logic
2. **Integration test**: Run a single iteration through the TUI, verify all panels update
3. **OOM test**: Simulate subprocess OOM, verify TUI stays alive and shows infrastructure failure
4. **Mode toggle test**: Switch between autopilot and semi-auto mid-iteration, verify override works
5. **Streaming test**: Verify LLM tokens appear character-by-character in conversation panel
6. **Manual smoke test**: Run full loop with 2-3 iterations, exercise all tabs and keybindings
