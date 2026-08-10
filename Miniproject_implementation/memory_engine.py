

"""
Continuous Summary Memory — Total Recall Edition
=============================================
Single file: agent_memory.txt
- On EVERY interaction, the LLM summarizes and updates the file.
- Every prompt sent to the LLM is prefixed with the current memory.
- Memory is kept under 24000 characters. Old details are compressed.
- Also maintains a raw interaction log (agent_log.txt) for full history.
"""

from datetime import datetime
import threading
from pathlib import Path
from typing import List, Dict, Any

MEMORY_FILE = Path("agent_memory.txt")
LOG_FILE = Path("agent_log.txt")
MAX_MEMORY_CHARS = 4096
MAX_LOG_ENTRIES = 100
_file_lock = threading.Lock()


def get_memory() -> str:
    """Read the current memory notebook. Returns empty string if no memory yet."""
    try:
        with _file_lock:
            if MEMORY_FILE.exists():
                content = MEMORY_FILE.read_text(encoding="utf-8").strip()
                return content[:MAX_MEMORY_CHARS]
    except Exception:
        pass
    return ""


def write_memory(content: str) -> None:
    """Overwrite the memory notebook with new content."""
    try:
        trimmed = content.strip()[:MAX_MEMORY_CHARS]
        with _file_lock:
            tmp_path = MEMORY_FILE.with_suffix(".txt.tmp")
            tmp_path.write_text(trimmed, encoding="utf-8")
            tmp_path.replace(MEMORY_FILE)
    except Exception:
        pass


def append_log(user_input: str, agent_response: str, action_type: str = "chat") -> None:
    """
    Append a raw interaction to the log file.
    Uses a robust format that supports multi-line responses.
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Format: [Timestamp] (Type) User: Input | Agent: Response
        entry = f"[{timestamp}] ({action_type}) User: {user_input} | Agent: {agent_response}\n"

        with _file_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(entry)

            # Periodically trim the log if it gets massive (e.g. > 10,000 lines)
            if LOG_FILE.stat().st_size > 10 * 1024 * 1024: # 10MB
                lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
                if len(lines) > 2000:
                    trimmed = lines[-MAX_LOG_ENTRIES:]
                    LOG_FILE.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except Exception:
        pass


def get_log_summary(n: int = 20) -> str:
    """Get the last N complete interactions as a string."""
    try:
        with _file_lock:
            if not LOG_FILE.exists():
                return ""
            
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
            entries = []
            current_entry = []
            
            for line in lines:
                # Identify new entry by timestamp header
                if line.startswith("[") and "]" in line and ":" in line and ("User:" in line or "Agent:" in line):
                    if current_entry:
                        entries.append("\n".join(current_entry))
                    current_entry = [line]
                else:
                    if current_entry:
                        current_entry.append(line)
            
            if current_entry:
                entries.append("\n".join(current_entry))
                
            recent = entries[-n:]
            return "\n\n".join(recent)
    except Exception:
        return ""


def clear_all_memory() -> None:
    """Factory reset for the agent's memory."""
    try:
        with _file_lock:
            if MEMORY_FILE.exists(): MEMORY_FILE.unlink()
            if LOG_FILE.exists(): LOG_FILE.unlink()
    except Exception:
        pass


def get_stats() -> dict:
    """Get memory system stats."""
    try:
        with _file_lock:
            mem_size = MEMORY_FILE.stat().st_size if MEMORY_FILE.exists() else 0
            log_size = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
            return {
                "memory_size_kb": round(mem_size / 1024, 1),
                "log_size_kb": round(log_size / 1024, 1),
                "max_chars": MAX_MEMORY_CHARS
            }
    except Exception:
        return {}


def is_ready() -> bool:
    return True
