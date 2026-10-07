import pytest
from conftest import TRANSCRIPT_1, TRANSCRIPT_2, choice, extraction, item, noul, update

from meeting_ledger.config import Settings
from meeting_ledger.ingest import parse
from meeting_ledger.ledger import LedgerError, grounded, new_status, owner_present

FIRST = extraction([
    item("action", "Send the deck to Ben", "Ana", "2026-01-09", [1], "I'll send the deck to Ben by Friday"),
    item("commitment", "Introduce Ana to Carla at Northwind", "Ben", "", [2], "I promise to introduce you to Carla"),
    item("decision", "Launch in Lisbon first", "", "", [3], "We decided to launch in Lisbon first"),
    item("question", "Is a licence needed for Lisbon?", "", "", [4], "Do we need a licence for Lisbon?"),
])


def add_first(ledger):
    return ledger.add(parse(TRANSCRIPT_1), "2026-01-05", source="one.txt")


def test_add_stores_meeting_items_and_segments(make_ledger):
    ledger = make_ledger(FIRST)
    r = add_first(ledger)
    m = ledger.store.meeting(r.meeting_id)
    assert (m["title"], m["tldr"], m["held_on"], m["model"]) == ("Sync", "We met.", "2026-01-05", "fake-claude")
    assert [i["kind"] for i in r.items] == ["action", "commitment", "decision", "question"]
    assert len(ledger.store.segments(r.meeting_id)) == 4
    assert r.reviews == 0 and all(not i["flags"] for i in r.items)


def test_user_title_wins_over_claude_title(make_ledger):
    ledger = make_ledger(FIRST)
    r = ledger.add(parse(TRANSCRIPT_1), "2026-01-05", title="Board call")
    assert ledger.store.meeting(r.meeting_id)["title"] == "Board call"


def test_participants_include_speakers_claude_missed(make_ledger):
    ledger = make_ledger(extraction(participants=["Ana Silva"]))
    r = ledger.add(parse("Ana: hi\nBen: hello\nCleo: hey"), "2026-01-05")
    assert ledger.store.meeting(r.meeting_id)["participants"] == ["Ana Silva", "Ben", "Cleo"]


def test_empty_input_is_an_error(make_ledger):
    with pytest.raises(LedgerError):
        make_ledger(FIRST).add([], "2026-01-05")


def test_extractor_gets_numbered_segments_date_and_open_items(make_ledger):
    ledger = make_ledger(FIRST, extraction())
    add_first(ledger)
    ledger.add(parse(TRANSCRIPT_2), "2026-01-12", title="Follow-up")
    second = ledger.extractor.calls[1]
    assert second["held_on"] == "2026-01-12" and second["title"] == "Follow-up"
    texts = [i["text"] for i in second["open_items"]]
    # open action, commitment and question, plus the standing decision so a reversal can be spotted
    assert texts == ["Send the deck to Ben", "Introduce Ana to Carla at Northwind", "Is a licence needed for Lisbon?",
                     "Launch in Lisbon first"]


# grounding ---------------------------------------------------------------------------------------

def test_grounded_quote():
    segs = parse(TRANSCRIPT_1)
    assert grounded("i’ll send the DECK to ben", segs[:1])
    assert not grounded("I'll send the budget", segs[:1])
    assert not grounded("", segs[:1])


def test_quote_not_in_cited_lines_is_flagged_for_review(make_ledger):
    ledger = make_ledger(extraction([item(evidence=[2], quote="I'll send the deck to Ben")]))
    r = add_first(ledger)
    assert r.items[0]["flags"] == ["quote_not_found"]
    assert r.reviews == 1 and ledger.store.reviews()[0]["reason"] == "Its quote isn't in the lines it cites"


def test_invalid_or_missing_evidence_is_flagged(make_ledger):
    ledger = make_ledger(extraction([item(evidence=[99, 0])]))
    r = add_first(ledger)
    assert r.items[0]["evidence"] == [] and "no_evidence" in r.items[0]["flags"]


def test_duplicate_evidence_lines_are_kept_once(make_ledger):
    ledger = make_ledger(extraction([item(evidence=[1, 1], quote="send the deck")]))
    assert add_first(ledger).items[0]["evidence"] == [1]


# the decision model on new items -----------------------------------------------------------------

