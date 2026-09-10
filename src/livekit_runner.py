import asyncio
import json
import os
import time
import uuid
from typing import Optional, Tuple, List, Dict
from livekit import api, rtc
from .simulated_user import SimulatedCaller
from .tts import generate_wav_from_text, play_audio_to_agent
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()
REPLY_AUDIO_DIR = Path("replyAudioFiles")
from .config import resolve_credentials

PROJECT_ROOT = Path(__file__).resolve().parents[1]
config_json_path = PROJECT_ROOT / "data" / "livekit_bot_config.json"
default_happy_paths_yaml = PROJECT_ROOT / "data" / "happy_paths.yaml"
ENV = os.getenv("ENV") or "App3"  # Default to App3 if ENV not set



async def run_livekit_test(scenario_data: dict) -> Tuple[List[Dict], SimulatedCaller]:
    """Runs a single test case in LiveKit and returns the conversation history."""
    # Resolve credentials per-scenario so multiple YAML files are handled correctly.
    source_yaml = scenario_data.get("_source_file")
    yaml_path = Path(source_yaml) if source_yaml else default_happy_paths_yaml
    try:
        LIVEKIT_URL, API_KEY, API_SECRET, BOT_ID = resolve_credentials(ENV, config_json_path, yaml_path)
    except Exception as e:
        print(f"Configuration error for scenario {scenario_data.get('id')}: {e}")
        raise

    # Debug: print selected environment and bot id
    print(f"CONFIG: ENV={ENV}, selected BOT_ID={BOT_ID}")

    room = rtc.Room()
    caller = SimulatedCaller(scenario_data)
    test_finished = asyncio.Event()
    active_tasks = set()  # Track all created tasks
    first_transcription_received = asyncio.Event()
    # --- LATENCY TRACKER VARIABLES ---
    last_caller_speech_end = time.time()
    first_word_received = False
    current_agent_latency = None

    @room.on("transcription_received")
    def on_transcription(segments, participant=None, publication=None):
        nonlocal last_caller_speech_end, first_word_received, current_agent_latency
        if participant and participant.identity == room.local_participant.identity:
            return
        for seg in segments:

            # 1. Track Time-to-First-Word (TTFW) using the first non-final segment
            if not first_word_received and seg.text.strip():
                latency_sec = time.time() - last_caller_speech_end
                print(f"[Latency] Agent started speaking in: {latency_sec:.2f}s")
                first_word_received = True
                current_agent_latency = latency_sec
            #print(f"DEBUG: transcription_received segment text={seg.text!r} final={seg.final} participant={getattr(participant, 'identity', None)}")
            if seg.final:
                print(f"DEBUG: transcription_received segment text={seg.text!r} final={seg.final} participant={getattr(participant, 'identity', None)}")
                first_transcription_received.set()
                task = asyncio.create_task(handle_turn(seg.text))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)

    async def handle_turn(agent_text: str):
        nonlocal current_agent_latency, last_caller_speech_end, first_word_received
        try:
            print(f"DEBUG: Handling turn with agent_text: {agent_text}")
            # Intercept filler words
            ignore_phrases = [
                "wait a moment",
                "one moment please",
                "give me a second",
                "hold on",
                "please wait",
                "I'm still working on",
                "Great. I’ll proceed with that slot"
            ]

            current_agent_message = {"role": "user", "content": agent_text}
            if current_agent_latency is not None:
                current_agent_message["latency_sec"] = current_agent_latency
            caller.conversation_history.append(current_agent_message)
            current_agent_latency = None
            
            if any(phrase in agent_text.lower() for phrase in ignore_phrases):
                print("DEBUG: Ignoring filler phrase")
                first_word_received = False
                return
            

            reply = await caller.generate_reply(agent_text)
            
            if "[END_CALL]" in reply:
                reply = reply.replace("[END_CALL]", "").strip()
                test_finished.set() # Signal that the test is done
                print("DEBUG: Test finished signal set")

            if reply:
                print(f"DEBUG: Playing reply: {reply}")
                #wav_file = f"REPLY_AUDIO_DIR/temp_reply_{uuid.uuid4().hex}.wav"
                wav_file = str(REPLY_AUDIO_DIR / f"dynamic_reply_{uuid.uuid4().hex}.wav")
                await generate_wav_from_text(reply, wav_file)
                print(f"DEBUG: Generated wav file: {wav_file}")
                await play_audio_to_agent(room, wav_file)
                last_caller_speech_end = time.time()
                first_word_received = False
                print("DEBUG: Played audio to agent")
                if os.path.exists(wav_file): os.remove(wav_file) # cleanup
        except Exception as e:
            print(f"ERROR in handle_turn: {e}")
            import traceback
            traceback.print_exc()

    # Connect to room
    scenario_id = str(scenario_data.get("id", "unknown")).replace(" ", "_")
    room_name = f"qa-test-room-{scenario_id}-{uuid.uuid4().hex[:6]}"
    metadata = json.dumps(
        {
            "is_preview": True,
            "bot_external_id": BOT_ID,
            "scenario_id": scenario_id,
            "iana_timezone":"Asia/Kolkata"
        }
    )
    token = api.AccessToken(API_KEY, API_SECRET) \
        .with_identity(f"QA_Bot_{uuid.uuid4().hex[:4]}") \
        .with_metadata(metadata) \
        .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt()
    
    try:
        print(f"DEBUG: Connecting to room at {LIVEKIT_URL} room={room_name}")
        await room.connect(LIVEKIT_URL, token)
        print("DEBUG: Successfully connected to room")
        last_caller_speech_end = time.time()
    except Exception as e:
        print(f"ERROR: Failed to connect to LiveKit room: {e}")
        import traceback
        traceback.print_exc()
        raise

    # Wait for the agent's initial (welcome) transcription before replying.
    try:
        print("DEBUG: Waiting for agent welcome transcription (timeout=60s)")
        await asyncio.wait_for(first_transcription_received.wait(), timeout=60.0)
        print("DEBUG: Agent welcome transcription received; proceeding with conversation")
    except asyncio.TimeoutError:
        print("DEBUG: No welcome transcription from agent within 60s. Continuing to wait for conversation or test timeout.")

    # Wait until AI says [END_CALL] or timeout
    try:
        await asyncio.wait_for(test_finished.wait(), timeout=600.0)
    except asyncio.TimeoutError:
        print("Test timed out.")
    finally:
        # Wait for any pending tasks
        if active_tasks:
            print(f"DEBUG: Waiting for {len(active_tasks)} pending tasks...")
            await asyncio.gather(*active_tasks, return_exceptions=True)
        
        try:
            await room.disconnect()
            print("DEBUG: Room disconnected successfully")
        except Exception as e:
            print(f"ERROR: Failed to disconnect room: {e}")
        
    return caller.conversation_history, caller