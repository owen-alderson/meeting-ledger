"""Audio and video files are transcribed on your Mac with NVIDIA Parakeet (via parakeet-mlx), so
the recording itself never leaves the machine. Install with `pipx install 'meeting-ledger[audio]'`;
ffmpeg must be on PATH for anything that isn't a plain WAV file."""

import shutil
from pathlib import Path

from .ingest import MAX_SEGMENT, Segment

MODEL = "mlx-community/parakeet-tdt-0.6b-v3"  # 25 European languages, sentence timestamps
CHUNK_SECONDS = 120.0  # long recordings are transcribed in overlapping chunks to bound memory


class TranscribeError(Exception):
    pass


def load_model(name: str = MODEL):
    try:
        from parakeet_mlx import from_pretrained
    except ImportError as e:
        raise TranscribeError(
            "Audio needs the optional transcription extra (Apple Silicon only): "
            "pipx install 'meeting-ledger[audio]'  (or pip install 'meeting-ledger[audio]')"
        ) from e
    return from_pretrained(name)


def to_segments(result) -> list[Segment]:
    """Parakeet sentences -> numbered segments, merging very short sentences so lines read naturally."""
    segments: list[Segment] = []
    for sentence in result.sentences:
        text = sentence.text.strip()
        if not text:
            continue
        last = segments[-1] if segments else None
        if last and len(last.text) < 60 and len(last.text) + len(text) < MAX_SEGMENT:
            last.text = f"{last.text} {text}"
            last.end = round(sentence.end, 2)
        else:
            segments.append(Segment(len(segments) + 1, text, "", round(sentence.start, 2), round(sentence.end, 2)))
    return segments


def transcribe(path: Path, model=None) -> list[Segment]:
    if not path.exists():
        raise TranscribeError(f"File not found: {path}")
    if path.suffix.lower() != ".wav" and shutil.which("ffmpeg") is None:
        raise TranscribeError("ffmpeg is needed to read this file. Install it with: brew install ffmpeg")
    model = model or load_model()
    result = model.transcribe(path, chunk_duration=CHUNK_SECONDS)
    segments = to_segments(result)
    if not segments:
        raise TranscribeError(f"No speech found in {path.name}.")
    return segments
