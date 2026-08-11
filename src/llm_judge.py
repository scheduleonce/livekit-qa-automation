import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Union

from openai import AzureOpenAI

DEFAULT_JUDGE_MODEL = os.environ.get(
    "AZURE_OPENAI_DEPLOYMENT_NAME",
    os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
)
DEFAULT_OPENAI_API_VERSION = os.environ.get(
    "AZURE_OPENAI_API_VERSION",
    os.environ.get("OPENAI_API_VERSION", "2024-02-15-preview"),
)

JUDGE_SYSTEM_PROMPT = (
    "You are a conversation evaluation judge. "
    "You will review the full dialogue between a CUSTOMER and an AI AGENT and decide whether the agent satisfied the expected outcomes. "
    "Return only valid JSON with the keys: success (true/false), reasoning (string), and details (array of objects). "
    "Each details item must include id, passed, and rationale. "
    "Do not include any markdown, code fences, or extra text."
)


def _get_azure_openai_client() -> AzureOpenAI:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = DEFAULT_OPENAI_API_VERSION
    logging.info(
        "Initializing Azure OpenAI client for LLM judge: endpoint=%s model=%s api_version=%s",
        endpoint,
        DEFAULT_JUDGE_MODEL,
        api_version,
    )

    print(
        f"LLM judge Azure config: endpoint={endpoint}, model={DEFAULT_JUDGE_MODEL}, api_version={api_version}, api_key_set={bool(api_key)}"
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
        client = _get_azure_openai_client()
        logging.info(
            "Sending LLM judge request: endpoint=%s model=%s messages=%d",
            os.environ.get("AZURE_OPENAI_ENDPOINT"),
            DEFAULT_JUDGE_MODEL,
            len(messages),
        )
        print(
            f"Sending LLM judge request: endpoint={os.environ.get('AZURE_OPENAI_ENDPOINT')} model={DEFAULT_JUDGE_MODEL} messages={len(messages)}"
        )
        response = client.chat.completions.create(
            model=DEFAULT_JUDGE_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=500,
        )
        raw_text = response.choices[0].message.content
        logging.info("LLM judge response received successfully")
        print("LLM judge response received successfully")
        parsed = _parse_judge_response(raw_text)

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
