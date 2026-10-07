import builtins
from dataclasses import dataclass, field

import pytest

from meeting_ledger import transcribe
from meeting_ledger.transcribe import TranscribeError, to_segments


@dataclass
class Sentence:
    text: str
    start: float
    end: float


@dataclass
class Result:
    sentences: list = field(default_factory=list)


class FakeModel:
    def __init__(self, sentences):
        self.sentences, self.calls = sentences, []

    def transcribe(self, path, chunk_duration=None):
        self.calls.append((path, chunk_duration))
        return Result(self.sentences)


def test_sentences_become_numbered_segments_with_times():
    segs = to_segments(Result([Sentence("We should launch in May, and I will draft the full launch plan by Friday.", 0.0, 4.2),
                               Sentence("Sounds right to me, let's go with that.", 4.5, 7.0)]))
    assert [(s.n, s.start, s.end) for s in segs] == [(1, 0.0, 4.2), (2, 4.5, 7.0)]


def test_very_short_sentences_are_merged():
    segs = to_segments(Result([Sentence("Okay.", 0, 1), Sentence("Yes.", 1, 2), Sentence("Let's start with the budget review now please.", 2, 5)]))
    assert segs[0].text == "Okay. Yes. Let's start with the budget review now please." and segs[0].end == 5


def test_blank_sentences_skipped():
    assert to_segments(Result([Sentence("  ", 0, 1)])) == []


def test_transcribe_uses_chunks(tmp_path):
    audio = tmp_path / "call.wav"
    audio.write_bytes(b"RIFF")
    model = FakeModel([Sentence("Hello there everyone, thanks for joining the call today.", 0, 3)])
    segs = transcribe.transcribe(audio, model)
    assert segs[0].text.startswith("Hello") and model.calls == [(audio, transcribe.CHUNK_SECONDS)]


def test_missing_file(tmp_path):
    with pytest.raises(TranscribeError, match="not found"):
        transcribe.transcribe(tmp_path / "nope.m4a", FakeModel([]))


def test_no_speech(tmp_path):
    audio = tmp_path / "silence.wav"
    audio.write_bytes(b"RIFF")
    with pytest.raises(TranscribeError, match="No speech"):
        transcribe.transcribe(audio, FakeModel([]))


def test_needs_ffmpeg_for_compressed_audio(tmp_path, monkeypatch):
    audio = tmp_path / "call.m4a"
    audio.write_bytes(b"x")
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: None)
    with pytest.raises(TranscribeError, match="brew install ffmpeg"):
        transcribe.transcribe(audio, FakeModel([]))


def test_missing_extra_gives_install_hint(monkeypatch):
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("parakeet_mlx"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(TranscribeError, match=r"meeting-ledger\[audio\]"):
        transcribe.load_model()
