import pytest
from src.livekit_runner import run_livekit_session
from src.evaluator import evaluate_transcript

@pytest.mark.asyncio
async def test_agent_scenario(scenario_data):
    persona = scenario_data['persona_prompt']
    outcomes = scenario_data['expected_outcomes']
    
    transcript = await run_livekit_session(persona)
    evaluation = await evaluate_transcript(transcript, outcomes)
    
    assert evaluation["success"], evaluation["reasoning"]