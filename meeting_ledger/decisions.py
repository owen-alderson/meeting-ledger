"""The decision model checks Claude's work with typed, calibrated answers.

Every question is a System One primitive (Noul = probability a statement is true, Choice = one
label from a set with its confidence). Three interchangeable backends answer them:

- Kev, or any server with the same API (TYPESAFE_BASE_URL): open weights, runs on your own machine
- Jev, TypeSafe's hosted model (TYPESAFE_API_KEY)
- otherwise TypeSafe's open-source system-one-adapter, which asks Claude Haiku the same questions
"""

import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from typesafe_sdk import Choice, Noul

from .ingest import Segment

FALLBACK_MODEL = "claude-haiku-4-5"
FLAG_BELOW = 0.5  # an item whose "supported" or "agreed" probability is below this goes to review

ITEM_QUESTIONS = {
    "supported": Noul(
        instructions=(
            "The transcript lines support the item as written: same meaning, and the owner and due "
            "date (if the item gives them) match what was said."
        )
    ),
}

AGREED_QUESTION = Noul(
    instructions=(
        "The owner explicitly agreed to do this or committed to it in these lines, rather than the "
        "task being suggested, floated, or assigned to them without them accepting it."
    )
)

STATUSES = {
    "not_mentioned": "These lines don't actually say anything about the item",
    "progressed": "Work on it moved forward or it was reaffirmed, but it isn't finished",
    "done": "It was completed, delivered, or (for a question) answered",
    "blocked": "It is stuck, waiting on something or someone else",
    "dropped": "It was cancelled or is no longer needed",
    "contradicted": "These lines say the opposite of it (a reversed decision, a withdrawn promise)",
}

STATUS_QUESTION = Choice(
    instructions="What do these lines from a later meeting say about the open item from an earlier meeting?",
    criteria=STATUSES,
)


@dataclass
class Verdict:
    answers: dict
    backend: str
    latency_ms: float


def _answers(response) -> dict:
    """Flatten an SDK SystemOneResponse into plain JSON-able values."""
    out = {}
    for name, a in response.answers.items():
        if a.type == "noul":
            out[name] = {"p": a.noul}
        elif a.type == "choice":
            out[name] = {"choice": a.choice, "confidence": a.confidence}
        else:
            out[name] = {"score": a.score, "max": max(a.legend), "confidence": a.confidence}
    return out


def default_backend() -> str:
    base = os.environ.get("TYPESAFE_BASE_URL", "").strip()
    if base:
        model = os.environ.get("TYPESAFE_MODEL", "kev-latest")
        return f"{model} @ {urlparse(base).netloc or base}"
    if os.environ.get("TYPESAFE_API_KEY"):
        return "jev"
    return f"{FALLBACK_MODEL} (adapter)"


def lines_state(segments: list[Segment]) -> str:
    return "\n".join(s.label() for s in segments) or "(no lines)"


class Decider:
    def __init__(self, client=None, backend: str | None = None):
        self._client = client
        self.backend = backend or default_backend()

    @property
    def client(self):
        if self._client is None:
            base = os.environ.get("TYPESAFE_BASE_URL", "").strip()
            if base or self.backend == "jev":
                from typesafe_sdk import TypeSafeClient

                kwargs = {}
                if base:
                    kwargs = {"base_url": base, "model": os.environ.get("TYPESAFE_MODEL", "kev-latest"),
                              "api_key": os.environ.get("TYPESAFE_API_KEY") or "local"}
                self._client = TypeSafeClient(**kwargs)
            else:
                from system_one_adapter import SystemOneAdapterClient

                self._client = SystemOneAdapterClient(
                    structured_outputs=True,
                    llm_answer_mode="probabilities",
                    normalize_probabilities=True,
                    provider="anthropic",
                    model=FALLBACK_MODEL,
                )
        return self._client

    def ask(self, state: dict, questions: dict) -> Verdict:
        start = time.perf_counter()
        response = self.client.system_one(state=state, questions=questions)
        return Verdict(_answers(response), self.backend, (time.perf_counter() - start) * 1000)

    def check_item(self, kind: str, text: str, owner: str, due: str, lines: list[Segment]) -> Verdict:
        """Is this freshly extracted item supported by its lines, and did its owner really take it on?"""
        questions = dict(ITEM_QUESTIONS)
        if kind in ("action", "commitment") and owner:
            questions["agreed"] = AGREED_QUESTION
        state = {"item": {"kind": kind, "text": text, "owner": owner or "(none)", "due": due or "(none)"},
                 "transcript_lines": lines_state(lines)}
        return self.ask(state, questions)

    def check_update(self, item: dict, lines: list[Segment]) -> Verdict:
        """Independently of Claude, what do these later lines say about an earlier open item?"""
        state = {
            "open_item": {"kind": item["kind"], "text": item["text"], "owner": item["owner"] or "(none)",
                          "from_meeting": f"{item['meeting_title']} ({item['held_on']})"},
            "later_meeting_lines": lines_state(lines),
        }
        return self.ask(state, {"status": STATUS_QUESTION})
