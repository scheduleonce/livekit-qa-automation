import pytest

from src import evaluator


def test_llm_evaluator_type_uses_llm_judge(monkeypatch):
    expected = [{"id": "context", "evaluator": {"type": "llm"}}]
    judge_result = {
        "success": True,
        "details": [{"id": "context", "passed": True}],
        "reasoning": "Context was retained.",
    }

    def fake_judge(history, outcomes, objective):
        assert outcomes == expected
        assert objective == "Book a meeting."
        return judge_result

    monkeypatch.setattr(evaluator, "judge_conversation", fake_judge)

    result = evaluator.evaluate_test_run(
        [{"role": "user", "content": "It is booked."}],
        expected,
        "Book a meeting.",
    )

    assert result == {
        "success": True,
        "details": judge_result["details"],
        "reasoning": "Context was retained.",
    }


@pytest.mark.parametrize(
    ("outcomes", "message"),
    [
        ([["not", "a", "mapping"]], r"expectedOutcomes\[0\] must be a mapping"),
        (
            [{"id": "bad", "evaluator": ["not", "a", "mapping"]}],
            r"expectedOutcomes\[0\]\.evaluator must be a mapping",
        ),
    ],
)
def test_malformed_outcomes_raise_clear_error(outcomes, message):
    with pytest.raises(ValueError, match=message):
        evaluator.evaluate_test_run([], outcomes)


def test_malformed_conversation_message_raises_clear_error():
    with pytest.raises(ValueError, match="Conversation history item 0"):
        evaluator.evaluate_test_run([["not", "a", "mapping"]], [])
