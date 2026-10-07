"""meeting-ledger: `meeting-ledger` opens the terminal UI; subcommands do everything from the shell."""

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .config import DATA_DIR, Settings, load_env

STATUS_WORDS = "open, blocked, done, dropped (decisions: standing, reversed)"


def fail(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 1


def need_anthropic_key() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return (f"ANTHROPIC_API_KEY is not set. Add it to {DATA_DIR / '.env'} or export it "
            "(get one at https://console.anthropic.com/).")


def meeting_date(given: str, path: Path | None) -> str:
    if given:
        date.fromisoformat(given)  # raises ValueError on a bad date
        return given
    if path is not None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        if m:
            try:
                return date.fromisoformat(m.group(1)).isoformat()
            except ValueError:
                pass
        if path.exists():
            return date.fromtimestamp(path.stat().st_mtime).isoformat()
    return date.today().isoformat()


def make_ledger(settings: Settings):
    from .decisions import Decider
    from .extract import Extractor
    from .ledger import Ledger
    from .store import Store

    return Ledger(Store(settings.db_path), Extractor(model=settings.model), Decider(), settings)


def read_input(source: str):
    """Return (segments, path or None) for a text/subtitle file, an audio/video file, or '-' for stdin."""
    from .ingest import is_audio, parse
    from .transcribe import transcribe

    if source == "-":
        if sys.stdin.isatty():
            print("Paste the notes or transcript, then press Ctrl+D:", file=sys.stderr)
        return parse(sys.stdin.read()), None
    path = Path(source).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {source}")
    if is_audio(path):
        print(f"Transcribing {path.name} on this Mac…", file=sys.stderr)
        return transcribe(path), path
    return parse(path.read_text(encoding="utf-8", errors="replace"), path.name), path


def cmd_add(args, settings: Settings) -> int:
    from .obsidian import export, render

    missing = need_anthropic_key()
    if missing:
        return fail(missing)
    ledger = make_ledger(settings)
    for source in args.files:
        segments, path = read_input(source)
        held_on = meeting_date(args.date, path)
        print(f"Processing {path.name if path else 'pasted notes'} ({len(segments)} lines, {held_on})…", file=sys.stderr)
        result = ledger.add(segments, held_on, args.title, str(path or "stdin"))
        text = render(ledger.store, result.meeting_id, transcript=False)
        print(text)
        for item, status in result.applied:
            print(f"✓ Earlier item #{item['id']} → {status}: {item['text']}")
        for item in result.newly_stale:
            print(f"⚠ Stale: #{item['id']} {item['text']} ({item['owner'] or 'no owner'}, from {item['held_on']})")
        if result.reviews:
            print(f"→ {result.reviews} thing(s) to review: run `meeting-ledger review` or open the TUI.")
        if args.output:
            Path(args.output).write_text(render(ledger.store, result.meeting_id), encoding="utf-8")
            print(f"Saved to {args.output}", file=sys.stderr)
        if settings.obsidian_vault and not args.no_export:
            print(f"Exported to {export(ledger.store, result.meeting_id, settings.obsidian_vault, settings.obsidian_folder)}",
                  file=sys.stderr)
    return 0


def print_answer(answer) -> None:
    sources = answer.sources()
    index = {(c.meeting_id, tuple(c.lines)): k + 1 for k, c in enumerate(sources)}
    out = []
    for part in answer.parts:
        marks = "".join(f"[{index[(c.meeting_id, tuple(c.lines))]}]" for c in part.cites)
        out.append(part.text + marks)
    print("".join(out).strip())
    if sources:
        print()
        for k, c in enumerate(sources, 1):
            lines = ", ".join(f"L{n}" for n in c.lines)
            print(f"[{k}] {c.title} ({c.held_on}) {lines}")


def print_hits(hits: list[dict]) -> None:
    for h in hits:
        who = f"{h['speaker']}: " if h["speaker"] else ""
        print(f"{h['held_on']}  {h['title']}  L{h['n']}  {who}{h['text']}")


def cmd_ask(args, settings: Settings) -> int:
    from .ask import Asker
    from .store import Store

    store = Store(settings.db_path)
    question = " ".join(args.question)
    if need_anthropic_key():
        hits = store.search(question)
        print_hits(hits) if hits else print("Nothing in your meetings matches that.")
        print("\n(Set ANTHROPIC_API_KEY to get a written answer with citations instead of matching lines.)", file=sys.stderr)
        return 0
    print_answer(Asker(store, model=settings.model).ask(question))
    return 0


def cmd_items(args, settings: Settings) -> int:
    from .ledger import Ledger
    from .store import Store

    ledger = Ledger(Store(settings.db_path), settings=settings)
    items = ledger.open_items(args.owner, args.stale)
    if not items:
        print("No open items." if not args.stale else "Nothing stale.")
    for i in items:
        flags = [i["status"]] if i["status"] != "open" else []
        if i["stale"]:
            flags.append(f"STALE, missed {i['misses']} meetings")
        if i["flags"]:
            flags.append("needs review")
        due = f" due {i['due']}" if i["due"] else ""
        tail = f"  [{', '.join(flags)}]" if flags else ""
        print(f"#{i['id']:<4} {i['kind']:<10} {i['owner'] or '-':<12} {i['text']}{due}  ({i['held_on']}){tail}")
    return 0


def cmd_show(args, settings: Settings) -> int:
    from .obsidian import render
    from .store import Store

    store = Store(settings.db_path)
    if args.id is None:
        for m in store.meetings():
            print(f"#{m['id']:<4} {m['held_on']}  {m['title']}  ({m['item_count']} items)")
        return 0
    if store.meeting(args.id) is None:
        return fail(f"No meeting #{args.id}.")
    print(render(store, args.id, transcript=not args.no_transcript))
    return 0


def cmd_set(args, settings: Settings) -> int:
    from .ledger import Ledger
    from .store import Store

    item = Ledger(Store(settings.db_path), settings=settings).set_status(args.item, args.status, args.note)
    print(f"#{item['id']} {item['text']} → {item['status']}")
    return 0


def cmd_review(args, settings: Settings) -> int:
    from .ledger import Ledger
    from .store import Store

    ledger = Ledger(Store(settings.db_path), settings=settings)
    if args.id is not None:
        choice = args.decision
        if choice in ("accept", "proposed"):
            review = ledger.store.review(args.id)
            choice = review["proposed"] if review and review["kind"] == "update" else "keep"
        elif choice == "checked":
            review = ledger.store.review(args.id)
            choice = review["checked"] if review and review["checked"] != "not_mentioned" else "reject"
        ledger.resolve(args.id, None if choice == "reject" else choice)
        print(f"Review #{args.id} settled.")
        return 0
    reviews = ledger.store.reviews()
    if not reviews:
        print("Nothing to review.")
    for r in reviews:
        if r["kind"] == "item":
            print(f"#{r['id']:<4} new {r['item_kind']}: {r['item_text']}  ({r['meeting_title']}, {r['held_on']})\n"
                  f"      why: {r['reason']}\n      → review {r['id']} accept | reject")
        else:
            conf = f" ({r['confidence']:.0%})" if r["confidence"] is not None else ""
            print(f"#{r['id']:<4} update to #{r['item_id']} {r['item_text']}  in {r['meeting_title']} ({r['held_on']})\n"
                  f"      Claude: {r['proposed']} · decision model: {r['checked'] or '-'}{conf} · why: {r['reason']}\n"
                  f"      → review {r['id']} proposed | checked | reject")
    return 0


def cmd_export(args, settings: Settings) -> int:
    from .obsidian import export
    from .store import Store

    vault = Path(args.vault).expanduser() if args.vault else settings.obsidian_vault
    if vault is None:
        return fail("Give --vault DIR or set OBSIDIAN_VAULT.")
    if not vault.is_dir():
        return fail(f"Not a folder: {vault}")
    store = Store(settings.db_path)
    ids = [m["id"] for m in store.meetings()] if args.all else [args.id]
    if ids == [None]:
        return fail("Give a meeting id or --all.")
    for mid in ids:
        if store.meeting(mid) is None:
            return fail(f"No meeting #{mid}.")
        print(export(store, mid, vault, args.folder or settings.obsidian_folder))
    return 0


def cmd_mcp(args, settings: Settings) -> int:
    try:
        from .mcp_server import build_server
    except ImportError:
        return fail("The MCP server needs the mcp extra: pipx install 'meeting-ledger[mcp]'")
    server = build_server(make_ledger(settings))
    server.run("stdio")
    return 0


def cmd_tui(args, settings: Settings, demo: bool = False) -> int:
    from .tui import LedgerApp

    if demo:
        from .demo import seed

        ledger = seed()
    else:
        ledger = make_ledger(settings)
    LedgerApp(ledger, demo=demo).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meeting-ledger",
        description="Your meetings, on the record: who decided what, who promised what, and whether it happened. "
                    "Run with no command to open the terminal UI.",
    )
    p.add_argument("--version", action="version", version=f"meeting-ledger {__version__}")
    p.add_argument("--db", help=f"database file (default {DATA_DIR / 'ledger.db'})")
    sub = p.add_subparsers(dest="command")

    a = sub.add_parser("add", help="process a transcript, notes, or an audio/video file ('-' reads stdin)")
    a.add_argument("files", nargs="+")
    a.add_argument("--title", default="", help="meeting title (default: Claude names it)")
    a.add_argument("--date", default="", help="meeting date YYYY-MM-DD (default: from the file name, else the file's date)")
    a.add_argument("-o", "--output", help="also save the meeting as a Markdown file")
    a.add_argument("--no-export", action="store_true", help="don't write to the Obsidian vault this time")

    q = sub.add_parser("ask", help="ask your meetings a question; the answer cites meetings and lines")
    q.add_argument("question", nargs="+")

    i = sub.add_parser("items", help="open action items, commitments and questions")
    i.add_argument("--owner", default="", help="only this person's (name or part of it)")
    i.add_argument("--stale", action="store_true", help="only items nobody has mentioned for a while")

    s = sub.add_parser("show", help="list meetings, or print one as Markdown")
    s.add_argument("id", nargs="?", type=int)
    s.add_argument("--no-transcript", action="store_true")

    st = sub.add_parser("set", help=f"change an item's status: {STATUS_WORDS}")
    st.add_argument("item", type=int)
    st.add_argument("status")
    st.add_argument("--note", default="")

    r = sub.add_parser("review", help="list what needs your review, or settle one")
    r.add_argument("id", nargs="?", type=int)
    r.add_argument("decision", nargs="?", default="accept",
                   help="accept (= proposed), checked (use the decision model's status), reject, or a status")

    e = sub.add_parser("export", help="write meetings to an Obsidian vault as Markdown notes")
    e.add_argument("id", nargs="?", type=int)
    e.add_argument("--all", action="store_true")
    e.add_argument("--vault", help="vault folder (default OBSIDIAN_VAULT)")
    e.add_argument("--folder", help="subfolder inside the vault (default Meetings)")

    sub.add_parser("mcp", help="run the MCP server over stdio (for Claude Desktop, Claude Code, …)")
    sub.add_parser("demo", help="open the terminal UI on fictional demo meetings (no API key needed)")
    return p


COMMANDS = {"add": cmd_add, "ask": cmd_ask, "items": cmd_items, "show": cmd_show, "set": cmd_set,
            "review": cmd_review, "export": cmd_export, "mcp": cmd_mcp}


def main(argv: list[str] | None = None) -> int:
    load_env(Path.cwd() / ".env")
    load_env(DATA_DIR / ".env")
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.db:
        settings.db_path = Path(args.db).expanduser()

    from .extract import ClaudeError
    from .ledger import LedgerError
    from .transcribe import TranscribeError

    try:
        if args.command is None:
            return cmd_tui(args, settings)
        if args.command == "demo":
            return cmd_tui(args, settings, demo=True)
        return COMMANDS[args.command](args, settings)
    except (ClaudeError, LedgerError, TranscribeError, FileNotFoundError, ValueError) as e:
        return fail(str(e))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
