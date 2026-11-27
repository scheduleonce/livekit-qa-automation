from types import SimpleNamespace

import pytest
from livekit import rtc

from src import tts


class FakeAudioSource:
    def __init__(self):
        self.capture_count = 0
        self.playout_wait_count = 0
        self.closed = False
        self.capture_error = None

    async def capture_frame(self, _frame):
        if self.capture_error is not None:
            raise self.capture_error
        self.capture_count += 1

    async def wait_for_playout(self):
        self.playout_wait_count += 1

    async def aclose(self):
        self.closed = True


class FakeLocalParticipant:
    def __init__(self):
        self.publish_count = 0
        self.unpublished_sids = []

    async def publish_track(self, _track, _options):
        self.publish_count += 1
        return SimpleNamespace(sid="audio-track")

    async def unpublish_track(self, sid):
        self.unpublished_sids.append(sid)


@pytest.mark.asyncio
async def test_audio_playback_reuses_track_and_drains_each_reply(monkeypatch):
    source = FakeAudioSource()
    participant = FakeLocalParticipant()
    room = SimpleNamespace(
        connection_state=rtc.ConnectionState.CONN_CONNECTED,
        local_participant=participant,
    )

    monkeypatch.setattr(tts.rtc, "AudioSource", lambda _sample_rate, _channels: source)
    monkeypatch.setattr(
        tts.rtc.LocalAudioTrack,
        "create_audio_track",
        staticmethod(lambda _name, _audio_source: object()),
    )
    monkeypatch.setattr(
        tts,
        "_prepare_audio_buffer",
        lambda _path: (b"\x01\x00", tts.TARGET_SAMPLE_RATE, 1),
    )

    playback = tts.AudioPlaybackSession(room)
    await playback.play("first.wav")
    await playback.play("second.wav")

    assert participant.publish_count == 1
    assert participant.unpublished_sids == []
    assert source.capture_count == 2
    assert source.playout_wait_count == 2

    source.capture_error = RuntimeError("frame capture failed")
    with pytest.raises(RuntimeError, match="frame capture failed"):
        await playback.play("failed.wav")

    source.capture_error = None
    await playback.close()

    assert participant.unpublished_sids == ["audio-track"]
    assert source.closed
