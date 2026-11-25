import asyncio

import pytest

from src.livekit_runner import _wait_for_event_or_finished


@pytest.mark.asyncio
async def test_event_wait_returns_when_test_finishes_first():
    event = asyncio.Event()
    test_finished = asyncio.Event()
    test_finished.set()

    await _wait_for_event_or_finished(event, test_finished, timeout=1)


@pytest.mark.asyncio
async def test_event_wait_times_out_when_neither_event_is_set():
    with pytest.raises(asyncio.TimeoutError):
        await _wait_for_event_or_finished(
            asyncio.Event(), asyncio.Event(), timeout=0.01
        )
