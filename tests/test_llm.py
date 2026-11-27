import json
from types import SimpleNamespace

from twice.llm import CATEGORIES, FALLBACK_BETA, Judge, _describe_error, render, run_judgments
from twice.model import Turn
from twice.store import read_jsonl


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _client(*replies):
    messages = FakeMessages(replies)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def _response(payload, stop_reason="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop_reason, model="claude-opus-5-5", content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


TURN = Turn("s:1", "s", "claude-code", "/p", "main", "", "No, never use em dashes.", "Agent said: done", [], 2.0, True)


def test_request_shape():
    client, messages = _client(_response({"is_correction": True, "lesson": "Never use em dashes.", "category": "style"}))
    result = Judge(client=client).judge(TURN)
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"]["effort"] == "low"
    schema = call["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["category"]["enum"] == list(CATEGORIES)
    assert "<developer_message>" in call["messages"][0]["content"]
    assert result["status"] == "ok" and result["is_correction"] is True
    assert result["lesson"] == "Never use em dashes."


def test_refusal_truncation_and_bad_json():
    client, _ = _client(
        _response("", stop_reason="refusal"),
        _response("", stop_reason="max_tokens"),
        _response("not json"),
    )
    judge = Judge(client=client)
    assert judge.judge(TURN)["status"] == "refused"
    assert judge.judge(TURN)["status"] == "truncated"
    assert judge.judge(TURN)["status"] == "bad_json"


def test_unknown_category_becomes_other():
    client, _ = _client(_response({"is_correction": True, "lesson": "x", "category": "vibes"}))
    assert Judge(client=client).judge(TURN)["category"] == "other"


def test_run_judgments_appends_each_result(tmp_path):
    client, _ = _client(*[_response({"is_correction": False, "lesson": "", "category": "other"})] * 3)
    path = tmp_path / "j.jsonl"
    done, problem = run_judgments([TURN, TURN, TURN], Judge(client=client), path, say=lambda _: None)
    assert done == 3 and problem is None
    assert len(read_jsonl(path)) == 3


def test_errors_map_to_messages():
    class APIStatusError(Exception):
        def __init__(self, status_code=500, message="boom"):
            self.status_code, self.message = status_code, message

    class AuthenticationError(APIStatusError):
        pass

    class NotFoundError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class APIConnectionError(Exception):
        pass

    fake = SimpleNamespace(
        AuthenticationError=AuthenticationError, NotFoundError=NotFoundError, RateLimitError=RateLimitError,
        APIStatusError=APIStatusError, APIConnectionError=APIConnectionError,
    )
    assert "ANTHROPIC_API_KEY" in _describe_error(AuthenticationError(401), fake)
    assert "--model" in _describe_error(NotFoundError(404), fake)
    assert "Rate limited" in _describe_error(RateLimitError(429), fake)
    assert "API error 500" in _describe_error(APIStatusError(500), fake)
    assert "reach the API" in _describe_error(APIConnectionError(), fake)
    assert _describe_error(ValueError(), fake) is None


def test_render_has_both_parts():
    text = render(TURN)
    assert "Agent said: done" in text and "No, never use em dashes." in text
