from datetime import datetime
import logging
import os
import uuid
from pathlib import Path

class TranscriptExporter:
    def __init__(self, output_dir: str = "transcripts"):
        self.output_dir = Path(output_dir)
        # Create the directory when the class is initialized
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, test_id: str, conversation_history: list) -> str:
        """
        Formats and saves the conversation history to a text file.
        Returns the file path where the transcript was saved.
        """
        

        if not conversation_history:
            logging.warning("No conversation history to save.")
            return ""
        
        timestamp = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
        ENV = os.getenv("ENV")
        file_path = self.output_dir / f"transcript_{ENV}_{test_id}_{timestamp}.txt"
        
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"--- Conversation Transcript: {test_id} ---\n\n")
                
                for message in conversation_history:
                    role = message.get("role")
                    content = message.get("content")
                    latency = message.get("latency_sec")
                    
                    # Skip the hidden system prompt
                    if role == "system":
                        continue
                    # LiveKit Agent is "user", Simulated Caller is "assistant"
                    elif role == "user":
                        if latency is not None:
                            f.write(f"Agent: {content} [Latency = {latency}]\n\n")
                        else:
                            f.write(f"Agent: {content}\n\n")
                    elif role == "assistant":
                        f.write(f"Visitor: {content}\n\n")
                        
            logging.info(f"Saved conversation transcript to {file_path}")
            return str(file_path)
            
        except Exception as exc:
            logging.error(f"Failed to save transcript for {test_id}: {exc}")
            return ""