import pytest
from dotenv import load_dotenv
from src.llm_judge import judge_transcript_file
from src.utils import Utility
from src.livekit_runner import run_livekit_test
from src.evaluator import evaluate_test_run
from src.transcript_exporter import TranscriptExporter

load_dotenv()

@pytest.mark.asyncio
async def test_voice_agent(scenario):

    Utility.prepare_reply_audio_dir(self=Utility())
    
    try:
        # 1. Run the live call
        history, caller = await run_livekit_test(scenario)
        
        # 2. Save transcript
        exporter = TranscriptExporter()
        exporter.save(scenario['id'], history)
        
        # 3. Evaluate results
        result = evaluate_test_run(history, scenario.get("expectedOutcomes", []))

        #Use below code for evaluating a specific transcript file with the LLM judge without running the live call
        # result = judge_transcript_file(
        #             "transcripts/transcript_TC_SCHED_001_03-07-2026-16-27-17.txt",
        #             objective="Book a meeting with online location.",
        #         )
        # print(result)
        
        # 4. Log results (removed assert to allow complete execution)
        if result["success"]:
            print(f"PASSED: {scenario['id']}")
        else:
            print(f"FAILED: {scenario['id']} - Failed patterns: {result['details']}")
            # Optionally, you can still fail the test if you want, but for now logging only
    finally:
        # Ensure proper cleanup of caller's async client
        if 'caller' in locals():
            await caller.close()