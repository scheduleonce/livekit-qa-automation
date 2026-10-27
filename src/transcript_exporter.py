from datetime import datetime
import logging
from pathlib import Path
from threading import Lock

from .config import get_target_environment


class TranscriptExporter:
    # Shared across all instances within the same Python process.
    # Creates one combined transcript per run.
    _run_timestamp = datetime.now().strftime("%d-%m-%Y-%H-%M-%S-%f")
    _combined_file_lock = Lock()

    def __init__(self, output_dir: str = "transcripts"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        target_env = get_target_environment()
        self.combined_file_path = self.output_dir / (
            f"combined_transcript_{target_env}_{self._run_timestamp}.txt"
        )

    def save(self, test_id: str, conversation_history: list) -> str:
        """
        Saves the conversation history to an individual text file
        and appends it to the combined transcript.

        Includes agent latency when available.
        Returns the individual transcript file path.
        """
        if not conversation_history:
            logging.warning("No conversation history to save.")
            return ""

        timestamp = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
        target_env = get_target_environment()

        file_path = self.output_dir / (
            f"transcript_{target_env}_{test_id}_{timestamp}.txt"
        )

        try:
            lines = [
                f"--- Conversation Transcript: {test_id} ---\n\n"
            ]

            for message in conversation_history:
                role = message.get("role")
                content = message.get("content")
                latency = message.get("latency_sec")

                # Skip the hidden system prompt.
                if role == "system":
                    continue

                # LiveKit Agent is "user".
                elif role == "user":
                    if latency is not None:
                        lines.append(
                            f"Agent: {content} "
                            f"[Agent Latency = {latency}]\n\n"
                        )
                    else:
                        lines.append(f"Agent: {content}\n\n")

                # Simulated Caller is "assistant".
                elif role == "assistant":
                    lines.append(f"Visitor: {content}\n\n")

            transcript = "".join(lines)

            # Save the individual transcript as before.
            with open(file_path, "w", encoding="utf-8") as individual_file:
                individual_file.write(transcript)

            logging.info(
                f"Saved conversation transcript to {file_path}"
            )

        except Exception as exc:
            logging.error(
                f"Failed to save transcript for {test_id}: {exc}"
            )
            return ""

        # Append to the combined transcript.
        # A combined-file failure does not affect the individual file.
        try:
            with self._combined_file_lock:
                with open(
                    self.combined_file_path,
                    "a",
                    encoding="utf-8",
                ) as combined_file:
                    combined_file.write(transcript)
                    combined_file.write("=" * 80 + "\n\n")

            logging.info(
                f"Added transcript for {test_id} to "
                f"{self.combined_file_path}"
            )

        except Exception as exc:
            logging.error(
                f"Failed to append combined transcript "
                f"for {test_id}: {exc}"
            )

        return str(file_path)