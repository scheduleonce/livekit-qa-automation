import wave
import numpy as np

from livekit import rtc
import pyttsx3
import asyncio

def generate_wav_from_text_sync(text: str, filename: str):
    """Synchronous function to generate TTS."""
    engine = pyttsx3.init()
    
    # Optional: Slow down the speech slightly so the agent understands it better
    rate = engine.getProperty('rate')
    engine.setProperty('rate', rate - 25) 
    
    # Save to WAV file
    engine.save_to_file(text, filename)
    engine.runAndWait()

async def generate_wav_from_text(text: str, filename: str):
    """Asynchronous wrapper so it doesn't block the LiveKit event loop."""
    print(f"[TTS] Generating audio for: '{text}'...")
    await asyncio.to_thread(generate_wav_from_text_sync, text, filename)
    print(f"[TTS] Audio saved to {filename}")

async def play_audio_to_agent(room: rtc.Room, wav_filepath: str):
    """Streams a WAV file into the LiveKit room as if a human is speaking into a mic."""
    print(f"\n[Bot Mic] Starting audio stream: {wav_filepath}")
    
    with wave.open(wav_filepath, 'rb') as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        
        # 1. Create the LiveKit Audio Source and Track
        source = rtc.AudioSource(sample_rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track("bot_mic", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        
        # 2. Publish track to the room
        publication = await room.local_participant.publish_track(track, options)
        # --- Wait for the web browser to subscribe before speaking ---
        print("[Bot Mic] Waiting 1 second for UI subscription...")
        await asyncio.sleep(1.0)
        
        # 3. Stream the audio in 20ms chunks (Standard WebRTC interval)
        chunk_duration_ms = 20
        samples_per_chunk = int(sample_rate * (chunk_duration_ms / 1000.0))
        
        while True:
            raw_data = wav.readframes(samples_per_chunk)
            if not raw_data:
                break # EOF
                
            num_frames = len(raw_data) // 2 # 16-bit audio = 2 bytes per sample
            
            # Create a blank WebRTC Audio Frame
            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=num_frames // channels
            )
            
            # Copy our WAV data into the LiveKit Audio Frame
            audio_data = np.frombuffer(frame.data, dtype=np.int16)
            buffer_aux = np.frombuffer(raw_data, dtype=np.int16)
            np.copyto(audio_data, buffer_aux)
            
            # Push the frame and sleep to simulate real-time speaking
            await source.capture_frame(frame)
            await asyncio.sleep(chunk_duration_ms / 1000.0) 
            
        print("[Bot Mic] Finished speaking.")
        
        # 4. Unpublish the track when done so the agent knows you stopped talking
        await room.local_participant.unpublish_track(publication.sid)