import json
from types import SimpleNamespace

from src.llm_judge import JUDGE_SYSTEM_PROMPT, judge_conversation


def _response(content, finish_reason="stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


def _client_with_responses(responses):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )
    return client, calls


def test_judge_prompt_uses_general_evidence_based_conversation_rules():
    assert "read the transcript chronologically" in JUDGE_SYSTEM_PROMPT
    assert "semantically equivalent wording" in JUDGE_SYSTEM_PROMPT
    assert "do not invent requirements" in JUDGE_SYSTEM_PROMPT
    assert "cite or accurately paraphrase the relevant turn(s)" in JUDGE_SYSTEM_PROMPT


def test_judge_retries_incomplete_response(monkeypatch):
    client, calls = _client_with_responses(
        [
            _response('{"success": false, "details": []}', "length"),
            _response(
                json.dumps(
                    {
                        "success": True,
                        "reasoning": "The agent completed the requested task.",
                        "details": [
                            {
                                "id": "booking",
                                "passed": True,
                                "rationale": "The agent booked the meeting.",
                            }
                        ],
                    }
                )
            ),
        ]
    )
    monkeypatch.setattr("src.llm_judge._get_azure_openai_client", lambda: client)

    result = judge_conversation(
        [{"role": "assistant", "content": "Your meeting is booked."}],
        [{"id": "booking", "description": "Book the meeting."}],
    )

    assert result["success"] is True
    assert result["reasoning"] == "The agent completed the requested task."
    assert len(calls) == 2
    assert "previous response was incomplete" in calls[1]["messages"][-1]["content"]


def test_judge_fails_clearly_after_incomplete_responses(monkeypatch):
    client, calls = _client_with_responses(
        [
            _response('{"success": false, "details": []}', "length"),
            _response('{"success": false, "details": []}', "length"),
        ]
    )
    monkeypatch.setattr("src.llm_judge._get_azure_openai_client", lambda: client)

    result = judge_conversation(
        [{"role": "assistant", "content": "Your meeting is booked."}],
        [{"id": "booking", "description": "Book the meeting."}],
    )

    assert result == {
        "success": False,
        "reasoning": "LLM judge returned an incomplete response after 2 attempts.",
        "details": [],
    }
    assert len(calls) == 2
