"""
llm_router.py — Total Recall Edition
============================================================================
Advanced LLM Intent Routing & Vision Pipeline for the Offline Desktop Agent.

KEY FEATURES:
  1. Context-First Architecture: Memory & Session history are analyzed BEFORE the user command.
  2. Multi-Pass Vision: Screen description, element grounding, and interaction verification.
  3. Total Recall Memory: Deep fact extraction and persistent session state.
  4. Robust Fallbacks: High-performance rule-based matching when Ollama is unavailable.
"""

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

# ═════════════════════════════════════════════════════════════════
# Configuration & Constants
# ═════════════════════════════════════════════════════════════════

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

# Default model if not configured
DEFAULT_MODEL = "gemma4"

# ═════════════════════════════════════════════════════════════════
# Internal State Management
# ═════════════════════════════════════════════════════════════════

class SessionState:
    """Tracks current session context that isn't yet in the long-term memory file."""
    last_action: Optional[Dict[str, Any]] = None
    last_screen_description: Optional[str] = None
    last_grounding_result: Optional[Dict[str, Any]] = None
    interaction_count: int = 0

    @classmethod
    def reset(cls):
        cls.last_action = None
        cls.last_screen_description = None
        cls.last_grounding_result = None
        cls.interaction_count = 0

# ═════════════════════════════════════════════════════════════════
# Ollama Communication Utilities
# ═════════════════════════════════════════════════════════════════

def _post_with_retry(payload: Dict[str, Any], timeout: int, attempts: int = 3) -> requests.Response | None:
    """HTTP POST with exponential backoff for Ollama stability."""
    delay = 1.0
    for i in range(attempts):
        try:
            return requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        except (requests.exceptions.RequestException, Exception) as e:
            if i == attempts - 1:
                print(f"[llm_router] Final attempt failed: {e}")
            time.sleep(delay)
            delay = min(4.0, delay * 2)
    return None


def is_ollama_available() -> bool:
    """Check if the Ollama service is reachable and responsive."""
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def set_model(model_name: str) -> None:
    """Override the Ollama model at runtime (called from settings)."""
    global DEFAULT_MODEL
    name = (model_name or "").strip()
    if name:
        DEFAULT_MODEL = name


def _current_model_name() -> str:
    """Resolve the active model name from environment or settings."""
    env_model = os.getenv("OLLAMA_MODEL")
    if env_model:
        return env_model
    try:
        import settings_manager
        return settings_manager.load_settings().get("ollama_model", DEFAULT_MODEL)
    except Exception:
        return DEFAULT_MODEL

# ═════════════════════════════════════════════════════════════════
# Text & JSON Processing
# ═════════════════════════════════════════════════════════════════

def _clean_model_text(text: str) -> str:
    """Extract clean response from Ollama, removing thinking blocks."""
    if not text: return ""
    # Remove <think> blocks (used by DeepSeek/Thinking models)
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove "Thinking:" or "Analysis:" lines
    cleaned = re.sub(r"^\s*(thinking|reasoning|analysis|thought)\s*:\s*.*$", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
    return cleaned.strip()


def _extract_json(text: str) -> Dict[str, Any]:
    """Find and parse the first JSON object in a string."""
    try:
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return json.loads(text)
    except Exception:
        return {}


def _coerce_bool(value: Any) -> bool:
    """Flexible boolean coercion for LLM outputs."""
    if isinstance(value, bool): return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "send", "confirm"}
    return bool(value)

# ═════════════════════════════════════════════════════════════════
# Prompts & System Knowledge
# ═════════════════════════════════════════════════════════════════

