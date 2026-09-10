"""
settings_manager.py — Persistent user settings for the Desktop Agent.
======================================================================
Reads/writes user_settings.json. Merges with defaults on load.
""" 

import json
import os
from pathlib import Path
from typing import Any, Dict

SETTINGS_PATH = Path("user_settings.json")

DEFAULT_SETTINGS = {
    "wake_words": ["computer", "assistant"],
    "speech_rate": 160,
    "ollama_model": "gemma4:e2b",
    "whisper_model": "large-v3",
    "always_use_llm": True,
}


def _validate_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return settings with safe types and sensible bounds."""
    merged = DEFAULT_SETTINGS.copy()
    if isinstance(settings, dict):
        merged.update(settings)

    wake_words = merged.get("wake_words")
    if not isinstance(wake_words, list):
        wake_words = DEFAULT_SETTINGS["wake_words"]
    wake_words = [
        str(word).strip().lower()
        for word in wake_words
        if str(word).strip()
    ]
    merged["wake_words"] = wake_words or DEFAULT_SETTINGS["wake_words"]

    try:
        speech_rate = int(merged.get("speech_rate", DEFAULT_SETTINGS["speech_rate"]))
    except (TypeError, ValueError):
        speech_rate = DEFAULT_SETTINGS["speech_rate"]
    merged["speech_rate"] = max(80, min(260, speech_rate))

    model = str(merged.get("ollama_model", "")).strip()
    merged["ollama_model"] = model or DEFAULT_SETTINGS["ollama_model"]

    whisper_model = str(merged.get("whisper_model", "")).strip()
    merged["whisper_model"] = whisper_model or DEFAULT_SETTINGS["whisper_model"]

    merged["always_use_llm"] = bool(merged.get("always_use_llm", True))
    return merged


def load_settings() -> Dict[str, Any]:
    """Load settings from disk, merging with defaults for missing keys."""
    if not SETTINGS_PATH.exists():
        settings = DEFAULT_SETTINGS.copy()
        save_settings(settings)
        return settings

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            user_settings = json.load(f)
    except Exception:
        settings = DEFAULT_SETTINGS.copy()
        save_settings(settings)
        return settings

    settings = _validate_settings(user_settings)
    if settings != user_settings:
        save_settings(settings)
    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    """Persist settings to disk."""
    clean_settings = _validate_settings(settings)
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(clean_settings, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, SETTINGS_PATH)
