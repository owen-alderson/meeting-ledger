import json

import httpx2
import pytest
from typesafe_sdk import SystemOneResponse

from meeting_ledger.config import Settings
from meeting_ledger.decisions import Decider
from meeting_ledger.extract import Extraction, Item, Update
from meeting_ledger.ingest import parse
from meeting_ledger.ledger import Ledger
from meeting_ledger.store import Store


class FakeSystemOne:
    """Stands in for TypeSafeClient: answers every question with a real SystemOneResponse.

    Defaults: every Noul is 95% likely, every Choice picks its first label at 90%. `answers`
    overrides by question name; `fn(state, questions)` overrides per call.
    """

    def __init__(self, answers=None, fn=None, error=None):
        self.answers, self.fn, self.error, self.calls = answers or {}, fn, error, []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        overrides = dict(self.answers)
        if self.fn:
            overrides.update(self.fn(state, questions) or {})
        out = {}
        for name, q in questions.items():
            if name in overrides:
                out[name] = overrides[name]
            elif q.type == "noul":
                out[name] = noul(0.95)
            else:
                out[name] = choice(next(iter(q.criteria)), 0.9)
        return SystemOneResponse.model_validate_json(
            json.dumps({"model": "jev-test", "usage": {"input_tokens": 10, "output_tokens": 0}, "answers": out})
        )


def noul(p):
    return {"type": "noul", "noul": p}


def choice(label, confidence=0.95):
    return {"type": "choice", "choice": label, "confidence": confidence, "probabilities": {label: confidence}}


class FakeExtractor:
    """Returns queued Extractions in order, recording what it was given. Updates may name their
    item by a piece of its text (item="price sheet") instead of an id."""

    model = "fake-claude"

    def __init__(self, *extractions, error=None):
        self.queue, self.error, self.calls = list(extractions), error, []

    def extract(self, segments, held_on, title_hint="", open_items=None):
        self.calls.append({"segments": segments, "held_on": held_on, "title": title_hint, "open_items": open_items or []})
        if self.error:
            raise self.error
        ex = self.queue.pop(0)
        for u in ex.updates:
            if isinstance(u.item_id, str):
                u.item_id = next(i["id"] for i in open_items if u.item_id.lower() in i["text"].lower())
        return ex


def extraction(items=(), updates=(), title="Sync", tldr="We met.", participants=("Ana", "Ben")) -> Extraction:
    return Extraction(title, tldr, list(participants), list(items), list(updates))


def item(kind="action", text="Send the deck", owner="Ana", due="", evidence=(1,), quote="send the deck") -> Item:
    return Item(kind, text, owner, due, list(evidence), quote)


def update(target, status="done", evidence=(1,), quote="", note="") -> Update:
    return Update(target, status, list(evidence), quote, note)


TRANSCRIPT_1 = """Ana: I'll send the deck to Ben by Friday.
Ben: Great. I promise to introduce you to Carla at Northwind.
Ana: We decided to launch in Lisbon first.
Ben: Do we need a licence for Lisbon?"""

TRANSCRIPT_2 = """Ana: The deck went out on Thursday.
Ben: Carla is travelling, so the intro is blocked until she's back.
Ana: Let's talk about hiring."""


@pytest.fixture
def segs():
    return lambda text=TRANSCRIPT_1, name="": parse(text, name)


@pytest.fixture
def system_one():
    return FakeSystemOne()


@pytest.fixture
def store():
    s = Store()
    yield s
    s.close()


@pytest.fixture
def make_ledger(store, system_one):
    def make(*extractions, settings=None):
        return Ledger(store, FakeExtractor(*extractions), Decider(client=system_one, backend="fake"), settings or Settings())
    return make


def anthropic_client(handler):
    """A real Anthropic SDK client whose HTTP goes to `handler(request) -> httpx2.Response`."""
    import anthropic

    return anthropic.Anthropic(api_key="sk-test", max_retries=0,
                               http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def message(content, stop_reason="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}
