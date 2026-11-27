import asyncio
import logging
import math
import os
import uuid
import wave
from pathlib import Path
from typing import Tuple

import azure.cognitiveservices.speech as speechsdk
import edge_tts
import imageio_ffmpeg
import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from scipy import signal

load_dotenv()

# Logger setup
logger = logging.getLogger(__name__)

# Constants
TARGET_SAMPLE_RATE = 48000
FRAME_DURATION_MS = 20
TRAILING_SILENCE_SECONDS = 0.4

# Default Endpoint (Ensures proper https:// schema for SDK)
AZURE_SPEECH_ENDPOINT = os.getenv(
    "AZURE_SPEECH_ENDPOINT",
    "https://dev-speeck-service.cognitiveservices.azure.com/"
).strip()


# ============================================================================
# Provider 1: Azure Speech SDK
# ============================================================================

def _synthesize_azure_speech(
    text: str,
    output_file: Path,
    speech_key: str,
    speech_endpoint: str,
    voice: str,
) -> None:
    """Synchronous worker for Azure Speech SDK synthesis using official SpeechConfig."""
    
    # 1. Clean endpoint format (Ensure https:// and no trailing websocket paths)
    endpoint = speech_endpoint.strip()
    if not endpoint.startswith(("https://", "http://")):
        endpoint = f"https://{endpoint}"

    if "/tts/" in endpoint:
        endpoint = endpoint.split("/tts/")[0]
    endpoint = endpoint.rstrip("/")

    # 2. Pass clean HTTPS endpoint to Azure SpeechConfig
    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key,
        endpoint=endpoint,
    )
    speech_config.speech_synthesis_language = "en-US"
    speech_config.speech_synthesis_voice_name = voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff48Khz16BitMonoPcm
    )

    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(output_file))
    speech_synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    
    result = speech_synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError("Azure Speech returned no WAV audio.")
        return

    if result.reason == speechsdk.ResultReason.Canceled:
        cancellation = result.cancellation_details
        reason = getattr(cancellation, "reason", "unknown")
        details = getattr(cancellation, "error_details", "")
        raise RuntimeError(f"Azure Speech synthesis canceled ({reason}): {details}")

    raise RuntimeError(f"Azure Speech unexpected result reason: {result.reason}")


async def _generate_azure_speech_wav(text: str, output_file: Path, voice: str) -> None:
    """Generate 48kHz WAV audio using Azure Speech SDK offloaded to an async thread."""
    speech_key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    speech_endpoint = AZURE_SPEECH_ENDPOINT

    if not speech_key:
        raise RuntimeError("Azure Speech requires AZURE_SPEECH_KEY environment variable.")

    if not speech_endpoint:
        raise RuntimeError("Azure Speech requires AZURE_SPEECH_ENDPOINT environment variable.")

    print(f"[CHECK] Using Azure Speech endpoint: {speech_endpoint}")
    print(f"[CHECK] Using Azure Speech key length: {len(speech_key)}")
    print(f"[TTS] Generating Azure Speech audio using voice: {voice}")

    await asyncio.to_thread(
        _synthesize_azure_speech,
        text,
        output_file,
        speech_key,
        speech_endpoint,
        voice,
    )


# ============================================================================
# Provider 2: Edge Cloud TTS
# ============================================================================

