"""Voice answers: decode what the browser recorded, and transcribe it locally with Whisper on CPU.

Nothing leaves the server. A transcript is only a draft: the person corrects it, and it counts only when they save it.
"""
from __future__ import annotations

import io
import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

WHISPER_MODEL = "openai/whisper-tiny"
MAX_SECONDS = 300
MAX_BYTES = 15 * 1024 * 1024
ALLOWED_MIME = ("audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/aac", "audio/x-m4a", "audio/m4a",
                "video/webm", "video/mp4")
_SAMPLE_RATE = 16000


class VoiceError(ValueError):
    """Shown to the user."""


def decode_audio(data: bytes) -> np.ndarray:
    """Any browser format to 16 kHz mono floats. PyAV bundles the codecs, so no separate ffmpeg is needed."""
    import av

    try:
        chunks = []
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise VoiceError("That file has no audio in it.")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=_SAMPLE_RATE)
            total = 0
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    pcm = out.to_ndarray().flatten()
                    chunks.append(pcm)
                    total += pcm.size
                if total > MAX_SECONDS * _SAMPLE_RATE:
                    raise VoiceError("Please keep a voice answer under five minutes.")
    except VoiceError:
        raise
    except Exception as e:  # PyAV raises many decoder-specific types on bad files
        raise VoiceError("That recording could not be read. Please try again or type your answer.") from e
    if not chunks:
        raise VoiceError("That recording is empty.")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class Whisper:
    available = True

    def __init__(self, model_name: str = WHISPER_MODEL):
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        self.torch = torch
        self.processor = WhisperProcessor.from_pretrained(model_name)
        self.model = WhisperForConditionalGeneration.from_pretrained(model_name).eval()

    def transcribe(self, audio: np.ndarray) -> str:
        pieces = []
        window = 30 * _SAMPLE_RATE
        for start in range(0, len(audio), window):
            clip = audio[start:start + window]
            if clip.size < _SAMPLE_RATE // 4:
                continue
            features = self.processor(clip, sampling_rate=_SAMPLE_RATE, return_tensors="pt").input_features
            with self.torch.inference_mode():
                ids = self.model.generate(features, max_new_tokens=220, task="transcribe")
            pieces.append(self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip())
        return " ".join(p for p in pieces if p).strip()


_lock = threading.Lock()
_whisper = None


def get_transcriber():
    global _whisper
    with _lock:
        if _whisper is None:
            try:
                _whisper = Whisper()
            except Exception:
                log.exception("Whisper could not be loaded; voice notes are kept without a draft transcript")
                _whisper = False
        return _whisper or None


def transcribe_bytes(data: bytes, transcriber) -> str:
    audio = decode_audio(data)
    if transcriber is None:
        return ""
    return transcriber.transcribe(audio)
