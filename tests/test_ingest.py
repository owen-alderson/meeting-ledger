from pathlib import Path

import pytest

from meeting_ledger.ingest import MAX_SEGMENT, is_audio, normalise, numbered, parse, parse_subtitles, parse_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_plain_text_one_segment_per_line_numbered_from_one():
    segs = parse_text("first line\n\nsecond line\n")
    assert [(s.n, s.text) for s in segs] == [(1, "first line"), (2, "second line")]


def test_speaker_labels_are_split_off():
    segs = parse_text("Ana: hello there\nBen Ortiz: hi")
    assert [(s.speaker, s.text) for s in segs] == [("Ana", "hello there"), ("Ben Ortiz", "hi")]


def test_note_style_prefixes_are_not_speakers():
    segs = parse_text("Action: send the deck\nDecision: launch in May\nTODO: book room")
    assert all(s.speaker == "" for s in segs)
    assert segs[0].text == "Action: send the deck"


def test_lowercase_or_long_prefixes_are_not_speakers():
    segs = parse_text("talked to ana: she agrees\nThe Very Long Group Of Many People Here: hi")
    assert all(s.speaker == "" for s in segs)


def test_bracketed_timestamps():
    segs = parse_text("[00:01:05] Ana: we start\n[12:30] Ben: ok")
    assert (segs[0].start, segs[0].speaker, segs[0].text) == (65.0, "Ana", "we start")
    assert segs[1].start == 750.0


def test_speaker_with_parenthesised_time():
    s = parse_text("Ana (01:02): hello")[0]
    assert (s.speaker, s.start, s.text) == ("Ana", 62.0, "hello")


def test_markdown_bullets_and_rules_are_cleaned():
    segs = parse_text("# Notes\n- point one\n* point two\n---\n• point three")
    assert [s.text for s in segs] == ["# Notes", "point one", "point two", "point three"]


def test_long_paragraph_is_split_at_sentences():
    sentence = "This is a fairly long sentence about the plan for next quarter. "
    segs = parse_text(sentence * 15)
    assert len(segs) > 1
    assert all(len(s.text) <= MAX_SEGMENT for s in segs)
    assert [s.n for s in segs] == list(range(1, len(segs) + 1))


def test_vtt_voice_tags_and_times():
    segs = parse_subtitles((FIXTURES / "zoom.vtt").read_text())
    assert segs[0].speaker == "Ana Silva"
    assert segs[0].start == 1.5
    assert "<" not in segs[0].text


def test_vtt_merges_consecutive_cues_from_same_speaker():
    segs = parse_subtitles((FIXTURES / "zoom.vtt").read_text())
    ana = [s for s in segs if s.speaker == "Ana Silva"]
    assert ana[0].text == "Morning everyone. Let's get going."
    assert ana[0].end == 6.0


def test_vtt_name_prefix_speakers():
    text = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nBen: hi\n\n2\n00:00:03.000 --> 00:00:04.000\nAna: hello\n"
    assert [(s.speaker, s.text) for s in parse(text)] == [("Ben", "hi"), ("Ana", "hello")]


def test_srt_unlabelled_cues_merge_into_one_line_with_its_time_span():
    segs = parse((FIXTURES / "call.srt").read_text(), "call.srt")
    assert [s.text for s in segs] == ["Welcome to the call. Thanks for having me."]
    assert (segs[0].start, segs[0].end) == (1.0, 6.0)


def test_srt_detected_without_extension():
    text = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    assert parse(text)[0].text == "Hello"


def test_vtt_detected_by_header_and_bom():
    assert parse("﻿WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n")[0].text == "hi"


def test_empty_input():
    assert parse("") == []
    assert parse("\n \n") == []


def test_label_and_numbered():
    segs = parse_text("Ana: hi\nno speaker")
    assert numbered(segs) == "[1] Ana: hi\n[2] no speaker"


def test_normalise_ignores_case_punctuation_and_curly_quotes():
    assert normalise("I’ll SEND it,  by Friday!") == normalise("i'll send it by friday")


@pytest.mark.parametrize("name,audio", [("call.m4a", True), ("x.MP3", True), ("v.mp4", True), ("t.vtt", False), ("n.txt", False)])
def test_is_audio(name, audio):
    assert is_audio(Path(name)) is audio
