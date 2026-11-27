import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Union

from openai import AzureOpenAI

DEFAULT_JUDGE_MODEL = "gpt-4o"
DEFAULT_OPENAI_API_VERSION = "2024-02-15-preview"
MAX_JUDGE_ATTEMPTS = 2

JUDGE_SYSTEM_PROMPT = (
    "You are a conversation evaluation judge. "
    "You will review the full dialogue between a CUSTOMER and an AI AGENT and decide whether the agent satisfied the expected outcomes. "
    "Apply these evaluation principles to every criterion and every scenario: read the transcript chronologically; track the customer's goals, constraints, clarifications, and changes of preference; and assess each agent turn in that context. "
    "Treat semantically equivalent wording as satisfying a requirement; do not require exact phrases unless explicitly required. "
    "Distinguish related but different facts (for example, a date being unavailable versus a time window being unavailable), and distinguish an option being offered from the customer accepting it or a task being completed. "
    "Judge only against the stated criteria and transcript evidence: do not invent requirements, infer facts that were not stated, or claim that information was omitted when it appears anywhere in the relevant conversation. "
    "For every failed criterion, cite or accurately paraphrase the relevant turn(s) and explain which stated requirement they violate. Consider later turns that clarify, update, or resolve the context; if the evidence does not establish a violation, do not assert one. "
    "Return only valid JSON with the keys: success (true/false), reasoning (string), and details (array of objects). "
    "Each details item must include id, passed, and rationale. "
    "Do not include any markdown, code fences, or extra text."
)


def _get_azure_openai_client() -> AzureOpenAI:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    model = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", DEFAULT_JUDGE_MODEL)
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_OPENAI_API_VERSION)
    logging.info(
        "Initializing Azure OpenAI client for LLM judge: endpoint=%s model=%s api_version=%s",
        endpoint,
        model,
        api_version,
    )

    print(
        f"LLM judge Azure config: endpoint={endpoint}, model={model}, api_version={api_version}, api_key_set={bool(api_key)}"
    )
    if not endpoint or not api_key:
        logging.warning(
            "Azure OpenAI endpoint or API key is missing for LLM judge: endpoint=%s api_key_set=%s",
            endpoint,
            bool(api_key),
        )
    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )


def _clean_json_response(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content, flags=re.IGNORECASE)
    return content


def _build_judge_message(
    conversation_history: List[Dict[str, str]],
    expected_outcomes: List[Dict[str, Any]],
    objective: str = "",
) -> str:
    conversation_lines = []
    for msg in conversation_history:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "").strip()
        if not content:
            continue
        conversation_lines.append(f"{role}: {content}")

    prompt_parts = [
        "Here is the full conversation transcript:\n",
        f"{chr(10).join(conversation_lines)}",
    ]

    if objective:
        prompt_parts.extend([
            "\n\nConversation objective:\n",
            f"{objective.strip()}\n",
        ])

    if expected_outcomes:
        outcomes_lines = []
        for outcome in expected_outcomes:
            outcome_id = outcome.get("id", "unknown")
            description = outcome.get("description", "").strip()
            outcomes_lines.append(f"- {outcome_id}: {description}")

        prompt_parts.extend([
            "\nExpected outcomes to judge:\n",
            f"{chr(10).join(outcomes_lines)}\n",
            "For each expected outcome, decide whether the agent satisfied it. "
            "Then return a JSON object with success, reasoning, and details.",
        ])
    else:
        prompt_parts.extend([
            "\nEvaluate the agent's behavior in the transcript on its own merits.",
            " Consider whether the agent handled the conversation appropriately,",
            " responded clearly, and achieved the objective.",
            " Return a JSON object with success, reasoning, and details.",
        ])

    return "".join(prompt_parts)


def _parse_judge_response(response_text: str) -> Dict[str, Any]:
    cleaned = _clean_json_response(response_text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        # Try to extract the first JSON object from the response text.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _is_complete_judge_response(
    parsed: Dict[str, Any],
    expected_outcomes: List[Dict[str, Any]],
) -> bool:
    if (
        not isinstance(parsed.get("success"), bool)
        or not isinstance(parsed.get("reasoning"), str)
        or not parsed["reasoning"].strip()
        or not isinstance(parsed.get("details"), list)
    ):
        return False

    details = parsed["details"]
    if expected_outcomes and len(details) < len(expected_outcomes):
        return False

    return all(
        isinstance(item, dict)
        and "id" in item
        and isinstance(item.get("passed"), bool)
        and isinstance(item.get("rationale"), str)
        for item in details
    )


