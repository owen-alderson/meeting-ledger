from meeting_ledger.ingest import parse
from meeting_ledger.store import Store, fts_query


def add(store, text="Ana: hello world\nBen: the budget is due", held_on="2026-01-05", title="Sync"):
    return store.add_meeting(title, held_on, parse(text), "notes.txt", "tl;dr", ["Ana", "Ben"], "m")


def test_meeting_round_trip(store):
    mid = add(store)
    m = store.meeting(mid)
    assert (m["title"], m["held_on"], m["participants"], m["source"]) == ("Sync", "2026-01-05", ["Ana", "Ben"], "notes.txt")
    assert [s.text for s in store.segments(mid)] == ["hello world", "the budget is due"]


def test_segments_subset(store):
    mid = add(store)
    assert [s.n for s in store.segments(mid, [2])] == [2]


def test_meetings_newest_first_with_item_count(store):
    a = add(store, held_on="2026-01-05")
    b = add(store, held_on="2026-02-01")
    store.add_item(a, "action", "x")
    assert [(m["id"], m["item_count"]) for m in store.meetings()] == [(b, 0), (a, 1)]


def test_item_defaults(store):
    mid = add(store)
    action = store.item(store.add_item(mid, "action", "Send", "Ana", evidence=[1], flags=["x"]))
    decision = store.item(store.add_item(mid, "decision", "Go"))
    assert action["status"] == "open" and action["evidence"] == [1] and action["flags"] == ["x"]
    assert action["meeting_title"] == "Sync"
    assert decision["status"] == "standing"


def test_open_items_excludes_decisions_and_closed(store):
    mid = add(store)
    keep = store.add_item(mid, "action", "a", "Ana")
    store.add_item(mid, "decision", "d")
    done = store.add_item(mid, "action", "b")
    store.update_item(done, status="done")
    blocked = store.add_item(mid, "commitment", "c")
    store.update_item(blocked, status="blocked")
    assert [i["id"] for i in store.open_items()] == [keep, blocked]
    assert [i["id"] for i in store.open_items(owner="an")] == [keep]


def test_delete_meeting_cascades(store):
    mid = add(store)
    iid = store.add_item(mid, "action", "x")
    store.add_review(iid, mid, "item", "why")
    store.delete_meeting(mid)
    assert store.meeting(mid) is None and store.item(iid) is None
    assert store.reviews() == [] and store.search("budget") == []


def test_search_ranks_and_joins_meeting(store):
    add(store, "Ana: lunch\nBen: the budget for marketing is due friday", title="Budget review")
    hits = store.search("what is the marketing budget?")
    assert hits[0]["title"] == "Budget review" and hits[0]["n"] == 2


def test_search_stems_words(store):
    add(store, "Ana: we are hiring two engineers")
    assert store.search("hire engineer")


def test_search_with_only_stopwords_or_symbols(store):
    add(store)
    assert store.search("what is the") == []
    assert store.search('") OR *') == []


def test_fts_query_quotes_terms():
    assert fts_query('budget "NEAR" x') == '"budget" OR "near"'


def test_reviews_and_resolution(store):
    mid = add(store)
    iid = store.add_item(mid, "action", "x")
    rid = store.add_review(iid, mid, "update", "disagree", "done", "progressed", 0.6, [1])
    r = store.reviews()[0]
    assert (r["item_text"], r["proposed"], r["checked"], r["evidence"]) == ("x", "done", "progressed", [1])
    store.resolve_review(rid, "accepted")
    assert store.reviews() == [] and store.review(rid)["resolved"] == "accepted"


def test_decision_stats(store):
    store.log_decision("item_check", "jev", 100)
    store.log_decision("item_check", "jev", 300)
    assert store.decision_stats() == [{"backend": "jev", "kind": "item_check", "calls": 2, "avg_ms": 200.0}]


def test_file_database_persists(tmp_path):
    path = tmp_path / "sub" / "ledger.db"
    s = Store(path)
    add(s)
    s.close()
    assert len(Store(path).meetings()) == 1
