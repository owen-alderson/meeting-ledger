"""An MCP server over your meeting ledger, so Claude Desktop, Claude Code or any MCP client can
search past meetings and track open items. Runs locally over stdio: `meeting-ledger mcp`.

The tools return raw transcript lines and items with their meeting and line numbers; the client's
own model does the reasoning, so no Anthropic key is needed here.
"""

from .ledger import Ledger, LedgerError
from .store import Store


def _item(i: dict, ledger: Ledger) -> dict:
    return {
        "id": i["id"], "kind": i["kind"], "text": i["text"], "owner": i["owner"], "due": i["due"],
        "status": i["status"], "stale": ledger.is_stale(i), "meetings_missed": i["misses"],
        "from_meeting": {"id": i["meeting_id"], "title": i.get("meeting_title", ""), "date": i.get("held_on", "")},
        "evidence_lines": i["evidence"], "quote": i["quote"], "needs_review": bool(i["flags"]),
    }


def search_meetings(store: Store, query: str, limit: int = 20) -> list[dict]:
    hits = store.search(query, max(1, min(limit, 50)))
    return [{"meeting_id": h["meeting_id"], "meeting": h["title"], "date": h["held_on"], "line": h["n"],
             "speaker": h["speaker"], "text": h["text"]} for h in hits]


def list_meetings(store: Store, limit: int = 20) -> list[dict]:
    return [{"id": m["id"], "title": m["title"], "date": m["held_on"], "tldr": m["tldr"],
             "participants": m["participants"], "items": m["item_count"]} for m in store.meetings()[:limit]]


def list_open_items(ledger: Ledger, owner: str = "", stale_only: bool = False) -> list[dict]:
    return [_item(i, ledger) for i in ledger.open_items(owner, stale_only)]


def get_meeting(ledger: Ledger, meeting_id: int, include_transcript: bool = True) -> dict:
    store = ledger.store
    m = store.meeting(meeting_id)
    if m is None:
        raise LedgerError(f"No meeting #{meeting_id}.")
    out = {"id": m["id"], "title": m["title"], "date": m["held_on"], "tldr": m["tldr"],
           "participants": m["participants"],
           "items": [_item({**i, "meeting_title": m["title"], "held_on": m["held_on"]}, ledger) for i in store.items(meeting_id)]}
    if include_transcript:
        out["transcript"] = [{"line": s.n, "speaker": s.speaker, "text": s.text} for s in store.segments(meeting_id)]
    return out


def update_item(ledger: Ledger, item_id: int, status: str, note: str = "") -> dict:
    return _item(ledger.set_status(item_id, status, note), ledger)


def build_server(ledger: Ledger):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        "meeting-ledger",
        instructions=(
            "The user's meeting ledger: transcripts of past meetings with numbered lines, and the decisions, "
            "action items, commitments and open questions taken from them, with their status over time. "
            "Cite meetings by title and date and lines as L<n>. Items marked stale were not mentioned in "
            "several meetings their owner attended."
        ),
    )

    @server.tool(description="Full-text search over every meeting transcript. Returns matching lines with meeting, date and line number.")
    def search(query: str, limit: int = 20) -> list[dict]:
        return search_meetings(ledger.store, query, limit)

    @server.tool(description="List meetings, newest first, with their TL;DR.")
    def meetings(limit: int = 20) -> list[dict]:
        return list_meetings(ledger.store, limit)

    @server.tool(description="Open action items, commitments and questions. Filter by owner (name or part of it) or only stale ones.")
    def open_items(owner: str = "", stale_only: bool = False) -> list[dict]:
        return list_open_items(ledger, owner, stale_only)

    @server.tool(description="One meeting: TL;DR, participants, items with their evidence lines, and optionally the numbered transcript.")
    def meeting(meeting_id: int, include_transcript: bool = True) -> dict:
        return get_meeting(ledger, meeting_id, include_transcript)

    @server.tool(description="Change an item's status. Actions, commitments and questions: open, blocked, done, dropped. Decisions: standing, reversed.")
    def set_item_status(item_id: int, status: str, note: str = "") -> dict:
        return update_item(ledger, item_id, status, note)

    return server
