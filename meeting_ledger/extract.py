"""Claude extracts: the TL;DR, decisions, action items, commitments and open questions of a
meeting, each pointing at the numbered transcript lines it came from. Claude also proposes which
items from earlier meetings this one moved on; the decision model then checks every proposal."""

import json
from dataclasses import dataclass, field

import anthropic

from .ingest import Segment, numbered

KINDS = ["decision", "action", "commitment", "question"]
UPDATE_STATUSES = ["progressed", "done", "blocked", "dropped", "contradicted"]

SYSTEM_PROMPT = """You turn raw meeting transcripts and notes into a precise record of what happened.

The transcript is given as numbered lines: [n] Speaker: text. Transcripts come from speech
recognition, so expect misheard words; never "correct" a name or number unless the transcript
itself makes the correction obvious.

Extract:
- decision: something the group decided or confirmed.
- action: a task someone will do. The owner is the person doing it.
- commitment: a promise one person made to another (pay, introduce, send, deliver, decide by a
  date). Promises about money, equity, introductions and deadlines matter most: never drop one.
- question: something left unresolved that needs a follow-up.

Rules:
- Only extract what the transcript actually says. If it is ambiguous, extract it and keep the
  ambiguity in the text; do not guess.
- evidence: the line numbers that support the item (usually 1-3).
- quote: a short verbatim excerpt (5-25 words) copied exactly from one of those lines.
- owner: the person responsible, as named in the transcript; "" if nobody took it on.
- due: an ISO date (YYYY-MM-DD) if it can be worked out from the meeting date, otherwise the words
  used ("next week"), otherwise "".
- tldr: one or two sentences: what the meeting was about and its main outcome.
- participants: everyone who spoke or was clearly present.

You are also given the items still open from earlier meetings, and recent decisions still standing.
For each one this meeting clearly talks about, add an update with its status (progressed, done, blocked, dropped, or contradicted
when this meeting says the opposite of it) and the evidence lines. Leave out items this meeting
doesn't mention: no update is the signal that something was forgotten."""


def _schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": KINDS},
            "text": {"type": "string"},
            "owner": {"type": "string"},
            "due": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "integer"}},
            "quote": {"type": "string"},
        },
        "required": ["kind", "text", "owner", "due", "evidence", "quote"],
        "additionalProperties": False,
    }
    update = {
        "type": "object",
        "properties": {
            "item_id": {"type": "integer"},
            "status": {"type": "string", "enum": UPDATE_STATUSES},
            "evidence": {"type": "array", "items": {"type": "integer"}},
            "quote": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["item_id", "status", "evidence", "quote", "note"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "tldr": {"type": "string"},
            "participants": {"type": "array", "items": {"type": "string"}},
            "items": {"type": "array", "items": item},
            "updates": {"type": "array", "items": update},
        },
        "required": ["title", "tldr", "participants", "items", "updates"],
        "additionalProperties": False,
    }


SCHEMA = _schema()


class ClaudeError(Exception):
    pass


@dataclass
class Item:
    kind: str
    text: str
    owner: str = ""
    due: str = ""
    evidence: list[int] = field(default_factory=list)
    quote: str = ""


@dataclass
class Update:
    item_id: int
    status: str
    evidence: list[int] = field(default_factory=list)
    quote: str = ""
    note: str = ""


@dataclass
class Extraction:
    title: str
    tldr: str
    participants: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    updates: list[Update] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> "Extraction":
        return cls(
            title=data.get("title", "").strip(),
            tldr=data.get("tldr", "").strip(),
            participants=[p.strip() for p in data.get("participants", []) if p.strip()],
            items=[Item(**{k: i[k] for k in Item.__dataclass_fields__ if k in i})
                   for i in data.get("items", []) if i.get("kind") in KINDS and i.get("text", "").strip()],
            updates=[Update(**{k: u[k] for k in Update.__dataclass_fields__ if k in u})
                     for u in data.get("updates", []) if u.get("status") in UPDATE_STATUSES],
        )


def open_items_brief(items: list[dict]) -> str:
    if not items:
        return "(none)"
    lines = []
    for i in items:
        owner = f", owner {i['owner']}" if i["owner"] else ""
        due = f", due {i['due']}" if i["due"] else ""
        lines.append(f"#{i['id']} {i['kind']}{owner}{due} (from \"{i['meeting_title']}\", {i['held_on']}): {i['text']}")
    return "\n".join(lines)


def build_prompt(segments: list[Segment], held_on: str, title_hint: str, open_items: list[dict]) -> str:
    hint = f"Title given by the user: {title_hint}\n" if title_hint else ""
    return (
        f"Meeting date: {held_on}\n{hint}\n"
        f"<open_items>\n{open_items_brief(open_items)}\n</open_items>\n\n"
        f"<transcript>\n{numbered(segments)}\n</transcript>"
    )


class Extractor:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str = "claude-opus-5-5"):
        self._client = client
        self.model = model

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def extract(self, segments: list[Segment], held_on: str, title_hint: str = "",
                open_items: list[dict] | None = None) -> Extraction:
        prompt = build_prompt(segments, held_on, title_hint, open_items or [])
        return Extraction.from_json(self._json_call(SYSTEM_PROMPT, prompt, SCHEMA))

    def _json_call(self, system: str, prompt: str, schema: dict, max_tokens: int = 16000) -> dict:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                # On a policy decline the API re-runs the request on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise ClaudeError("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY).") from e
        except anthropic.RateLimitError as e:
            raise ClaudeError("Rate limited by the Anthropic API; try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise ClaudeError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError("Could not reach the Anthropic API.") from e

        if response.stop_reason == "refusal":
            raise ClaudeError("Claude declined to process this transcript.")
        if response.stop_reason == "max_tokens":
            raise ClaudeError("The extraction was cut off (max_tokens). Try splitting the transcript.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ClaudeError("Claude returned malformed output.") from e
