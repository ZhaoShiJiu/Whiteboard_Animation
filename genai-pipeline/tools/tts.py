import json
import os
import re
import time
from typing import Optional

from . import utils
from .utils import _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def generate_tts_audio_tool_fn(
    text: str,
    speaker_one: str = None,
    speaker_two: str = None,
    language: str = "english",
    logger: Optional["ContextLogger"] = None,
):
    """
    Generates high-quality TTS audio from text using MiniMax Speech-2.8-HD via the AI Gateway.

    With MiniMax's native subtitle_enable, word/sentence-level timestamps are returned
    alongside the audio, making a separate transcription step unnecessary.

    Args:
        text: The text to convert to speech.
        speaker_one: Optional name of the first speaker (ignored if MiniMax voice_id is used directly).
        speaker_two: Optional name of the second speaker (ignored if MiniMax voice_id is used directly).
        language: The target language of the text to read (default: English).
        logger: Optional ContextLogger for structured logging.
    Returns:
        Tuple of (audio_path: str, subtitles_json_path: str | None).
        audio_path is the path to the generated mp3 file.
        subtitles_json_path is the path to the subtitle JSON, or None if not available.
    """

    _emit(logger, "info", f"Generating TTS audio", extra={"text_length": len(text), "language": language})

    try:
        from ai_gateway import generate

        # Strip any bracketed English pacing cues leaked by the narration refiner.
        # MiniMax TTS reads these aloud verbatim — they must be removed before synthesis.
        text = re.sub(
            r"\[(pause|softly|slowly|quickly|emphasis|whisper|loudly|slow|fast|quiet)\]",
            "",
            text,
            flags=re.IGNORECASE,
        )
        # Clean up empty brackets, collapsed spaces, and leading/trailing whitespace
        text = re.sub(r"\[\]", "", text)
        text = re.sub(r"  +", " ", text)
        text = text.strip()

        # Map speaker names to MiniMax voice_ids
        # Default: male-qn-qingse (bright male voice, good for narration)
        voice_id = "male-qn-qingse"
        if speaker_one:
            voice_id = "male-qn-qingse"
        if speaker_two:
            # For multi-speaker scenarios, we use the same voice
            # MiniMax supports voice cloning but that's beyond scope
            pass

        # Resolve language_boost from human-readable language name
        lang_boost = None
        lang_lower = language.lower() if language else "english"
        lang_map = {
            "chinese": "Chinese",
            "english": "English",
            "japanese": "Japanese",
            "korean": "Korean",
            "spanish": "Spanish",
            "french": "French",
            "german": "German",
            "auto": "auto",
        }
        lang_boost = lang_map.get(lang_lower)

        response = generate(
            task="voice",
            prompt=text,
            options={
                "voice_id": "Chinese_crisp_podcaster_nv1",
                "speed": 1.1,
                "volume": 1.1,
                "pitch": 1.0,
                "language_boost": lang_boost,
                "sample_rate": 32000,
                "format": "mp3",
                "subtitle_enable": True,
                "subtitle_type": "sentence",
            },
        )

        # Save audio bytes to file
        audio_bytes = response.content
        timestamp = int(time.time())
        filename = f"generated_audio_{timestamp}.mp3"
        output_dir = utils.GLOBAL_OUTPUT_DIR if utils.GLOBAL_OUTPUT_DIR else "."
        audio_path = os.path.join(output_dir, filename)

        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        _emit(logger, "info", "TTS audio generated",
              extra={"path": audio_path, "provider": response.provider, "model": response.model})

        # Save subtitle timestamps from MiniMax native subtitle API
        subtitles_json_path = None
        if response.subtitles:
            sub_data = {"subtitles": response.subtitles}
            sub_filename = f"tts_subtitles_{timestamp}.json"
            subtitles_json_path = os.path.join(output_dir, sub_filename)
            with open(subtitles_json_path, "w", encoding="utf-8") as f:
                json.dump(sub_data, f, indent=2, ensure_ascii=False)
            _emit(logger, "info", "TTS subtitles saved",
                  extra={"path": subtitles_json_path, "segment_count": len(response.subtitles)})

        return audio_path, subtitles_json_path

    except Exception as e:
        _emit(logger, "error", f"TTS generation failed: {e}", extra={"error": str(e)})
        return f"An error occurred during TTS generation: {str(e)}", None
