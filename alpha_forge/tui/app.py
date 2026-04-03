"""Alpha Forge TUI application."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.message import Message
from textual.theme import Theme

from alpha_forge.app.domain.states import IterationStage
from alpha_forge.app.event_bus import EventBus
from alpha_forge.tui.screens.main_screen import MainScreen, StatusBar
from alpha_forge.tui.widgets.code_panel import CodePanel
from alpha_forge.tui.widgets.command_palette import CommandPalette, CommandSubmitted
from alpha_forge.tui.widgets.conversation import ConversationStream
from alpha_forge.tui.widgets.guards_panel import GuardsPanel
from alpha_forge.tui.widgets.log_panel import LogPanel
from alpha_forge.tui.widgets.metrics_panel import MetricsPanel
from alpha_forge.tui.widgets.override_modal import OverrideModal
from alpha_forge.tui.widgets.state_sidebar import StateSidebar
from alpha_forge.tui.widgets.verdicts_panel import VerdictsPanel
from alpha_forge.tui.workers.loop_worker import LoopWorker
from alpha_forge.tui.rendering import normalize_verdict_output

logger = logging.getLogger(__name__)

CSS_PATH = Path(__file__).parent / "styles" / "theme.tcss"


class BusEvent(Message):
    """Thread-safe bridge: posted from worker thread via post_message."""

    def __init__(self, event_name: str, data: dict) -> None:
        super().__init__()
        self.event_name = event_name
        self.data = data


ALPHA_FORGE_THEME = Theme(
    name="alpha-forge",
    primary="#58a6ff",
    secondary="#8b949e",
    warning="#d29922",
    error="#f85149",
    success="#7ee787",
    accent="#58a6ff",
    foreground="#c9d1d9",
    background="#0d1117",
    surface="#161b22",
    panel="#21262d",
    dark=True,
    variables={
        "border-muted": "#30363d",
        "text-muted": "#8b949e",
    },
)


class AlphaForgeApp(App):
    """Main TUI application for Alpha Forge."""

    CSS_PATH = str(CSS_PATH)

    BINDINGS = [
        Binding("shift+tab", "toggle_mode", "Toggle autopilot/semi-auto"),
        Binding("tab", "focus_next", "Focus next panel"),
        Binding("1", "tab_metrics", "Metrics tab", show=False),
        Binding("2", "tab_code", "Code tab", show=False),
        Binding("3", "tab_verdicts", "Verdicts tab", show=False),
        Binding("4", "tab_guards", "Guards tab", show=False),
        Binding("5", "tab_log", "Log tab", show=False),
        Binding("slash", "show_command", "Command palette"),
        Binding("d", "toggle_diff", "Toggle diff", show=False),
        Binding("p", "toggle_pause", "Pause/resume", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        workspace: str = "alpha_research",
        configs_dir: str = "configs",
        family_id: str | None = None,
        max_iterations: int = 10,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.register_theme(ALPHA_FORGE_THEME)
        self.workspace = workspace
        self.configs_dir = configs_dir
        self.family_id = family_id
        self.max_iterations = max_iterations
        self._mode = "autopilot"
        self._worker: LoopWorker | None = None
        self._bus: EventBus | None = None

    def compose(self):
        yield MainScreen()

    def on_mount(self) -> None:
        # Activate custom theme
        self.theme = "alpha-forge"

        # Set up EventBus — use post_message for thread-safe delivery
        loop = asyncio.get_event_loop()
        self._bus = EventBus(loop, app=self)

        # Install log handler
        log_panel = self.query_one("#log-panel", LogPanel)
        log_panel.install_handler()

        # Start the orchestrator loop
        self._start_loop()

    def on_bus_event(self, event: BusEvent) -> None:
        """Handle all events from the worker thread via Textual's message pump."""
        handler = self._event_handlers.get(event.event_name)
        if handler:
            handler(self, event.data)

    @staticmethod
    def _dispatch_stage_changed(app: "AlphaForgeApp", data: dict) -> None:
        stage_str = data.get("stage", "")
        family_id = data.get("family_id", "")
        try:
            stage = IterationStage(stage_str)
            app.query_one("#sidebar", StateSidebar).update_stage(stage)
        except ValueError:
            pass
        detail = data.get("detail", "")
        msg = f"Stage: {stage_str}" + (f" — {detail}" if detail else "")
        app.query_one("#conversation", ConversationStream).add_system_message(msg)
        if family_id:
            app._refresh_sidebar_family(family_id)

    @staticmethod
    def _dispatch_verdict_received(app: "AlphaForgeApp", data: dict) -> None:
        conv = app.query_one("#conversation", ConversationStream)
        for output in data.get("outputs", []):
            display = normalize_verdict_output(output)
            conv.add_verdict(
                display.judge,
                display.verdict,
                display.reasoning,
                display.must_fix,
            )
        verdicts = app.query_one("#verdicts-panel", VerdictsPanel)
        verdicts.update_verdicts(data.get("outputs", []))

    @staticmethod
    def _dispatch_guards_complete(app: "AlphaForgeApp", data: dict) -> None:
        app.query_one("#guards-panel", GuardsPanel).update_guards(data.get("results", []))

    @staticmethod
    def _dispatch_backtest_complete(app: "AlphaForgeApp", data: dict) -> None:
        app.query_one("#metrics-panel", MetricsPanel).update_metrics(data.get("results", []))

    @staticmethod
    def _dispatch_iteration_complete(app: "AlphaForgeApp", data: dict) -> None:
        score = data.get("score", 0)
        qualified = data.get("qualified", False)
        iteration = data.get("iteration", 0)
        family_id = data.get("family_id", "")
        app.query_one("#conversation", ConversationStream).add_system_message(
            f"Iteration complete. Score: {score:.3f} {'✓ Qualified' if qualified else '✗ Not qualified'}"
        )
        if iteration:
            app.query_one("#status-bar", StatusBar).set_iteration(iteration)
        if family_id:
            app._refresh_sidebar_family(family_id)

    @staticmethod
    def _dispatch_override_needed(app: "AlphaForgeApp", data: dict) -> None:
        if app._mode != "semi_auto":
            return
        modal = OverrideModal(
            judge_type=data.get("judge", ""),
            verdict=data.get("verdict", ""),
            reasoning=data.get("reasoning_summary") or data.get("reasoning", ""),
        )
        def handle_decision(decision: dict | None) -> None:
            if decision and app._bus:
                app._bus.release_gate(decision)
        app.push_screen(modal, handle_decision)

    def _refresh_sidebar_family(self, family_id: str) -> None:
        """Load family from store and update the sidebar."""
        try:
            from alpha_forge.app.storage.markdown_store import MarkdownStore
            store = MarkdownStore(self.workspace)
            family = store.read_family(family_id)
            self.query_one("#sidebar", StateSidebar).update_family(family)
        except Exception:
            pass

    @staticmethod
    def _dispatch_loop_started(app: "AlphaForgeApp", data: dict) -> None:
        family_id = data.get("family_id", "auto")
        app.query_one("#conversation", ConversationStream).add_system_message(
            f"Loop started for family: {family_id}"
        )
        status = app.query_one("#status-bar", StatusBar)
        status.set_family(family_id)
        status.set_iteration(0, app.max_iterations)
        app._refresh_sidebar_family(family_id)

    @staticmethod
    def _dispatch_loop_finished(app: "AlphaForgeApp", data: dict) -> None:
        app.query_one("#conversation", ConversationStream).add_system_message(
            f"Loop finished. Final state: {data.get('final_state', 'unknown')}"
        )

    @staticmethod
    def _dispatch_loop_error(app: "AlphaForgeApp", data: dict) -> None:
        app.query_one("#conversation", ConversationStream).add_system_message(
            f"Loop error: {data.get('error', 'unknown')}"
        )

    @staticmethod
    def _dispatch_llm_start(app: "AlphaForgeApp", data: dict) -> None:
        role = data.get("role", "LLM")
        task = data.get("task", "")
        conv = app.query_one("#conversation", ConversationStream)
        conv.begin_streaming(role, task)

    @staticmethod
    def _dispatch_llm_token(app: "AlphaForgeApp", data: dict) -> None:
        conv = app.query_one("#conversation", ConversationStream)
        conv.add_researcher_token(data.get("token", ""))

    @staticmethod
    def _dispatch_llm_end(app: "AlphaForgeApp", data: dict) -> None:
        conv = app.query_one("#conversation", ConversationStream)
        conv.end_streaming()
        conv.add_system_message(f"── {data.get('role', 'LLM')} done ──")

    _event_handlers = {
        "stage_changed": _dispatch_stage_changed,
        "verdict_received": _dispatch_verdict_received,
        "guards_complete": _dispatch_guards_complete,
        "backtest_complete": _dispatch_backtest_complete,
        "iteration_complete": _dispatch_iteration_complete,
        "verdict_awaiting_override": _dispatch_override_needed,
        "loop_started": _dispatch_loop_started,
        "loop_finished": _dispatch_loop_finished,
        "loop_error": _dispatch_loop_error,
        "llm_start": _dispatch_llm_start,
        "llm_token": _dispatch_llm_token,
        "llm_end": _dispatch_llm_end,
    }

    def _start_loop(self) -> None:
        self._worker = LoopWorker(
            workspace=self.workspace,
            configs_dir=self.configs_dir,
            bus=self._bus,
            family_id=self.family_id,
            max_iterations=self.max_iterations,
        )
        self.run_worker(self._worker.run, thread=True, group="loop")

    def on_worker_state_changed(self, event) -> None:
        """Catch worker crashes and display them."""
        from textual.worker import WorkerState
        if event.state == WorkerState.ERROR:
            error = event.worker.error
            msg = f"Worker crashed: {type(error).__name__}: {error}"
            logger.error(msg, exc_info=error)
            try:
                self.query_one("#conversation", ConversationStream).add_system_message(
                    msg,
                    style="bold red",
                )
            except Exception:
                pass

    # --- Actions ---

    def action_toggle_mode(self) -> None:
        if self._mode == "autopilot":
            self._mode = "semi_auto"
        else:
            self._mode = "autopilot"
        if self._bus:
            self._bus.semi_auto = (self._mode == "semi_auto")
        self.query_one("#status-bar", StatusBar).set_mode(self._mode)

    def action_show_command(self) -> None:
        self.query_one("#command-palette", CommandPalette).show()

    def action_toggle_diff(self) -> None:
        self.query_one("#code-panel", CodePanel).toggle_diff()

    def action_toggle_pause(self) -> None:
        if self._worker:
            if self._worker.orchestrator and self._worker.orchestrator._paused:
                self._worker.resume()
                conv = self.query_one("#conversation", ConversationStream)
                conv.add_system_message("Resumed")
            else:
                self._worker.pause()
                conv = self.query_one("#conversation", ConversationStream)
                conv.add_system_message("Pausing at next iteration boundary...")

    def action_tab_metrics(self) -> None:
        self.query_one("#bottom-tabs").active = "tab-metrics"

    def action_tab_code(self) -> None:
        self.query_one("#bottom-tabs").active = "tab-code"

    def action_tab_verdicts(self) -> None:
        self.query_one("#bottom-tabs").active = "tab-verdicts"

    def action_tab_guards(self) -> None:
        self.query_one("#bottom-tabs").active = "tab-guards"

    def action_tab_log(self) -> None:
        self.query_one("#bottom-tabs").active = "tab-log"

    # --- Command handling ---

    def on_command_submitted(self, message: CommandSubmitted) -> None:
        """Handle slash commands from the command palette."""
        name = message.name
        args = message.args
        conv = self.query_one("#conversation", ConversationStream)

        if name == "seed" and args:
            conv.add_system_message(f"Seeding: {' '.join(args)}")
            # TODO: spawn seed intake in background thread
        elif name == "strike" and args and args[0] == "reset":
            self._do_strike_reset(conv)
        elif name == "override" and args:
            if self._bus and self._bus._gate:
                self._bus.release_gate({"action": "override", "verdict": args[0]})
                conv.add_system_message(f"Override: {args[0]}")
        elif name == "family" and args:
            conv.add_system_message(f"Switching to family: {args[0]}")
            # TODO: switch active family
        elif name == "tier" and len(args) >= 2:
            from alpha_forge.app.agents.llm_config import load_config
            config = load_config()
            config.roles[args[0]] = args[1]
            conv.add_system_message(f"Tier updated: {args[0]} → {args[1]}")
        elif name == "config":
            conv.add_system_message("Config display not yet implemented")
        elif name == "history":
            conv.add_system_message("History display not yet implemented")
        elif name == "export":
            conv.add_system_message("Export not yet implemented")
        elif name == "retry":
            self._do_retry(conv)
        elif name == "threads" and args:
            conv.add_system_message(f"DuckDB threads set to {args[0]}")
        else:
            conv.add_system_message(f"Unknown command: {name}")

    def _get_active_family_id(self) -> str | None:
        """Read the active family ID from STATE.md."""
        from alpha_forge.app.storage.markdown_store import MarkdownStore
        store = MarkdownStore(self.workspace)
        state = store.read_global_state()
        return state.get("active_family")

    def _do_strike_reset(self, conv: ConversationStream) -> None:
        """Reset strikes on the active family and re-queue it."""
        from alpha_forge.app.domain.states import FamilyState
        from alpha_forge.app.domain.strikes import reset_strikes
        from alpha_forge.app.storage.markdown_store import MarkdownStore

        family_id = self._get_active_family_id()
        if not family_id:
            conv.add_system_message("No active family found")
            return

        store = MarkdownStore(self.workspace)
        family = store.read_family(family_id)
        old_strikes = family.strike_count
        family = reset_strikes(family)
        # Preserve revision states so orchestrator resumes at the right phase
        revision_states = {
            FamilyState.PLAN_REVISION_REQUIRED,
            FamilyState.CODE_REVISION_REQUIRED,
        }
        new_state = family.state if family.state in revision_states else FamilyState.QUEUED
        family = family.model_copy(update={"state": new_state})
        store.write_family(family)
        conv.add_system_message(
            f"Strikes reset on {family_id}: {old_strikes} → 0, state → {new_state.value}"
        )

    def _do_retry(self, conv: ConversationStream) -> None:
        """Reset strikes, re-queue, and restart the loop."""
        self._do_strike_reset(conv)
        conv.add_system_message("Restarting loop...")
        self._start_loop()