def parse_exported_transcript(file_path: Union[str, Path]) -> List[Dict[str, str]]:
    """Parse a transcript file created by TranscriptExporter into conversation history."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Transcript file not found: {path}")

    conversation_history: List[Dict[str, str]] = []
    latency_pattern = re.compile(r"\s*\[Latency\s*=\s*[0-9]+(?:\.[0-9]+)?\]\s*$")

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("---"):
                continue

            if stripped.startswith("Agent:"):
                content = stripped[len("Agent:"):].strip()
                content = latency_pattern.sub("", content).strip()
                conversation_history.append({"role": "user", "content": content})
            elif stripped.startswith("Visitor:"):
                content = stripped[len("Visitor:"):].strip()
                conversation_history.append({"role": "assistant", "content": content})

    return conversation_history


def judge_transcript_file(
    file_path: Union[str, Path],
    expected_outcomes: List[Dict[str, Any]] = None,
    objective: str = "",
) -> Dict[str, Any]:
    """Judge an exported transcript file directly with the LLM judge."""
    if expected_outcomes is None:
        expected_outcomes = []
    conversation_history = parse_exported_transcript(file_path)
    return judge_conversation(conversation_history, expected_outcomes, objective)


def judge_conversation(
    conversation_history: List[Dict[str, str]],
    expected_outcomes: List[Dict[str, Any]],
    objective: str = "",
) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": _build_judge_message(conversation_history, expected_outcomes, objective)},
    ]

    try:
        model = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", DEFAULT_JUDGE_MODEL)
        client = _get_azure_openai_client()
        logging.info(
            "Sending LLM judge request: endpoint=%s model=%s messages=%d",
            os.environ.get("AZURE_OPENAI_ENDPOINT"),
            model,
            len(messages),
        )
        print(
            f"Sending LLM judge request: endpoint={os.environ.get('AZURE_OPENAI_ENDPOINT')} model={model} messages={len(messages)}"
        )
        parsed = {}
        for attempt in range(MAX_JUDGE_ATTEMPTS):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=1000,
            )
            choice = response.choices[0]
            raw_text = choice.message.content or ""
            parsed = _parse_judge_response(raw_text)
            if _is_complete_judge_response(parsed, expected_outcomes):
                logging.info("LLM judge response received successfully")
                print("LLM judge response received successfully")
                break

            logging.warning(
                "LLM judge returned an incomplete response (attempt %d/%d, finish_reason=%s)",
                attempt + 1,
                MAX_JUDGE_ATTEMPTS,
                choice.finish_reason,
            )
            if attempt + 1 < MAX_JUDGE_ATTEMPTS:
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response was incomplete or invalid. Return a complete "
                        "JSON object with a boolean success, non-empty reasoning, and a details "
                        "array containing one valid result per expected outcome."
                    ),
                })
        else:
            return {
                "success": False,
                "reasoning": (
                    "LLM judge returned an incomplete response after "
                    f"{MAX_JUDGE_ATTEMPTS} attempts."
                ),
                "details": [],
            }

        success = bool(parsed.get("success", False))
        reasoning = str(parsed.get("reasoning", "")).strip()
        details = parsed.get("details", [])

        if not isinstance(details, list):
            details = []

        normalized_details = []
        for item in details:
            if not isinstance(item, dict):
                continue
            normalized_details.append({
                "id": item.get("id"),
                "passed": bool(item.get("passed", False)),
                "rationale": str(item.get("rationale", "")).strip(),
            })

        if not normalized_details and not expected_outcomes:
            normalized_details = [
                {
                    "id": "llm_judge",
                    "passed": success,
                    "rationale": reasoning,
                }
            ]

        return {
            "success": success,
            "reasoning": reasoning or "Judge returned no reasoning.",
            "details": normalized_details,
        }
    except Exception as exc:
        logging.error("LLM judge request failed: %s", exc, exc_info=True)
        print("LLM judge request failed:", repr(exc))
        return {
            "success": False,
            "reasoning": f"LLM judge error: {exc}",
            "details": [],
        }
