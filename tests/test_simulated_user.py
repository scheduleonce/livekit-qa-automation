from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.simulated_user import SimulatedCaller


def _caller_with_failing_openai_request():
    caller = object.__new__(SimulatedCaller)
    caller.conversation_history = [{"role": "system", "content": "caller prompt"}]
    caller.deployment_name = "test-deployment"
    caller.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=TimeoutError("Azure OpenAI timed out"))
            )
        )
    )
    return caller


@pytest.mark.asyncio
async def test_generate_reply_propagates_openai_failure():
    caller = _caller_with_failing_openai_request()

    with pytest.raises(TimeoutError, match="Azure OpenAI timed out"):
        await caller.generate_reply("Hello")

    assert caller.conversation_history == [
        {"role": "system", "content": "caller prompt"}
    ]


@pytest.mark.asyncio
async def test_generate_initial_utterance_propagates_openai_failure():
    caller = _caller_with_failing_openai_request()

    with pytest.raises(TimeoutError, match="Azure OpenAI timed out"):
        await caller.generate_initial_utterance()

    assert caller.conversation_history == [
        {"role": "system", "content": "caller prompt"}
    ]