def test_item_check_gets_cited_lines_with_one_line_of_context(make_ledger, system_one):
    ledger = make_ledger(extraction([item(evidence=[2], quote="I promise to introduce you to Carla", kind="commitment", owner="Ben")]))
    add_first(ledger)
    state, questions = system_one.calls[0]
    assert state["transcript_lines"].splitlines()[0].startswith("[1] Ana:")
    assert state["transcript_lines"].splitlines()[-1].startswith("[3] Ana:")
    assert set(questions) == {"supported", "agreed"}


def test_decisions_and_unowned_items_are_not_asked_about_agreement(make_ledger, system_one):
    ledger = make_ledger(extraction([item("decision", "Launch", "", "", [3], "launch in Lisbon"),
                                     item("action", "Book a room", "", "", [1], "send the deck")]))
    add_first(ledger)
    assert all(set(q) == {"supported"} for _, q in system_one.calls)


def test_low_support_and_no_agreement_are_flagged(make_ledger, system_one):
    system_one.answers = {"supported": noul(0.2), "agreed": noul(0.1)}
    r = add_first(make_ledger(extraction([item(quote="send the deck")])))
    assert r.items[0]["flags"] == ["unsupported", "not_agreed"]
    assert (r.items[0]["supported"], r.items[0]["agreed"]) == (0.2, 0.1)


def test_decision_backend_failure_keeps_the_item_but_flags_it(make_ledger, system_one):
    system_one.error = RuntimeError("down")
    r = add_first(make_ledger(extraction([item(quote="send the deck")])))
    assert r.items[0]["flags"] == ["unchecked"] and r.reviews == 1


def test_decisions_are_logged_with_backend_and_latency(make_ledger):
    ledger = make_ledger(FIRST)
    add_first(ledger)
    stats = ledger.store.decision_stats()
    assert stats[0]["backend"] == "fake" and stats[0]["kind"] == "item_check" and stats[0]["calls"] == 4


# follow-ups on earlier items ---------------------------------------------------------------------

def test_agreed_confident_update_is_applied_with_evidence(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [1], "The deck went out on Thursday", "sent")]))
    add_first(ledger)
    system_one.answers = {"status": choice("done", 0.92)}
    r = ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    assert [(i["text"], s) for i, s in r.applied] == [("Send the deck to Ben", "done")]
    deck = ledger.store.item(r.applied[0][0]["id"])
    assert deck["status"] == "done"
    event = ledger.store.events(deck["id"])[0]
    assert (event["by"], event["evidence"], event["note"], event["meeting_id"]) == ("auto", [1], "sent", r.meeting_id)


def test_disagreement_goes_to_review_and_changes_nothing(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [1], "The deck went out on Thursday")]))
    add_first(ledger)
    system_one.answers = {"status": choice("progressed", 0.9)}
    r = ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    assert r.applied == [] and r.reviews == 1
    review = ledger.store.reviews()[0]
    assert (review["kind"], review["proposed"], review["checked"]) == ("update", "done", "progressed")
    assert "the decision model says progressed" in review["reason"]
    assert ledger.store.item(review["item_id"])["status"] == "open"


def test_low_confidence_agreement_goes_to_review(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [1], "The deck went out on Thursday")]))
    add_first(ledger)
    system_one.answers = {"status": choice("done", 0.6)}
    r = ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    assert r.applied == [] and "low confidence (60%)" in ledger.store.reviews()[0]["reason"]


def test_confidence_threshold_is_configurable(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [1], "The deck went out on Thursday")]),
                         settings=Settings(min_confidence=0.5))
    add_first(ledger)
    system_one.answers = {"status": choice("done", 0.6)}
    assert ledger.add(parse(TRANSCRIPT_2), "2026-01-12").applied


def test_ungrounded_update_quote_goes_to_review(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [2], "The deck went out on Thursday")]))
    add_first(ledger)
    system_one.answers = {"status": choice("done", 0.99)}
    ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    assert "quote isn't in the lines" in ledger.store.reviews()[0]["reason"]


def test_update_without_evidence_is_not_sent_to_the_decision_model(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [], "x")]))
    add_first(ledger)
    calls = len(system_one.calls)
    ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    assert len(system_one.calls) == calls
    assert "cites no transcript lines" in ledger.store.reviews()[0]["reason"]


