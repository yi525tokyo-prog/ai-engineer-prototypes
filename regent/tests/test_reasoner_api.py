"""With an API key, Regent calls Claude directly: structured answers, and answers streamed as written."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class _Stream:
    def __init__(self, pieces):
        self.text_stream = iter(pieces)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn")


class _Client:
    def __init__(self):
        self.sent = []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw)
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               content=[SimpleNamespace(type="text", text=json.dumps({"mode": "answer"}))])

    def stream(self, **kw):
        self.sent.append(kw)
        return _Stream(["三つ", "の道", "があります。"])


@pytest.fixture()
def api(monkeypatch, tmp_path):
    pytest.importorskip("anthropic")
    from regent.software import reasoner as R

    client = _Client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(R, "_client", lambda: client)
    return R.Reasoner("auto", record_dir=tmp_path), client


def test_structured_answers_come_straight_from_the_api(api):
    r, client = api
    assert r.backend == "api"
    schema = {"type": "object", "required": ["mode"], "properties": {
        "mode": {"type": "string"}, "extra": {"type": ["object", "null"], "properties": {"x": {"type": "string"}}}}}
    ans = r.ask("route", "Decide.", {"sentence": "hi"}, schema, effort="low")
    assert ans.output == {"mode": "answer"} and ans.provider == "api" and ans.cost_usd > 0
    sent = client.sent[-1]["output_config"]
    assert sent["effort"] == "low"
    # every object is closed, as structured output requires
    assert sent["format"]["schema"]["additionalProperties"] is False
    assert sent["format"]["schema"]["properties"]["extra"]["additionalProperties"] is False


def test_an_answer_is_handed_over_while_it_is_written(api):
    r, _ = api
    seen = []
    text = r.stream_text("Answer.", "世界の労働をなくす方法", seen.append)
    assert text == "三つの道があります。" and seen == ["三つ", "三つの道", "三つの道があります。"]
