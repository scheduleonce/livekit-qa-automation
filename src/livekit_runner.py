import asyncio
import json
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
MAX_CONSECUTIVE_HOLD_PROMPTS = 3

HOLD_PROMPT_MARKERS = (
    "please wait",
    "please hold",
    "one moment",
    "hold on",
    "give me a second",
    "still working on",
    "thank you for your patience",
    "thank you for waiting",
    "bear with me",
)


def _is_hold_prompt(agent_text: str) -> bool:
    normalized_text = agent_text.casefold()
    return any(marker in normalized_text for marker in HOLD_PROMPT_MARKERS)


async def _wait_for_event(
    event: asyncio.Event,
    timeout_seconds: int,
    timeout_message: str,
) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(timeout_message) from exc


async def _bounded_cleanup(awaitable, operation: str) -> None:
    task = asyncio.create_task(awaitable)
    done, _ = await asyncio.wait(
        {task},
        timeout=CLEANUP_TIMEOUT_SECONDS,
    )

    if not done:
        task.cancel()
        print(f"WARNING: {operation} exceeded cleanup timeout")
        done, _ = await asyncio.wait({task}, timeout=1)

    if done:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"WARNING: {operation} failed: {exc}")


async def run_livekit_test(
    scenario_data: dict,
) -> Tuple[List[Dict], SimulatedCaller]:
    """Run one LiveKit test scenario and return its conversation history."""

    source_yaml = scenario_data.get("_source_file")
    yaml_path = (
        Path(source_yaml)
        if source_yaml
        else DEFAULT_HAPPY_PATHS_YAML
    )

    try:
        target_env = get_target_environment()

        livekit_url, api_key, api_secret, bot_id = resolve_credentials(
            target_env,
            CONFIG_JSON_PATH,
            yaml_path,
        )

    except Exception as exc:
        print(
            f"Configuration error for scenario "
            f"{scenario_data.get('id')}: {exc}"
        )
        raise

    print(f"CONFIG: ENV={target_env}, selected BOT_ID={bot_id}")

    room = rtc.Room()
    caller = SimulatedCaller(scenario_data)

    test_finished = asyncio.Event()
    first_transcription_received = asyncio.Event()
    turn_queue: asyncio.Queue[tuple[str, float | None]] = asyncio.Queue()
    turn_error: Exception | None = None
    turn_processor_task: asyncio.Task | None = None

    # Conversation lifecycle flags.
    shutting_down = False
    room_connected = False

    # Latency tracking.
    last_caller_speech_end = time.time()
    first_word_received = False
    current_agent_latency = None
    agent_turn_count = 0
    consecutive_hold_prompts = 0

    async def handle_turn(agent_text: str, agent_latency: float | None):
        """
        Handle one completed agent transcription.

        Only one turn is processed at a time to prevent multiple
        LiveKit audio tracks from being published simultaneously.
        """

        nonlocal current_agent_latency
        nonlocal last_caller_speech_end
        nonlocal first_word_received
        nonlocal turn_error
        nonlocal agent_turn_count
        nonlocal consecutive_hold_prompts

        wav_file: Path | None = None
        should_end_call = False

        try:
            agent_text = agent_text.strip()

            if not agent_text:
                print("DEBUG: Ignoring empty final transcription")
                return

            agent_turn_count += 1
            if agent_turn_count > MAX_AGENT_TURNS:
                raise RuntimeError(
                    f"Conversation exceeded the limit of {MAX_AGENT_TURNS} agent turns."
                )

            print(
                "DEBUG: Handling agent transcription "
                f"({len(agent_text)} characters)"
            )

            current_agent_message = {
                "role": "user",
                "content": agent_text,
            }

            if agent_latency is not None:
                current_agent_message["latency_sec"] = (
                    agent_latency
                )

            caller.conversation_history.append(
                current_agent_message
            )

            current_agent_latency = None

            if _is_hold_prompt(agent_text):
                consecutive_hold_prompts += 1
                first_word_received = False

                if consecutive_hold_prompts >= MAX_CONSECUTIVE_HOLD_PROMPTS:
                    raise RuntimeError(
                        "Agent repeated hold/wait prompts "
                        f"{consecutive_hold_prompts} times without progressing."
                    )

                print(
                    "DEBUG: Ignoring agent hold prompt "
                    f"({consecutive_hold_prompts}/"
                    f"{MAX_CONSECUTIVE_HOLD_PROMPTS})"
                )
                return

            consecutive_hold_prompts = 0
            reply = await asyncio.wait_for(
                caller.generate_reply(agent_text),
                timeout=TURN_TIMEOUT_SECONDS,
            )

            if "[END_CALL]" in reply:
                should_end_call = True
                reply = reply.replace("[END_CALL]", "").strip()
                print("DEBUG: End-call instruction received")

            if reply:
                if shutting_down or not room_connected:
                    print(
                        "DEBUG: Skipping reply because the room "
                        "is closing or disconnected"
                    )
                    return

                print(
                    "DEBUG: Playing simulated caller audio : "
                    f"{reply}"
                )

                wav_file = (
                    REPLY_AUDIO_DIR
                    / f"dynamic_reply_{uuid.uuid4().hex}.wav"
                )

                await asyncio.wait_for(
                    generate_wav_from_text(reply, str(wav_file)),
                    timeout=TURN_TIMEOUT_SECONDS,
                )

                print(f"DEBUG: Generated WAV file: {wav_file}")

                # TTS generation can take several seconds.
                # Confirm the room is still available before playback.
                if shutting_down or not room_connected:
                    print(
                        "DEBUG: Room closed while generating audio; "
                        "skipping playback"
                    )
                    return

                await asyncio.wait_for(
                    play_audio_to_agent(room, str(wav_file)),
                    timeout=TURN_TIMEOUT_SECONDS,
                )

                last_caller_speech_end = time.time()
                first_word_received = False

                print("DEBUG: Played audio to agent")

            if should_end_call:
                # Do not finish the test until the final reply has
                # completely finished playing.
                print("DEBUG: Test finished signal set")
                test_finished.set()

        except Exception as exc:
            turn_error = exc
            print(f"ERROR while handling conversation turn: {exc}")

            import traceback
            traceback.print_exc()

            test_finished.set()

        finally:
            if wav_file is not None and wav_file.exists():
                try:
                    wav_file.unlink()

                    print(
                        "DEBUG: Removed temporary WAV file: "
                        f"{wav_file}"
                    )

                except OSError as cleanup_error:
                    print(
                        "WARNING: Could not remove temporary WAV file "
                        f"{wav_file}: {cleanup_error}"
                    )

    async def process_turn_queue() -> None:
        while True:
            agent_text, agent_latency = await turn_queue.get()
            try:
                await handle_turn(agent_text, agent_latency)
            finally:
                turn_queue.task_done()

            if test_finished.is_set():
                return

    @room.on("transcription_received")
    def on_transcription(
        segments,
        participant=None,
        publication=None,
    ):
        nonlocal last_caller_speech_end
        nonlocal first_word_received
        nonlocal current_agent_latency

        # Ignore transcriptions generated by the local QA participant.
        if (
            participant
            and room.local_participant
            and participant.identity
            == room.local_participant.identity
        ):
            return

        if shutting_down:
            print(
                "DEBUG: Ignoring transcription because "
                "the test is shutting down"
            )
            return

        for segment in segments:
            text = segment.text.strip()

            if not text:
                continue

            # Track the first transcription fragment received from the agent.
            if not first_word_received:
                latency_sec = (
                    time.time() - last_caller_speech_end
                )

                print(
                    "[Latency] Agent started speaking in: "
                    f"{latency_sec:.2f}s"
                )

                first_word_received = True
                current_agent_latency = latency_sec

            if not segment.final:
                continue

            print(
                "DEBUG: Final agent transcription received : "
                f"{text}"
            )

            first_transcription_received.set()
            turn_queue.put_nowait((text, current_agent_latency))
            current_agent_latency = None

    scenario_id = str(
        scenario_data.get("id", "unknown")
    ).replace(" ", "_")

    room_name = (
        f"qa-test-room-{scenario_id}-"
        f"{uuid.uuid4().hex[:6]}"
    )

    metadata = json.dumps(
        {
            "is_preview": True,
            "bot_external_id": bot_id,
            "scenario_id": scenario_id,
            "iana_timezone": "Asia/Kolkata",
        }
    )

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(
            f"QA_Bot_{uuid.uuid4().hex[:4]}"
        )
        .with_metadata(metadata)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
            )
        )
        .to_jwt()
    )

    try:
        print(
            f"DEBUG: Connecting to room at {livekit_url} "
            f"room={room_name}"
        )

        await asyncio.wait_for(
            room.connect(livekit_url, token),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        room_connected = True

        print("DEBUG: Successfully connected to room")

        last_caller_speech_end = time.time()

    except BaseException as exc:
        print(
            f"ERROR: Failed to connect to LiveKit room: {exc}"
        )
        await _bounded_cleanup(caller.close(), "simulated caller client close")
        raise

    turn_processor_task = asyncio.create_task(process_turn_queue())

    try:
        print(
            "DEBUG: Waiting for agent welcome transcription "
            f"(timeout={WELCOME_TIMEOUT_SECONDS}s)"
        )

        await _wait_for_event(
            first_transcription_received,
            WELCOME_TIMEOUT_SECONDS,
            "No agent welcome transcription was received within "
            f"{WELCOME_TIMEOUT_SECONDS} seconds.",
        )

        print(
            "DEBUG: Agent welcome transcription received; "
            "proceeding with conversation"
        )

        await _wait_for_event(
            test_finished,
            CONVERSATION_TIMEOUT_SECONDS,
            "Conversation did not finish within "
            f"{CONVERSATION_TIMEOUT_SECONDS} seconds.",
        )

    except asyncio.TimeoutError as exc:
        turn_error = exc
        print(f"ERROR: {turn_error}")

    finally:
        shutting_down = True

        if turn_processor_task and not turn_processor_task.done():
            turn_processor_task.cancel()
            done, _ = await asyncio.wait(
                {turn_processor_task},
                timeout=CLEANUP_TIMEOUT_SECONDS,
            )
            if not done:
                print("WARNING: Turn processor did not stop during cleanup")

        if room_connected:
            room_connected = False
            await _bounded_cleanup(room.disconnect(), "LiveKit room disconnect")
            print("DEBUG: Room disconnect requested")

    if turn_error is not None:
        await _bounded_cleanup(caller.close(), "simulated caller client close")
        raise RuntimeError(
            "LiveKit conversation did not complete successfully: "
            f"{turn_error}"
        ) from turn_error

    return caller.conversation_history, caller