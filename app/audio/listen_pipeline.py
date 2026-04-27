"""Always-on listening pipeline: wakeword -> utterance capture -> local STT."""

from __future__ import annotations

import logging
import queue
import re
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
import openwakeword
from openwakeword.model import Model
from openwakeword.utils import download_models

from app.alerts.voice import speak
from app.automation.announcer import BackgroundAnnouncer
from app.automation.workflows import WorkflowConfigError, WorkflowDefinition
from app.config import PlutoSettings
from app.intent.router import IntentRouter
from app.integrations.executor import AssistantExecutor
from app.state.session_store import save_last_transcript


@dataclass
class TranscriptEvent:
    text: str
    wakeword: str
    wake_score: float
    duration_sec: float


class AlwaysOnListener:
    """Local microphone listener with openWakeWord and faster-whisper."""

    sample_rate = 16_000
    chunk_size = 1_280  # 80ms frames expected by openWakeWord

    def __init__(self, settings: PlutoSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

        self.silence_rms_threshold = settings.silence_rms_threshold
        self.min_utterance_sec = settings.min_utterance_sec
        self.max_utterance_sec = settings.max_utterance_sec
        self.silence_stop_sec = settings.silence_stop_sec
        self.wakeword_cooldown_sec = settings.wakeword_cooldown_sec

        self.wakeword_model = self._init_wakeword_model()
        self.stt_model = self._init_stt_model()
        self.intent_router = IntentRouter(settings, logger)
        self.executor = AssistantExecutor(settings, logger)
        self.announcer = BackgroundAnnouncer(settings, logger, self.executor.eventkit)
        self.pending_workflow: WorkflowDefinition | None = None

    def _init_wakeword_model(self) -> Model:
        configured_models = self._resolve_wakeword_models()
        self.logger.info("Configured wakeword models: %s", ", ".join(configured_models))
        for attempt in range(1, 3):
            try:
                return Model(wakeword_models=configured_models, inference_framework="onnx")
            except Exception as exc:  # pragma: no cover - runtime environment dependent
                # Common first-run failure: packaged model files are absent. Download and retry once.
                if ("NO_SUCHFILE" in str(exc) or "File doesn't exist" in str(exc)) and attempt == 1:
                    self.logger.info("openWakeWord model files missing, attempting local download...")
                    download_models()
                    continue
                if attempt < 2:
                    self.logger.warning("openWakeWord init failed (attempt %s/2): %s", attempt, exc)
                    time.sleep(0.4)
                    continue
                raise RuntimeError(
                    "Failed to initialize openWakeWord model. "
                    f"Check local model files/network for first-time model download. Details: {exc}"
                ) from exc
        raise RuntimeError("openWakeWord initialization failed")

    def _resolve_wakeword_models(self) -> list[str]:
        resolved: list[str] = []
        missing_custom: list[str] = []
        supported_pretrained = set(openwakeword.MODELS.keys())

        model_dir = self.settings.wakeword_model_dir
        for name in self.settings.wakeword_models:
            if name in supported_pretrained:
                resolved.append(name)
                continue

            custom_path = model_dir / f"{name}.onnx"
            if custom_path.exists():
                resolved.append(str(custom_path))
            else:
                missing_custom.append(name)

        if missing_custom:
            self.logger.warning(
                "Custom wakeword model file(s) missing for: %s. "
                "Add ONNX files in %s named <wakeword>.onnx (example: pluto.onnx).",
                ", ".join(missing_custom),
                model_dir,
            )

        if not resolved:
            raise RuntimeError(
                "No valid wakeword models configured. "
                "Set PLUTO_WAKEWORD_MODELS to built-ins (e.g. 'alexa,hey_mycroft') or add custom ONNX "
                f"model files under {model_dir}."
            )

        return resolved

    def _init_stt_model(self) -> WhisperModel:
        for attempt in range(1, 3):
            try:
                return WhisperModel(
                    self.settings.stt_model,
                    device="cpu",
                    compute_type="int8",
                )
            except Exception as exc:  # pragma: no cover - runtime environment dependent
                if attempt < 2:
                    self.logger.warning("faster-whisper init failed (attempt %s/2): %s", attempt, exc)
                    time.sleep(0.4)
                    continue
                raise RuntimeError(
                    f"Failed to initialize faster-whisper model '{self.settings.stt_model}'. "
                    f"Ensure model download is available locally and try again. Details: {exc}"
                ) from exc
        raise RuntimeError("faster-whisper initialization failed")

    def run(self, listen_seconds: int = 0, on_transcript: Callable[[TranscriptEvent], None] | None = None) -> None:
        audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=128)
        start_time = time.monotonic()
        last_wake_at = 0.0
        last_debug_log_at = 0.0
        last_background_poll_at = 0.0
        suppressed_until = 0.0

        def _audio_callback(indata: bytes, frames: int, _time_info: object, status: sd.CallbackFlags) -> None:
            if status:
                self.logger.debug("mic callback status=%s", status)

            chunk = np.frombuffer(indata, dtype=np.int16).copy()
            if chunk.size != frames:
                self.logger.debug("received unexpected chunk size=%s expected=%s", chunk.size, frames)

            try:
                audio_queue.put_nowait(chunk)
            except queue.Full:
                self.logger.debug("audio queue full; dropping oldest frame")
                _ = audio_queue.get_nowait()
                audio_queue.put_nowait(chunk)

        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.chunk_size,
                channels=1,
                dtype="int16",
                callback=_audio_callback,
            ):
                self.logger.info("Listening for wake word (threshold=%.2f)...", self.settings.wakeword_threshold)

                while True:
                    if listen_seconds > 0 and (time.monotonic() - start_time) >= listen_seconds:
                        self.logger.info("Listen timeout reached (%ss).", listen_seconds)
                        return

                    self._tick_timers()
                    now_loop = time.monotonic()
                    if now_loop - last_background_poll_at >= self.settings.poll_interval_sec:
                        self.announcer.tick()
                        last_background_poll_at = now_loop

                    try:
                        chunk = audio_queue.get(timeout=1.0)
                    except queue.Empty:
                        continue

                    predictions = self.wakeword_model.predict(chunk)
                    wakeword, wake_score_raw = max(predictions.items(), key=lambda item: item[1])
                    wake_score = float(wake_score_raw)

                    now = time.monotonic()
                    if now < suppressed_until:
                        if wake_score >= self.settings.wakeword_threshold:
                            self.logger.debug(
                                "wake detected during post-action suppression (remaining=%.2fs); ignored",
                                suppressed_until - now,
                            )
                        continue

                    if now - last_debug_log_at >= self.settings.debug_log_interval_sec:
                        self.logger.debug("wake_scores top=%s:%.3f", wakeword, wake_score)
                        last_debug_log_at = now

                    if wake_score < self.settings.wakeword_threshold:
                        continue

                    if (now - last_wake_at) < self.wakeword_cooldown_sec:
                        self.logger.debug("wake detected during cooldown; ignored")
                        continue

                    last_wake_at = now

                    self.logger.info("Wake detected: %s (score=%.3f)", wakeword, wake_score)

                    utterance = self._record_utterance(audio_queue)
                    if utterance.size == 0:
                        self.logger.info("No utterance captured after wake word.")
                        continue

                    transcript = self._transcribe(utterance)
                    if not transcript.strip():
                        self.logger.info("No speech recognized.")
                        continue

                    command_text = self._strip_wake_phrase(transcript)
                    if not command_text:
                        self.logger.info("Only wake phrase recognized; waiting for the next command.")
                        continue

                    if self._is_exit_listening_phrase(command_text):
                        self.logger.info("Exit phrase recognized; stopping listen mode.")
                        speak("Stopping Pluto.", self.settings)
                        return

                    if self.pending_workflow is not None:
                        workflow = self.pending_workflow
                        self.pending_workflow = None
                        confirmation = self._parse_confirmation(command_text)
                        if confirmation is True:
                            action_result = self.executor.execute_confirmed_workflow(workflow)
                            suppressed_until = time.monotonic() + self.settings.post_action_suppression_sec
                            self.logger.info("Execution result: %s", action_result.to_dict())
                            continue

                        speak("Workflow cancelled.", self.settings)
                        suppressed_until = time.monotonic() + self.settings.post_action_suppression_sec
                        self.logger.info("Workflow confirmation rejected or unclear: %s", command_text)
                        continue

                    event = TranscriptEvent(
                        text=command_text,
                        wakeword=wakeword,
                        wake_score=wake_score,
                        duration_sec=utterance.size / self.sample_rate,
                    )

                    save_last_transcript(
                        self.settings.session_state_path,
                        command_text,
                        metadata={
                            "wakeword": wakeword,
                            "wake_score": wake_score,
                            "audio_duration_sec": round(event.duration_sec, 3),
                            "stt_model": self.settings.stt_model,
                        },
                    )
                    self.logger.info("Recognized: %s", command_text)
                    parsed_intent = self.intent_router.parse(command_text)
                    self.logger.info("Parsed intent: %s", parsed_intent.model_dump_json())
                    if parsed_intent.intent == "run_workflow":
                        try:
                            workflow = self.executor.workflow_for_intent(parsed_intent)
                        except WorkflowConfigError as exc:
                            speak("Workflow configuration is invalid.", self.settings)
                            self.logger.warning("Workflow config invalid: %s", exc)
                            continue
                        if workflow is not None and self.executor.workflow_needs_confirmation(workflow):
                            self.pending_workflow = workflow
                            prompt = self.executor.workflow_confirmation_prompt(workflow)
                            speak(prompt, self.settings)
                            suppressed_until = time.monotonic() + self.settings.post_action_suppression_sec
                            self.logger.info("Workflow pending confirmation: %s", workflow.name)
                            continue
                    action_result = self.executor.execute(parsed_intent)
                    suppressed_until = time.monotonic() + self.settings.post_action_suppression_sec
                    self.logger.info("Execution result: %s", action_result.to_dict())

                    if on_transcript:
                        on_transcript(event)

        except sd.PortAudioError as exc:
            message = str(exc)
            raise RuntimeError(
                "Microphone access failed. Confirm an input device is available and grant mic permission "
                "in System Settings -> Privacy & Security -> Microphone. "
                f"Original error: {message}"
            ) from exc

    def _record_utterance(self, audio_queue: queue.Queue[np.ndarray]) -> np.ndarray:
        self.logger.info("Recording utterance...")
        chunks: list[np.ndarray] = []
        speech_detected = False

        start = time.monotonic()
        last_voice = start

        while True:
            now = time.monotonic()
            elapsed = now - start
            if elapsed >= self.max_utterance_sec:
                break

            try:
                chunk = audio_queue.get(timeout=1.0)
            except queue.Empty:
                break

            chunks.append(chunk)
            rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float32)))))

            if rms >= self.silence_rms_threshold:
                speech_detected = True
                last_voice = time.monotonic()
                self.logger.debug("speech chunk rms=%.2f", rms)

            silence_elapsed = time.monotonic() - last_voice
            if speech_detected and elapsed >= self.min_utterance_sec and silence_elapsed >= self.silence_stop_sec:
                break

        if not chunks:
            return np.array([], dtype=np.int16)

        audio = np.concatenate(chunks)
        self.logger.debug(
            "utterance captured duration=%.2fs speech_detected=%s",
            audio.size / self.sample_rate,
            speech_detected,
        )
        return audio

    def _transcribe(self, audio_int16: np.ndarray) -> str:
        self.logger.info("Transcribing locally with faster-whisper (%s)...", self.settings.stt_model)

        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        try:
            segments, _ = self.stt_model.transcribe(
                audio_float32,
                language="en",
                beam_size=1,
                vad_filter=True,
                temperature=0.0,
            )
        except Exception as exc:  # pragma: no cover - runtime environment dependent
            raise RuntimeError(
                "Local transcription failed. If this is first run, the Whisper model may still need download. "
                f"Details: {exc}"
            ) from exc

        text = " ".join(segment.text.strip() for segment in segments if segment.text).strip()
        return text

    def _tick_timers(self) -> None:
        due = self.executor.timers.pop_due()
        for timer in due:
            message = f"Timer done: {timer.label}"
            spoke = speak(message, self.settings)
            self.logger.info("Timer fired: id=%s label=%s spoke=%s", timer.timer_id, timer.label, spoke)

    @staticmethod
    def _is_exit_listening_phrase(transcript: str) -> bool:
        normalized = re.sub(r"[^a-z0-9\s]", " ", transcript.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized in {
            "stop",
            "stop listening",
            "exit",
            "exit pluto",
            "quit",
            "quit pluto",
            "stop pluto",
            "alexa stop",
            "alexa stop listening",
            "hey pluto stop",
            "hey pluto stop listening",
            "hey alexa stop",
            "hey alexa stop listening",
        }

    @staticmethod
    def _strip_wake_phrase(transcript: str) -> str:
        normalized = transcript.strip()
        normalized = re.sub(
            r"^\s*(?:hey\s+)?(?:pluto|alexa)\b[\s,.:;-]*",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        return normalized.strip()

    @staticmethod
    def _parse_confirmation(transcript: str) -> bool | None:
        normalized = re.sub(r"[^a-z0-9\s]", " ", transcript.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized in {"yes", "confirm", "go ahead", "run it"}:
            return True
        if normalized in {"no", "cancel", "stop"}:
            return False
        return None