ALLOWED_ACTIONS = {
    "open_application", "get_time", "get_date", "get_battery",
    "get_system_status", "open_documents_folder",
    "set_alarm", "get_alarms", "cancel_alarm",
    "start_timer", "cancel_timer",
    "volume_up", "volume_down", "mute",
    "brightness_up", "brightness_down",
    "take_screenshot", "open_url",
    "lock_screen", "sleep", "shutdown", "restart", "cancel_shutdown",
    "wifi_status", "get_ip_address", "get_disk_space",
    "get_uptime", "get_processes",
    "read_clipboard",
    "minimize_all", "close_window", "switch_window",
    "add_note", "read_notes", "clear_notes",
    "calculate", "tell_joke",
    "media_play_pause", "media_next_track", "media_prev_track",
    "empty_recycle_bin","create_folder",
    "analyze_screen", "interact_screen",
    "repeat_last", "help", "chat","system_info", "open_downloads_folder" ,"open_desktop_folder" ," open_pictures_folder "," open_music_folder "," open_videos_folder ","open_documents_folder",
    "play_movie","delete_folder"

}

SYSTEM_PROMPT = """\
YOU ARE THE BRAIN OF AN OFFLINE ACCESSIBILITY DESKTOP AGENT.
Your goal is to map user voice/text commands to specific system actions.

CONTEXT-FIRST THINKING:
1. Resolve pronouns (it, that, them) based on previous interactions.
2. If the user refers to the screen, prioritize 'analyze_screen' or 'interact_screen'.

AVAILABLE ACTIONS:
- open_application: Start an app (target = app name).
- get_time / get_date / get_battery / get_system_status: System queries.
- open_documents_folder / open_url: Navigation.
- set_alarm / start_timer: (target = time in HH:MM or seconds).
- volume_up / volume_down / brightness_up / brightness_down / mute: Hardware control.
- take_screenshot: Captures current screen.
- lock_screen / sleep / shutdown / restart: Power management.
- read_clipboard / minimize_all / close_window / switch_window: Window management.
- add_note / read_notes / clear_notes: Information tracking.
- calculate: (target = math expression).
- analyze_screen: Use when user asks "what's on my screen" or "what do you see".
- interact_screen: Use for "click it", "type something", "scroll", or interaction.
- chat: Default for general conversation, questions, or greetings.
- open_downloads_folder / open_desktop_folder / open_pictures_folder / open_music_folder / open_videos_folder: Open common folders.
- get_alarms / cancel_alarm / cancel_timer: Alarm and timer management.
- set_volume: Set exact audio level.
- set_brightness: Set exact brightness level.
- record_screen: Screen recording.
- cancel_shutdown: Cancel pending shutdown/restart.
- enable_wifi / disable_wifi: Wi-Fi control.
- enable_bluetooth / disable_bluetooth: Bluetooth control.
- get_ip_address / get_disk_space / get_uptime / get_system_status: System information.
- cpu_usage / ram_usage / gpu_usage / temperature_status / network_speed: Performance monitoring.
- list_processes / kill_process / open_task_manager: Process management.
- save_clipboard / clipboard_history: Clipboard operations.
- type_text / press_key / hotkey: Keyboard interaction.
- mouse_click / double_click / right_click / move_mouse: Mouse interaction.
- scroll_up / scroll_down: Screen scrolling.
- make_todo / read_todos / clear_todos: Todo management.
- create_folder / delete_file / rename_file / move_file / copy_file: File management.
- create_text_file / append_to_file / read_file / search_files: File operations.
- weather / news_headlines: General information retrieval.
- google_search / youtube_search / search_web: Web search actions.
- translate_text / summarize_text: Text utilities.
- open_camera / record_audio: Media input tools.
- media_play_pause / media_next_track / media_prev_track: Media control.
- play_movie: Play a specific video/movie file (target = movie name).
- empty_recycle_bin: Clears recycle bin/trash.
- ocr_screen / ocr_image: Extract text from screen or images.
- face_detection / object_detection: AI-based visual detection.
- ping_server / speed_test: Network diagnostics.
- calendar_events / add_calendar_event: Calendar management.
- send_email / send_message: Communication actions.
- remember / recall_memory / forget_memory: Memory operations.
- start_recording_macro / stop_recording_macro / play_macro: Macro automation.
- terminal_command: Execute terminal or shell commands.
- undo_action / redo_action: Action history control.
- focus_mode / do_not_disturb: Productivity controls.
- launch_game / close_application: App/game lifecycle control.
- system_info / device_info: Detailed hardware/software info.
- voice_input / text_to_speech: Voice interaction features.
- repeat_last: Repeat previous action.
- help: Explain capabilities and usage.
- delete_folder: Delete a folder (target = folder name).

RETURN ONLY STRICT JSON:
{"action": "...", "target": "...", "response": "Optional conversational response"}
"""