def test_contradiction_always_goes_to_review(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("Lisbon first", "contradicted", [3], "Let's talk about hiring")]))
    add_first(ledger)
    system_one.answers = {"status": choice("contradicted", 0.99)}
    ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    review = ledger.store.reviews()[0]
    assert review["item_text"] == "Launch in Lisbon first" and "contradicts an earlier item" in review["reason"]


def test_blocked_update(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("Carla", "blocked", [2], "the intro is blocked until she's back")]))
    add_first(ledger)
    system_one.answers = {"status": choice("blocked", 0.9)}
    r = ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    assert ledger.store.item(r.applied[0][0]["id"])["status"] == "blocked"


def test_updates_for_unknown_or_repeated_items_are_ignored(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update(999, "done", [1], "deck went out"),
                                                    update("deck", "done", [1], "The deck went out on Thursday"),
                                                    update("deck", "dropped", [1], "The deck went out on Thursday")]))
    add_first(ledger)
    system_one.answers = {"status": choice("done", 0.9)}
    r = ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    assert [s for _, s in r.applied] == ["done"] and r.reviews == 0


def test_follow_up_state_names_the_earlier_meeting(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [1], "The deck went out on Thursday")]))
    add_first(ledger)
    ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    state, questions = system_one.calls[-1]
    assert state["open_item"]["from_meeting"] == "Sync (2026-01-05)"
    assert set(questions) == {"status"} and "[1] Ana: The deck went out" in state["later_meeting_lines"]


@pytest.mark.parametrize("kind,label,expected", [
    ("action", "progressed", "open"), ("action", "done", "done"), ("commitment", "blocked", "blocked"),
    ("question", "dropped", "dropped"), ("action", "contradicted", "dropped"),
    ("decision", "contradicted", "reversed"), ("decision", "progressed", "standing"), ("decision", "dropped", "reversed"),
])
def test_new_status(kind, label, expected):
    assert new_status(kind, label) == expected


# staleness ---------------------------------------------------------------------------------------

def test_unmentioned_items_whose_owner_was_there_go_stale(make_ledger):
    ledger = make_ledger(FIRST, extraction(), extraction())
    add_first(ledger)
    ledger.add(parse("Ana: hiring update\nBen: ok"), "2026-01-12")
    r = ledger.add(parse("Ana: more hiring\nBen: fine"), "2026-01-19")
    stale = ledger.open_items(stale_only=True)
    assert {i["text"] for i in stale} == {"Send the deck to Ben", "Introduce Ana to Carla at Northwind", "Is a licence needed for Lisbon?"}
    assert {i["text"] for i in r.newly_stale} == {i["text"] for i in stale}


def test_items_are_not_stale_when_their_owner_was_absent(make_ledger):
    ledger = make_ledger(FIRST, extraction(participants=["Cleo"]), extraction(participants=["Cleo"]))
    add_first(ledger)
    ledger.add(parse("Cleo: solo planning"), "2026-01-12")
    ledger.add(parse("Cleo: more planning"), "2026-01-19")
    assert ledger.open_items(stale_only=True) == []


def test_unowned_item_counts_when_someone_from_its_meeting_attends(make_ledger):
    ledger = make_ledger(FIRST, extraction(participants=["Ben Ortiz"]))
    add_first(ledger)
    ledger.add(parse("Ben: hello"), "2026-01-12")
    question = next(i for i in ledger.open_items() if i["kind"] == "question")
    assert question["misses"] == 1


def test_a_mention_resets_misses(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(), extraction(updates=[update("deck", "progressed", [1], "The deck went out on Thursday")]))
    add_first(ledger)
    ledger.add(parse("Ana: hiring"), "2026-01-12")
    system_one.answers = {"status": choice("progressed", 0.9)}
    ledger.add(parse(TRANSCRIPT_2), "2026-01-19")
    deck = next(i for i in ledger.open_items() if "deck" in i["text"])
    assert deck["misses"] == 0 and not deck["stale"]


def test_a_mention_that_goes_to_review_still_counts_as_mentioned(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(), extraction(updates=[update("deck", "done", [1], "The deck went out on Thursday")]))
    add_first(ledger)
    ledger.add(parse("Ana: hiring"), "2026-01-12")
    system_one.answers = {"status": choice("progressed", 0.9)}
    ledger.add(parse(TRANSCRIPT_2), "2026-01-19")
    assert next(i for i in ledger.open_items() if "deck" in i["text"])["misses"] == 0


