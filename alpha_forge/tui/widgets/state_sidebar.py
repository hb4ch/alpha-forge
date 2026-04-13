"""Left sidebar: pipeline steps, family info, family selector."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Rule, Static

from alpha_forge.app.domain.models import IdeaFamily
from alpha_forge.app.domain.states import IterationStage

PIPELINE_STEPS = [
    IterationStage.DRAFT_PLAN,
    IterationStage.PLAN_JUDGED,
    IterationStage.CODE_WRITE,
    IterationStage.CODE_JUDGED,
    IterationStage.RUN_GUARDS,
    IterationStage.RUN_BACKTEST,
    IterationStage.RUN_ROBUSTNESS,
    IterationStage.RESULT_JUDGED,
]


class PipelineView(Static):
    """Shows iteration stages as a vertical checklist."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_stage: IterationStage | None = None

    def update_stage(self, stage: IterationStage) -> None:
        self._current_stage = stage
        self._render_pipeline()

    def _render_pipeline(self) -> None:
        lines = []
        # Determine the index of the current stage in the pipeline (if present)
        try:
            current_idx = PIPELINE_STEPS.index(self._current_stage) if self._current_stage else -1
        except ValueError:
            current_idx = -1  # Stage not in pipeline (e.g. BACKTEST_FAILED, ITERATION_SUCCESS)

        for i, step in enumerate(PIPELINE_STEPS):
            if self._current_stage and step == self._current_stage:
                lines.append(f"[bold yellow]  \u25b8 {step.value}[/]")
            elif current_idx >= 0 and i < current_idx:
                lines.append(f"[green]  \u2713[/] [dim]{step.value}[/]")
            else:
                lines.append(f"[dim]  \u25cb {step.value}[/]")

        # Show non-pipeline stages (errors, terminal) below the pipeline
        if self._current_stage and current_idx < 0:
            label = self._current_stage.value
            if "FAILED" in label:
                lines.append(f"\n[bold red]  \u2717 {label}[/]")
            elif "SUCCESS" in label:
                lines.append(f"\n[bold green]  \u2713 {label}[/]")
            else:
                lines.append(f"\n[bold yellow]  \u25b8 {label}[/]")

        self.update("\n".join(lines))


class FamilyInfo(Static):
    """Shows current family metadata."""

    DEFAULT_CSS = "FamilyInfo { height: auto; }"

    def on_mount(self) -> None:
        self.update("[dim italic]No family loaded[/]")

    def update_family(self, family: IdeaFamily) -> None:
        budget = family.mutation_budget
        iteration_progress = f"{family.current_iteration}/{family.max_iterations}"
        self.update(
            f"[bold]{family.family_id}[/]\n"
            f"Iteration: {iteration_progress}\n"
            f"Seed: {family.seed_id}\n"
            f"State: {family.state}\n"
            f"\n"
            f"Best score: {family.best_score:.3f}\n"
            f"Best qualified: {family.best_qualified_score:.3f}\n"
            f"Budget: H:{budget.horizon} V:{budget.venue}\n"
        )


class StateSidebar(Vertical):
    """Left sidebar combining family info, pipeline, and family list."""

    def compose(self) -> ComposeResult:
        yield Static("[bold $accent]ALPHA FORGE[/]", id="sidebar-banner")
        with Vertical(id="family-section") as v:
            v.border_title = "Family"
            yield FamilyInfo(id="family-info")
        with Vertical(id="pipeline-section") as v:
            v.border_title = "Pipeline"
            yield PipelineView(id="pipeline-view")
        with Vertical(id="families-section") as v:
            v.border_title = "Families"
            yield ListView(id="family-list")

    def update_family(self, family: IdeaFamily) -> None:
        self.query_one("#family-info", FamilyInfo).update_family(family)

    def update_stage(self, stage: IterationStage) -> None:
        self.query_one("#pipeline-view", PipelineView).update_stage(stage)

    def set_families(self, families: list[str], active: str) -> None:
        lv = self.query_one("#family-list", ListView)
        lv.clear()
        for fid in families:
            prefix = "\u25b6 " if fid == active else "  "
            lv.append(ListItem(Label(f"{prefix}{fid}")))