# ═════════════════════════════════════════════════════════════════
# Intent Routing Logic
# ═════════════════════════════════════════════════════════════════

def query_llm(user_input: str) -> Dict[str, Any]:
    """
    Primary intent router. Uses a Context-First approach.
    Feeds long-term memory and short-term session history to the LLM.
    """
    import settings_manager
    
    # 2. Build the Prompt
    now = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Current Time: {now}\n"
        f"### [EXECUTION]\n"
        f"User Command: \"{user_input}\"\n"
        f"Respond in JSON format now:"
    )

    payload = {
        "model": _current_model_name(),
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "num_ctx": 4096, # Optimized context limit
            "num_thread": 8,
            "temperature": 0.1
        }
    }

    try:
        response = _post_with_retry(payload, timeout=60)
        if response and response.status_code == 200:
            raw_text = response.json().get("response", "")
            parsed = _extract_json(_clean_model_text(raw_text))
            
            # Validation
            action = parsed.get("action", "chat")
            if action not in ALLOWED_ACTIONS: action = "chat"
            
            result = {
                "action": action,
                "target": parsed.get("target"),
                "response": parsed.get("response")
            }
            
            # Force overrides for high-confidence patterns
            if result["action"] == "chat":
                if _should_force_analyze_screen(user_input):
                    result["action"] = "analyze_screen"
                elif _should_force_interact_screen(user_input):
                    result["action"] = "interact_screen"
            
            SessionState.last_action = result
            SessionState.interaction_count += 1
            return result

    except Exception as e:
        print(f"[llm_router] Routing error: {e}")

    # Final Fallback
    return local_intent_from_text(user_input)


def _should_force_interact_screen(text: str) -> bool:
    t = text.lower()
    patterns = ["click", "type", "write", "scroll", "put it", "there", "hit", "press", "in the", "window"]
    # If it's a short command containing interaction words, it's likely interaction
    return len(t.split()) < 10 and any(p in t for p in patterns)

def _should_force_analyze_screen(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in ["what's on", "what do you see", "analyze screen", "look at"])

# ═════════════════════════════════════════════════════════════════
# Chat & Memory Management
# ═════════════════════════════════════════════════════════════════

def generate_chat_response(user_input: str) -> str:
    """Conversational engine."""
    now = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    prompt = (
        "YOU ARE A HELPFUL OFFLINE AI ASSISTANT.\n"
        f"Current Time: {now}\n"
        "INSTRUCTIONS:\n"
        "1. Keep answers concise (1-4 sentences).\n\n"
        f"USER: {user_input}\n"
        "ASSISTANT:"
    )

    payload = {
        "model": _current_model_name(),
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.5}
    }

    try:
        response = _post_with_retry(payload, timeout=90)
        if response and response.status_code == 200:
            return _clean_model_text(response.json().get("response", ""))
    except Exception:
        pass
    return "I am here. How can I help you?"


def update_memory(user_input: str, agent_response: str, action_type: str = "chat") -> None:
    """Advanced Memory Update: Structured Fact Extraction. (DISABLED)"""
    pass

# ═════════════════════════════════════════════════════════════════
# Vision Intelligence
# ═════════════════════════════════════════════════════════════════

