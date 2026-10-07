from datetime import date

import pytest
from conftest import FakeExtractor, FakeSystemOne, extraction, item

from meeting_ledger import __main__ as cli
from meeting_ledger.decisions import Decider
from meeting_ledger.demo import seed
from meeting_ledger.ledger import Ledger
from meeting_ledger.store import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    monkeypatch.chdir(tmp_path)
    for var in ("ANTHROPIC_API_KEY", "OBSIDIAN_VAULT", "MEETING_LEDGER_DB"):
        monkeypatch.delenv(var, raising=False)
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("MEETING_LEDGER_DB", str(db))
    return tmp_path, db


@pytest.fixture
def fake_pipeline(monkeypatch, env):
    """`add` uses a fake extractor and decider but the real store, CLI and rendering."""
    extractor = FakeExtractor(extraction([item(text="Send the deck", evidence=[1], quote="send the deck")],
                                         title="Planning"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(cli, "make_ledger", lambda settings: Ledger(Store(settings.db_path), extractor,
                                                                     Decider(client=FakeSystemOne(), backend="fake"), settings))
    return extractor


def seeded(db):
    seed(Store(db))


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.startswith("meeting-ledger ")


def test_add_needs_an_api_key(env, capsys):
    tmp, _ = env
    (tmp / "n.txt").write_text("Ana: hi")
    assert cli.main(["add", "n.txt"]) == 1
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_add_text_file(fake_pipeline, env, capsys):
    tmp, db = env
    (tmp / "2026-03-02 planning.txt").write_text("Ana: I'll send the deck tomorrow.")
    assert cli.main(["add", "2026-03-02 planning.txt", "-o", "out.md"]) == 0
    out = capsys.readouterr().out
    assert "# Planning" in out and "- [ ] Send the deck — Ana (L1)" in out
    assert fake_pipeline.calls[0]["held_on"] == "2026-03-02"
    assert "## Transcript" in (tmp / "out.md").read_text()
    assert Store(db).meetings()[0]["source"].endswith("2026-03-02 planning.txt")


def test_add_exports_to_obsidian_when_configured(fake_pipeline, env, monkeypatch, capsys):
    tmp, _ = env
    vault = tmp / "vault"
    vault.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    (tmp / "n.txt").write_text("Ana: I'll send the deck tomorrow.")
    assert cli.main(["add", "n.txt", "--date", "2026-03-04"]) == 0
    assert (vault / "Meetings" / "2026-03-04 Planning.md").exists()


def test_add_from_stdin(fake_pipeline, env, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("Ana: I'll send the deck."))
    assert cli.main(["add", "-"]) == 0
    assert fake_pipeline.calls[0]["held_on"] == date.today().isoformat()


def test_add_missing_file_and_bad_date(fake_pipeline, env, capsys):
    tmp, _ = env
    assert cli.main(["add", "nope.txt"]) == 1
    (tmp / "n.txt").write_text("hi")
    assert cli.main(["add", "n.txt", "--date", "March 4"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_meeting_date_rules(tmp_path):
    named = tmp_path / "standup 2026-05-06.vtt"
    named.write_text("x")
    plain = tmp_path / "standup.vtt"
    plain.write_text("x")
    assert cli.meeting_date("2026-01-01", named) == "2026-01-01"
    assert cli.meeting_date("", named) == "2026-05-06"
    assert cli.meeting_date("", plain) == date.today().isoformat()  # file's own date: written just now
    assert cli.meeting_date("", None) == date.today().isoformat()


def test_ask_without_a_key_prints_matching_lines(env, capsys):
    _, db = env
    seeded(db)
    assert cli.main(["ask", "Northside", "intro"]) == 0
    captured = capsys.readouterr()
    assert "Weekly sync with Sam  L2" in captured.out and "Set ANTHROPIC_API_KEY" in captured.err


def test_items_and_stale(env, capsys):
    _, db = env
    seeded(db)
    cli.main(["items"])
    out = capsys.readouterr().out
    assert "Pay Priya's label design invoice due 2026-09-30" in out and "STALE, missed 2 meetings" in out
    cli.main(["items", "--stale"])
    assert len(capsys.readouterr().out.strip().splitlines()) == 1
    cli.main(["items", "--owner", "nobody"])
    assert "No open items." in capsys.readouterr().out


def test_show_list_and_one(env, capsys):
    _, db = env
    seeded(db)
    cli.main(["show"])
    assert "#3    2026-10-06  Weekly sync with Sam  (2 items)" in capsys.readouterr().out
    cli.main(["show", "1", "--no-transcript"])
    out = capsys.readouterr().out
    assert "# Wholesale launch sync" in out and "## Transcript" not in out
    assert cli.main(["show", "9"]) == 1


def test_set_status(env, capsys):
    _, db = env
    seeded(db)
    invoice = next(i for i in Ledger(Store(db)).open_items() if "invoice" in i["text"])
    assert cli.main(["set", str(invoice["id"]), "done"]) == 0
    assert "→ done" in capsys.readouterr().out
    assert cli.main(["set", str(invoice["id"]), "finished"]) == 1


def test_review_list_and_settle(env, capsys):
    _, db = env
    seeded(db)
    cli.main(["review"])
    out = capsys.readouterr().out
    assert "new action: Redo the website menu page" in out and "Claude: progressed · decision model: done (55%)" in out
    assert cli.main(["review", "3", "checked"]) == 0
    inspection = next(i for i in Store(db).items(2) if "inspection" in i["text"])
    assert inspection["status"] == "done"
    assert cli.main(["review", "1"]) == 0  # accept = keep the flagged item
    assert cli.main(["review", "2", "reject"]) == 0
    cli.main(["review"])
    assert "Nothing to review." in capsys.readouterr().out
    assert cli.main(["review", "2", "reject"]) == 1  # already settled


def test_export(env, capsys):
    tmp, db = env
    seeded(db)
    vault = tmp / "vault"
    vault.mkdir()
    assert cli.main(["export", "--all", "--vault", str(vault)]) == 0
    assert len(list((vault / "Meetings").glob("*.md"))) == 3
    assert cli.main(["export", "--vault", str(vault)]) == 1  # needs an id or --all
    assert cli.main(["export", "1"]) == 1  # no vault configured
    assert cli.main(["export", "1", "--vault", str(tmp / "missing")]) == 1


def test_env_file_is_loaded(env, monkeypatch):
    tmp, _ = env
    (tmp / ".env").write_text("STALE_AFTER=5\n")
    monkeypatch.delenv("STALE_AFTER", raising=False)
    seen = {}
    monkeypatch.setitem(cli.COMMANDS, "items", lambda args, settings: seen.setdefault("stale_after", settings.stale_after) and 0)
    cli.main(["items"])
    assert seen["stale_after"] == 5


def test_add_audio_file_is_transcribed_first(fake_pipeline, env, monkeypatch, capsys):
    from meeting_ledger.ingest import Segment

    tmp, db = env
    (tmp / "call.m4a").write_bytes(b"audio")
    seen = []
    monkeypatch.setattr("meeting_ledger.transcribe.transcribe",
                        lambda path: seen.append(path) or [Segment(1, "I'll send the deck tomorrow.", "", 0.0, 2.5)])
    assert cli.main(["add", "call.m4a", "--date", "2026-03-05"]) == 0
    assert seen[0].name == "call.m4a" and "Transcribing call.m4a" in capsys.readouterr().err
    assert Store(db).segments(1)[0].end == 2.5
