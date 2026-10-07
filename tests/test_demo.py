"""The demo runs the real pipeline end to end, so it doubles as an integration test of the story the
README tells."""

from meeting_ledger.demo import seed


def test_demo_story():
    ledger = seed()
    store = ledger.store
    assert [m["title"] for m in store.meetings()] == ["Weekly sync with Sam", "Weekly sync", "Wholesale launch sync"]

    statuses = {i["text"]: i["status"] for m in store.meetings() for i in store.items(m["id"])}
    assert statuses["Send the price sheet to Bloom Café, Hartley's and the Corner Room"] == "done"  # agreed, applied
    assert statuses["Is a food-safety certificate needed to sell wholesale?"] == "done"

    reviews = {r["item_text"]: r for r in store.reviews()}
    assert "contradicts" in reviews["Launch wholesale with Bloom Café, Hartley's and the Corner Room first; park the online shop until January"]["reason"]
    assert reviews["Book the council food-safety inspection"]["checked"] == "done"  # the two models disagree
    assert reviews["Redo the website menu page"]["kind"] == "item"  # Theo never agreed

    stale = ledger.open_items(stale_only=True)
    assert [(i["text"], i["misses"]) for i in stale] == [("Pay Priya's label design invoice", 2)]
    sam = next(i for i in ledger.open_items() if i["owner"] == "Sam")
    assert sam["misses"] == 0  # Sam missed the second meeting, so it doesn't count against him


def test_demo_is_in_memory_and_repeatable():
    a, b = seed(), seed()
    assert len(a.store.meetings()) == len(b.store.meetings()) == 3
