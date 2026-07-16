import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Tuple, Union

from openai import AzureOpenAI

VALID_AGENT_NAMES = {
    "Intent_Detection",
    "Slot_Manager",
    "Name_Email_Agent",
    "SMS_Consent_Agent",
    "Confirmation_Agent",
    #"Booking_Hub",
    "Unknown",
}

DEFAULT_PERFORMANCE_REPORT_PATH = Path("reports/smart_agent_performance_report.csv")

TRANSCRIPT_LATENCY_PATTERN = re.compile(
    r"^\s*(?P<spoken>.*?)\s*\[Latency\s*=\s*(?P<latency>[0-9]+(?:\.[0-9]+)?)\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)

classification_cache: Dict[str, Dict[str, str]] = {}


def extract_spoken_text_latency(transcript: str) -> List[Tuple[str, float]]:
    """Extract spoken text and latency pairs from a transcript string.

    Args:
        transcript: The transcript text to scan.

    Returns:
        A list of (spoken_text, latency) tuples.
    """
    results: List[Tuple[str, float]] = []
    if not transcript:
        return results

    for match in TRANSCRIPT_LATENCY_PATTERN.finditer(transcript):
        spoken_text = match.group("spoken").strip()
        latency_str = match.group("latency").strip()
        try:
            latency_value = float(latency_str)
        except ValueError:
            continue
        if spoken_text:
            results.append((spoken_text, latency_value))

    return results


def parse_transcript_directory(directory_path: Union[str, Path]) -> List[Dict[str, Union[str, float]]]:
    """Parse all .txt transcripts in a directory and extract spoken text latency pairs.

    Args:
        directory_path: Path to the transcript directory.

    Returns:
        A list of dictionaries with keys 'spoken_text', 'latency', and 'source_file'.
    """
    transcripts_dir = Path(directory_path)
    if not transcripts_dir.exists():
        raise FileNotFoundError(f"Transcript directory not found: {transcripts_dir}")
    if not transcripts_dir.is_dir():
        raise NotADirectoryError(f"Transcript path is not a directory: {transcripts_dir}")

    extracted: List[Dict[str, Union[str, float]]] = []
    for transcript_path in sorted(transcripts_dir.glob("*.txt")):
        try:
            raw_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for spoken_text, latency in extract_spoken_text_latency(raw_text):
            extracted.append(
                {
                    "spoken_text": spoken_text,
                    "latency": latency,
                    "source_file": transcript_path.name,
                }
            )
    return extracted


def _get_azure_openai_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    )


def _clean_classification_response(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content, flags=re.IGNORECASE)
    brace_match = re.search(r"\{[^}]*\"agent_name\"[^}]*\}", content, re.DOTALL)
    if brace_match:
        return brace_match.group(0)
    return content


def _normalize_agent_name(agent_name: str) -> str:
    normalized = agent_name.strip().strip('"').lower()
    normalized = re.sub(r"[\s-]+", "_", normalized)
    mapping = {
        "intent_detection": "Intent_Detection",
        "intent detection": "Intent_Detection",
        "intentdetection": "Intent_Detection",
        "slot_manager": "Slot_Manager",
        "slot manager": "Slot_Manager",
        "slotmanager": "Slot_Manager",
        "name_email_agent": "Name_Email_Agent",
        "name email agent": "Name_Email_Agent",
        "nameemailagent": "Name_Email_Agent",
        "sms_consent_agent": "SMS_Consent_Agent",
        "sms consent agent": "SMS_Consent_Agent",
        "smsconsentagent": "SMS_Consent_Agent",
        "confirmation_agent": "Confirmation_Agent",
        "confirmation agent": "Confirmation_Agent",
        "confirmationagent": "Confirmation_Agent",
        "booking_hub": "Booking_Hub",
        "booking hub": "Booking_Hub",
        "bookinghub": "Booking_Hub",
        "unknown": "Unknown",
    }
    return mapping.get(normalized, "Unknown")


