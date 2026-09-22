"""Explicit model observations for policy/API tests, never a language parser."""

from collections import deque
from copy import deepcopy

import pytest

from app import main
from app.schemas import ModelConfig, TurnAnalysis


class ObservationQueue:
    def __init__(self):
        self.pending = deque()
        self.calls = []
        self.render_calls = []

    def add(self, message: str, observations: TurnAnalysis | dict):
        analysis = observations if isinstance(observations, TurnAnalysis) else TurnAnalysis(**observations)
        self.pending.append((message, analysis))

    async def analyze(self, message: str, context: dict, config: ModelConfig):
        assert isinstance(config, ModelConfig)
        assert self.pending, "Unexpected model invocation: enqueue explicit observations in the test"
        expected, analysis = self.pending.popleft()
        assert message == expected, "Model call order differs from the test's explicit scenario"
        self.calls.append({"message": message, "context": deepcopy(context), "config": config.model_copy()})
        return analysis.model_copy(deep=True)

    async def render(self, approved_reply: str, message: str, context: dict, config: ModelConfig):
        assert isinstance(config, ModelConfig)
        self.render_calls.append({"approved_reply": approved_reply, "message": message,
                                  "context": deepcopy(context), "config": config.model_copy()})
        return approved_reply


@pytest.fixture
def model_observations(monkeypatch):
    observations = ObservationQueue()
    monkeypatch.setattr(main, "analyze_turn", observations.analyze)
    monkeypatch.setattr(main, "render_reply", observations.render)
    yield observations
    assert not observations.pending, "Some explicitly queued model results were never consumed"
