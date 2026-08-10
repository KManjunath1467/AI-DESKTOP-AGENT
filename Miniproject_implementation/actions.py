"""
actions.py — All system actions for the Offline AI Desktop Agent.
=================================================================
Covers: time, date, battery, system info, app launching, screen capture,
mouse/keyboard control, alarms, volume, brightness, web search, wifi,
clipboard, window management, lock/sleep/shutdown, screenshot save.
"""

import ast
import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

import base64
import io
import ctypes

import psutil
from PIL import ImageGrab, Image
import pyautogui

pyautogui.FAILSAFE = False


def _enable_dpi_awareness() -> None:
    """Ensure coordinates match actual screen pixels on Windows."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_dpi_awareness()


def _system_drive_path() -> str:
    drive = os.environ.get("SystemDrive", "C:").rstrip("\\/")
    return drive + "\\"


# ═════════════════════════════════════════════════════════════════
# Data Classes
# ═════════════════════════════════════════════════════════════════

@dataclass
class ActionResult:
    """Standard return type for every action function."""
    message: str
    success: bool = True


@dataclass
class Alarm:
    """Represents a scheduled alarm/reminder."""
    alarm_id: str
    time_str: str          # HH:MM in 24-hour format
    label: str = "Alarm"
    active: bool = True


# ═════════════════════════════════════════════════════════════════
# Alarm / Reminder System
# ═════════════════════════════════════════════════════════════════

ALARMS_FILE = Path("alarms.json")
_alarms_lock = threading.Lock()


def _parse_time_to_datetime(time_str: str) -> Optional[datetime.datetime]:
    """Parse common spoken/written time formats into a datetime object."""
    value = (time_str or "").strip()
    if not value:
        return None

    value = re.sub(r"\s+", " ", value.upper().replace(".", ""))
    value = re.sub(r"\bA M\b", "AM", value)
    value = re.sub(r"\bP M\b", "PM", value)

    formats_to_try = [
        "%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p",
        "%H:%M:%S", "%I:%M:%S %p",
    ]
    for fmt in formats_to_try:
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue

    hour_match = re.fullmatch(r"\d{1,2}", value)
    if hour_match:
        hour = int(value)
        if 0 <= hour <= 23:
            return datetime.datetime.now().replace(
                hour=hour, minute=0, second=0, microsecond=0
            )

    return None


def _normalize_alarm_search_time(time_str: str) -> Optional[str]:
    parsed = _parse_time_to_datetime(time_str)
    return parsed.strftime("%H:%M") if parsed else None


def _load_alarms() -> List[Dict]:
    """Load alarms from the persistent JSON file."""
    try:
        if ALARMS_FILE.exists():
            with open(ALARMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            alarms = []
            for alarm in data:
                if not isinstance(alarm, dict):
                    continue
                time_str = str(alarm.get("time_str", "")).strip()
                if not re.fullmatch(r"\d{2}:\d{2}", time_str):
                    continue
                alarms.append({
                    "alarm_id": str(alarm.get("alarm_id", "")) or f"alarm_{time_str}",
                    "time_str": time_str,
                    "label": str(alarm.get("label", "Alarm")).strip() or "Alarm",
                    "date": str(alarm.get("date", "")).strip(),
                    "active": bool(alarm.get("active", True)),
                })
            return alarms
    except Exception:
        pass
    return []


def _save_alarms(alarms: List[Dict]) -> None:
    """Persist alarms to disk."""
    try:
        tmp_path = ALARMS_FILE.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(alarms, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, ALARMS_FILE)
    except Exception:
        pass


def set_alarm(time_str: str, label: str = "Alarm") -> ActionResult:
    """
    Set an alarm. time_str should be HH:MM (24-hour) or natural language
    that has already been parsed into HH:MM by the LLM.
    Supports: "14:30", "2:30 PM", "5 PM", etc.
    """
    parsed_time = _parse_time_to_datetime(time_str)

    if parsed_time is None:
        return ActionResult(
            f"I could not understand the time '{time_str}'. "
            "Please say it like '5 PM' or '14:30'.",
            success=False,
        )

    # Build the alarm time for today
    now = datetime.datetime.now()
    alarm_dt = now.replace(
        hour=parsed_time.hour, minute=parsed_time.minute,
        second=0, microsecond=0
    )
    # If that time already passed today, set it for tomorrow
    if alarm_dt <= now:
        alarm_dt += datetime.timedelta(days=1)

    alarm_time_24 = alarm_dt.strftime("%H:%M")
    display_time = alarm_dt.strftime("%I:%M %p")
    alarm_id = f"alarm_{alarm_dt.strftime('%Y%m%d_%H%M')}"

    with _alarms_lock:
        alarms = _load_alarms()
        alarms.append({
            "alarm_id": alarm_id,
            "time_str": alarm_time_24,
            "label": label,
            "date": alarm_dt.strftime("%Y-%m-%d"),
            "active": True,
        })
        _save_alarms(alarms)

    return ActionResult(f"Alarm set for {display_time}: {label}.")


def get_alarms() -> ActionResult:
    """List all active alarms."""
    alarms = _load_alarms()
    active = [a for a in alarms if a.get("active", True)]
    if not active:
        return ActionResult("You have no active alarms.")

    lines = []
    for a in active:
        try:
            t = datetime.datetime.strptime(a["time_str"], "%H:%M")
            display = t.strftime("%I:%M %p")
        except Exception:
            display = a["time_str"]
        lines.append(f"  {display} — {a.get('label', 'Alarm')}")

    return ActionResult("Your alarms:\n" + "\n".join(lines))


def cancel_alarm(label_or_time: str = "") -> ActionResult:
    """Cancel an alarm by label or time."""
    with _alarms_lock:
        alarms = _load_alarms()
        if not alarms:
            return ActionResult("You have no alarms to cancel.")

        search = label_or_time.lower().strip()
        search_time = _normalize_alarm_search_time(label_or_time)
        remaining = []
        cancelled = 0
        for a in alarms:
            if search and (
                search in a.get("label", "").lower() or
                search in a.get("time_str", "") or
                (search_time is not None and search_time == a.get("time_str", ""))
            ):
                cancelled += 1
            else:
                remaining.append(a)

        if cancelled == 0:
            # Cancel all if no match or no search term
            if not search:
                _save_alarms([])
                return ActionResult("All alarms have been cancelled.")
            return ActionResult(f"I could not find an alarm matching '{label_or_time}'.")

        _save_alarms(remaining)
        return ActionResult(f"Cancelled {cancelled} alarm(s).")


def check_alarms_due() -> Optional[Dict]:
    """Check if any alarm is due right now. Called by the alarm daemon."""
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")
    current_date = now.strftime("%Y-%m-%d")

    with _alarms_lock:
        alarms = _load_alarms()
        triggered = []
        remaining = []

        for a in alarms:
            if (
                a.get("active", True) and
                a.get("time_str") == current_time and
                (not a.get("date") or a.get("date") == current_date)
            ):
                triggered.append(a)
            else:
                remaining.append(a)

        if triggered:
            _save_alarms(remaining)

    if not triggered:
        return None
    if len(triggered) == 1:
        return triggered[0]

    labels = ", ".join(a.get("label", "Alarm") for a in triggered)
    return {
        "alarm_id": "multiple",
        "time_str": current_time,
        "label": labels,
        "active": True,
    }


# ═════════════════════════════════════════════════════════════════
# Screen Capture & Vision
# ═════════════════════════════════════════════════════════════════

def get_screen_size() -> Tuple[int, int]:
    """Returns (width, height) of primary screen."""
    try:
        return pyautogui.size()
    except Exception:
        img = ImageGrab.grab()
        return img.size


def capture_screen(max_width: int = 1280) -> Optional[str]:
    """
    Captures the full screen and returns a base64-encoded JPEG string.
    Downscales to max_width to reduce vision model token usage.
    """
    try:
        img = ImageGrab.grab(all_screens=True)  # Capture everything
    except Exception:
        img = None

    if img is None:
        try:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception as e:
            print(f"[actions] Screen capture error: {e}")
            return None

    try:
        w, h = img.size
        if w > max_width:
            ratio = max_width / w
            img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)

        # Save a debug copy so the developer can see what the AI sees
        img.convert("RGB").save("debug_vision.jpg", format="JPEG")

        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="JPEG", quality=82)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"[actions] Screen capture error: {e}")
        return None


def _grab_screen_image() -> Optional[Image.Image]:
    try:
        return ImageGrab.grab(all_screens=True)
    except Exception:
        pass

    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)
            return Image.frombytes("RGB", raw.size, raw.rgb)
    except Exception as e:
        print(f"[actions] Screen grab error: {e}")
        return None


def save_screenshot() -> ActionResult:
    """Saves a screenshot to the user's Pictures folder."""
    try:
        img = ImageGrab.grab()
        pictures = Path.home() / "Pictures" / "Screenshots"
        pictures.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = pictures / f"screenshot_{stamp}.png"
        img.save(str(path))
        return ActionResult(f"Screenshot saved to {path}.")
    except Exception as e:
        return ActionResult(f"Could not save screenshot: {e}", success=False)


