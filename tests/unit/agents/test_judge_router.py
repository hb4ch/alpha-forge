"""Tests for aggregate_verdict in judge_router."""

import pytest

from alpha_forge.app.agents.judge_router import aggregate_verdict
from alpha_forge.app.domain.models import JudgeOutput
from alpha_forge.app.domain.states import Verdict


def _out(verdict: Verdict) -> JudgeOutput:
    return JudgeOutput(verdict=verdict)


class TestAggregateVerdict:
    """Tests for the priority-based verdict aggregation logic."""

    def test_all_approve(self):
        outputs = [_out(Verdict.APPROVE), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.APPROVE

    def test_any_reject_mixed_with_approve(self):
        outputs = [_out(Verdict.APPROVE), _out(Verdict.REJECT)]
        assert aggregate_verdict(outputs) == Verdict.REJECT

    def test_fork_required_mixed_with_revise_and_approve(self):
        outputs = [
            _out(Verdict.FORK_REQUIRED),
            _out(Verdict.REVISE),
            _out(Verdict.APPROVE),
        ]
        assert aggregate_verdict(outputs) == Verdict.FORK_REQUIRED

    def test_revise_mixed_with_approve(self):
        outputs = [_out(Verdict.REVISE), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_approve_with_constraints_mixed_with_approve(self):
        outputs = [_out(Verdict.APPROVE_WITH_CONSTRAINTS), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.APPROVE_WITH_CONSTRAINTS

    def test_empty_list(self):
        assert aggregate_verdict([]) == Verdict.APPROVE

    def test_single_reject(self):
        outputs = [_out(Verdict.REJECT)]
        assert aggregate_verdict(outputs) == Verdict.REJECT

    def test_all_revise(self):
        outputs = [_out(Verdict.REVISE), _out(Verdict.REVISE)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_approve_with_constraints_and_approve_only(self):
        outputs = [
            _out(Verdict.APPROVE_WITH_CONSTRAINTS),
            _out(Verdict.APPROVE),
            _out(Verdict.APPROVE),
        ]
        assert aggregate_verdict(outputs) == Verdict.APPROVE_WITH_CONSTRAINTS