def test_stale_after_is_configurable(make_ledger):
    ledger = make_ledger(FIRST, extraction(), settings=Settings(stale_after=1))
    add_first(ledger)
    r = ledger.add(parse("Ana: hiring"), "2026-01-12")
    assert len(r.newly_stale) == 3


def test_owner_present_matches_first_names_and_full_names():
    assert owner_present("Ana", ["Ana Silva", "Ben"])
    assert owner_present("Ana Silva", ["Ana"])
    assert not owner_present("Ana", ["Anabel Ortiz"])
    assert not owner_present("", ["Ana"])


# changes by a person -----------------------------------------------------------------------------

def test_set_status_logs_an_event_and_resets_misses(make_ledger):
    ledger = make_ledger(FIRST, extraction())
    add_first(ledger)
    ledger.add(parse("Ana: hiring"), "2026-01-12")
    deck = next(i for i in ledger.open_items() if "deck" in i["text"])
    updated = ledger.set_status(deck["id"], "done", "sent by email")
    assert updated["status"] == "done" and updated["misses"] == 0
    assert ledger.store.events(deck["id"])[-1]["by"] == "you"


def test_set_status_validates(make_ledger):
    ledger = make_ledger(FIRST)
    r = add_first(ledger)
    decision = next(i for i in r.items if i["kind"] == "decision")
    with pytest.raises(LedgerError):
        ledger.set_status(decision["id"], "done")
    with pytest.raises(LedgerError):
        ledger.set_status(r.items[0]["id"], "finished")
    with pytest.raises(LedgerError):
        ledger.set_status(999, "done")
    assert ledger.set_status(decision["id"], "reversed")["status"] == "reversed"


def _flagged(make_ledger, system_one):
    system_one.answers = {"agreed": noul(0.1)}
    ledger = make_ledger(extraction([item(quote="send the deck")]))
    add_first(ledger)
    return ledger, ledger.store.reviews()[0]


def test_accepting_a_flagged_item_keeps_it_and_clears_flags(make_ledger, system_one):
    ledger, review = _flagged(make_ledger, system_one)
    ledger.resolve(review["id"], "keep")
    kept = ledger.store.item(review["item_id"])
    assert kept["flags"] == [] and kept["status"] == "open" and ledger.store.reviews() == []


def test_rejecting_a_flagged_item_deletes_it(make_ledger, system_one):
    ledger, review = _flagged(make_ledger, system_one)
    ledger.resolve(review["id"], None)
    assert ledger.store.item(review["item_id"]) is None and ledger.store.reviews() == []


def _update_review(make_ledger, system_one):
    ledger = make_ledger(FIRST, extraction(updates=[update("deck", "done", [1], "The deck went out on Thursday")]))
    add_first(ledger)
    system_one.answers = {"status": choice("progressed", 0.9)}
    ledger.add(parse(TRANSCRIPT_2), "2026-01-12")
    return ledger, ledger.store.reviews()[0]


def test_accepting_an_update_applies_it(make_ledger, system_one):
    ledger, review = _update_review(make_ledger, system_one)
    ledger.resolve(review["id"], review["proposed"])
    assert ledger.store.item(review["item_id"])["status"] == "done"
    assert ledger.store.events(review["item_id"])[-1]["by"] == "review"
    assert ledger.store.review(review["id"])["resolved"] == "accepted"


def test_rejecting_an_update_changes_nothing(make_ledger, system_one):
    ledger, review = _update_review(make_ledger, system_one)
    ledger.resolve(review["id"], None)
    assert ledger.store.item(review["item_id"])["status"] == "open"
    assert ledger.store.review(review["id"])["resolved"] == "rejected"


def test_resolving_twice_is_an_error(make_ledger, system_one):
    ledger, review = _update_review(make_ledger, system_one)
    ledger.resolve(review["id"], None)
    with pytest.raises(LedgerError):
        ledger.resolve(review["id"], None)


def test_resolving_with_an_invalid_status_is_an_error(make_ledger, system_one):
    ledger, review = _update_review(make_ledger, system_one)
    with pytest.raises(LedgerError):
        ledger.resolve(review["id"], "reversed")
