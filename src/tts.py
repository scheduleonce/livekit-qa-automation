import asyncio
import math
import os
import uuid
import wave
from pathlib import Path

import edge_tts
import imageio_ffmpeg
import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from scipy import signal


load_dotenv()


async def generate_wav_from_text(
    text: str,
    filename: str,
) -> None:
    """Generate a 48 kHz mono WAV file using Edge Cloud TTS."""

    output_file = Path(filename)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    gender = os.getenv(
        "VOICE_GENDER",
        "male",
    ).strip().lower()

    if gender == "female":
        voice = "en-US-AriaNeural"
    else:
        voice = "en-US-ChristopherNeural"

    temporary_mp3 = output_file.with_name(
        f"{output_file.stem}_{uuid.uuid4().hex}.mp3"
    )

    print(
        f"[TTS] Generating Edge Cloud audio "
        f"using voice {voice}..."
    )

    try:
        communicator = edge_tts.Communicate(
            text=text,
            voice=voice,
        )

        await communicator.save(
            str(temporary_mp3)
        )

        if not temporary_mp3.exists():
            raise RuntimeError(
                "Edge TTS did not create an MP3 file"
            )

        if temporary_mp3.stat().st_size == 0:
            raise RuntimeError(
                "Edge TTS created an empty MP3 file"
            )

        print(
            f"[TTS] Edge MP3 generated: "
            f"{temporary_mp3.stat().st_size} bytes"
        )

        ffmpeg_executable = (
            imageio_ffmpeg.get_ffmpeg_exe()
        )

        process = await asyncio.create_subprocess_exec(
            ffmpeg_executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(temporary_mp3),
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(output_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode(
                "utf-8",
                errors="replace",
            )

            raise RuntimeError(
                "FFmpeg failed to convert Edge TTS "
                f"audio to WAV: {error_message}"
            )

        if not output_file.exists():
            raise RuntimeError(
                f"WAV file was not created: {output_file}"
            )

        if output_file.stat().st_size == 0:
            raise RuntimeError(
                f"WAV file is empty: {output_file}"
            )

        print(
            f"[TTS] Edge Cloud audio saved to "
            f"{output_file} "
            f"({output_file.stat().st_size} bytes)"
        )

    finally:
        if temporary_mp3.exists():
            try:
                temporary_mp3.unlink()
            except OSError as cleanup_error:
                print(
                    "[TTS] Warning: temporary MP3 "
                    f"cleanup failed: {cleanup_error}"
                )


async def play_audio_to_agent(
    room: rtc.Room,
    wav_filepath: str,
) -> None:
    """Stream a WAV file into LiveKit as microphone audio."""

    print(
        f"\n[Bot Mic] Starting audio stream: "
        f"{wav_filepath}"
    )

    with wave.open(wav_filepath, "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        raw_data = wav.readframes(
            wav.getnframes()
        )

    print(
        f"[Bot Mic] Input audio: "
        f"{sample_rate} Hz, "
        f"{channels} channel(s), "
        f"{sample_width * 8}-bit"
    )

    if sample_width == 1:
        audio = np.frombuffer(
            raw_data,
            dtype=np.uint8,
        ).astype(np.int16)

        audio = audio - 128
        audio = audio << 8

    elif sample_width == 2:
        audio = np.frombuffer(
            raw_data,
            dtype=np.int16,
        )

    elif sample_width == 4:
        audio = np.frombuffer(
            raw_data,
            dtype=np.int32,
        ) >> 16

    else:
        raise ValueError(
            f"Unsupported WAV sample width: "
            f"{sample_width} bytes"
        )

    if channels > 1:
        audio = audio.reshape(
            -1,
            channels,
        )
    else:
        audio = audio.reshape(
            -1,
            1,
        )

    if channels != 1:
        audio = audio.astype(np.int32)

        audio = np.mean(
            audio,
            axis=1,
        )
    else:
        audio = audio[:, 0].astype(
            np.int32
        )

    target_sample_rate = 48000

    if sample_rate != target_sample_rate:
        print(
            f"[Bot Mic] Resampling from "
            f"{sample_rate} Hz to "
            f"{target_sample_rate} Hz"
        )

        gcd = math.gcd(
            sample_rate,
            target_sample_rate,
        )

        up = target_sample_rate // gcd
        down = sample_rate // gcd

        audio = signal.resample_poly(
            audio.astype(np.float64),
            up,
            down,
        )

        sample_rate = target_sample_rate

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

    publication = await (
        room.local_participant.publish_track(
            track,
            options,
        )
    )

    print(
        "[Bot Mic] Waiting 1 second "
        "for UI subscription..."
    )

    await asyncio.sleep(1.0)

    chunk_duration_ms = 20
    bytes_per_frame = channels * sample_width

    chunk_size = int(
        sample_rate
        * (chunk_duration_ms / 1000.0)
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
                len(chunk_bytes)
                // bytes_per_frame
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
        await (
            room.local_participant.unpublish_track(
                publication.sid
            )
        )