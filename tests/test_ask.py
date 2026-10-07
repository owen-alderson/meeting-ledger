import json

import httpx2
import pytest
from conftest import anthropic_client, message

from meeting_ledger.ask import Asker, group_hits, search_blocks
from meeting_ledger.extract import ClaudeError
from meeting_ledger.ingest import parse


@pytest.fixture
def filled(store):
    a = store.add_meeting("Kickoff", "2026-01-05", parse("Ana: hello\nBen: the online shop waits until January\nAna: agreed"))
    b = store.add_meeting("Review", "2026-02-02", parse("Ana: we reopen the online shop in March"))
    return store, a, b


def cited_answer(seen):
    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=message([
            {"type": "text", "text": "The online shop now reopens in March", "citations": [
                {"type": "search_result_location", "cited_text": "[1] Ana: we reopen the online shop in March",
                 "source": "meeting:2", "title": "Review (2026-02-02)", "search_result_index": 0,
                 "start_block_index": 0, "end_block_index": 1}]},
            {"type": "text", "text": "; it was January before."},
            {"type": "text", "text": "", "citations": [
                {"type": "search_result_location", "cited_text": "...", "source": "meeting:1", "title": "Kickoff",
                 "search_result_index": 1, "start_block_index": 0, "end_block_index": 1}]},
        ]))
    return handler


def test_search_results_go_to_claude_with_citations_on(filled):
    store, a, b = filled
    seen = {}
    Asker(store, anthropic_client(cited_answer(seen))).ask("when does the online shop open?")
    content = seen["body"]["messages"][0]["content"]
    results = [c for c in content if c["type"] == "search_result"]
    assert [r["source"] for r in results] == [f"meeting:{b}", f"meeting:{a}"]  # newest meeting first
    assert results[1]["title"] == "Kickoff (2026-01-05)"
    assert results[1]["content"] == [{"type": "text", "text": "[2] Ben: the online shop waits until January"}]
    assert all(r["citations"] == {"enabled": True} for r in results)
    assert content[-1] == {"type": "text", "text": "when does the online shop open?"}
    assert "output_config" not in seen["body"] or "format" not in seen["body"]["output_config"]  # incompatible with citations


def test_citations_map_back_to_meetings_and_lines(filled):
    store, a, b = filled
    answer = Asker(store, anthropic_client(cited_answer({}))).ask("online shop?")
    assert answer.text == "The online shop now reopens in March; it was January before."
    first = answer.parts[0].cites[0]
    assert (first.meeting_id, first.lines, first.held_on) == (b, [1], "2026-02-02")
    assert [(c.meeting_id, c.lines) for c in answer.sources()] == [(b, [1]), (a, [2])]


def test_no_matches_skips_the_api(filled):
    store, *_ = filled

    def handler(request):
        raise AssertionError("should not be called")

    answer = Asker(store, anthropic_client(handler)).ask("quantum gravity")
    assert answer.hits == [] and "Nothing" in answer.text


def test_api_error_is_readable(filled):
    store, *_ = filled

    def handler(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad"}})

    with pytest.raises(ClaudeError, match="API key"):
        Asker(store, anthropic_client(handler)).ask("online shop")


def test_out_of_range_citation_is_ignored(filled):
    store, *_ = filled

    def handler(request):
        return httpx2.Response(200, json=message([{"type": "text", "text": "x", "citations": [
            {"type": "search_result_location", "cited_text": "", "source": "s", "title": "t",
             "search_result_index": 9, "start_block_index": 0, "end_block_index": 1}]}]))

    answer = Asker(store, anthropic_client(handler)).ask("online shop")
    assert answer.parts[0].cites == []


def test_group_hits_orders_lines_within_a_meeting():
    hits = [{"meeting_id": 1, "title": "A", "held_on": "2026-01-01", "n": 5, "speaker": "", "text": "x"},
            {"meeting_id": 1, "title": "A", "held_on": "2026-01-01", "n": 2, "speaker": "Ana", "text": "y"}]
    groups = group_hits(hits)
    assert [h["n"] for h in groups[0]["lines"]] == [2, 5]
    assert search_blocks(groups)[0]["content"][0]["text"] == "[2] Ana: y"
