"""Headless UI tests (Textual Pilot) on the demo ledger."""

import pytest
from textual.widgets import DataTable, Static, TabbedContent

from meeting_ledger.demo import seed
from meeting_ledger.tui import LedgerApp


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return LedgerApp(seed(), demo=True)


def text(app, selector) -> str:
    return str(app.query_one(selector, Static).render())


def table(app, selector) -> DataTable:
    return app.query_one(selector, DataTable)


async def test_title_bar_counts(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        assert text(app, "#title") == ("MEETING LEDGER  ·  3 meetings  ·  6 open  ·  1 stale  ·  3 to review  ·  "
                                       "DEMO (fictional data)")


async def test_opens_on_the_latest_meeting(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        assert table(app, "#meeting-list").row_count == 3
        assert "Weekly sync with Sam" in text(app, "#tldr")
        assert "Moved: Introduce Maya to the head buyer at Northside Market → open" in text(app, "#tldr")
        assert table(app, "#transcript").row_count == 9


async def test_highlighting_an_item_jumps_to_its_cited_lines(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        items = table(app, "#meeting-items")
        items.focus()
        await pilot.press("down")  # second item of the latest meeting: margins sheet, lines 7-8
        await pilot.pause()
        assert app.cited == {7, 8}
        assert table(app, "#transcript").cursor_row == 6


async def test_switching_meetings(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        meetings = table(app, "#meeting-list")
        meetings.focus()
        await pilot.press("down", "down")
        await pilot.pause()
        assert app.meeting_id == 1 and "Wholesale launch sync" in text(app, "#tldr")
        assert table(app, "#meeting-items").row_count == 5


async def test_open_items_tab_marks_stale_and_sets_status(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "items"
        items = table(app, "#open-items")
        assert items.row_count == 6
        invoice_row = next(r for r in range(items.row_count) if "invoice" in str(items.get_row_at(r)[3]))
        assert "STALE" in str(items.get_row_at(invoice_row)[6])
        items.focus()
        items.move_cursor(row=invoice_row)
        await pilot.press("d")
        await pilot.pause()
        assert items.row_count == 5
        assert "0 stale" in text(app, "#title")
        assert "→ done" in text(app, "#status")


async def test_stale_filter(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("2", "s")
        await pilot.pause()
        assert table(app, "#open-items").row_count == 1
        await pilot.press("s")
        await pilot.pause()
        assert table(app, "#open-items").row_count == 6


async def test_selecting_an_open_item_opens_its_meeting(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("2")
        items = table(app, "#open-items")
        items.focus()
        await pilot.press("enter")  # first row: the Northside intro, from the first meeting, line 6
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "meetings"
        assert app.meeting_id == 1 and app.cited == {6}


async def test_review_detail_and_accept(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("3")
        reviews = table(app, "#review-table")
        reviews.focus()
        await pilot.pause()
        assert reviews.row_count == 3
        assert "Theo could redo the website" in text(app, "#review-detail")
        await pilot.press("a")
        await pilot.pause()
        assert reviews.row_count == 2
        assert app.ledger.store.item(app.ledger.store.review(1)["item_id"])["flags"] == []


async def test_review_use_the_checkers_status(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("3")
        reviews = table(app, "#review-table")
        reviews.focus()
        reviews.move_cursor(row=2)  # the inspection: Claude said progressed, the checker said done
        await pilot.pause()
        assert "Decision model: done (55%)" in text(app, "#review-detail")
        await pilot.press("c")
        await pilot.pause()
        inspection = next(i for i in app.ledger.store.items(2) if "inspection" in i["text"])
        assert inspection["status"] == "done"


async def test_review_reject_deletes_a_bad_item(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("3")
        table(app, "#review-table").focus()
        await pilot.press("r")
        await pilot.pause()
        assert not any("website" in i["text"] for i in app.ledger.store.items(2))


async def test_status_keys_do_nothing_on_the_review_tab_and_vice_versa(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("a")  # accept outside the review tab
        await pilot.pause()
        assert len(app.ledger.store.reviews()) == 3


async def test_ask_without_a_key_shows_matching_lines(app):
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.press("4")
        await pilot.click("#question")
        await pilot.press(*"online shop", "enter")
        await pilot.pause()
        answer = text(app, "#answer")
        assert "set ANTHROPIC_API_KEY" in answer and "park the online shop until January" in answer
