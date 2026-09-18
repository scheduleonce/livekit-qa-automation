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

    active_tasks: set[asyncio.Task] = set()

    # Conversation lifecycle flags.
    turn_in_progress = False
    shutting_down = False
    room_connected = False

    # Latency tracking.
    last_caller_speech_end = time.time()
    first_word_received = False
    current_agent_latency = None

    async def handle_turn(agent_text: str):
        """
        Handle one completed agent transcription.

        Only one turn is processed at a time to prevent multiple
        LiveKit audio tracks from being published simultaneously.
        """

        nonlocal current_agent_latency
        nonlocal last_caller_speech_end
        nonlocal first_word_received
        nonlocal turn_in_progress

        wav_file: Path | None = None
        should_end_call = False

        try:
            agent_text = agent_text.strip()

            if not agent_text:
                print("DEBUG: Ignoring empty final transcription")
                return

            print(f"DEBUG: Handling turn with agent_text: {agent_text}")

            ignore_phrases = [
                "wait a moment",
                "one moment please",
                "give me a second",
                "hold on",
                "please wait",
                "i'm still working on",
                "i’m still working on",
                "great. i'll proceed with that slot",
                "great. i’ll proceed with that slot",
            ]

            current_agent_message = {
                "role": "user",
                "content": agent_text,
            }

            if current_agent_latency is not None:
                current_agent_message["latency_sec"] = (
                    current_agent_latency
                )

            caller.conversation_history.append(
                current_agent_message
            )

            current_agent_latency = None
            normalized_text = agent_text.lower()

            if any(
                phrase in normalized_text
                for phrase in ignore_phrases
            ):
                print("DEBUG: Ignoring filler phrase")
                first_word_received = False
                return

            reply = await caller.generate_reply(agent_text)

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

                print(f"DEBUG: Playing reply: {reply}")

                wav_file = (
                    REPLY_AUDIO_DIR
                    / f"dynamic_reply_{uuid.uuid4().hex}.wav"
                )

                await generate_wav_from_text(
                    reply,
                    str(wav_file),
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

                await play_audio_to_agent(
                    room,
                    str(wav_file),
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
            print(f"ERROR in handle_turn: {exc}")

            import traceback
            traceback.print_exc()

            # Avoid waiting for the full timeout when final playback fails.
            if should_end_call:
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

            turn_in_progress = False

    @room.on("transcription_received")
    def on_transcription(
        segments,
        participant=None,
        publication=None,
    ):
        nonlocal last_caller_speech_end
        nonlocal first_word_received
        nonlocal current_agent_latency
        nonlocal turn_in_progress

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
                "DEBUG: transcription_received "
                f"segment text={text!r} "
                f"final={segment.final} "
                f"participant="
                f"{getattr(participant, 'identity', None)}"
            )

            first_transcription_received.set()

            # Prevent overlapping conversation turns.
            if turn_in_progress:
                print(
                    "DEBUG: Ignoring final transcription while "
                    f"another turn is active: {text!r}"
                )
                continue

            # Set the flag before creating the task so another event
            # cannot create a second task during the scheduling gap.
            turn_in_progress = True

            task = asyncio.create_task(
                handle_turn(text)
            )

            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)

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

        await room.connect(livekit_url, token)
        room_connected = True

        print("DEBUG: Successfully connected to room")

        last_caller_speech_end = time.time()

    except Exception as exc:
        print(
            f"ERROR: Failed to connect to LiveKit room: {exc}"
        )

        import traceback
        traceback.print_exc()

        raise

    # Wait for the initial welcome message.
    try:
        print(
            "DEBUG: Waiting for agent welcome transcription "
            "(timeout=60s)"
        )

        await asyncio.wait_for(
            first_transcription_received.wait(),
            timeout=60.0,
        )

        print(
            "DEBUG: Agent welcome transcription received; "
            "proceeding with conversation"
        )

    except asyncio.TimeoutError:
        print(
            "DEBUG: No welcome transcription from agent within "
            "60 seconds. Continuing to wait."
        )

    # Wait for [END_CALL] or the overall test timeout.
    try:
        await asyncio.wait_for(
            test_finished.wait(),
            timeout=600.0,
        )

    except asyncio.TimeoutError:
        print("Test timed out after 600 seconds.")

    finally:
        # Prevent transcription events from creating new tasks.
        shutting_down = True

        # Capture the current tasks once. A while-loop is intentionally
        # avoided because completed tasks can remain in the set until
        # their done callbacks receive another event-loop cycle.
        if active_tasks:
            pending_tasks = list(active_tasks)

            print(
                f"DEBUG: Waiting for {len(pending_tasks)} "
                "pending task(s)..."
            )

            await asyncio.gather(
                *pending_tasks,
                return_exceptions=True,
            )

            # Explicitly remove them instead of depending only on
            # the asynchronous done callback.
            active_tasks.difference_update(pending_tasks)

        if room_connected:
            try:
                room_connected = False

                await room.disconnect()

                print(
                    "DEBUG: Room disconnected successfully"
                )

            except Exception as exc:
                print(
                    "ERROR: Failed to disconnect room: "
                    f"{exc}"
                )

    return caller.conversation_history, caller