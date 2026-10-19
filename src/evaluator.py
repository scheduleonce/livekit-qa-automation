import logging
import re
from typing import Any, Dict, List

from .llm_judge import judge_conversation


def evaluate_test_run(
    conversation_history: list,
    expected_outcomes: list,
    objective: str = "",
) -> dict:
    """
    Evaluates the conversation transcript against expected outcomes.

    If any outcome is configured with evaluator.type == "llm" or "llm_judge",
    or if no expected outcomes are supplied, the full conversation is
    evaluated by the LLM judge.
    """
    if not isinstance(expected_outcomes, list):
        raise ValueError("expectedOutcomes must be a list of mappings.")

    if not isinstance(conversation_history, list):
        raise ValueError("Conversation history must be a list of message mappings.")
    for index, message in enumerate(conversation_history):
        if not isinstance(message, dict):
            raise ValueError(
                f"Conversation history item {index} must be a mapping, "
                f"not {type(message).__name__}."
            )

    for index, outcome in enumerate(expected_outcomes):
        if not isinstance(outcome, dict):
            raise ValueError(
                f"expectedOutcomes[{index}] must be a mapping, "
                f"not {type(outcome).__name__}."
            )
        evaluator = outcome.get("evaluator", {})
        if not isinstance(evaluator, dict):
            raise ValueError(
                f"expectedOutcomes[{index}].evaluator must be a mapping, "
                f"not {type(evaluator).__name__}."
            )
        evaluator_type = evaluator.get("type")
        if not isinstance(evaluator_type, str) or evaluator_type not in {
            "regex",
            "llm",
            "llm_judge",
        }:
            raise ValueError(
                f"expectedOutcomes[{index}].evaluator.type must be "
                "'regex', 'llm', or 'llm_judge'."
            )

    evaluation_results: List[Dict[str, Any]] = []
    all_passed = True

    llm_judge_outcomes = [
        outcome for outcome in expected_outcomes
        if outcome.get("evaluator", {}).get("type") in {"llm", "llm_judge"}
    ]

    if llm_judge_outcomes or not expected_outcomes:
        judge_result = judge_conversation(conversation_history, expected_outcomes, objective)
        all_passed = bool(judge_result.get("success", False))
        evaluation_results = judge_result.get("details", [])

        if not all_passed:
            logging.error("LLM Judge failed: %s", judge_result.get("reasoning"))
        else:
            logging.info("LLM Judge passed conversation.")

        return {
            "success": all_passed,
            "details": evaluation_results,
            "reasoning": judge_result.get("reasoning", ""),
        }

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
            pattern = outcome["evaluator"].get("pattern", "").lower()
            
            # Check if the regex pattern exists anywhere in the agent's transcript
            passed = bool(re.search(pattern, full_agent_transcript))
            
            if not passed:
                all_passed = False
                logging.error(f"Failed Outcome: {outcome['id']} - Could not find pattern '{pattern}'")
            else:
                logging.info(f"Passed Outcome: {outcome['id']}")
                
            evaluation_results.append({
                "id": outcome["id"],
                "description": outcome.get("description", ""),
                "passed": passed
            })
            
    return {
        "success": all_passed,
        "details": evaluation_results
    }