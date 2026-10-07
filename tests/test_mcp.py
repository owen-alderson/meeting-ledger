import pytest
from mcp import Client

from meeting_ledger import mcp_server
from meeting_ledger.demo import seed
from meeting_ledger.ledger import LedgerError


@pytest.fixture
def ledger():
    return seed()


def test_search(ledger):
    hits = mcp_server.search_meetings(ledger.store, "Northside intro")
    assert hits[0] == {"meeting_id": 3, "meeting": "Weekly sync with Sam", "date": "2026-10-06", "line": 2,
                       "speaker": "Sam", "text": "Thanks. Before you ask, I haven't done the Northside intro yet. "
                                                "It's top of my list for next week."}


def test_search_limit_is_clamped(ledger):
    assert len(mcp_server.search_meetings(ledger.store, "the café cafés Maya Theo", limit=1000)) <= 50
    assert len(mcp_server.search_meetings(ledger.store, "Maya", limit=0)) == 1


def test_open_items_and_stale(ledger):
    items = mcp_server.list_open_items(ledger)
    invoice = next(i for i in items if "invoice" in i["text"])
    assert invoice["stale"] and invoice["meetings_missed"] == 2 and invoice["owner"] == "Maya"
    assert invoice["from_meeting"] == {"id": 1, "title": "Wholesale launch sync", "date": "2026-09-22"}
    assert [i["text"] for i in mcp_server.list_open_items(ledger, stale_only=True)] == ["Pay Priya's label design invoice"]
    assert all(i["owner"] == "Sam" for i in mcp_server.list_open_items(ledger, owner="sam"))


def test_get_meeting(ledger):
    m = mcp_server.get_meeting(ledger, 1)
    assert m["title"] == "Wholesale launch sync" and len(m["items"]) == 5 and m["transcript"][0]["line"] == 1
    assert "transcript" not in mcp_server.get_meeting(ledger, 1, include_transcript=False)
    with pytest.raises(LedgerError):
        mcp_server.get_meeting(ledger, 42)


def test_update_item(ledger):
    invoice = next(i for i in ledger.open_items() if "invoice" in i["text"])
    out = mcp_server.update_item(ledger, invoice["id"], "done", "paid on the 7th")
    assert out["status"] == "done" and not out["stale"]
    with pytest.raises(LedgerError):
        mcp_server.update_item(ledger, invoice["id"], "teleported")


def test_list_meetings(ledger):
    assert [m["date"] for m in mcp_server.list_meetings(ledger.store)] == ["2026-10-06", "2026-09-29", "2026-09-22"]


async def test_server_over_the_mcp_protocol(ledger):
    """A real MCP client talking to the real server in-process."""
    server = mcp_server.build_server(ledger)
    async with Client(server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {"search", "meetings", "open_items", "meeting", "set_item_status"}
        result = await client.call_tool("open_items", {"stale_only": True})
        assert not result.is_error
        assert "Pay Priya's label design invoice" in result.content[0].text
        bad = await client.call_tool("meeting", {"meeting_id": 99})
        assert bad.is_error
