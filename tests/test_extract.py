"""The real Anthropic SDK, with HTTP answered by a mock transport: checks the exact request we send
and how every kind of response is handled."""

import json

import httpx2
import pytest
from conftest import anthropic_client, message

from meeting_ledger.extract import SCHEMA, ClaudeError, Extraction, Extractor, build_prompt, open_items_brief
from meeting_ledger.ingest import parse

GOOD = {
    "title": "Pricing sync",
    "tldr": "Agreed the new price.",
    "participants": ["Ana", " ", "Ben"],
    "items": [
        {"kind": "decision", "text": "Price goes to €12", "owner": "", "due": "", "evidence": [1], "quote": "twelve euros"},
        {"kind": "action", "text": "Update the site", "owner": "Ben", "due": "2026-01-09", "evidence": [2], "quote": "I'll update"},
        {"kind": "rumour", "text": "ignored kind", "owner": "", "due": "", "evidence": [], "quote": ""},
        {"kind": "action", "text": "  ", "owner": "", "due": "", "evidence": [], "quote": ""},
    ],
    "updates": [
        {"item_id": 7, "status": "done", "evidence": [2], "quote": "done", "note": "sent"},
        {"item_id": 8, "status": "teleported", "evidence": [], "quote": "", "note": ""},
    ],
}


def run(handler_body=None, status=200, stop_reason="end_turn", text=None):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        if status != 200:
            return httpx2.Response(status, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
        content = [{"type": "text", "text": text if text is not None else json.dumps(handler_body or GOOD)}]
        return httpx2.Response(200, json=message(content, stop_reason))

    extractor = Extractor(anthropic_client(handler), model="claude-opus-5-5")
    return extractor, seen


def test_request_shape():
    extractor, seen = run()
    extractor.extract(parse("Ana: twelve euros\nBen: I'll update the site"), "2026-01-05", "Pricing",
                      [{"id": 7, "kind": "action", "owner": "Ben", "due": "", "meeting_title": "Kickoff",
                        "held_on": "2026-01-01", "text": "Send the deck"}])
    body = seen["body"]
    assert seen["url"].endswith("/v1/messages?beta=true")
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    prompt = body["messages"][0]["content"]
    assert "Meeting date: 2026-01-05" in prompt and "Title given by the user: Pricing" in prompt
    assert "[1] Ana: twelve euros" in prompt and "[2] Ben: I'll update the site" in prompt
    assert '#7 action, owner Ben (from "Kickoff", 2026-01-01): Send the deck' in prompt
    assert "commitment" in body["system"]


def test_response_parsing_drops_invalid_entries():
    extractor, _ = run()
    ex = extractor.extract(parse("Ana: hi"), "2026-01-05")
    assert isinstance(ex, Extraction)
    assert ex.participants == ["Ana", "Ben"]
    assert [(i.kind, i.text, i.evidence) for i in ex.items] == [("decision", "Price goes to €12", [1]),
                                                                ("action", "Update the site", [2])]
    assert [(u.item_id, u.status, u.note) for u in ex.updates] == [(7, "done", "sent")]


def test_schema_is_strict():
    assert SCHEMA["additionalProperties"] is False
    item = SCHEMA["properties"]["items"]["items"]
    assert set(item["required"]) == set(item["properties"]) and item["additionalProperties"] is False
    assert item["properties"]["kind"]["enum"] == ["decision", "action", "commitment", "question"]


@pytest.mark.parametrize("status,message_part", [(401, "API key"), (429, "Rate limited"), (500, "API error 500")])
def test_api_errors_become_readable(status, message_part):
    extractor, _ = run(status=status)
    with pytest.raises(ClaudeError, match=message_part):
        extractor.extract(parse("Ana: hi"), "2026-01-05")


def test_connection_error():
    def handler(request):
        raise httpx2.ConnectError("offline")

    with pytest.raises(ClaudeError, match="reach"):
        Extractor(anthropic_client(handler)).extract(parse("Ana: hi"), "2026-01-05")


def test_refusal():
    extractor, _ = run(stop_reason="refusal", text="")
    with pytest.raises(ClaudeError, match="declined"):
        extractor.extract(parse("Ana: hi"), "2026-01-05")


def test_max_tokens():
    extractor, _ = run(stop_reason="max_tokens", text='{"title": "x"')
    with pytest.raises(ClaudeError, match="cut off"):
        extractor.extract(parse("Ana: hi"), "2026-01-05")


def test_malformed_json():
    extractor, _ = run(text="not json")
    with pytest.raises(ClaudeError, match="malformed"):
        extractor.extract(parse("Ana: hi"), "2026-01-05")


def test_open_items_brief_empty_and_prompt_without_hint():
    assert open_items_brief([]) == "(none)"
    prompt = build_prompt(parse("hi"), "2026-01-05", "", [])
    assert "Title given" not in prompt and "<open_items>\n(none)\n</open_items>" in prompt
