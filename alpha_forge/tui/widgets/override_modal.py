"""Override modal for semi-auto mode verdict decisions."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea


class OverrideModal(ModalScreen[dict]):
    """Modal for overriding a judge verdict in semi-auto mode."""

    BINDINGS = [
        ("a", "accept", "Accept verdict"),
        ("o", "override_approve", "Override → APPROVE"),
        ("r", "override_reject", "Override → REJECT"),
        ("v", "override_revise", "Override → REVISE"),
        ("s", "skip_autopilot", "Skip to autopilot"),
        ("escape", "dismiss_modal", "Cancel"),
    ]

    def __init__(self, judge_type: str, verdict: str, reasoning: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.judge_type = judge_type
        self.verdict = verdict
        self.reasoning = reasoning

    def compose(self) -> ComposeResult:
        with Vertical(id="override-container"):
            yield Label(f"⚖ {self.judge_type.title()} Judge verdict: {self.verdict.upper()}", id="verdict-label")
            yield Static(self.reasoning[:300] if self.reasoning else "", id="reasoning-text")
            yield Label("")
            yield Label("[a] Accept verdict")
            yield Label("[o] Override → APPROVE")
            yield Label("[r] Override → REJECT")
            yield Label("[v] Override → REVISE with feedback")
            yield Label("[s] Skip to autopilot")
            yield TextArea(id="feedback-input")
            yield Button("Submit Revision Feedback", id="submit-feedback", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#feedback-input", TextArea).display = False
        self.query_one("#submit-feedback", Button).display = False

    def action_accept(self) -> None:
        self.dismiss({"action": "accept", "verdict": self.verdict})

    def action_override_approve(self) -> None:
        self.dismiss({"action": "override", "verdict": "approve"})

    def action_override_reject(self) -> None:
        self.dismiss({"action": "override", "verdict": "reject"})

    def action_override_revise(self) -> None:
        self.query_one("#feedback-input", TextArea).display = True
        self.query_one("#submit-feedback", Button).display = True
        self.query_one("#feedback-input").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-feedback":
            feedback = self.query_one("#feedback-input", TextArea).text
            self.dismiss({"action": "override", "verdict": "revise", "feedback": feedback})

    def action_skip_autopilot(self) -> None:
        self.dismiss({"action": "skip_to_autopilot"})

    def action_dismiss_modal(self) -> None:
        self.dismiss({"action": "accept", "verdict": self.verdict})
