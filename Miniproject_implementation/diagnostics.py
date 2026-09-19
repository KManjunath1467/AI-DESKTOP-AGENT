"""
diagnostics.py — Startup health checks for the Desktop Agent.
==============================================================
Verifies that all required Python packages, the Ollama binary,
the Ollama service, and the configured model are available.
"""

import importlib 
import os
import shutil 
from typing import List, Tuple

import requests 

PIP_INSTALL_NAMES = {
    "PIL": "Pillow",
}


def _check_dependency(module_name: str) -> Tuple[bool, str]:
    """Check if a Python module is importable."""
    try:
        importlib.import_module(module_name)
        return True, f"{module_name} is installed."
    except Exception:
        package_name = PIP_INSTALL_NAMES.get(module_name, module_name)
        return False, f"{module_name} is MISSING - run: pip install {package_name}"


def _check_ollama_binary() -> Tuple[bool, str]:
    """Check if the Ollama binary is on PATH."""
    binary = shutil.which("ollama")
    if binary:
        return True, f"Ollama binary found at {binary}."
    return False, "Ollama binary not found in PATH."


def _check_ollama_service() -> Tuple[bool, str]:
    """Check if Ollama HTTP API is reachable."""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=3)
        if response.status_code == 200:
            return True, "Ollama service is reachable."
        return False, "Ollama is running but not responding correctly."
    except Exception:
        return False, "Ollama service is not reachable on localhost:11434."


def _check_ollama_model() -> Tuple[bool, str]:
    """Check if the configured model is downloaded in Ollama."""
    model_name = _configured_model_name()
    requested = model_name.split(":")[0].strip().lower()
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=3)
        if response.status_code != 200:
            return False, "Could not verify installed Ollama models."

        models = response.json().get("models", [])
        installed = {
            m.get("name", "").strip().lower()
            for m in models
            if isinstance(m, dict)
        }
        if requested in installed or model_name.lower() in installed:
            return True, f"Ollama model '{model_name}' is available."
        return False, f"Model '{model_name}' not found. Run: ollama pull {model_name}"
    except Exception:
        return False, "Could not verify installed Ollama models."


def _configured_model_name() -> str:
    env_model = os.getenv("OLLAMA_MODEL")
    if env_model:
        return env_model
    try:
        import settings_manager
        return settings_manager.load_settings().get("ollama_model", "gemma4:e2b")
    except Exception:
        return "gemma4:e2b"




def run_startup_diagnostics() -> List[Tuple[bool, str]]:
    """Run all startup checks and return a list of (ok, message) tuples."""
    return [
        _check_dependency("faster_whisper"),
        _check_dependency("pyaudio"),
        _check_dependency("pyttsx3"),
        _check_dependency("customtkinter"),
        _check_dependency("PIL"),
        _check_dependency("pyautogui"),
        _check_dependency("psutil"),
        _check_ollama_binary(),
        _check_ollama_service(),
        _check_ollama_model(),
    ]