def classify_agent_llm(spoken_text: str, deployment_name: Union[str, None] = None) -> Dict[str, str]:
    """Classify the speaker utterance using Azure OpenAI and return a JSON object.

    This function memoizes identical spoken text values in an in-memory cache.

    Args:
        spoken_text: The utterance text to classify.
        deployment_name: Optional Azure OpenAI deployment/model name.

    Returns:
        A dictionary with a single key, agent_name.
    """
    normalized_text = spoken_text.strip()
    if not normalized_text:
        return {"agent_name": "Unknown"}

    cached = classification_cache.get(normalized_text)
    if cached is not None:
        return cached

    model_name = deployment_name or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    prompt = (
        "You are an automated router that classifies voice agent utterances into specific agent categories. "
        "You must map the utterance to one of these exact Agent Names based on these strict definitions:\n\n"
        "- Intent_Detection: Greets the user, asks how to assist, or determines if they want to book a meeting or leave a message.\n"
        #"- Booking_Hub: Asks the user to select a meeting category, type, or department.\n"
        "- Slot_Manager: Discusses meeting durations (15/30 mins), dates, times, timezones, cities, location preference (like online, phone, inperson) or checks calendar availability.\n"
        "- Name_Email_Agent: Asks for, spells out, or confirms the user's first name, last name, or email address.\n"
        "- SMS_Consent_Agent: Asks for a phone number or confirms if the user wants to receive SMS/text notifications.\n"
        "- Confirmation_Agent: Discusses budget, company name, confirms the final booking summary, or asks if there is anything else.\n\n"
        "If you absolutely cannot map the utterance to one of these definitions, return 'Unknown'.\n"
        "Return only valid JSON with a single key 'agent_name'. Do not include any additional text or markdown."
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Classify this utterance: {normalized_text}"},
    ]

    result: Dict[str, str] = {"agent_name": "Unknown"}
    try:
        client = _get_azure_openai_client()
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0,
            max_tokens=30,
        )
        content = _clean_classification_response(response.choices[0].message.content)

        try:
            parsed = json.loads(content)
            print(f"\n[DEBUG] Classification response for '{normalized_text}...': {parsed}")
            agent_name = str(parsed.get("agent_name", "Unknown")).strip()
        except json.JSONDecodeError:
            agent_name_match = re.search(r'"agent_name"\s*:\s*"(?P<agent>[^\"]+)"', content)
            if agent_name_match:
                agent_name = agent_name_match.group("agent").strip()
            else:
                agent_name = content.strip()

        agent_name = _normalize_agent_name(agent_name)

        result = {"agent_name": agent_name}
    except Exception as e:
        print(f"\n[!] Azure OpenAI Error for text '{normalized_text[:30]}...': {e}")
        result = {"agent_name": "Unknown"}
    finally:
        classification_cache[normalized_text] = result

    return result


def aggregate_latencies_by_agent(entries: Iterable[Dict[str, Union[str, float]]]) -> DefaultDict[str, List[float]]:
    """Aggregate latency values by classified agent name."""
    aggregated: DefaultDict[str, List[float]] = defaultdict(list)
    for entry in entries:
        spoken = str(entry.get("spoken_text", "")).strip()
        latency = entry.get("latency")
        if spoken == "" or not isinstance(latency, (int, float)):
            continue

        classification = classify_agent_llm(spoken)
        agent_name = classification.get("agent_name", "Unknown")
        aggregated[agent_name].append(float(latency))

    return aggregated


def generate_smart_agent_performance_report(
    aggregated_metrics: Dict[str, List[float]],
    output_path: Union[str, Path] = DEFAULT_PERFORMANCE_REPORT_PATH,
) -> Path:
    """Generate a performance report table and save it to CSV."""
    report_path = Path(output_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for agent_name in sorted(aggregated_metrics.keys()):
        latencies = aggregated_metrics.get(agent_name, [])
        total_calls = len(latencies)
        if total_calls > 0:
            avg_latency = sum(latencies) / total_calls
            max_latency = max(latencies)
        else:
            avg_latency = 0.0
            max_latency = 0.0

        rows.append(
            {
                "agent_name": agent_name,
                "avg_latency": avg_latency,
                "max_latency": max_latency,
                "total_calls": total_calls,
            }
        )

    column_width = max((len(row["agent_name"]) for row in rows), default=10)
    column_width = max(column_width, len("Agent Name"))
    header = ["Agent Name", "Avg Latency", "Max Latency", "Total Calls"]
    row_format = f"{{:<{column_width}}}  {{:>12}}  {{:>11}}  {{:>11}}"

    print(row_format.format(*header))
    print("-" * (column_width + 40))
    for row in rows:
        print(
            row_format.format(
                row["agent_name"],
                f"{row['avg_latency']:.3f}",
                f"{row['max_latency']:.3f}",
                row["total_calls"],
            )
        )

    with report_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)
        for row in rows:
            writer.writerow(
                [
                    row["agent_name"],
                    f"{row['avg_latency']:.3f}",
                    f"{row['max_latency']:.3f}",
                    row["total_calls"],
                ]
            )

    return report_path


def build_smart_agent_performance_report(
    transcript_dir: Union[str, Path],
    output_path: Union[str, Path] = DEFAULT_PERFORMANCE_REPORT_PATH,
) -> Path:
    """Parse transcripts, classify agents, aggregate latencies, and generate a report."""
    entries = parse_transcript_directory(transcript_dir)
    aggregated = aggregate_latencies_by_agent(entries)
    return generate_smart_agent_performance_report(aggregated, output_path)
