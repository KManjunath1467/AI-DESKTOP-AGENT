"""
AudioEngine — Robust Voice & STT System (SAPI5 Edition)
-------------------------------------------------------
- Uses Whisper for accurate English speech recognition.
- Uses direct SAPI5 via win32com for rock-solid Windows TTS.
- Push-to-talk: microphone is only active while a hotkey is held.
- TTS-aware: mic is suppressed while the agent is speaking.
"""

import queue
import re
import threading
import time
from typing import Callable, Optional

import numpy as np
import speech_recognition as sr
from faster_whisper import WhisperModel
from pynput import keyboard as kb

# Common Whisper hallucinations
HALLUCINATION_PHRASES = {
    "thank you very much", "thank you", "thanks for watching",
    "please subscribe", "subtitles by", "you", "be", "the", "bye",
    "recap by", "watching", "thank you for watching", "bye bye",
    "thanks", "the end", "hmm", "ah",
}


class AudioEngine:
    def __init__(
        self,
        output_callback: Optional[Callable[[str], None]] = None,
        wake_words: Optional[list] = None,
        speech_rate: int = 160,
        stt_model_name: str = "small",
        push_to_talk_key: str = "ctrl",
    ):
        self.output_callback = output_callback
        self.stt_running = False
        self._conversation_until = 0.0
        
        # ── TTS state ───────────────────────────────────────────
        self._tts_queue = queue.Queue()
        self._stt_model_name = stt_model_name or "small"
        self._speech_rate = speech_rate
        self._is_speaking = False          # True while TTS audio is playing
        self._speaking_lock = threading.Lock()

        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

        self._wake_words = tuple(
            str(word).strip().lower()
            for word in (wake_words or ("computer", "assistant"))
            if str(word).strip()
        ) or ("computer", "assistant")
        
        self._model = None

        # ── Push-to-talk state ──────────────────────────────────
        self._ptt_key = push_to_talk_key.lower()
        self._ptt_held = False             # True while the hotkey is held
        self._ptt_lock = threading.Lock()
        self._kb_listener: Optional[kb.Listener] = None

        # Command queue: ensures commands never overlap
        self._command_queue: queue.Queue = queue.Queue()
        self._command_worker_thread = threading.Thread(
            target=self._command_worker, daemon=True
        )
        self._command_worker_thread.start()

    def _log(self, text: str) -> None:
        if self.output_callback:
            self.output_callback(text)
        else:
            print(text)

    def activate(self, seconds: int = 60) -> None:
        """Manually open the conversation window for `seconds` seconds."""
        self._conversation_until = time.time() + seconds

    # ═════════════════════════════════════════════════════════════
    # Push-to-talk keyboard hooks (pynput)
    # ═════════════════════════════════════════════════════════════

    def _key_matches(self, key) -> bool:
        """Return True if the pressed key matches the configured PTT key."""
        ptt = self._ptt_key
        # Check modifier keys
        if ptt in ("ctrl", "control"):
            return key in (kb.Key.ctrl_l, kb.Key.ctrl_r)
        if ptt in ("alt",):
            return key in (kb.Key.alt_l, kb.Key.alt_r, kb.Key.alt_gr)
        if ptt in ("shift",):
            return key in (kb.Key.shift_l, kb.Key.shift_r)
        # Regular character key
        try:
            return hasattr(key, 'char') and key.char and key.char.lower() == ptt
        except AttributeError:
            return False

    def _on_key_press(self, key) -> None:
        if self._key_matches(key):
            with self._ptt_lock:
                self._ptt_held = True

    def _on_key_release(self, key) -> None:
        if self._key_matches(key):
            with self._ptt_lock:
                self._ptt_held = False

    def _start_keyboard_listener(self) -> None:
        """Start the global keyboard listener for push-to-talk."""
        if self._kb_listener is not None:
            return
        self._kb_listener = kb.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._kb_listener.daemon = True
        self._kb_listener.start()

    @property
    def is_ptt_active(self) -> bool:
        """True when the push-to-talk key is currently held down."""
        with self._ptt_lock:
            return self._ptt_held

    @property
    def is_speaking(self) -> bool:
        with self._speaking_lock:
            return self._is_speaking

    def _tts_worker(self) -> None:
        """
        Dedicated TTS thread using direct SAPI5 (win32com).
        This is much more stable than pyttsx3 on Windows.
        """
        try:
            import win32com.client
            import pythoncom
            
            # COM must be initialized in each thread
            pythoncom.CoInitialize()
            
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            
            # Select best voice
            voices = speaker.GetVoices()
            target_voice = None
            for i in range(voices.Count):
                v = voices.Item(i)
                name = v.GetDescription().lower()
                if "zira" in name:
                    target_voice = v
                    break
                elif "hazel" in name:
                    target_voice = v
            
            if target_voice:
                speaker.Voice = target_voice
                self._log(f"Agent: Selected voice: {target_voice.GetDescription()}")

            sapi_rate = int((self._speech_rate - 160) / 10)
            speaker.Rate = max(-10, min(10, sapi_rate))
            
            self._log("Agent: TTS Engine (SAPI5) initialized.")
            
            while True:
                text = self._tts_queue.get()
                if text is None: break
                try:
                    with self._speaking_lock:
                        self._is_speaking = True
                    speaker.Speak(text)
                except Exception as e:
                    self._log(f"TTS Thread Error: {e}")
                finally:
                    with self._speaking_lock:
                        self._is_speaking = False
                    self._tts_queue.task_done()
                    
            pythoncom.CoUninitialize()
        except Exception as e:
            self._log(f"TTS Initialization Error: {e}")
            self._tts_worker_pyttsx3()

    def _tts_worker_pyttsx3(self) -> None:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", self._speech_rate)
            while True:
                text = self._tts_queue.get()
                if text is None: break
                try:
                    with self._speaking_lock:
                        self._is_speaking = True
                    engine.say(text)
                    engine.runAndWait()
                except Exception:
                    pass
                finally:
                    with self._speaking_lock:
                        self._is_speaking = False
                    self._tts_queue.task_done()
        except Exception:
            pass

    def speak(self, text: str) -> None:
        self._log(f"Agent: {text}")
        if not text: return
        self._tts_queue.put(text)

    def _command_worker(self) -> None:
        while True:
            item = self._command_queue.get()
            if item is None: break
            callback, text = item
            try:
                callback(text)
            except Exception as e:
                self._log(f"Command worker error: {e}")
            finally:
                self._command_queue.task_done()

    def _dispatch(self, callback: Callable, text: str) -> None:
        self._command_queue.put((callback, text))

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            self._log(f"Agent: Loading STT engine ({self._stt_model_name})...")
            # Try float16 first (better quality), fall back to int8 (lower RAM)
            try:
                self._model = WhisperModel(self._stt_model_name, device="cpu", compute_type="float32")
            except Exception:
                self._log("Agent: float32 not available, falling back to int8...")
                self._model = WhisperModel(self._stt_model_name, device="cpu", compute_type="int8")
            self._log("Agent: STT engine ready.")
            return self._model
        except Exception as error:
            self._log(f"STT Loading Error: {error}")
            return None

    def listen_continuously(self, command_callback: Callable[[str], None]) -> None:
        """
        Push-to-talk listener.  The mic only records while the PTT key
        (default: Ctrl) is held down.  While the agent is speaking
        (TTS), captured audio is silently discarded so it never
        hears its own voice.
        """
        self.stt_running = True
        model = self._load_model()
        if model is None:
            self.stt_running = False
            return

        # Start the global keyboard listener for PTT
        self._start_keyboard_listener()

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300     # higher = less random noise
        recognizer.dynamic_energy_threshold = True
        recognizer.dynamic_energy_adjustment_ratio = 1.5
        recognizer.pause_threshold = 0.8

        ptt_label = self._ptt_key.capitalize()
        self._log(f"Agent: Push-to-talk ready — hold [{ptt_label}] to speak.")

        while self.stt_running:
            try:
                with sr.Microphone(sample_rate=16000) as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    self._log(f"Agent: Mic calibrated (threshold={recognizer.energy_threshold:.0f})")

                    while self.stt_running:
                        # ── Wait for PTT key press ──────────────
                        if not self.is_ptt_active:
                            time.sleep(0.05)     # tiny sleep to avoid busy-wait
                            continue

                        # ── Skip if agent is currently speaking ─
                        if self.is_speaking:
                            time.sleep(0.05)
                            continue

                        try:
                            audio = recognizer.listen(
                                source, timeout=4, phrase_time_limit=15
                            )

                            # Discard if TTS started while we were recording
                            if self.is_speaking:
                                continue

                            audio_data = (
                                np.frombuffer(audio.get_raw_data(), dtype=np.int16)
                                .astype(np.float32) / 32768.0
                            )

                            # Skip very short audio (clicks / pops)
                            if len(audio_data) < 8000:   # < 0.5 seconds
                                continue

                            segments, info = model.transcribe(
                                audio_data,
                                language="en",
                                beam_size=5,
                                best_of=3,
                                vad_filter=True,
                                vad_parameters=dict(
                                    min_speech_duration_ms=150,
                                    min_silence_duration_ms=500,
                                    speech_pad_ms=250,
                                ),
                                condition_on_previous_text=False,
                                no_speech_threshold=0.5,
                            )
                            text = " ".join(s.text for s in segments).strip()

                            if not text or text.lower().strip(",.!? ") in HALLUCINATION_PHRASES:
                                continue

                            self._dispatch(command_callback, text)

                        except sr.WaitTimeoutError:
                            continue
                        except Exception as inner_error:
                            if "-9988" in str(inner_error) or "Stream closed" in str(inner_error):
                                raise inner_error
                            self._log(f"Mic error: {inner_error}")
                            time.sleep(0.5)

            except Exception as outer_error:
                if self.stt_running:
                    self._log(f"Mic connection lost: {outer_error}. Restarting stream...")
                    time.sleep(2)

    def _wake_word_detected(self, text: str) -> bool:
        t = text.lower()
        for wake in self._wake_words:
            # \b matches word boundaries, so it matches exactly "computer" and not "computerized"
            if re.search(rf'\b{re.escape(wake)}\b', t):
                return True
        return False

    def _strip_wake_words(self, text: str) -> str:
        cleaned = text.strip()
        for wake in self._wake_words:
            cleaned = re.sub(rf"^\W*{re.escape(wake)}\W*", "", cleaned, flags=re.IGNORECASE).strip()
        return cleaned

    def stop(self) -> None:
        self.stt_running = False
        self._tts_queue.put(None)
        self._command_queue.put(None)
        if self._kb_listener is not None:
            try:
                self._kb_listener.stop()
            except Exception:
                pass