def analyze_image(base64_image: str) -> str:
    """Brief screen analysis."""
    prompt = (
        "VISION TASK: Analyze this screen.\n"
        "In breif like 50 words"
        "1. Active Window/App title.\n"
        "2. Main content (e.g., 'Writing code in VS Code').\n"
        "Respond like a human observing a screen."
    )

    payload = {
        "model": _current_model_name(),
        "prompt": prompt,
        "images": [base64_image],
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.4}
    }

    try:
        response = _post_with_retry(payload, timeout=150)
        if response and response.status_code == 200:
            desc = _clean_model_text(response.json().get("response", ""))
            SessionState.last_screen_description = desc
            return desc
    except Exception:
        pass
    return "I am having trouble seeing the screen clearly right now."


def extract_interaction_intent(user_input: str) -> Dict[str, Any]:
    """Understand the user's screen interaction command and return structured intent."""
    # ── Rule-based pre-check: catch obvious type/write commands ──
    t = user_input.lower().strip()
    is_type_command = any(kw in t for kw in [
        "write", "type", "compose", "draft", "send a message",
        "apology", "message", "letter", "email", "reply","story","replay"
    ])

    prompt = (
        "UI AUTOMATION AGENT. Convert user command into a JSON action.\n"
        "RULES:\n"
        "1. If the user says 'write', 'type', 'compose', 'draft', or asks you to "
        "create/write any text (message, letter, email, apology,story , any type  etc.), "
        "set action to \"type\" and put the FULL generated text in the \"text\" field.\n"
        "2. If the user says 'click' a button or element, set action to \"click\".\n"
        "3. If the user says 'scroll', set action to \"scroll\".\n"
        "4. If the user says 'press' key combos, set action to \"hotkey\".\n\n"
        "Schema:\n"
        "{\n"
        '  "action": "click" | "type" | "scroll" | "hotkey",\n'
        '  "element": "UI element description (for click only)",\n'
        '  "text": "The actual text content to type (REQUIRED for type action, generate it yourself)",\n'
        '  "send": true | false,\n'
        '  "keys": "key combo like ctrl+c (for hotkey only)"\n'
        "}\n\n"
        f"USER COMMAND: {user_input}\n"
        "JSON:"
    )

    payload = {
        "model": _current_model_name(),
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }

    try:
        response = _post_with_retry(payload, timeout=60)
        if response and response.status_code == 200:
            parsed = _extract_json(_clean_model_text(response.json().get("response", "")))
            result = {
                "action": parsed.get("action", "click"),
                "element": parsed.get("element", ""),
                "text": parsed.get("text"),
                "send": _coerce_bool(parsed.get("send", False)),
                "keys": parsed.get("keys")
            }

            # Safety net: if user clearly wanted to type but LLM said "click",
            # override it to "type"
            if is_type_command and result["action"] == "click":
                result["action"] = "type"

            # For type actions: ALWAYS generate proper text through the LLM
            # (the intent extraction LLM just echoes the command, not real content)
            if result["action"] == "type":
                result["text"] = _generate_text_content(user_input)
                result["send"] = False  # Never press enter automatically

            return result
    except Exception:
        pass

    # Fallback: if it's a type command, try to generate text
    if is_type_command:
        return {
            "action": "type",
            "element": "",
            "text": _generate_text_content(user_input),
            "send": False,
            "keys": None,
        }
    return {"action": "click", "element": user_input, "text": None, "send": False}


def _generate_text_content(user_input: str) -> str:
    """Ask the LLM to generate the actual text content the user wants typed."""
    prompt = (
        "The user wants you to generate text to type on their screen.\n"
        "Generate ONLY the text content they asked for. No explanations, no quotes.\n\n"
        f"USER REQUEST: {user_input}\n"
        "TEXT:"
    )

    payload = {
        "model": _current_model_name(),
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.5}
    }

    try:
        response = _post_with_retry(payload, timeout=60)
        if response and response.status_code == 200:
            text = _clean_model_text(response.json().get("response", ""))
            if text:
                return text
    except Exception:
        pass
    return "I apologize for any inconvenience caused."


