from pathlib import Path

import pytest

from meeting_ledger.demo import seed
from meeting_ledger.obsidian import export, note_index, person_link, render, safe_filename

GOLDEN = Path(__file__).parent / "fixtures" / "weekly-sync.md"


@pytest.fixture
def demo():
    return seed()


def test_render_matches_golden_file(demo):
    """The full note for the demo's second meeting. Regenerate deliberately if the format changes."""
    assert render(demo.store, 2) == GOLDEN.read_text()


def test_render_lists_earlier_items_this_meeting_moved(demo):
    text = render(demo.store, 2)
    assert "## Earlier items this meeting moved" in text
    assert "- Send the price sheet to Bloom Café, Hartley's and the Corner Room → **done** (L2)" in text


def test_render_marks_review_and_skips_transcript(demo):
    text = render(demo.store, 2, transcript=False)
    assert "- [ ] Redo the website menu page — Theo (L8, L9) ⚠️ needs review" in text
    assert "## Transcript" not in text


def test_render_unknown_meeting(demo):
    with pytest.raises(KeyError):
        render(demo.store, 99)


def test_person_links(tmp_path):
    (tmp_path / "People").mkdir()
    for name in ("Maya Lindqvist", "Theo Adeyemi", "Theo Brandt"):
        (tmp_path / "People" / f"{name}.md").write_text("")
    (tmp_path / ".trash").mkdir()
    (tmp_path / ".trash" / "Priya Raman.md").write_text("")
    notes = note_index(tmp_path)
    assert person_link("Maya Lindqvist", notes) == "[[Maya Lindqvist]]"
    assert person_link("maya", notes) == "[[Maya Lindqvist|maya]]"
    assert person_link("Theo", notes) == "Theo"  # two Theos: ambiguous, so no link
    assert person_link("Priya Raman", notes) == "Priya Raman"  # only in a dot-folder
    assert person_link("", notes) == "" and person_link("Maya", None) == "Maya"


def test_export_writes_a_linked_note(demo, tmp_path):
    (tmp_path / "Maya Lindqvist.md").write_text("")
    path = export(demo.store, 1, tmp_path)
    assert path == tmp_path / "Meetings" / "2026-09-22 Wholesale launch sync.md"
    text = path.read_text()
    assert '  - "[[Maya Lindqvist]]"' in text and "Pay Priya's label design invoice — [[Maya Lindqvist|Maya]]" in text


def test_export_folder_and_overwrite(demo, tmp_path):
    first = export(demo.store, 3, tmp_path, "Calls")
    second = export(demo.store, 3, tmp_path, "Calls")
    assert first == second and first.parent.name == "Calls"


def test_safe_filename():
    assert safe_filename('Q3: "Plan" / #1 [draft]?') == "Q3 Plan  1 draft"
    assert safe_filename("///") == "Meeting"


def test_yaml_title_is_quoted(store):
    from meeting_ledger.ingest import parse

    mid = store.add_meeting('Re: "budget"', "2026-01-01", parse("hi"))
    assert 'title: "Re: \\"budget\\""' in render(store, mid)