async def _generate_edge_tts_wav(text: str, output_file: Path, voice: str) -> None:
    """Generate audio via Edge TTS and convert to 48kHz PCM WAV using FFmpeg."""
    temporary_mp3 = output_file.with_name(f"{output_file.stem}_{uuid.uuid4().hex}.mp3")
    print(f"[TTS] Generating Edge Cloud audio using voice: {voice}")

    try:
        communicator = edge_tts.Communicate(text=text, voice=voice)
        await communicator.save(str(temporary_mp3))

        if not temporary_mp3.exists() or temporary_mp3.stat().st_size == 0:
            raise RuntimeError("Edge TTS created an empty or missing MP3 file.")

        logger.info(f"[TTS] Edge MP3 generated: {temporary_mp3.stat().st_size} bytes")

        ffmpeg_executable = imageio_ffmpeg.get_ffmpeg_exe()
        process = await asyncio.create_subprocess_exec(
            ffmpeg_executable,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(temporary_mp3),
            "-ac", "1",
            "-ar", str(TARGET_SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            str(output_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"FFmpeg failed to convert MP3 to WAV: {error_msg}")

        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError(f"WAV output file is empty or missing: {output_file}")

        logger.info(f"[TTS] Edge Cloud audio saved to {output_file} ({output_file.stat().st_size} bytes)")

    finally:
        if temporary_mp3.exists():
            try:
                temporary_mp3.unlink()
            except OSError as cleanup_error:
                logger.warning(f"[TTS] Temporary MP3 cleanup failed: {cleanup_error}")


# ============================================================================
# Public TTS Entrypoint Facade
# ============================================================================

async def generate_wav_from_text(text: str, filename: str) -> None:
    """Generate a 48 kHz mono WAV file using the configured TTS provider."""
    output_file = Path(filename)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    provider = os.getenv("VOICE_PROVIDER", "azure-speech").strip().lower()

    if provider == "azure-speech":
        voice = os.getenv("AZURE_SPEECH_VOICE", "en-US-AvaMultilingualNeural").strip()
        await _generate_azure_speech_wav(text, output_file, voice)
    elif provider == "edge":
        gender = os.getenv("VOICE_GENDER", "male").strip().lower()
        voice = "en-US-AriaNeural" if gender == "female" else "en-US-ChristopherNeural"
        await _generate_edge_tts_wav(text, output_file, voice)
    else:
        raise ValueError(f"Unsupported VOICE_PROVIDER: {provider!r}. Expected 'azure-speech' or 'edge'.")


# ============================================================================
# Audio Resampling & LiveKit WebRTC Streaming
# ============================================================================

def _prepare_audio_buffer(wav_filepath: str) -> Tuple[bytes, int, int]:
    """Parse, resample to 48kHz mono, and return raw PCM bytes."""
    with wave.open(wav_filepath, "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        raw_data = wav.readframes(wav.getnframes())

    if sample_width == 1:
        audio = (np.frombuffer(raw_data, dtype=np.uint8).astype(np.int16) - 128) << 8
    elif sample_width == 2:
        audio = np.frombuffer(raw_data, dtype=np.int16)
    elif sample_width == 4:
        audio = np.frombuffer(raw_data, dtype=np.int32) >> 16
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width} bytes")

    audio = audio.reshape(-1, channels) if channels > 1 else audio.reshape(-1, 1)

    if channels > 1:
        audio = np.mean(audio.astype(np.int32), axis=1)
    else:
        audio = audio[:, 0].astype(np.int32)

    if sample_rate != TARGET_SAMPLE_RATE:
        gcd = math.gcd(sample_rate, TARGET_SAMPLE_RATE)
        up = TARGET_SAMPLE_RATE // gcd
        down = sample_rate // gcd
        audio = signal.resample_poly(audio.astype(np.float64), up, down)

    pcm_bytes = np.clip(audio, -32768, 32767).astype(np.int16).tobytes()
    return pcm_bytes, TARGET_SAMPLE_RATE, 1


class AudioPlaybackSession:
    """Publish one microphone track and reuse it for all caller replies in a room."""

    def __init__(self, room: rtc.Room) -> None:
        self._room = room
        self._source = rtc.AudioSource(TARGET_SAMPLE_RATE, 1)
        self._track = rtc.LocalAudioTrack.create_audio_track("bot_mic", self._source)
        self._publication: rtc.LocalTrackPublication | None = None
        self._closed = False

    async def initialize(self) -> None:
        """Pre-publish the persistent 48kHz mono microphone track once per room."""
        if self._closed:
            raise RuntimeError("Audio playback session is closed.")
        if self._publication is not None:
            return
        if self._room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
            raise ConnectionError("Cannot publish audio track to a disconnected LiveKit room.")

        options = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE,
            dtx=False,
        )
        self._publication = await self._room.local_participant.publish_track(
            self._track, options
        )

        # Allow brief initial negotiation settle before first turn audio arrives
        for _ in range(5):
            if self._room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
                raise ConnectionError(
                    "LiveKit room disconnected while waiting for initial audio track publish."
                )
            await asyncio.sleep(0.1)

    async def play(self, wav_filepath: str) -> None:
        if self._closed:
            raise RuntimeError("Audio playback session is closed.")
        if self._room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
            raise ConnectionError("Cannot stream audio to a disconnected LiveKit room.")

        if self._publication is None:
            await self.initialize()

        logger.info(f"[Bot Mic] Starting audio stream: {wav_filepath}")
        pcm_data, sample_rate, channels = _prepare_audio_buffer(wav_filepath)
        if sample_rate != TARGET_SAMPLE_RATE or channels != 1:
            raise ValueError("Audio playback must be resampled to 48 kHz mono.")

        bytes_per_sample = 2
        # Append trailing silence so the remote agent's VAD detects end-of-utterance cleanly
        trailing_silence = bytes(
            int(sample_rate * TRAILING_SILENCE_SECONDS) * channels * bytes_per_sample
        )
        full_pcm = pcm_data + trailing_silence

        chunk_size = int(sample_rate * (FRAME_DURATION_MS / 1000.0)) * (
            channels * bytes_per_sample
        )
        offset = 0

        while offset < len(full_pcm):
            if self._room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
                raise ConnectionError("LiveKit room disconnected during audio playback.")

            chunk_bytes = full_pcm[offset : offset + chunk_size]
            offset += len(chunk_bytes)
            if not chunk_bytes:
                break

            num_frames = len(chunk_bytes) // (channels * bytes_per_sample)
            if num_frames == 0:
                break

            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=num_frames,
            )

            audio_data = np.frombuffer(frame.data, dtype=np.int16)
            buffer_aux = np.frombuffer(chunk_bytes, dtype=np.int16)
            np.copyto(audio_data, buffer_aux)

            await self._source.capture_frame(frame)
            await asyncio.sleep(FRAME_DURATION_MS / 1000.0)

        await self._source.wait_for_playout()
        logger.info("[Bot Mic] Finished speaking.")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        try:
            if (
                self._publication is not None
                and self._room.connection_state != rtc.ConnectionState.CONN_DISCONNECTED
            ):
                await self._source.wait_for_playout()
                await self._room.local_participant.unpublish_track(
                    self._publication.sid
                )
        finally:
            await self._source.aclose()