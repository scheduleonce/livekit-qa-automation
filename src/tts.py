import asyncio
import math
import os
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from openai import OpenAI
from scipy import signal


load_dotenv()


def generate_wav_from_text_sync(text: str, filename: str):
    """Generate a WAV file using OpenAI TTS."""

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing from the environment configuration"
        )

    output_file = Path(filename)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    client = OpenAI(api_key=api_key)

    print("[TTS] Requesting speech audio from OpenAI...")

    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=text,
        response_format="wav",
    ) as response:
        response.stream_to_file(str(output_file))


async def generate_wav_from_text(text: str, filename: str):
    """Generate speech without blocking the LiveKit event loop."""

    print(f"[TTS] Generating audio for: '{text}'...")

    await asyncio.to_thread(
        generate_wav_from_text_sync,
        text,
        filename,
    )

    print(f"[TTS] Audio saved to {filename}")


async def play_audio_to_agent(room: rtc.Room, wav_filepath: str):
    """Stream a WAV file into LiveKit as microphone audio."""

    print(f"\n[Bot Mic] Starting audio stream: {wav_filepath}")

    with wave.open(wav_filepath, "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        raw_data = wav.readframes(wav.getnframes())

    # Convert raw audio bytes into a NumPy array.
    if sample_width == 2:
        audio = np.frombuffer(raw_data, dtype=np.int16)

    elif sample_width == 1:
        audio = np.frombuffer(
            raw_data,
            dtype=np.uint8,
        ).astype(np.int16)

        audio = audio - 128
        audio = audio << 8

    elif sample_width == 4:
        audio = np.frombuffer(
            raw_data,
            dtype=np.int32,
        ) >> 16

    else:
        raise ValueError(
            f"Unsupported WAV sample width: {sample_width} bytes"
        )

    # Reshape the audio to (frames, channels).
    if channels > 1:
        audio = audio.reshape(-1, channels)
    else:
        audio = audio.reshape(-1, 1)

    # Convert multi-channel audio to mono.
    if channels != 1:
        audio = audio.astype(np.int32)
        audio = np.mean(audio, axis=1)
    else:
        audio = audio[:, 0].astype(np.int32)

    # LiveKit expects 48 kHz audio.
    if sample_rate != 48000:
        gcd = math.gcd(sample_rate, 48000)
        up = 48000 // gcd
        down = sample_rate // gcd

        audio = signal.resample_poly(
            audio.astype(np.float64),
            up,
            down,
        )

        sample_rate = 48000

    audio = np.clip(
        audio,
        -32768,
        32767,
    ).astype(np.int16)

    channels = 1
    sample_width = 2
    raw_data = audio.tobytes()

    source = rtc.AudioSource(
        sample_rate,
        channels,
    )

    track = rtc.LocalAudioTrack.create_audio_track(
        "bot_mic",
        source,
    )

    options = rtc.TrackPublishOptions(
        source=rtc.TrackSource.SOURCE_MICROPHONE
    )

    publication = await room.local_participant.publish_track(
        track,
        options,
    )

    print("[Bot Mic] Waiting 1 second for UI subscription...")
    await asyncio.sleep(1.0)

    chunk_duration_ms = 20
    bytes_per_frame = channels * sample_width

    chunk_size = int(
        sample_rate * (chunk_duration_ms / 1000.0)
    ) * bytes_per_frame

    offset = 0

    try:
        while offset < len(raw_data):
            chunk_bytes = raw_data[
                offset: offset + chunk_size
            ]

            offset += len(chunk_bytes)

            if not chunk_bytes:
                break

            num_frames = (
                len(chunk_bytes) // bytes_per_frame
            )

            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=num_frames,
            )

            audio_data = np.frombuffer(
                frame.data,
                dtype=np.int16,
            )

            buffer_aux = np.frombuffer(
                chunk_bytes,
                dtype=np.int16,
            )

            np.copyto(
                audio_data,
                buffer_aux,
            )

            await source.capture_frame(frame)

            await asyncio.sleep(
                chunk_duration_ms / 1000.0
            )

        print("[Bot Mic] Finished speaking.")

    finally:
        await room.local_participant.unpublish_track(
            publication.sid
        )