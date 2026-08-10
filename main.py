"""
main.py — Offline Accessibility AI Agent (Main Application)
============================================================
Entry point and GUI. Responsibilities:
  - CustomTkinter dark-themed GUI with transcript, status, help panel.
  - Voice listener startup (AudioEngine) with wake-word activation.
  - Typed command input for testing/demo.
  - Alarm daemon: background thread checks every 30 seconds for due alarms.
  - Routes every user command through llm_router.query_llm() → action handlers.
  - Direct typing at cursor position, vision-based clicking.
  - Background memory updates after every meaningful interaction.
  - Session log export.
"""

import threading
import time
import sys
import tkinter.messagebox as messagebox
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import pyautogui

import actions
import diagnostics
import llm_router
import memory_engine
from audio_engine import AudioEngine
import settings_manager


# ═════════════════════════════════════════════════════════════════
# GUI Theme
# ═════════════════════════════════════════════════════════════════

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ═════════════════════════════════════════════════════════════════
# Main Application Class
# ═════════════════════════════════════════════════════════════════

class OfflineAccessibleAgentGUI(ctk.CTk):

    def __init__(self):
        super().__init__()

        # ── Settings & Model ────────────────────────────────────
        self.settings = settings_manager.load_settings()
        llm_router.set_model(self.settings.get("ollama_model", "gemma4"))

        # ── Window ──────────────────────────────────────────────
        self.title("AI Desktop Agent")
        self.geometry("1024x720")
        self.minsize(800, 550)
        
        # Color Palette - Midnight Black & Neon
        self.configure(fg_color="#000000") # Pure black background

        # ── Grid layout ────────────────────────────────────────
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        # ── Transcript (left panel) ────────────────────────────
        transcript_frame = ctk.CTkFrame(self, fg_color="#0A0A0A", corner_radius=16, border_width=1, border_color="#1A1A1A")
        transcript_frame.grid(row=0, column=0, padx=(20, 10), pady=(20, 10), sticky="nsew")
        transcript_frame.grid_columnconfigure(0, weight=1)
        transcript_frame.grid_rowconfigure(0, weight=1)

        self.transcript = ctk.CTkTextbox(
            transcript_frame, font=("Segoe UI", 16), wrap="word",
            text_color="#F0F0F0", fg_color="transparent",
            scrollbar_button_color="#222222", scrollbar_button_hover_color="#333333"
        )
        self.transcript.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        self.command_history = []
        
        # ── Continuous Full Session Log ────────────────────────
        self.session_log_path = Path("full_session_history.txt")
        try:
            with open(self.session_log_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{'='*50}\nNEW SESSION STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*50}\n")
        except Exception:
            pass

        # ── Right panel ────────────────────────────────────────
        right = ctk.CTkFrame(self, fg_color="#0A0A0A", corner_radius=16, border_width=1, border_color="#1A1A1A")
        right.grid(row=0, column=1, padx=(10, 20), pady=(20, 10), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        self.status = ctk.CTkLabel(
            right, text="Status: Starting",
            font=("Inter", 24, "bold"), text_color="#00E5FF",
            anchor="w"
        )
        self.status.grid(row=0, column=0, padx=20, pady=(25, 5), sticky="w")

        self.llm_status = ctk.CTkLabel(
            right, text="LLM: Checking...",
            font=("Inter", 13, "bold"), text_color="#B388FF",
            anchor="w"
        )
        self.llm_status.grid(row=0, column=0, padx=20, pady=(70, 5), sticky="w")

        # Help Text (Stylized)
        help_bg = ctk.CTkFrame(right, fg_color="#121212", corner_radius=12)
        help_bg.grid(row=1, column=0, padx=15, pady=15, sticky="nsew")
        help_bg.grid_columnconfigure(0, weight=1)
        
        help_title = ctk.CTkLabel(help_bg, text="Quick Commands", font=("Inter", 14, "bold"), text_color="#A0A0A0")
        help_title.pack(anchor="w", padx=15, pady=(15, 5))

        self.help_text = ctk.CTkLabel(
            help_bg, justify="left", font=("Segoe UI", 13),
            text_color="#CCCCCC",
            text=(
                "🎙️ Hold [Ctrl] to speak\n"
                "🎵 'Open Spotify'\n"
                "⏰ 'Set alarm for 5 PM'\n"
                "📸 'Take a screenshot.'\n"
                "👁️ 'What's on my screen'\n"
                "🖱️ 'Click the play button'\n"
                "✍️ 'Write a polite email'\n"
                "🔒 'Lock the screen'\n"
                "📉 'Minimize all windows'\n"
                "🔁 'Repeat'\n"
                "❓ 'Help'"
            ),
        )
        self.help_text.pack(anchor="w", padx=15, pady=5)

        # ── Buttons ─────────────────────────────────────────────
        buttons = ctk.CTkFrame(right, fg_color="transparent")
        buttons.grid(row=2, column=0, padx=15, pady=(0, 20), sticky="ew")
        buttons.grid_columnconfigure((0, 1), weight=1)

        self.activate_btn = ctk.CTkButton(
            buttons, text="🎤 Activate", font=("Inter", 14, "bold"),
            fg_color="#00E5FF", hover_color="#00B8D4", text_color="#000000",
            corner_radius=10, height=40,
            command=self.activate_listening,
        )
        self.activate_btn.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        
        self.stop_btn = ctk.CTkButton(
            buttons, text="Stop Agent", font=("Inter", 14, "bold"),
            fg_color="#1F1F1F", hover_color="#FF3B30", text_color="#FFFFFF",
            corner_radius=10, height=40,
            command=self.on_close,
        )
        self.stop_btn.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        # ── Text input bar ──────────────────────────────────────
        input_panel = ctk.CTkFrame(self, fg_color="#0A0A0A", corner_radius=16, border_width=1, border_color="#1A1A1A")
        input_panel.grid(row=1, column=0, columnspan=2, padx=20, pady=(10, 20), sticky="ew")
        input_panel.grid_columnconfigure(0, weight=1)

        self.text_input_var = ctk.StringVar()
        self.text_input = ctk.CTkEntry(
            input_panel,
            textvariable=self.text_input_var,
            placeholder_text="Type a command here... (e.g. 'turn up the volume')",
            font=("Segoe UI", 16), fg_color="#000000", text_color="#FFFFFF",
            border_width=0, corner_radius=12, height=50
        )
        self.text_input.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="ew")
        self.text_input.bind("<Return>", lambda _: self.submit_typed_command())

        self.send_btn = ctk.CTkButton(
            input_panel, text="Send ➔", font=("Inter", 15, "bold"), width=100, height=50,
            fg_color="#7B1FA2", hover_color="#4A148C", text_color="#FFFFFF",
            corner_radius=12,
            command=self.submit_typed_command,
        )
        self.send_btn.grid(row=0, column=1, padx=(5, 10), pady=10, sticky="e")

        # ── Audio Engine ────────────────────────────────────────
        self.audio = AudioEngine(
            output_callback=self.log,
            wake_words=self.settings.get("wake_words"),
            speech_rate=int(self.settings.get("speech_rate", 160)),
            stt_model_name=self.settings.get("whisper_model", "small"),
            push_to_talk_key=self.settings.get("push_to_talk_key", "ctrl"),
        )

        # ── State ───────────────────────────────────────────────
        self.running = True
        self.last_response = "I am ready."
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ── Diagnostics (silent) ────────────────────────────────
        for ok, message in diagnostics.run_startup_diagnostics():
            print(("[OK] " if ok else "[WARN] ") + message)

        # ── Startup (background) ───────────────────────────────
        threading.Thread(target=self.startup, daemon=True).start()
        threading.Thread(target=self._llm_health_daemon, daemon=True).start()
        
        # Start Pulse Animation
        

    # ═════════════════════════════════════════════════════════════
    # GUI Helpers
    # ═════════════════════════════════════════════════════════════

    

    def set_status(self, text: str) -> None:
        try:
            self.after(0, lambda: self.status.configure(text=f"Status: {text}"))
        except Exception:
            pass

    def _append_to(self, widget: ctk.CTkTextbox, text: str) -> None:
        try:
            widget.insert("end", text + "\n")
            widget.see("end")
        except Exception:
            pass

    def log(self, text: str) -> None:
        self.command_history.append(text)
        try:
            self.after(0, lambda: self._append_to(self.transcript, text))
        except Exception:
            pass
        try:
            with open(self.session_log_path, "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%H:%M:%S")
                f.write(f"[{timestamp}] {text}\n")
        except Exception:
            pass

    def _say(self, text: str) -> None:
        """Speak + log + remember the response."""
        self.last_response = text
        self.audio.speak(text)

    def _update_memory_async(self, user_text: str, agent_text: str, action_type: str = "chat") -> None:
        """
        Fire-and-forget memory update.
        Step 1 (instant): Write raw entry to agent_log.txt
        Step 2 (background): LLM summarizes and updates agent_memory.txt
        """
        # Raw log — instant, never fails
        memory_engine.append_log(user_text, agent_text, action_type)
        # LLM-summarized memory — background thread
        threading.Thread(
            target=llm_router.update_memory,
            args=(user_text, agent_text, action_type),
            daemon=True,
        ).start()

    # ═════════════════════════════════════════════════════════════
    # Startup & Background Daemons
    # ═════════════════════════════════════════════════════════════

    def startup(self) -> None:
        self.set_status("Initializing")
        self.log("Agent: Initialization complete. I am ready.")
        self.set_status("Listening")

        # Register timer callback so timers can speak
        actions.set_timer_callback(lambda msg: self._say(msg))

        # Start alarm daemon
        threading.Thread(target=self._alarm_daemon, daemon=True).start()

        # Keep alive
        while self.running:
            time.sleep(0.2)

    def _alarm_daemon(self) -> None:
        """
        Background thread that checks for due alarms every 30 seconds.
        When an alarm fires, the agent speaks the reminder and shows a toast.
        """
        last_check_minute = -1
        while self.running:
            now = datetime.now()
            current_minute = now.hour * 60 + now.minute

            # Only check once per minute (not every 30s for the same minute)
            if current_minute != last_check_minute:
                last_check_minute = current_minute
                alarm = actions.check_alarms_due()
                if alarm:
                    label = alarm.get("label", "Alarm")
                    time_str = alarm.get("time_str", "")
                    msg = f"Alarm! {label}. The time is {time_str}."
                    self.log(f"⏰ ALARM: {msg}")
                    self._say(msg)
                    # Also try a Windows toast notification
                    try:
                        from ctypes import windll
                        windll.user32.MessageBeep(0x00000040)
                    except Exception:
                        pass
            time.sleep(30)

    def _llm_health_daemon(self) -> None:
        last_state = None
        while self.running:
            online = llm_router.is_ollama_available()
            if online != last_state:
                last_state = online
                status_text = "LLM: Online" if online else "LLM: Offline"
                color = "#00E676" if online else "#FF3B30"
                try:
                    self.after(0, lambda: self.llm_status.configure(text=status_text, text_color=color))
                except Exception:
                    pass
            time.sleep(5)

    # ═════════════════════════════════════════════════════════════
    # Confirmation Dialog (for destructive actions)
    # ═════════════════════════════════════════════════════════════

    def _ask_confirmation(self, prompt: str, source: str = "voice") -> bool:
        self.set_status("Awaiting Confirmation")
        if source == "typed":
            result = {"confirmed": False}
            done = threading.Event()

            def _show_dialog() -> None:
                try:
                    result["confirmed"] = bool(
                        messagebox.askyesno("Confirm Action", prompt, parent=self)
                    )
                except Exception:
                    result["confirmed"] = False
                finally:
                    done.set()

            try:
                self.after(0, _show_dialog)
                done.wait(timeout=60)
            finally:
                self.set_status("Listening")
            return bool(result["confirmed"])

        for attempt in range(2):
            self.audio.speak(prompt + " Please say yes or no.")
            reply = self.audio.listen_once()
            self.log(f"You (confirmation): {reply}")
            lower = reply.lower().strip()
            if "yes" in lower:
                self.set_status("Listening")
                return True
            if "no" in lower or "cancel" in lower:
                self.set_status("Listening")
                return False
            if attempt == 0:
                self.audio.speak("I did not catch that. Please answer yes or no.")
        self.set_status("Listening")
        return False

    # ═════════════════════════════════════════════════════════════
    # Command Processing (THE CORE)
    # ═════════════════════════════════════════════════════════════

    def process_command(self, text: str, source: str = "voice") -> None:
        """Route a user command to the correct action handler."""
        user_text = text.lower()
        self.log(f"You: {text}")

        # ── Exit ────────────────────────────────────────────────
        if any(w in user_text for w in ("exit", "quit", "goodbye")):
            self._say("Shutting down. Goodbye.")
            self.running = False
            self.audio.stop()
            self.after(900, self.destroy)
            return

        # ── Query LLM for intent ───────────────────────────────
        self.set_status("Thinking")
        self.log("System: Thinking...")
        decision = llm_router.query_llm(text)
        action_name = decision.get("action")
        target = decision.get("target")
        chat_response = decision.get("response")

        # ── Confirmation for destructive actions ───────────────
        if actions.is_destructive_action(action_name):
            if not self._ask_confirmation(
                "This action might change your system state. Continue?",
                source=source,
            ):
                self._say("Okay, I have cancelled that.")
                return

        # ───────────────────────────────────────────────────────
        # Action Handlers
        # ───────────────────────────────────────────────────────

        # ── Time / Date / Battery / System ─────────────────────
        if action_name == "get_time":
            result = actions.get_time()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        elif action_name == "get_date":
            result = actions.get_date()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        elif action_name == "get_battery":
            result = actions.get_battery()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        elif action_name == "get_system_status":
            result = actions.get_system_status()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        # ── Alarms ─────────────────────────────────────────────
        elif action_name == "set_alarm":
            if not target:
                self._say("Please specify a time for the alarm, like 5 PM or 14:30.")
            else:
                label = chat_response or "Alarm"
                result = actions.set_alarm(target, label)
                self._say(result.message)
                self._update_memory_async(text, result.message, "set_alarm")

        elif action_name == "get_alarms":
            result = actions.get_alarms()
            self._say(result.message)
            self._update_memory_async(text, result.message, "alarm")

        elif action_name == "cancel_alarm":
            result = actions.cancel_alarm(target or "")
            self._say(result.message)
            self._update_memory_async(text, result.message, "alarm")

        # ── Volume ─────────────────────────────────────────────
        elif action_name == "volume_up":
            result = actions.volume_up()
            self._say(result.message)
            self._update_memory_async(text, result.message, "volume")

        elif action_name == "volume_down":
            result = actions.volume_down()
            self._say(result.message)
            self._update_memory_async(text, result.message, "volume")

        elif action_name == "mute":
            result = actions.volume_mute()
            self._say(result.message)
            self._update_memory_async(text, result.message, "volume")

        # ── Brightness ─────────────────────────────────────────
        elif action_name == "brightness_up":
            result = actions.brightness_up()
            self._say(result.message)
            self._update_memory_async(text, result.message, "brightness")

        elif action_name == "brightness_down":
            result = actions.brightness_down()
            self._say(result.message)
            self._update_memory_async(text, result.message, "brightness")

        # ── Screenshot ─────────────────────────────────────────
        elif action_name == "take_screenshot":
            result = actions.save_screenshot()
            self._say(result.message)
            self._update_memory_async(text, result.message, "screenshot")

        # ── Power Controls ─────────────────────────────────────
        elif action_name == "lock_screen":
            self._say("Locking the screen now.")
            self._update_memory_async(text, "Locked screen", "power")
            actions.lock_screen()

        elif action_name == "sleep":
            result = actions.sleep_pc()
            self._say(result.message)
            self._update_memory_async(text, result.message, "power")

        elif action_name == "shutdown":
            result = actions.shutdown_pc()
            self._say(result.message)
            self._update_memory_async(text, result.message, "power")

        elif action_name == "restart":
            result = actions.restart_pc()
            self._say(result.message)
            self._update_memory_async(text, result.message, "power")

        elif action_name == "cancel_shutdown":
            result = actions.cancel_shutdown()
            self._say(result.message)
            self._update_memory_async(text, result.message, "power")

        # ── Network ────────────────────────────────────────────
        elif action_name == "wifi_status":
            result = actions.get_wifi_status()
            self._say(result.message)
            self._update_memory_async(text, result.message, "network")

        # ── Clipboard ──────────────────────────────────────────
        elif action_name == "read_clipboard":
            result = actions.read_clipboard()
            self._say(result.message)
            self._update_memory_async(text, result.message, "clipboard")

        # ── Window Management ──────────────────────────────────
        elif action_name == "minimize_all":
            result = actions.minimize_all_windows()
            self._say(result.message)
            self._update_memory_async(text, result.message, "window")

        elif action_name == "close_window":
            actions.close_current_window()
            self._say("Window closed.")
            self._update_memory_async(text, "Closed current window", "window")

        elif action_name == "switch_window":
            actions.switch_window()
            self._say("Switched to next window.")
            self._update_memory_async(text, "Switched window", "window")

        # ── Timer ──────────────────────────────────────────────
        elif action_name == "start_timer":
            try:
                secs = int(target) if target else 60
            except (ValueError, TypeError):
                secs = 60
            label = chat_response or "Timer"
            result = actions.start_timer(secs, label)
            self._say(result.message)
            self._update_memory_async(text, result.message, "timer")

        elif action_name == "cancel_timer":
            result = actions.cancel_timer(target or "")
            self._say(result.message)
            self._update_memory_async(text, result.message, "timer")

        # ── Notes / To-Do ──────────────────────────────────────
        elif action_name == "add_note":
            result = actions.add_note(target or "")
            self._say(result.message)
            self._update_memory_async(text, result.message, "notes")

        elif action_name == "read_notes":
            result = actions.read_notes()
            self._say(result.message)
            self._update_memory_async(text, result.message, "notes")

        elif action_name == "clear_notes":
            result = actions.clear_notes()
            self._say(result.message)
            self._update_memory_async(text, result.message, "notes")

        # ── Math Calculator ────────────────────────────────────
        elif action_name == "calculate":
            result = actions.calculate(target or "")
            self._say(result.message)
            self._update_memory_async(text, result.message, "calculate")

        # ── Jokes ──────────────────────────────────────────────
        elif action_name == "tell_joke":
            result = actions.tell_joke()
            self._say(result.message)
            self._update_memory_async(text, result.message, "joke")

        # ── Media Controls ─────────────────────────────────────
        elif action_name == "media_play_pause":
            result = actions.media_play_pause()
            self._say(result.message)
            self._update_memory_async(text, result.message, "media")

        elif action_name == "media_next_track":
            result = actions.media_next()
            self._say(result.message)
            self._update_memory_async(text, result.message, "media")

        elif action_name == "media_prev_track":
            result = actions.media_prev()
            self._say(result.message)
            self._update_memory_async(text, result.message, "media")

        elif action_name == "play_movie":
            result = actions.play_movie(target or "")
            self._say(result.message)
            self._update_memory_async(text, result.message, "media")

        # ── Open URL ───────────────────────────────────────────
        elif action_name == "open_url":
            result = actions.open_url(target or "")
            self._say(result.message)
            self._update_memory_async(text, result.message, "web")

        # ── Extended System Info ───────────────────────────────
        elif action_name == "get_ip_address":
            result = actions.get_ip_address()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        elif action_name == "get_disk_space":
            result = actions.get_disk_space()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        elif action_name == "get_uptime":
            result = actions.get_uptime()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        elif action_name == "get_processes":
            result = actions.get_running_processes()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system_info")

        # ── Recycle Bin ────────────────────────────────────────
        elif action_name == "empty_recycle_bin":
            result = actions.empty_recycle_bin()
            self._say(result.message)
            self._update_memory_async(text, result.message, "system")

        # ── Open Application ───────────────────────────────────
        elif action_name == "open_application":
            result = actions.open_application(target)
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")

        elif action_name == "open_documents_folder":
            result = actions.open_documents_folder()
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")
        elif action_name =="open_downloads_folder":
            result = actions.open_downloads_folder()
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")
        elif action_name =="open_desktop_folder":
            result = actions.open_desktop_folder()
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")
        elif action_name =="open_pictures_folder":
            result = actions.open_pictures_folder()
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")
        elif action_name =="open_music_folder":
            result = actions.open_music_folder()
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")
        elif action_name =="open_videos_folder":
            result = actions.open_videos_folder()
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")
        elif action_name =="create_folder":
            result = actions.create_folder(target)
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")
        elif action_name =="delete_folder":
            result = actions.delete_folder(target)
            self._say(result.message)
            self._update_memory_async(text, result.message, "open_app")

        # ── Repeat / Help ──────────────────────────────────────
        elif action_name == "repeat_last":
            self.audio.speak(self.last_response)

        elif action_name == "help":
            self._say(actions.get_help_text().message)

        # ── Analyze Screen (Vision Pass 0) ─────────────────────
        elif action_name == "analyze_screen":
            self.set_status("Analyzing Screen")
            self._say("Let me look at your screen.")
            self.after(0, lambda: self.wm_state("iconic"))  # Hide agent
            time.sleep(1.0)          # Wait for redraw
            img_b64 = actions.capture_screen()
            self.after(0, lambda: self.wm_state("normal"))  # Show agent again
            if not img_b64:
                self._say("I could not capture the screen.")
            else:
                self.log("System: Querying vision model...")
                response = llm_router.analyze_image(img_b64)
                self._say(response)
                self._update_memory_async(text, response, "screen_analysis")

        # ── Interact with Screen (Vision Pass 1+2) ─────────────
        elif action_name == "interact_screen":
            self._handle_interact_screen(text)

        # ── Chat (General Q&A) ─────────────────────────────────
        elif action_name == "chat":
            if chat_response:
                self._say(chat_response)
                self._update_memory_async(text, chat_response, "chat")
            else:
                self.log("System: Generating answer...")
                answer = llm_router.generate_chat_response(text)
                if answer:
                    self._say(answer)
                    self._update_memory_async(text, answer, "chat")
                else:
                    self._say(
                        "I need my AI engine to answer that question, but it seems to be offline. "
                        "I can still do system tasks like opening apps, checking the time, "
                        "setting alarms, or controlling volume. Try one of those!"
                    )

        else:
            self._say("I did not understand the request. Please try again slowly.")

        self.set_status("Listening")

    # ═════════════════════════════════════════════════════════════
    # Screen Interaction Handler
    # ═════════════════════════════════════════════════════════════

    def _handle_interact_screen(self, text: str) -> None:
        """
        Screen interaction handler.
          - type/write: Types directly at the current cursor position.
          - hotkey: Presses key combinations.
          - click: Uses vision to find and click UI elements.
          - scroll: Scrolls the page up or down.
        """
        self.set_status("Understanding Intent...")
        self.log("System: Understanding your command...")

        # ── Understand WHAT to do ───────────────────────────────
        intent = llm_router.extract_interaction_intent(text)
        action_type = intent.get("action", "click")
        element_desc = intent.get("element", "")
        type_text = intent.get("text")
        should_send = intent.get("send", False)
        hotkeys = intent.get("keys")

        self.log(f"System: Intent → action={action_type}, element='{element_desc}'")
        if type_text:
            preview = type_text[:60] + "..." if len(type_text) > 60 else type_text
            self.log(f"System: Will type: '{preview}'")
        if hotkeys:
            self.log(f"System: Hotkey: {hotkeys}")

        # ── Hotkey: press key combo immediately ─────────────────
        if action_type == "hotkey" and hotkeys:
            try:
                keys = [k.strip() for k in hotkeys.split("+")]
                pyautogui.hotkey(*keys)
                self._say(f"Done. I pressed {hotkeys}.")
                self._update_memory_async(text, f"Pressed hotkey: {hotkeys}", "screen_interaction")
            except Exception as e:
                self._say(f"Could not press {hotkeys}: {e}")
            return

        # ── Type: type directly at current cursor position ──────
        if action_type in ("type", "click_and_type") and type_text:
            self._say("Let me type that.")
            try:
                try:
                    self.after(0, lambda: self.wm_state("iconic"))
                except Exception:
                    pass
                time.sleep(0.3)
                actions.type_text(type_text, press_enter=should_send)
                try:
                    self.after(0, lambda: self.wm_state("normal"))
                except Exception:
                    pass
                self._say("Done. I have typed your message.")
                self._update_memory_async(
                    text,
                    f"Typed text: '{type_text[:120]}'",
                    "screen_interaction",
                )
            except Exception as e:
                self._say(f"Could not type that text: {e}")
            return

        # ── Scroll: no vision needed ────────────────────────────
        if action_type in ("scroll_up", "scroll"):
            pyautogui.scroll(5)
            self._say("Scrolled up.")
            self._update_memory_async(text, "Scrolled up", "screen_interaction")
            return
        elif action_type == "scroll_down":
            pyautogui.scroll(-5)
            self._say("Scrolled down.")
            self._update_memory_async(text, "Scrolled down", "screen_interaction")
            return

        # ── Click: use vision to find the element on screen ─────
        self._say("Let me find that on the screen.")

        # Minimize agent, capture screen
        self.after(0, lambda: self.wm_state("iconic"))
        time.sleep(0.5)
        img_b64 = actions.capture_screen()

        if not img_b64:
            self.after(0, lambda: self.wm_state("normal"))
            self._say("I could not capture the screen.")
            return

        self.set_status("Locating Element...")
        self.log(f"System: Searching for '{element_desc}' on screen...")

        location = llm_router.find_element_on_screen(element_desc, img_b64)
        x_pct = location.get("x_pct", 50)
        y_pct = location.get("y_pct", 50)
        found = location.get("found", True)

        self.log(f"System: Found={found}, pos=({x_pct:.1f}%, {y_pct:.1f}%)")

        if not found:
            self.after(0, lambda: self.wm_state("normal"))
            self._say(f"I could not find {element_desc or 'that element'} on the screen.")
            self._update_memory_async(
                text,
                f"Could not find '{element_desc}' for screen interaction",
                "screen_interaction",
            )
            return

        actions.click_at_percent(x_pct, y_pct)
        self.after(0, lambda: self.wm_state("normal"))

        self._say(
            f"Done. I clicked on {element_desc or 'the element'}."
        )
        self._update_memory_async(
            text,
            f"Clicked on '{element_desc}' at ({x_pct:.0f}%, {y_pct:.0f}%)",
            "screen_interaction",
        )

    # ═════════════════════════════════════════════════════════════
    # Text Input, Activation, Export, Close
    # ═════════════════════════════════════════════════════════════

    def submit_typed_command(self) -> None:
        text = self.text_input_var.get().strip()
        if not text:
            return
        self.text_input_var.set("")
        threading.Thread(
            target=self.process_command, args=(text, "typed"), daemon=True
        ).start()

    def activate_listening(self) -> None:
        """Start the STT engine if it's not running, and open a command window."""
        if not self.audio.stt_running:
            self.set_status("Starting STT...")
            threading.Thread(
                target=self.audio.listen_continuously,
                args=(self.process_command,),
                daemon=True,
            ).start()
            self._say("Voice system activated. I am now listening.")
        else:
            # If already running, just open a 60-second immediate window
            self.audio.activate(60)
            self._say("Listening window opened. Go ahead.")
        
        ptt_key = self.settings.get("push_to_talk_key", "ctrl").capitalize()
        self.set_status("Listening")
        self.activate_btn.configure(
            text=f"🎤 Hold [{ptt_key}] to talk",
            fg_color="#2ecc71"
        )

    def export_log(self) -> None:
        try:
            logs_dir = Path("session_logs")
            logs_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = logs_dir / f"session_{stamp}.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.command_history))
            self._say(f"Session log exported to {path}.")
        except Exception:
            self._say("I could not export the session log.")

    def on_close(self) -> None:
        if not self.running:
            return
        self.running = False
        self.audio.stop()
        self.destroy()


# ═════════════════════════════════════════════════════════════════
# Entry Point
# ═════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = OfflineAccessibleAgentGUI()
    app.mainloop()
    sys.exit(0)
