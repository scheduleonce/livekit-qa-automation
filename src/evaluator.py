import re
import logging

def evaluate_test_run(conversation_history: list, expected_outcomes: list) -> dict:
    """
    Evaluates the conversation transcript against the regex patterns defined in the JSON scenario.
    """
    # Extract only the Agent's messages to evaluate what the agent said
    agent_messages = [
        msg.get("content", "")
        for msg in conversation_history
        if msg.get("role") == "user"
    ]
    full_agent_transcript = " ".join(agent_messages).lower()
    
    evaluation_results = []
    all_passed = True
    
    for outcome in expected_outcomes:
        if outcome.get("evaluator", {}).get("type") == "regex":
            pattern = outcome["evaluator"]["pattern"].lower()
            
            # Check if the regex pattern exists anywhere in the agent's transcript
            passed = bool(re.search(pattern, full_agent_transcript))
            
            if not passed:
                all_passed = False
                logging.error(f"Failed Outcome: {outcome['id']} - Could not find pattern '{pattern}'")
            else:
                logging.info(f"Passed Outcome: {outcome['id']}")
                
            evaluation_results.append({
                "id": outcome["id"],
                "description": outcome["description"],
                "passed": passed
            })
            
    return {
        "success": all_passed,
        "details": evaluation_results
    }