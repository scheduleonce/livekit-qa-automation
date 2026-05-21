

import logging
from pathlib import Path

REPLY_AUDIO_DIR = Path("replyAudioFiles")

class Utility:

    def prepare_reply_audio_dir(self):
        self.reply_audio_dir = REPLY_AUDIO_DIR
        self.reply_audio_dir.mkdir(parents=True, exist_ok=True)
        for item in self.reply_audio_dir.iterdir():
            if item.is_file():
                try:
                    item.unlink()
                except Exception as exc:
                    logging.warning("Could not remove old reply audio file %s: %s", item, exc)

