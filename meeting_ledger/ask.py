"""Ask your meetings a question. The best-matching transcript lines go to Claude as search-result
blocks with citations on, so every sentence of the answer points back to a meeting and its lines."""

from dataclasses import dataclass, field

import anthropic

from .extract import ClaudeError
from .store import Store

SYSTEM_PROMPT = """You answer questions about the user's past meetings using only the meeting excerpts
provided. Each excerpt is a run of numbered transcript lines from one meeting. Be brief and
concrete: who said or decided what, and when. If the excerpts don't answer the question, say so
plainly instead of guessing. When meetings disagree, give the most recent position and note that
it changed."""


@dataclass
class Cite:
    meeting_id: int
    title: str
    held_on: str
    lines: list[int]
    text: str


@dataclass
class Part:
    text: str
    cites: list[Cite] = field(default_factory=list)


@dataclass
class Answer:
    parts: list[Part]
    hits: list[dict]

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts)

    def sources(self) -> list[Cite]:
        """Distinct citations in the order they first appear."""
        seen, out = set(), []
        for p in self.parts:
            for c in p.cites:
                key = (c.meeting_id, tuple(c.lines))
                if key not in seen:
                    seen.add(key)
                    out.append(c)
        return out


def group_hits(hits: list[dict]) -> list[dict]:
    """One search result per meeting, its matching lines in transcript order."""
    meetings: dict[int, dict] = {}
    for h in hits:
        m = meetings.setdefault(h["meeting_id"], {"meeting_id": h["meeting_id"], "title": h["title"],
                                                  "held_on": h["held_on"], "lines": []})
        m["lines"].append(h)
    for m in meetings.values():
        m["lines"].sort(key=lambda h: h["n"])
    return sorted(meetings.values(), key=lambda m: m["held_on"], reverse=True)


def search_blocks(groups: list[dict]) -> list[dict]:
    return [
        {
            "type": "search_result",
            "source": f"meeting:{g['meeting_id']}",
            "title": f"{g['title']} ({g['held_on']})",
            "content": [{"type": "text", "text": f"[{h['n']}] {h['speaker'] + ': ' if h['speaker'] else ''}{h['text']}"}
                        for h in g["lines"]],
            "citations": {"enabled": True},
        }
        for g in groups
    ]


def to_parts(content, groups: list[dict]) -> list[Part]:
    parts = []
    for block in content:
        if block.type != "text":
            continue
        cites = []
        for c in getattr(block, "citations", None) or []:
            if getattr(c, "type", "") != "search_result_location" or c.search_result_index >= len(groups):
                continue
            g = groups[c.search_result_index]
            lines = [h["n"] for h in g["lines"][c.start_block_index:c.end_block_index]]
            cites.append(Cite(g["meeting_id"], g["title"], g["held_on"], lines, c.cited_text))
        parts.append(Part(block.text, cites))
    return parts


class Asker:
    def __init__(self, store: Store, client: anthropic.Anthropic | None = None, model: str = "claude-opus-5-5"):
        self.store = store
        self._client = client
        self.model = model

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def ask(self, question: str, limit: int = 24) -> Answer:
        hits = self.store.search(question, limit)
        if not hits:
            return Answer([Part("Nothing in your meetings matches that question.")], [])
        groups = group_hits(hits)
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": [*search_blocks(groups), {"type": "text", "text": question}]}],
                output_config={"effort": "low"},
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
            raise ClaudeError("Claude declined to answer this one.")
        return Answer(to_parts(response.content, groups), hits)
