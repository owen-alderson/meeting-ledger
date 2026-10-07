"""Turn a transcript or notes into numbered segments: the lines that everything else cites.

Understands WebVTT (Zoom, Teams, Meet exports), SRT, and plain text or Markdown with optional
"Name: what they said" speaker labels and [hh:mm:ss] timestamps.
"""

import re
from dataclasses import dataclass
from pathlib import Path

MAX_SEGMENT = 400  # characters; longer paragraphs are split at sentence ends so citations stay precise
MERGE_UP_TO = 300  # consecutive subtitle cues from one speaker are merged up to this length

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".webm"}


@dataclass
class Segment:
    n: int  # 1-based line number shown to Claude, in citations and in the UI
    text: str
    speaker: str = ""
    start: float | None = None  # seconds from the start of the recording
    end: float | None = None

    def label(self) -> str:
        who = f"{self.speaker}: " if self.speaker else ""
        return f"[{self.n}] {who}{self.text}"


_CUE_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_ARROW = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)")
_VOICE = re.compile(r"^<v(?:\.[^ >]*)?\s+([^>]+)>(.*?)(?:</v>)?$", re.S)
_TAGS = re.compile(r"</?[^>]+>")
_STAMP = re.compile(r"^\[?\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\]?\s*")
_SPEAKER = re.compile(r"^([A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3})\s*(?:\(((?:\d{1,2}:)?\d{1,2}:\d{2})\))?:\s+(.+)$")
_NOT_SPEAKERS = {"Note", "Notes", "Action", "Actions", "Decision", "Decisions", "Todo", "TODO", "Agenda", "Re",
                 "Update", "Next", "Q", "A", "Summary", "Question", "Answer", "Date", "Attendees", "Owner", "Due"}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _seconds(stamp: str) -> float | None:
    m = _CUE_TIME.fullmatch(stamp.strip())
    if m:
        h, mm, ss, frac = m.groups()
        return int(h or 0) * 3600 + int(mm) * 60 + int(ss) + int(frac.ljust(3, "0")) / 1000
    parts = stamp.strip().split(":")
    if all(p.isdigit() for p in parts) and 2 <= len(parts) <= 3:
        total = 0
        for p in parts:
            total = total * 60 + int(p)
        return float(total)
    return None


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_SEGMENT:
        return [text]
    out, current = [], ""
    for sentence in _SENTENCE_END.split(text):
        if current and len(current) + 1 + len(sentence) > MAX_SEGMENT:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def _speaker_line(line: str) -> tuple[str, str, float | None]:
    start = None
    m = _STAMP.match(line)
    if m:
        start = _seconds(m.group(1))
        line = line[m.end():]
    m = _SPEAKER.match(line)
    if m and m.group(1) not in _NOT_SPEAKERS:
        if m.group(2):
            start = _seconds(m.group(2))
        return m.group(1), m.group(3).strip(), start
    return "", line.strip(), start


def _number(raw: list[tuple[str, str, float | None, float | None]]) -> list[Segment]:
    segments = []
    for speaker, text, start, end in raw:
        for piece in _split_long(text):
            segments.append(Segment(len(segments) + 1, piece, speaker, start, end))
    return segments


def parse_text(text: str) -> list[Segment]:
    """Plain text or Markdown: one segment per non-empty line (headings and bullets kept as text)."""
    raw = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line or set(line) <= set("#=-_*"):
            continue
        speaker, said, start = _speaker_line(line)
        if said:
            raw.append((speaker, said, start, None))
    return _number(raw)


def _cues(text: str) -> list[tuple[float | None, float | None, list[str]]]:
    cues, block = [], []
    for line in text.splitlines() + [""]:
        if line.strip():
            block.append(line.rstrip())
            continue
        if block:
            arrow = next((i for i, b in enumerate(block) if _ARROW.match(b)), None)
            if arrow is not None:
                m = _ARROW.match(block[arrow])
                cues.append((_seconds(m.group(1)), _seconds(m.group(2)), block[arrow + 1:]))
            block = []
    return cues


def parse_subtitles(text: str) -> list[Segment]:
    """WebVTT or SRT. Speakers come from <v Name> voice tags or a "Name: " prefix."""
    raw: list[list] = []
    for start, end, lines in _cues(text):
        said = " ".join(lines).strip()
        speaker = ""
        m = _VOICE.match(said)
        if m:
            speaker, said = m.group(1).strip(), m.group(2)
        said = _TAGS.sub("", said).strip()
        if not speaker:
            speaker, said, _ = _speaker_line(said)
        if not said:
            continue
        last = raw[-1] if raw else None
        if last and last[0] == speaker and len(last[1]) + len(said) < MERGE_UP_TO:
            last[1] = f"{last[1]} {said}"
            last[3] = end
        else:
            raw.append([speaker, said, start, end])
    return _number([tuple(r) for r in raw])


def parse(text: str, name: str = "") -> list[Segment]:
    suffix = Path(name).suffix.lower()
    head = text.lstrip("﻿").lstrip()
    if suffix in (".vtt", ".srt") or head.startswith("WEBVTT") or re.match(r"^\d+\s*\n\s*\S+\s*-->", head):
        return parse_subtitles(head)
    return parse_text(head)


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SUFFIXES


def numbered(segments: list[Segment]) -> str:
    return "\n".join(s.label() for s in segments)


def normalise(text: str) -> str:
    """Lowercase, straighten quotes, drop punctuation and collapse whitespace, for quote matching."""
    text = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[^\w\s']", " ", text)
    return " ".join(text.split())