# ═════════════════════════════════════════════════════════════════
# Mouse & Keyboard Control
# ═════════════════════════════════════════════════════════════════

def click_at_percent(x_pct: float, y_pct: float) -> ActionResult:
    """
    Clicks at a position expressed as a percentage of screen size.
    x_pct=50, y_pct=90 → bottom-center of screen.
    """
    try:
        w, h = get_screen_size()
        x = max(0, min(int(w * x_pct / 100), w - 1))
        y = max(0, min(int(h * y_pct / 100), h - 1))
        pyautogui.moveTo(x, y, duration=0.25)
        pyautogui.click()
        return ActionResult(f"Clicked at ({x}, {y}).")
    except Exception as e:
        return ActionResult(f"Click error: {e}", success=False)


def type_text(text: str, press_enter: bool = False) -> ActionResult:
    """Types text at the current cursor position."""
    try:
        _time.sleep(0.4)
        try:
            pyautogui.typewrite(text, interval=0.03)
        except Exception:
            pyautogui.write(text, interval=0.03)
        if press_enter:
            pyautogui.press("enter")
        return ActionResult("Text typed successfully.")
    except Exception as e:
        return ActionResult(f"Typing error: {e}", success=False)




# ═════════════════════════════════════════════════════════════════
# Volume Control (Windows)
# ═════════════════════════════════════════════════════════════════

