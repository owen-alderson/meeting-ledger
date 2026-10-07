import json

import httpx2
from conftest import FakeSystemOne, choice, noul
from typesafe_sdk import TypeSafeClient

from meeting_ledger import decisions
from meeting_ledger.decisions import STATUSES, Decider, default_backend
from meeting_ledger.ingest import parse

EARLIER = {"kind": "action", "text": "Send the deck", "owner": "Ana", "meeting_title": "Kickoff", "held_on": "2026-01-05"}


def test_backend_selection(monkeypatch):
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert default_backend() == "claude-haiku-4-5 (adapter)"
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    assert default_backend() == "jev"
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:8009")
    monkeypatch.setenv("TYPESAFE_MODEL", "kev-latest")
    assert default_backend() == "kev-latest @ 127.0.0.1:8009"


def test_local_kev_client_uses_the_typesafe_sdk_with_base_url(monkeypatch):
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:8009")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    created = {}
    monkeypatch.setattr("typesafe_sdk.TypeSafeClient", lambda **kw: created.update(kw) or "client")
    assert Decider().client == "client"
    assert created == {"base_url": "http://127.0.0.1:8009", "model": "kev-latest", "api_key": "local"}


def test_adapter_fallback_client(monkeypatch):
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    created = {}
    monkeypatch.setattr("system_one_adapter.SystemOneAdapterClient", lambda **kw: created.update(kw) or "adapter")
    assert Decider().client == "adapter"
    assert created["provider"] == "anthropic" and created["model"] == "claude-haiku-4-5"
    assert created["structured_outputs"] is True


def test_check_item_questions_and_state():
    fake = FakeSystemOne(answers={"supported": noul(0.8), "agreed": noul(0.3)})
    verdict = Decider(client=fake, backend="t").check_item("action", "Send the deck", "Ana", "", parse("Ana: I'll send it"))
    state, questions = fake.calls[0]
    assert set(questions) == {"supported", "agreed"} and questions["supported"].type == "noul"
    assert state["item"] == {"kind": "action", "text": "Send the deck", "owner": "Ana", "due": "(none)"}
    assert state["transcript_lines"] == "[1] Ana: I'll send it"
    assert verdict.answers == {"supported": {"p": 0.8}, "agreed": {"p": 0.3}}
    assert verdict.backend == "t" and verdict.latency_ms >= 0


def test_check_item_for_unowned_question_asks_only_support():
    fake = FakeSystemOne()
    Decider(client=fake).check_item("question", "Licence?", "", "", [])
    state, questions = fake.calls[0]
    assert set(questions) == {"supported"} and state["transcript_lines"] == "(no lines)"


def test_check_update_is_a_choice_over_every_status():
    fake = FakeSystemOne(answers={"status": choice("blocked", 0.77)})
    verdict = Decider(client=fake).check_update(EARLIER, parse("Ana: stuck waiting on legal"))
    state, questions = fake.calls[0]
    assert questions["status"].type == "choice" and set(questions["status"].criteria) == set(STATUSES)
    assert state["open_item"]["from_meeting"] == "Kickoff (2026-01-05)"
    assert verdict.answers["status"] == {"choice": "blocked", "confidence": 0.77}


def test_real_sdk_request_to_a_kev_compatible_server():
    """The real TypeSafe SDK pointed at a local base_url, as with Kev: the wire format we depend on."""
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx2.Response(200, json={
            "model": "kev-latest", "usage": {"input_tokens": 50, "output_tokens": 0},
            "answers": {"status": {"type": "choice", "choice": "done", "confidence": 0.81,
                                   "probabilities": {k: (0.81 if k == "done" else 0.038) for k in STATUSES}}},
        })

    client = TypeSafeClient(api_key="local", base_url="http://kev.local:8009", model="kev-latest",
                            transport=httpx2.MockTransport(handler))
    verdict = Decider(client=client, backend="kev").check_update(EARLIER, parse("Ana: deck sent"))
    assert seen["url"] == "http://kev.local:8009/v1/systemone"
    assert seen["body"]["model"] == "kev-latest"
    assert seen["body"]["questions"]["status"]["type"] == "choice"
    assert verdict.answers["status"] == {"choice": "done", "confidence": 0.81}


def test_flag_threshold_constant():
    assert decisions.FLAG_BELOW == 0.5
