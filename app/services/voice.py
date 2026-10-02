"""Speech to text for Telegram voice messages, locally on the server.

Uses faster-whisper (Whisper on CPU, int8). Nothing leaves the server: the
audio is not sent to any outside service. The model (≈ 0.5 GB for "small") is
downloaded once into DATA_DIR/models on the first voice message and kept in
memory afterwards.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class VoiceError(Exception):
    pass


class Transcriber:
    def __init__(self, model_size: str, models_dir: Path, threads: int = 0):
        self.model_size = model_size
        self.models_dir = models_dir
        self.threads = threads
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self):
        # The container's file system is read-only: the download cache of
        # huggingface_hub (and its xet cache) must live on the data volume.
        os.environ.setdefault("HF_HOME", str(self.models_dir / "hf"))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover - present in the Docker image
            raise VoiceError("Распознавание речи не установлено.") from e
        self.models_dir.mkdir(parents=True, exist_ok=True)
        log.info("loading speech model %s (first time downloads it)", self.model_size)
        return WhisperModel(
            self.model_size, device="cpu", compute_type="int8", cpu_threads=self.threads, download_root=str(self.models_dir)
        )

    def _run(self, audio: bytes) -> str:
        if self._model is None:
            self._model = self._load()
        segments, _ = self._model.transcribe(io.BytesIO(audio), language="ru", beam_size=1, vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def transcribe(self, audio: bytes) -> str:
        # One at a time: the model is big and the CPU is shared with everything else.
        async with self._lock:
            try:
                return await asyncio.to_thread(self._run, audio)
            except VoiceError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("speech recognition failed")
                raise VoiceError("Не получилось распознать голосовое.") from e