def volume_up(steps: int = 5) -> ActionResult:
    """Increase system volume."""
    try:
        for _ in range(steps):
            pyautogui.press("volumeup")
        return ActionResult(f"Volume increased by {steps} steps.")
    except Exception as e:
        return ActionResult(f"Volume error: {e}", success=False)


def volume_down(steps: int = 5) -> ActionResult:
    """Decrease system volume."""
    try:
        for _ in range(steps):
            pyautogui.press("volumedown")
        return ActionResult(f"Volume decreased by {steps} steps.")
    except Exception as e:
        return ActionResult(f"Volume error: {e}", success=False)


def volume_mute() -> ActionResult:
    """Toggle mute."""
    try:
        pyautogui.press("volumemute")
        return ActionResult("Volume mute toggled.")
    except Exception as e:
        return ActionResult(f"Mute error: {e}", success=False)


# ═════════════════════════════════════════════════════════════════
# Brightness Control (Windows)
# ═════════════════════════════════════════════════════════════════

def brightness_up() -> ActionResult:
    """Increase screen brightness by ~10%. or anything similar to it  or just increase brightness"""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "$b=(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness;"
             "$n=[math]::Min(100,$b+10);"
             "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
             ".WmiSetBrightness(1,$n)"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return ActionResult("I could not change the brightness on this device.", success=False)
        return ActionResult("Brightness increased.")
    except Exception:
        return ActionResult("I could not change the brightness on this device.", success=False)


def brightness_down() -> ActionResult:
    """Decrease screen brightness by ~10%. or decrease brightness"""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "$b=(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness;"
             "$n=[math]::Max(0,$b-10);"
             "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
             ".WmiSetBrightness(1,$n)"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return ActionResult("I could not change the brightness on this device.", success=False)
        return ActionResult("Brightness decreased.")
    except Exception:
        return ActionResult("I could not change the brightness on this device.", success=False)


# ═════════════════════════════════════════════════════════════════
# System Power Controls
# ═════════════════════════════════════════════════════════════════

