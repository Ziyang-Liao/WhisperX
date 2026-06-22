"""Tests for the Bedrock translation engine — fake client, no real AWS calls.

Focus on the alignment guarantees: 1:1 segment count, timestamp preservation,
and the split-and-retry path when the model returns the wrong count.
"""

import json

import pytest

from app.services.translation_engine import (
    TranslationEngine,
    TranslationError,
    _parse_indexed_json,
)


class FakeBody:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data


class FakeBedrock:
    """Returns translations driven by a scripted responder(texts)->dict."""

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0

    def invoke_model(self, modelId, body):
        self.calls += 1
        req = json.loads(body)
        # The user prompt embeds the numbered source lines as JSON; recover them.
        user_text = req["messages"][0]["content"]
        src = json.loads(user_text[user_text.index("{"):])
        texts = [src[str(i)] for i in range(len(src))]
        obj = self.responder(texts)
        return {"body": FakeBody({"content": [{"type": "text", "text": json.dumps(obj)}]})}


def _engine(responder):
    return TranslationEngine(_client=FakeBedrock(responder))


def test_parse_indexed_json_plain():
    assert _parse_indexed_json('{"0": "a", "1": "b"}', 2) == ["a", "b"]


def test_parse_indexed_json_with_code_fence_and_prose():
    text = 'Here you go:\n```json\n{"0": "x", "1": "y"}\n```'
    assert _parse_indexed_json(text, 2) == ["x", "y"]


def test_parse_indexed_json_missing_key_raises():
    with pytest.raises(ValueError):
        _parse_indexed_json('{"0": "a"}', 2)


def test_translate_preserves_timestamps_and_count():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "hello"},
        {"start": 1.0, "end": 2.0, "text": "world"},
    ]
    eng = _engine(lambda texts: {str(i): t.upper() for i, t in enumerate(texts)})
    out = eng.translate_segments(segs, "ja")
    assert [s["text"] for s in out] == ["HELLO", "WORLD"]
    assert [(s["start"], s["end"]) for s in out] == [(0.0, 1.0), (1.0, 2.0)]


def test_empty_segments_short_circuits():
    eng = _engine(lambda texts: {})
    assert eng.translate_segments([], "ja") == []


def test_batching_covers_all_segments():
    segs = [{"start": float(i), "end": i + 1.0, "text": f"t{i}"} for i in range(95)]
    eng = _engine(lambda texts: {str(i): t for i, t in enumerate(texts)})
    eng.batch_size = 40
    out = eng.translate_segments(segs, "fr")
    assert len(out) == 95
    assert out[94]["text"] == "t94"
    # 95 segments / 40 per batch => 3 invocations
    assert eng._client.calls == 3


def test_count_mismatch_triggers_split_and_recovers():
    segs = [{"start": float(i), "end": i + 1.0, "text": f"t{i}"} for i in range(4)]

    def responder(texts):
        # Misbehave only on the full 4-segment batch (drop a key); behave once split.
        obj = {str(i): t for i, t in enumerate(texts)}
        if len(texts) == 4:
            obj.pop("3")
        return obj

    eng = _engine(responder)
    eng.batch_size = 40
    out = eng.translate_segments(segs, "es")
    assert len(out) == 4
    assert [s["text"] for s in out] == ["t0", "t1", "t2", "t3"]


def test_single_segment_persistent_failure_raises():
    def responder(texts):
        return {}  # never returns the required key

    eng = _engine(responder)
    with pytest.raises(TranslationError):
        eng.translate_segments([{"start": 0.0, "end": 1.0, "text": "x"}], "de")