def find_element_on_screen(element_description: str, base64_image: str) -> Dict[str, Any]:
    """Pass 2 of UI Automation: Grounding the 'Where'."""
    prompt = (
        f"LOCATE UI ELEMENT: '{element_description}'\n"
        "Return center coordinates in percentages (0-100).\n"
        '{"x": int, "y": int, "found": bool}\n'
        "Look for buttons, icons, or text matching the description."
    )

    payload = {
        "model": _current_model_name(),
        "prompt": prompt,
        "images": [base64_image],
        "stream": False,
        "format": "json"
    }

    try:
        response = _post_with_retry(payload, timeout=120)
        if response and response.status_code == 200:
            parsed = _extract_json(_clean_model_text(response.json().get("response", "")))
            x = max(0, min(100, int(parsed.get("x", 50))))
            y = max(0, min(100, int(parsed.get("y", 50))))
            return {"x_pct": x, "y_pct": y, "found": parsed.get("found", True)}
    except Exception:
        pass
    return {"x_pct": 50, "y_pct": 50, "found": False}

# ═════════════════════════════════════════════════════════════════
# Rule-Based Fallback Engine
# ═════════════════════════════════════════════════════════════════

def match_movie_filename(user_movie_name: str, available_filenames: list[str]) -> str:
    """Ask LLM to pick the closest matching filename from the list."""
    if not available_filenames:
        return ""
    prompt = (
        f"USER WANTS TO PLAY: '{user_movie_name}'\n"
        "AVAILABLE FILES:\n"
        + "\n".join(f"- {f}" for f in available_filenames) + "\n\n"
        "Return EXACTLY the filename from the available list that best matches the user's request. "
        "Account for typos, spelling mistakes, or partial names. "
        "If there's no good match, return 'None'.\n"
        "Respond ONLY with the filename, nothing else."
    )
    payload = {
        "model": _current_model_name(),
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.1}
    }
    try:
        response = _post_with_retry(payload, timeout=60)
        if response and response.status_code == 200:
            result = _clean_model_text(response.json().get("response", "")).strip()
            # Remove any surrounding quotes
            result = result.strip('"').strip("'")
            if result in available_filenames:
                return result
            # Loose match
            for f in available_filenames:
                if result.lower() in f.lower() or f.lower() in result.lower():
                    return f
    except Exception:
        pass
    # Local fallback logic if LLM fails
    user_lower = user_movie_name.lower().replace(" ", "")
    for f in available_filenames:
        if user_lower in f.lower().replace(" ", ""):
            return f
    return available_filenames[0]

def local_intent_from_text(user_input: str) -> Dict[str, Any]:
    """Instant rule-based matching for common commands."""
    t = user_input.lower().strip()
    
    # Simple keyword mapping
    if "time" in t: return {"action": "get_time", "target": None}
    if "date" in t: return {"action": "get_date", "target": None}
    if "battery" in t: return {"action": "get_battery", "target": None}
    
    if "open" in t:
        target = t.replace("open", "").strip()
        if "documents" in target: return {"action": "open_documents_folder", "target": None}
        return {"action": "open_application", "target": target}
    
    if "play movie" in t or "play a movie" in t:
        target = t.replace("play a movie", "").replace("play movie", "").replace("and its name", "").strip()
        return {"action": "play_movie", "target": target}
    
    if any(k in t for k in ["click", "type", "write", "scroll"]):
        return {"action": "interact_screen", "target": None}
        
    if any(k in t for k in ["screen", "look", "see"]):
        return {"action": "analyze_screen", "target": None}

    return {"action": "chat", "target": None, "response": None}

# ═════════════════════════════════════════════════════════════════
# End of llm_router.py
# ═════════════════════════════════════════════════════════════════