def lock_screen() -> ActionResult:
    """Lock the Windows desktop."""
    try:
        result = subprocess.run(
            ["rundll32.exe", "user32.dll,LockWorkStation"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return ActionResult("Could not lock the screen.", success=False)
        return ActionResult("Computer locked.")
    except Exception:
        return ActionResult("Could not lock the screen.", success=False)


def sleep_pc() -> ActionResult:
    """Put the computer to sleep."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Add-Type -Assembly System.Windows.Forms;"
             "[System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return ActionResult("Could not put the computer to sleep.", success=False)
        return ActionResult("Putting computer to sleep.")
    except Exception:
        return ActionResult("Could not put the computer to sleep.", success=False)


def shutdown_pc() -> ActionResult:
    """Shutdown the computer (with 30-second delay for cancellation)."""
    try:
        result = subprocess.run(["shutdown", "/s", "/t", "30"], capture_output=True, text=True, timeout=3)
        if result.returncode != 0:
            return ActionResult("Could not initiate shutdown.", success=False)
        return ActionResult(
            "Computer will shut down in 30 seconds. "
            "Say 'cancel shutdown' to stop it."
        )
    except Exception:
        return ActionResult("Could not initiate shutdown.", success=False)


def cancel_shutdown() -> ActionResult:
    """Cancel a pending shutdown."""
    try:
        result = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, timeout=3)
        if result.returncode != 0:
            return ActionResult("No pending shutdown to cancel.", success=False)
        return ActionResult("Shutdown cancelled.")
    except Exception:
        return ActionResult("No pending shutdown to cancel.", success=False)


def restart_pc() -> ActionResult:
    """Restart the computer (with 30-second delay)."""
    try:
        result = subprocess.run(["shutdown", "/r", "/t", "30"], capture_output=True, text=True, timeout=3)
        if result.returncode != 0:
            return ActionResult("Could not initiate restart.", success=False)
        return ActionResult(
            "Computer will restart in 30 seconds. "
            "Say 'cancel shutdown' to stop it."
        )
    except Exception:
        return ActionResult("Could not initiate restart.", success=False)


# ═════════════════════════════════════════════════════════════════
# Network & Connectivity
# ═════════════════════════════════════════════════════════════════

def get_wifi_status() -> ActionResult:
    """Check WiFi / network connectivity status."""
    sock = None
    try:
        import socket
        sock = socket.create_connection(("8.8.8.8", 53), timeout=3)
        return ActionResult("You are connected to the internet.")
    except OSError:
        return ActionResult("You are not connected to the internet.", success=False)
    finally:
        if sock is not None:
            sock.close()


# ═════════════════════════════════════════════════════════════════
# Clipboard
# ═════════════════════════════════════════════════════════════════

def read_clipboard() -> ActionResult:
    """Read the current clipboard text."""
    root = None
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        content = root.clipboard_get()
        if content.strip():
            preview = content[:200] + ("..." if len(content) > 200 else "")
            return ActionResult(f"Your clipboard contains: {preview}")
        return ActionResult("Your clipboard is empty.")
    except Exception:
        return ActionResult("I could not read the clipboard.", success=False)
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


# ═════════════════════════════════════════════════════════════════
# Window Management
# ═════════════════════════════════════════════════════════════════

def minimize_all_windows() -> ActionResult:
    """Minimize all open windows (show desktop)."""
    try:
        pyautogui.hotkey("win", "d")
        return ActionResult("All windows minimized.")
    except Exception:
        return ActionResult("Could not minimize windows.", success=False)


def close_current_window() -> ActionResult:
    """Close the currently focused window."""
    try:
        pyautogui.hotkey("alt", "F4")
        return ActionResult("Current window closed.")
    except Exception:
        return ActionResult("Could not close the window.", success=False)


def switch_window() -> ActionResult:
    """Switch to the next open window."""
    try:
        pyautogui.hotkey("alt", "tab")
        return ActionResult("Switched to next window.")
    except Exception:
        return ActionResult("Could not switch windows.", success=False)


# ═════════════════════════════════════════════════════════════════
# Application Launcher
# ═════════════════════════════════════════════════════════════════

APP_ALIASES = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "calci": "calc.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "google": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "browser": "msedge.exe",
    "settings": "ms-settings:",
    "paint": "mspaint.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "vlc": "vlc.exe",
    "spotify": "spotify.exe",
    "discord": "discord.exe",
    "teams": "teams.exe",
    "microsoft teams": "teams.exe",
    "zoom": "zoom.exe",
    "whatsapp": "WhatsApp.exe",
    "telegram": "Telegram.exe",
    "vs code": "code.exe",
    "visual studio code": "code.exe",
    "code": "code.exe",
    "snipping tool": "SnippingTool.exe",
    "snip": "SnippingTool.exe",
    "control panel": "control.exe",
    "clock": "ms-clock:",
    "camera": "microsoft.windows.camera:",
    "photos": "ms-photos:",
    "maps": "bingmaps:",
    "mail": "outlookmail:",
    "store": "ms-windows-store:",
    "music": "spotify.exe",
    "video": "vlc.exe",
}


def open_application(app_name: Optional[str]) -> ActionResult:
    """Open a Windows application by name with multi-strategy fallback."""
    if not app_name:
        return ActionResult("Please tell me which application to open.", success=False)

    app_name_lower = app_name.lower().strip()
    safe_app_query = re.sub(r"[^a-z0-9 ._:-]", "", app_name_lower).strip()

    # Strategy 0: AppUserModelID or Store app ID (e.g., whatsapp.root)
    if "." in app_name_lower and " " not in app_name_lower and not app_name_lower.endswith(".exe"):
        try:
            subprocess.Popen(["explorer", f"shell:AppsFolder\\{app_name_lower}"])
            return ActionResult(f"I have opened {app_name} for you.")
        except Exception:
            pass

    # Strategy 1: Known aliases (fast path)
    for key, command in APP_ALIASES.items():
        if key in app_name_lower:
            try:
                if command.startswith("ms-") or command.endswith(":"):
                    os.startfile(command)
                else:
                    subprocess.Popen(command)
                return ActionResult(f"I have opened {key} for you.")
            except Exception:
                pass

    # Strategy 2: Search PATH
    exe = app_name_lower if app_name_lower.endswith(".exe") else app_name_lower + ".exe"
    found = shutil.which(exe) or shutil.which(app_name_lower)
    if found:
        try:
            subprocess.Popen([found])
            return ActionResult(f"I have opened {app_name} for you.")
        except Exception:
            pass

    # Strategy 3: PowerShell Start-Process
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                f'Start-Process -FilePath "{app_name_lower}" -ErrorAction Stop',
            ],
            capture_output=True, timeout=8,
        )
        if result.returncode == 0:
            return ActionResult(f"I have opened {app_name} for you.")
    except Exception:
        pass

    # Strategy 4: os.startfile
    try:
        os.startfile(app_name_lower)
        return ActionResult(f"I have opened {app_name} for you.")
    except Exception:
        pass

    # Strategy 5: Start Menu shortcuts
    try:
        shortcut = _find_start_menu_shortcut(app_name_lower)
        if shortcut:
            os.startfile(shortcut)
            return ActionResult(f"I have opened {app_name} for you.")
    except Exception:
        pass

    # Strategy 6: Start Menu AppID (Store apps)
    try:
        query = safe_app_query or app_name_lower
        query_compact = re.sub(r"[^a-z0-9]", "", query)
        for q in (query, query_compact):
            if not q:
                continue
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    (
                        "$q=$args[0];"
                        "$app=(Get-StartApps | Where-Object { $_.Name -like \"*${q}*\" } | Sort-Object { $_.Name.Length } | Select-Object -First 1);"
                        "if($app){$app.AppID}"
                    ),
                    q,
                ],
                capture_output=True, text=True, timeout=8,
            )
            app_id = (result.stdout or "").strip()
            if app_id:
                subprocess.Popen(["explorer", f"shell:AppsFolder\\{app_id}"])
                return ActionResult(f"I have opened {app_name} for you.")
    except Exception:
        pass

    # Strategy 7: Windows shell start
    try:
        if not safe_app_query:
            raise ValueError("empty app search")
        subprocess.Popen(["cmd", "/c", "start", "", safe_app_query])
        return ActionResult(f"I have opened {app_name} for you.")
    except Exception:
        pass

    return ActionResult(
        f"I could not open '{app_name}'. Please make sure it is installed.",
        success=False,
    )


def _find_start_menu_shortcut(app_name_lower: str) -> Optional[str]:
    """Search Start Menu shortcuts for a matching app name."""
    if not app_name_lower:
        return None

    tokens = [t for t in re.split(r"\s+", app_name_lower) if t]
    compact_query = re.sub(r"[^a-z0-9]", "", app_name_lower)

    start_menu_dirs = []
    try:
        program_data = Path(os.environ.get("ProgramData", ""))
        if program_data:
            start_menu_dirs.append(program_data / "Microsoft/Windows/Start Menu/Programs")
    except Exception:
        pass

    try:
        app_data = Path(os.environ.get("AppData", ""))
        if app_data:
            start_menu_dirs.append(app_data / "Microsoft/Windows/Start Menu/Programs")
    except Exception:
        pass

    best_match = None
    best_score = 0
    for root in start_menu_dirs:
        if not root.exists():
            continue
        for path in root.rglob("*.lnk"):
            name = path.stem.lower()
            compact_name = re.sub(r"[^a-z0-9]", "", name)
            if app_name_lower == name:
                return str(path)
            if compact_query and compact_query == compact_name:
                return str(path)

            if not tokens:
                continue

            if not all(t in name for t in tokens):
                continue

            score = 10
            if name.startswith(app_name_lower):
                score += 5
            if compact_query and compact_name.startswith(compact_query):
                score += 3
            score += max(0, 10 - abs(len(name) - len(app_name_lower)))
            if score > best_score:
                best_score = score
                best_match = str(path)
    return best_match


def open_documents_folder() -> ActionResult:
    """Open the user's Documents folder."""
    try:
        os.startfile(str(Path.home() / "Documents"))
        return ActionResult("I have opened your Documents folder.")
    except Exception:
        return ActionResult("I could not open your Documents folder.", success=False)

def open_downloads_folder() -> ActionResult:
    """Open the user's Downloads folder."""
    try:
        os.startfile(str(Path.home() / "Downloads"))
        return ActionResult("I have opened your Downloads folder.")
    except Exception:
        return ActionResult("I could not open your Downloads folder.", success=False)


def open_desktop_folder() -> ActionResult:
    """Open the user's Desktop folder."""
    try:
        os.startfile(str(Path.home() / "Desktop"))
        return ActionResult("I have opened your Desktop folder.")
    except Exception:
        return ActionResult("I could not open your Desktop folder.", success=False)


def open_pictures_folder() -> ActionResult:
    """Open the user's Pictures folder."""
    try:
        os.startfile(str(Path.home() / "Pictures"))
        return ActionResult("I have opened your Pictures folder.")
    except Exception:
        return ActionResult("I could not open your Pictures folder.", success=False)


def open_music_folder() -> ActionResult:
    """Open the user's Music folder."""
    try:
        os.startfile(str(Path.home() / "Music"))
        return ActionResult("I have opened your Music folder.")
    except Exception:
        return ActionResult("I could not open your Music folder.", success=False)


def open_videos_folder() -> ActionResult:
    """Open the user's Videos folder."""
    try:
        os.startfile(str(Path.home() / "Videos"))
        return ActionResult("I have opened your Videos folder.")
    except Exception:
        return ActionResult("I could not open your Videos folder.", success=False)

def create_folder(folder_name: str, parent_folder: str = None) -> ActionResult:
    """Create a new folder in the user's Documents folder or specified parent."""
    if parent_folder:
        base_path = Path(parent_folder)
    else:
        base_path = Path.home() / "Documents"
    
    try:
        new_folder_path = base_path / folder_name
        new_folder_path.mkdir(exist_ok=True)
        return ActionResult(f"Created folder: {new_folder_path.resolve()}")
    except Exception as e:
        return ActionResult(f"Error creating folder: {str(e)}", success=False)
def create_file(file_name: str, parent_folder: str = None) -> ActionResult:
    """Create a new file in the user's Documents folder or specified parent."""
    if parent_folder:
        base_path = Path(parent_folder)
    else:
        base_path = Path.home() / "Documents"
    
    try:
        new_file_path = base_path / file_name
        new_file_path.touch(exist_ok=True)
        return ActionResult(f"Created file: {new_file_path.resolve()}")
    except Exception as e:
        return ActionResult(f"Error creating file: {str(e)}", success=False)
def delete_file(file_name: str, parent_folder: str = None) -> ActionResult:
    """Delete a file from the user's Documents folder or specified parent."""
    if parent_folder:
        base_path = Path(parent_folder)
    else:
        base_path = Path.home() / "Documents"
    
    try:
        file_path = base_path / file_name
        if file_path.exists():
            file_path.unlink()
            return ActionResult(f"Deleted file: {file_path.resolve()}")
        else:
            return ActionResult(f"File not found: {file_path.resolve()}", success=False)
    except Exception as e:
        return ActionResult(f"Error deleting file: {str(e)}", success=False)

def delete_folder(folder_name: str, parent_folder: str = None) -> ActionResult:
    """Delete a folder from the user's Documents folder or specified parent."""
    if parent_folder:
        base_path = Path(parent_folder)
    else:
        base_path = Path.home() / "Documents"
    
    try:
        folder_path = base_path / folder_name
        if folder_path.exists() and folder_path.is_dir():
            import shutil
            shutil.rmtree(folder_path)
            return ActionResult(f"Deleted folder: {folder_path.resolve()}")
        else:
            return ActionResult(f"Folder not found: {folder_path.resolve()}", success=False)
    except Exception as e:
        return ActionResult(f"Error deleting folder: {str(e)}", success=False)

# ═════════════════════════════════════════════════════════════════
# System Information
# ═════════════════════════════════════════════════════════════════

def get_time() -> ActionResult:
    return ActionResult(
        datetime.datetime.now().strftime("The time is %I:%M %p.")
    )


def get_date() -> ActionResult:
    return ActionResult(
        datetime.datetime.now().strftime("Today is %A, %d %B %Y.")
    )


def get_battery() -> ActionResult:
    battery = psutil.sensors_battery()
    if battery is None:
        return ActionResult("I cannot detect a battery on this device.")
    plugged = "plugged in" if battery.power_plugged else "not plugged in"
    return ActionResult(
        f"Your battery is at {battery.percent:.0f} percent and is {plugged}."
    )


def get_system_status() -> ActionResult:
    cpu = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(_system_drive_path())
    uptime_s = _time.time() - psutil.boot_time()
    hours = int(uptime_s // 3600)
    minutes = int((uptime_s % 3600) // 60)
    return ActionResult(
        f"CPU usage is {cpu} percent. "
        f"Memory usage is {mem.percent} percent. "
        f"Disk usage is {disk.percent} percent. "
        f"System uptime: {hours} hours and {minutes} minutes."
    )


def get_help_text() -> ActionResult:
    return ActionResult(
        "I can help you with:\n"
        "• Time, date, battery, system status\n"
        "• Open any application by name\n"
        "• Set alarms and reminders\n"
        "• Start timers and countdowns\n"
        "• Look at your screen and describe it\n"
        "• Click buttons, type text, fill forms\n"
        "• Volume up/down, mute, brightness up/down\n"
        "• Search Google, take screenshots\n"
        "• Lock, sleep, or shutdown your computer\n"
        "• Read your clipboard, minimize windows\n"
        "• Add and read notes or to-do items\n"
        "• Calculate math expressions\n"
        "• Tell jokes, open websites\n"
        "• Play/pause media, media controls\n"
        "• Check IP address, disk space\n"
        "• Answer any general knowledge question"
    )


# ═════════════════════════════════════════════════════════════════
# Timer / Countdown
# ═════════════════════════════════════════════════════════════════

_active_timers: Dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()
_timer_callback = None  # Set by main.py at startup


def set_timer_callback(callback):
    """Register a callback function that gets called when a timer fires."""
    global _timer_callback
    _timer_callback = callback


def start_timer(seconds: int, label: str = "Timer") -> ActionResult:
    """Start a countdown timer. When it finishes, speaks the label."""
    if seconds <= 0:
        return ActionResult("Please specify a positive number of seconds.", success=False)

    label = (label or "Timer").strip() or "Timer"

    def _timer_done():
        with _timers_lock:
            if _active_timers.get(label) is not timer:
                return
            _active_timers.pop(label, None)
        if _timer_callback:
            _timer_callback(f"⏱️ Timer done! {label} — {seconds} seconds have passed.")
    timer = threading.Timer(seconds, _timer_done)
    timer.daemon = True
    with _timers_lock:
        old_timer = _active_timers.pop(label, None)
        if old_timer:
            old_timer.cancel()
        _active_timers[label] = timer
    timer.start()

    if seconds >= 60:
        mins = seconds // 60
        secs = seconds % 60
        time_str = f"{mins} minute{'s' if mins != 1 else ''}"
        if secs:
            time_str += f" and {secs} second{'s' if secs != 1 else ''}"
    else:
        time_str = f"{seconds} second{'s' if seconds != 1 else ''}"

    return ActionResult(f"Timer set for {time_str}: {label}.")


def cancel_timer(label: str = "") -> ActionResult:
    """Cancel an active timer."""
    search = (label or "").strip().lower()
    with _timers_lock:
        if not _active_timers:
            return ActionResult("No active timers to cancel.")

        if search:
            matches = [
                timer_label for timer_label in _active_timers
                if search in timer_label.lower()
            ]
            if not matches:
                return ActionResult(f"I could not find a timer matching '{label}'.", success=False)
            for timer_label in matches:
                _active_timers[timer_label].cancel()
                del _active_timers[timer_label]
            return ActionResult(f"Cancelled {len(matches)} timer(s).")

        for t in _active_timers.values():
            t.cancel()
        _active_timers.clear()
    return ActionResult("All timers cancelled.")


# ═════════════════════════════════════════════════════════════════
# Notes / To-Do List
# ═════════════════════════════════════════════════════════════════

NOTES_FILE = Path("agent_notes.txt")


def add_note(content: str) -> ActionResult:
    """Add a note or to-do item."""
    if not content:
        return ActionResult("Please tell me what to note down.", success=False)
    try:
        timestamp = datetime.datetime.now().strftime("%d-%b %I:%M%p")
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {content}\n")
        return ActionResult(f"Noted: {content}")
    except Exception:
        return ActionResult("Could not save the note.", success=False)


def read_notes() -> ActionResult:
    """Read all saved notes."""
    try:
        if not NOTES_FILE.exists():
            return ActionResult("You have no saved notes.")
        content = NOTES_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return ActionResult("You have no saved notes.")
        lines = content.split("\n")
        recent = lines[-10:]  # Last 10 notes
        return ActionResult("Your recent notes:\n" + "\n".join(recent))
    except Exception:
        return ActionResult("Could not read notes.")


def clear_notes() -> ActionResult:
    """Clear all notes."""
    try:
        NOTES_FILE.write_text("", encoding="utf-8")
        return ActionResult("All notes cleared.")
    except Exception:
        return ActionResult("Could not clear notes.", success=False)


# ═════════════════════════════════════════════════════════════════
# Math Calculator
_MAX_MATH_ABS_VALUE = 10 ** 12
_MAX_MATH_EXPONENT = 12


def _normalize_math_expression(expression: str) -> str:
    expr = (expression or "").lower().strip()
    if not expr:
        return ""

    replacements = [
        ("to the power of", "**"),
        ("raised to", "**"),
        ("divided by", "/"),
        ("multiplied by", "*"),
        ("times", "*"),
        ("plus", "+"),
        ("minus", "-"),
        ("power", "**"),
    ]
    for word, symbol in replacements:
        expr = re.sub(rf"\b{re.escape(word)}\b", symbol, expr)

    expr = expr.replace("×", "*").replace("÷", "/")
    expr = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expr)
    expr = re.sub(r"\bx\b", "*", expr)
    expr = re.sub(r"\s+", " ", expr).strip()

    if not re.fullmatch(r"[0-9+\-*/().\s]+", expr):
        return ""
    return expr


def _safe_eval_math(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        value = node.value
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _safe_eval_math(node.operand)
        value = value if isinstance(node.op, ast.UAdd) else -value
    elif isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
    ):
        left = _safe_eval_math(node.left)
        right = _safe_eval_math(node.right)
        if isinstance(node.op, ast.Add):
            value = left + right
        elif isinstance(node.op, ast.Sub):
            value = left - right
        elif isinstance(node.op, ast.Mult):
            value = left * right
        elif isinstance(node.op, ast.Div):
            value = left / right
        else:
            if abs(right) > _MAX_MATH_EXPONENT:
                raise OverflowError("exponent too large")
            value = left ** right
    else:
        raise ValueError("unsupported math expression")

    if abs(value) > _MAX_MATH_ABS_VALUE:
        raise OverflowError("result too large")
    return value

# ═════════════════════════════════════════════════════════════════

def calculate(expression: str) -> ActionResult:
    """Evaluate a math expression safely."""
    expr = _normalize_math_expression(expression)
    if not expr:
        return ActionResult("Please give me a math expression to calculate.", success=False)

    try:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval_math(tree.body)
        if isinstance(result, float):
            result = round(result, 6)
        return ActionResult(f"The answer is {result}.")
    except ZeroDivisionError:
        return ActionResult("Cannot divide by zero.", success=False)
    except (SyntaxError, ValueError, TypeError, OverflowError):
        return ActionResult(f"I could not calculate '{expression}'.", success=False)


# ═════════════════════════════════════════════════════════════════
# Jokes (offline, no LLM needed)
# ═════════════════════════════════════════════════════════════════

import random

_JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "What's a computer's favorite snack? Microchips.",
    "Why was the computer cold? It left its Windows open.",
    "How do trees access the internet? They log in.",
    "What do you call a computer that sings? A-Dell.",
    "Why did the PowerPoint presentation cross the road? To get to the other slide.",
    "What's a computer's least favorite food? Spam.",
    "Why do Java developers wear glasses? Because they can't C sharp.",
    "What's the best thing about Boolean logic? Even if you're wrong, you're only off by a bit.",
    "Why did the developer go broke? Because he used up all his cache.",
    "How does a computer get drunk? It takes screenshots.",
    "What do computers snack on? Bytes.",
    "Why was the JavaScript developer sad? He didn't Node how to Express himself.",
    "What's a robot's favorite type of music? Heavy metal.",
    "Why did the computer go to the doctor? Because it had a virus.",
]


def tell_joke() -> ActionResult:
    """Tell a random joke."""
    return ActionResult(random.choice(_JOKES))


# ═════════════════════════════════════════════════════════════════
# Media Control Keys
# ═════════════════════════════════════════════════════════════════

def media_play_pause() -> ActionResult:
    """Press the media play/pause key."""
    try:
        pyautogui.press("space")
        time.sleep(0.1)
        pyautogui.press("space")
        return ActionResult("Play/pause toggled.")
    except Exception:
        return ActionResult("Done.", success=False)


def media_next() -> ActionResult:
    """Press the next track key."""
    try:
        pyautogui.press("nexttrack")
        return ActionResult("Skipped to next track.")
    except Exception:
        return ActionResult("Could not skip track.", success=False)


def media_prev() -> ActionResult:
    """Press the previous track key."""
    try:
        pyautogui.press("prevtrack")
        return ActionResult("Went to previous track.")
    except Exception:
        return ActionResult("Could not go to previous track.", success=False)


def media_stop() -> ActionResult:
    """Press the stop media key."""
    try:
        pyautogui.press("stop")
        return ActionResult("Media stopped.")
    except Exception:
        return ActionResult("Could not stop media.", success=False)


# ═════════════════════════════════════════════════════════════════
# Open URL / Website
# ═════════════════════════════════════════════════════════════════

def open_url(url: str) -> ActionResult:
    """Open a URL in the default browser."""
    url = (url or "").strip()
    if not url:
        return ActionResult("Please tell me which website to open.", success=False)
    try:
        import webbrowser

        url = re.sub(r"\s+", "", url)
        if "://" not in url:
            host_part = url.split("/", 1)[0].lower()
            is_local = (
                host_part == "localhost" or
                host_part.startswith("localhost:") or
                host_part.startswith("127.") or
                host_part.startswith("192.168.") or
                host_part.startswith("10.")
            )
            if "." not in host_part and ":" not in host_part and not is_local:
                url = f"{url}.com"
            url = ("http://" if is_local else "https://") + url

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ActionResult("That does not look like a valid web address.", success=False)

        final_url = parsed.geturl()
        webbrowser.open(final_url)
        return ActionResult(f"Opening {final_url}")
    except Exception:
        return ActionResult("Could not open the URL.", success=False)


# ═════════════════════════════════════════════════════════════════
# System Info (Extended)
# ═════════════════════════════════════════════════════════════════

def get_ip_address() -> ActionResult:
    """Get local IP address."""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        return ActionResult(f"Your local IP address is {ip}.")
    except Exception:
        return ActionResult("Could not determine IP address.")


def get_disk_space() -> ActionResult:
    """Check disk space on the C: drive."""
    try:
        usage = psutil.disk_usage(_system_drive_path())
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = usage.percent
        return ActionResult(
            f"Disk C: {free_gb:.1f} GB free out of {total_gb:.1f} GB total. "
            f"{used_pct}% used."
        )
    except Exception:
        return ActionResult("Could not check disk space.")


def get_uptime() -> ActionResult:
    """Get system uptime."""
    try:
        boot = datetime.datetime.fromtimestamp(psutil.boot_time())
        now = datetime.datetime.now()
        delta = now - boot
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        return ActionResult(f"System has been running for {hours} hours and {mins} minutes.")
    except Exception:
        return ActionResult("Could not determine uptime.")


def get_running_processes() -> ActionResult:
    """List top 5 processes by memory usage."""
    try:
        procs = []
        for p in psutil.process_iter(['name', 'memory_percent']):
            try:
                procs.append((p.info['name'], p.info['memory_percent']))
            except Exception:
                pass
        procs.sort(key=lambda x: x[1], reverse=True)
        top5 = procs[:5]
        lines = [f"  {name}: {mem:.1f}%" for name, mem in top5]
        return ActionResult("Top processes by memory:\n" + "\n".join(lines))
    except Exception:
        return ActionResult("Could not list processes.")


# ═════════════════════════════════════════════════════════════════
# Empty Recycle Bin
# ═════════════════════════════════════════════════════════════════

def empty_recycle_bin() -> ActionResult:
    """Empty the Windows Recycle Bin."""
    try:
        from ctypes import windll
        result = windll.shell32.SHEmptyRecycleBinW(None, None, 0x07)
        if result != 0:
            return ActionResult("Could not empty the recycle bin.", success=False)
        return ActionResult("Recycle bin emptied.")
    except Exception:
        return ActionResult("Could not empty the recycle bin.", success=False)


# ═════════════════════════════════════════════════════════════════
# Media File playback
# ═════════════════════════════════════════════════════════════════

def play_movie(target_name: str) -> ActionResult:
    """Play a movie from the Documents/movie folder using LLM to match the filename."""
    if not target_name:
        return ActionResult("Please specify a movie name to play.", success=False)
    
    try:
        movies_dir = Path.home() / "Documents" / "movie"
        if not movies_dir.exists():
            return ActionResult(f"The movie folder does not exist at {movies_dir}.", success=False)
        
        # Get list of video files
        valid_extensions = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}
        available_files = []
        for file in movies_dir.iterdir():
            if file.is_file() and file.suffix.lower() in valid_extensions:
                available_files.append(file.name)
        
        if not available_files:
            return ActionResult("No video files found in the movie folder.", success=False)
            
        import llm_router
        best_match = llm_router.match_movie_filename(target_name, available_files)
        
        if not best_match or best_match == "None":
            return ActionResult(f"Could not find a movie matching '{target_name}'.", success=False)
            
        movie_path = movies_dir / best_match
        os.startfile(str(movie_path))
        return ActionResult(f"Playing movie: {best_match}")
    except Exception as e:
        return ActionResult(f"Could not play movie: {e}", success=False)


# ═════════════════════════════════════════════════════════════════
# Utility
# ═════════════════════════════════════════════════════════════════

def is_destructive_action(action_name: Optional[str]) -> bool:
    """Returns True if the action needs user confirmation before execution."""
    return action_name in {
        "shutdown", "restart", "sleep",
        "close_window", "cancel_alarm",
        "clear_notes", "empty_recycle_bin",
    }
