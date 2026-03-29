"""Regression tests for TUI rendering of dynamic text."""
from __future__ import annotations

import asyncio

from rich.console import Group
from textual.app import App, ComposeResult

from alpha_forge.app.domain.models import LeakageJudgeOutput, RealismJudgeOutput
from alpha_forge.app.domain.states import RiskLevel, Verdict
from alpha_forge.tui.app import AlphaForgeApp
from alpha_forge.tui.rendering import normalize_verdict_output
from alpha_forge.tui.widgets.conversation import ConversationStream
from alpha_forge.tui.widgets.verdicts_panel import VerdictCard


class VerdictCardApp(App[None]):
    def compose(self) -> ComposeResult:
        yield VerdictCard(id="card")


class ConversationApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ConversationStream(id="conversation")


def test_verdict_card_renders_model_dump_payload_without_markup_error() -> None:
    payload = LeakageJudgeOutput(
        verdict=Verdict.APPROVE_WITH_CONSTRAINTS,
        must_fix=[
            "Thresholds stay fixed: LONG_THRESHOLD=0.55, SHORT_THRESHOLD=0.45.",
            "Literal Rich-like text must survive: [dim]not markup[/dim] and ['quoted'].",
        ],
        reasoning_summary="Judge summary with [square brackets], equals=signs, and quotes ' \" intact.",
        leakage_risk=RiskLevel.LOW,
    ).model_dump()

    async def run() -> None:
        app = VerdictCardApp()
        async with app.run_test() as pilot:
            card = app.query_one("#card", VerdictCard)
            card.set_verdict(payload)
            await pilot.pause()

            assert isinstance(card.content, Group)
            rendered_lines = [renderable.plain for renderable in card.content.renderables]
            assert "APPROVE_WITH_CONSTRAINTS" in rendered_lines[0]
            assert "risk:low" in rendered_lines[0]
            assert "Judge summary with [square brackets]" in rendered_lines[1]
            assert rendered_lines[2] == "  must_fix:"
            assert rendered_lines[3].startswith("    - Thresholds stay fixed")
            assert "[dim]not markup[/dim]" in rendered_lines[4]

    asyncio.run(run())


def test_conversation_stream_preserves_dynamic_text_literals() -> None:
    async def run() -> None:
        app = ConversationApp()
        async with app.run_test() as pilot:
            conversation = app.query_one("#conversation", ConversationStream)
            conversation.add_researcher_token("[alpha]\n")
            conversation.end_streaming()
            conversation.add_researcher_message("Planner [v2]", "Body with [bold]literal[/bold] text.")
            conversation.add_verdict(
                "realism",
                Verdict.REVISE,
                "Reasoning with [tags], x=1, and ['list'] text.",
                ["First [item]", "Second item with threshold=0.45"],
            )
            await pilot.pause()

            rendered_lines = [line.text for line in conversation.lines if line.text]
            assert "[alpha]" in rendered_lines[0]
            assert "Planner [v2]" in rendered_lines[1]
            assert "Body with [bold]literal[/bold] text." in rendered_lines[2]
            assert "⚖ Realism Judge REVISE" in rendered_lines[3]
            assert "Reasoning with [tags], x=1, and ['list'] text." in rendered_lines[4]
            assert "  must_fix:" in rendered_lines[5]
            assert "    - First [item]" in rendered_lines[6]
            assert "threshold=0.45" in rendered_lines[7]

    asyncio.run(run())


def test_dispatch_verdict_received_accepts_current_tier_payload_shape(monkeypatch) -> None:
    monkeypatch.setattr(AlphaForgeApp, "_start_loop", lambda self: None)

    payload = {
        "outputs": [
            RealismJudgeOutput(
                verdict=Verdict.REVISE,
                must_fix=["Fix timing gap [t+1] before promotion."],
                reasoning_summary="Close-to-close fill assumption is unrealistic [needs delay].",
                realism_risk=RiskLevel.HIGH,
            ).model_dump()
        ]
    }

    async def run() -> None:
        app = AlphaForgeApp()
        async with app.run_test() as pilot:
            AlphaForgeApp._dispatch_verdict_received(app, payload)
            await pilot.pause()

            conversation = app.query_one("#conversation", ConversationStream)
            verdicts = app.query_one("#verdicts-panel")

            assert any("unrealistic [needs delay]" in line.text for line in conversation.lines)
            assert len(verdicts.children) == 1

    asyncio.run(run())


def test_normalize_verdict_output_prefers_reasoning_summary_and_primary_level() -> None:
    payload = {
        "judge_type": "result",
        "verdict": Verdict.APPROVE,
        "reasoning": "old reasoning",
        "reasoning_summary": "summary wins",
        "result_quality": RiskLevel.MEDIUM,
        "must_fix": ("[keep literal]",),
    }

    display = normalize_verdict_output(payload)

    assert display.verdict == "approve"
    assert display.reasoning == "summary wins"
    assert display.must_fix == ["[keep literal]"]
    assert display.primary_label == "quality"
    assert display.primary_level == "medium"
