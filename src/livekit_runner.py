import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from livekit import api, rtc

from .config import get_target_environment, resolve_credentials
from .simulated_user import SimulatedCaller
from .tts import generate_wav_from_text, play_audio_to_agent

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLY_AUDIO_DIR = PROJECT_ROOT / "replyAudioFiles"
REPLY_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_JSON_PATH = PROJECT_ROOT / "data" / "livekit_bot_config.json"
DEFAULT_HAPPY_PATHS_YAML = PROJECT_ROOT / "data" / "happy_paths.yaml"

CONNECT_TIMEOUT_SECONDS = 30
WELCOME_TIMEOUT_SECONDS = 60
CONVERSATION_TIMEOUT_SECONDS = 600
TURN_TIMEOUT_SECONDS = 90
CLEANUP_TIMEOUT_SECONDS = 10
MAX_AGENT_TURNS = 60
MAX_CONSECUTIVE_HOLD_PROMPTS = 4
AGENT_TRANSCRIPTION_QUIET_PERIOD_SECONDS = 2.0
AGENT_SPEECH_END_TIMEOUT_SECONDS = 10

HOLD_PROMPT_MARKERS = (
    "please wait", "please hold", "one moment", "hold on",
    "give me a second", "still working on", "thank you for your patience",
    "thank you for waiting", "bear with me",
)

def _is_hold_prompt(agent_text: str) -> bool:
    normalized_text = agent_text.casefold()
    return any(marker in normalized_text for marker in HOLD_PROMPT_MARKERS)

def _merge_transcription_text(segments_dict: dict[str, str]) -> str:
    """Safely combine ordered segment strings into a unified transcript."""
    return " ".join(text.strip() for text in segments_dict.values() if text.strip())

async def _bounded_cleanup(awaitable, operation: str) -> None:
    task = asyncio.create_task(awaitable)
    done, _ = await asyncio.wait({task}, timeout=CLEANUP_TIMEOUT_SECONDS)
    if not done:
        task.cancel()
        logging.warning(f"{operation} exceeded cleanup timeout")
        await asyncio.wait({task}, timeout=1)
    else:
        try:
            task.result()
        except (asyncio.CancelledError, Exception) as exc:
            logging.warning(f"{operation} cleanup result: {exc}")

async def run_livekit_test(scenario_data: dict) -> Tuple[List[Dict], SimulatedCaller]:
    source_yaml = scenario_data.get("_source_file")
    yaml_path = Path(source_yaml) if source_yaml else DEFAULT_HAPPY_PATHS_YAML

    target_env = get_target_environment()
    livekit_url, api_key, api_secret, bot_id = resolve_credentials(
        target_env, CONFIG_JSON_PATH, yaml_path
    )

    logging.info(f"CONFIG: ENV={target_env}, selected BOT_ID={bot_id}")

    room = rtc.Room()
    caller = SimulatedCaller(scenario_data)

    test_finished = asyncio.Event()
    first_transcription_received = asyncio.Event()
    turn_queue: asyncio.Queue[tuple[str, float | None]] = asyncio.Queue()
    
    turn_error: Exception | None = None
    turn_error_phase: str | None = None
    turn_processor_task: asyncio.Task | None = None
    pending_transcription_task: asyncio.Task | None = None
    
    pending_agent_latency: float | None = None
    pending_agent_segments: dict[str, str] = {}
    active_speaker_identities: set[str] = set()
    agent_participant_identity: str | None = None
    agent_speech_stopped = asyncio.Event()
    agent_speech_stopped.set()

    shutting_down = False
    room_connected = False

    last_caller_speech_end = time.time()
    first_word_received = False
    current_agent_latency = None
    agent_turn_count = 0
    consecutive_hold_prompts = 0

    async def handle_turn(agent_text: str, agent_latency: float | None):
        nonlocal current_agent_latency, last_caller_speech_end, first_word_received
        nonlocal turn_error, turn_error_phase, agent_turn_count, consecutive_hold_prompts

        wav_file: Path | None = None
        should_end_call = False

        try:
            agent_text = agent_text.strip()
            if not agent_text:
                logging.info("DEBUG: Ignoring empty final transcription")
                return

            logging.info(f"DEBUG: Processing agent turn: '{agent_text}'")
            agent_turn_count += 1
            if agent_turn_count > MAX_AGENT_TURNS:
                raise RuntimeError(f"Conversation exceeded limit of {MAX_AGENT_TURNS} turns.")

            current_agent_message = {"role": "user", "content": agent_text}
            if agent_latency is not None:
                current_agent_message["latency_sec"] = agent_latency

            caller.conversation_history.append(current_agent_message)
            current_agent_latency = None

            if _is_hold_prompt(agent_text):
                consecutive_hold_prompts += 1
                logging.info(f"DEBUG: Detected hold prompt. Consecutive count: {consecutive_hold_prompts}")
                first_word_received = False
                if consecutive_hold_prompts >= MAX_CONSECUTIVE_HOLD_PROMPTS:
                    raise RuntimeError(f"Agent repeated hold prompts {consecutive_hold_prompts} times.")
                return

            consecutive_hold_prompts = 0
            reply = await asyncio.wait_for(
                caller.generate_reply(agent_text), timeout=TURN_TIMEOUT_SECONDS
            )

            if "[END_CALL]" in reply:
                should_end_call = True
                reply = reply.replace("[END_CALL]", "").strip()
                logging.info("DEBUG: Agent indicated call end in reply. Preparing to terminate conversation.")

            if reply:
                if shutting_down or room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
                    logging.info("DEBUG: Skipping reply generation due to shutdown or disconnected room.")
                    return

                logging.info(f"DEBUG: Generated reply: '{reply}'")
                wav_file = REPLY_AUDIO_DIR / f"dynamic_reply_{uuid.uuid4().hex}.wav"
                await asyncio.wait_for(
                    generate_wav_from_text(reply, str(wav_file)), timeout=TURN_TIMEOUT_SECONDS
                )
                logging.info(f"DEBUG: Generated WAV file at {wav_file}")

                if shutting_down or room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
                    logging.info("DEBUG: Skipping audio playback due to shutdown or disconnected room.")
                    return

                await asyncio.wait_for(
                    play_audio_to_agent(room, str(wav_file)), timeout=TURN_TIMEOUT_SECONDS
                )

                logging.info("DEBUG: Audio playback to agent completed successfully.")

                last_caller_speech_end = time.time()
                first_word_received = False

            if should_end_call:
                test_finished.set()

        except Exception as exc:
            turn_error = exc
            turn_error_phase = "handling agent turn"
            logging.exception("ERROR in handle_turn")
            test_finished.set()

        finally:
            if wav_file and wav_file.exists():
                try:
                    wav_file.unlink()
                except OSError as cleanup_error:
                    logging.warning(f"Could not remove WAV {wav_file}: {cleanup_error}")

    async def process_turn_queue() -> None:
        while not test_finished.is_set():
            try:
                agent_text, agent_latency = await asyncio.wait_for(turn_queue.get(), timeout=1.0)
                try:
                    await handle_turn(agent_text, agent_latency)
                finally:
                    turn_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                nonlocal turn_error, turn_error_phase
                turn_error = exc
                turn_error_phase = "processing turn queue"
                logging.exception("ERROR in process_turn_queue")
                test_finished.set()
                break

    async def flush_pending_transcription() -> None:
        nonlocal pending_agent_latency
        try:
            await asyncio.sleep(AGENT_TRANSCRIPTION_QUIET_PERIOD_SECONDS)

            while (
                agent_participant_identity
                and agent_participant_identity in active_speaker_identities
            ):
                try:
                    await asyncio.wait_for(
                        agent_speech_stopped.wait(), timeout=AGENT_SPEECH_END_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    break
                await asyncio.sleep(AGENT_TRANSCRIPTION_QUIET_PERIOD_SECONDS)

            final_text = _merge_transcription_text(pending_agent_segments)
            if final_text:
                turn_queue.put_nowait((final_text, pending_agent_latency))
                pending_agent_latency = None
                pending_agent_segments.clear()
        except asyncio.CancelledError:
            pass  # Normal task cancellation when newer audio arrives

    @room.on("active_speakers_changed")
    def on_active_speakers_changed(speakers):
        nonlocal active_speaker_identities
        active_speaker_identities = {s.identity for s in speakers if s is not None}
        if agent_participant_identity and agent_participant_identity in active_speaker_identities:
            agent_speech_stopped.clear()
        else:
            agent_speech_stopped.set()

    @room.on("transcription_received")
    def on_transcription(segments, participant=None, publication=None):
        nonlocal last_caller_speech_end, first_word_received, current_agent_latency
        nonlocal agent_participant_identity, pending_agent_latency, pending_transcription_task

        if participant and room.local_participant and participant.identity == room.local_participant.identity:
            return

        if participant:
            agent_participant_identity = participant.identity
            if agent_participant_identity in active_speaker_identities:
                agent_speech_stopped.clear()
            else:
                agent_speech_stopped.set()

        if shutting_down:
            logging.info("DEBUG: Ignoring transcription due to shutdown.")
            return

        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue

            if not first_word_received:
                latency_sec = time.time() - last_caller_speech_end
                first_word_received = True
                logging.info(f"[Latency]: First word received. Agent latency: {latency_sec:.2f} seconds")
                current_agent_latency = latency_sec

            if not segment.final:
                continue

            first_transcription_received.set()
            pending_agent_segments[segment.id] = text

            if pending_agent_latency is None:
                pending_agent_latency = current_agent_latency

            if pending_transcription_task and not pending_transcription_task.done():
                pending_transcription_task.cancel()

            pending_transcription_task = asyncio.create_task(flush_pending_transcription())

    scenario_id = str(scenario_data.get("id", "unknown")).replace(" ", "_")
    room_name = f"qa-test-room-{uuid.uuid4().hex[:6]}"

    logging.warning("LiveKit room name: %s", room_name)

    metadata = json.dumps({
        "is_preview": True,
        "bot_external_id": bot_id,
        "scenario_id": scenario_id,
        "iana_timezone": "Asia/Kolkata",
    })

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"QA_Bot_{uuid.uuid4().hex[:4]}")
        .with_metadata(metadata)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )

    try:
        await asyncio.wait_for(room.connect(livekit_url, token), timeout=CONNECT_TIMEOUT_SECONDS)
        room_connected = True
        last_caller_speech_end = time.time()
    except Exception as exc:
        await _bounded_cleanup(caller.close(), "simulated caller client close")
        raise

    turn_processor_task = asyncio.create_task(process_turn_queue())

    wait_phase = f"waiting for first agent transcription ({WELCOME_TIMEOUT_SECONDS}s timeout)"
    try:
        await asyncio.wait_for(
            first_transcription_received.wait(), timeout=WELCOME_TIMEOUT_SECONDS
        )
        wait_phase = f"waiting for conversation to finish ({CONVERSATION_TIMEOUT_SECONDS}s timeout)"
        await asyncio.wait_for(
            test_finished.wait(), timeout=CONVERSATION_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        turn_error = exc
        turn_error_phase = wait_phase
        logging.exception("LiveKit conversation timed out while %s", wait_phase)
    finally:
        shutting_down = True

        if pending_transcription_task and not pending_transcription_task.done():
            pending_transcription_task.cancel()

        if turn_processor_task and not turn_processor_task.done():
            turn_processor_task.cancel()

        if room_connected:
            room_connected = False
            await _bounded_cleanup(room.disconnect(), "LiveKit room disconnect")

    if turn_error is not None:
        await _bounded_cleanup(caller.close(), "simulated caller client close")
        phase = f" while {turn_error_phase}" if turn_error_phase else ""
        raise RuntimeError(
            f"LiveKit conversation error{phase}: "
            f"{type(turn_error).__name__}: {turn_error!r}"
        ) from turn_error

    return caller.conversation_history, caller