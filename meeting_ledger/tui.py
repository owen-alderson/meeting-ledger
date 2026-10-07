"""The terminal UI: meetings with their cited transcript lines, open items across meetings, the
review queue, and asking questions of your meetings."""

import os

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Input, Static, TabbedContent, TabPane

from .ledger import Ledger, LedgerError

ACCENT, AMBER, RED, GREEN, MUTED = "#2ec4b6", "#ffb000", "#F23645", "#00C853", "#808080"
KIND = {"decision": "decision", "action": "action", "commitment": "promise", "question": "question"}
STATUS_STYLE = {"open": "", "standing": "", "blocked": AMBER, "done": GREEN, "dropped": MUTED, "reversed": RED}


def status_text(item: dict, stale: bool = False) -> Text:
    if stale:
        return Text(f"STALE ({item['misses']} missed)", style=f"bold {RED}")
    return Text(item["status"], style=STATUS_STYLE.get(item["status"], ""))


class LedgerApp(App):
    TITLE = "meeting-ledger"
    CSS = f"""
    Screen {{ background: black; }}
    #title {{ height: 1; padding: 0 1; background: {ACCENT}; color: black; text-style: bold; }}
    TabbedContent {{ height: 1fr; }}
    DataTable {{ background: black; height: 1fr; scrollbar-color: #1d5f59; scrollbar-background: #111111;
                 scrollbar-color-hover: {ACCENT}; scrollbar-color-active: {ACCENT}; }}
    DataTable > .datatable--header {{ background: black; color: {ACCENT}; text-style: bold; }}
    DataTable > .datatable--cursor {{ background: #0d3b37; }}
    #meeting-list {{ width: 46; border-right: solid {ACCENT}; }}
    #meeting-detail {{ width: 1fr; }}
    #tldr {{ height: auto; padding: 0 1 1 1; }}
    #meeting-items {{ height: 2fr; border-bottom: solid #1f1f1f; }}
    #transcript {{ height: 3fr; }}
    #review-detail, #answer {{ padding: 1 1; }}
    #review-table {{ height: 1fr; border-bottom: solid {ACCENT}; }}
    #review-detail-scroll {{ height: 1fr; }}
    #question {{ background: black; border: solid {ACCENT}; }}
    #status {{ height: 1; padding: 0 1; background: #1c1c1c; color: {ACCENT}; }}
    Tabs .underline--bar {{ color: {ACCENT}; background: #1f1f1f; }}
    Tab.-active {{ color: {ACCENT}; text-style: bold; }}
    """
    BINDINGS = [
        Binding("1", "tab('meetings')", "Meetings"),
        Binding("2", "tab('items')", "Open items"),
        Binding("3", "tab('review')", "Review"),
        Binding("4", "tab('ask')", "Ask"),
        Binding("d", "set_status('done')", "Done"),
        Binding("b", "set_status('blocked')", "Blocked"),
        Binding("x", "set_status('dropped')", "Dropped"),
        Binding("o", "set_status('open')", "Reopen"),
        Binding("s", "toggle_stale", "Stale only"),
        Binding("a", "resolve('accept')", "Accept"),
        Binding("c", "resolve('checked')", "Use checker"),
        Binding("r", "resolve('reject')", "Reject"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, ledger: Ledger, demo: bool = False):
        super().__init__()
        self.ledger = ledger
        self.demo = demo
        self.stale_only = False
        self.meeting_id: int | None = None
        self.cited: set[int] = set()

    # layout --------------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with TabbedContent(initial="meetings"):
            with TabPane("Meetings", id="meetings"):
                with Horizontal():
                    yield DataTable(id="meeting-list", cursor_type="row")
                    with Vertical(id="meeting-detail"):
                        yield Static(id="tldr")
                        yield DataTable(id="meeting-items", cursor_type="row")
                        yield DataTable(id="transcript", cursor_type="row")
            with TabPane("Open items", id="items"):
                yield DataTable(id="open-items", cursor_type="row")
            with TabPane("Review", id="review"):
                yield DataTable(id="review-table", cursor_type="row")
                with VerticalScroll(id="review-detail-scroll"):
                    yield Static(id="review-detail")
            with TabPane("Ask", id="ask"):
                yield Input(placeholder="Ask your meetings… e.g. what did we decide about the online shop?", id="question")
                with VerticalScroll():
                    yield Static(id="answer")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#meeting-list", DataTable).add_columns("Date", "Meeting", "Items")
        self.query_one("#meeting-items", DataTable).add_columns("#", "Kind", "Item", "Owner", "Due", "Status", "Lines")
        self.query_one("#transcript", DataTable).add_columns("L", "Speaker", "Said")
        self.query_one("#open-items", DataTable).add_columns("#", "Kind", "Owner", "Item", "Due", "From", "Status")
        self.query_one("#review-table", DataTable).add_columns("#", "What", "Meeting", "Item")
        self.refresh_all()
        meetings = self.ledger.store.meetings()
        if meetings:
            self.show_meeting(meetings[0]["id"])

    # data ----------------------------------------------------------------------------------------

    def refresh_all(self) -> None:
        store = self.ledger.store
        meetings = store.meetings()
        open_items = self.ledger.open_items()
        reviews = store.reviews()
        stale = sum(1 for i in open_items if i["stale"])
        badge = "  ·  DEMO (fictional data)" if self.demo else ""
        self.query_one("#title", Static).update(
            f"MEETING LEDGER  ·  {len(meetings)} meetings  ·  {len(open_items)} open  ·  {stale} stale  ·  "
            f"{len(reviews)} to review{badge}"
        )
        table = self.query_one("#meeting-list", DataTable)
        table.clear()
        for m in meetings:
            table.add_row(m["held_on"], m["title"], str(m["item_count"]), key=str(m["id"]))
        self.fill_open_items()
        self.fill_reviews()
        if self.meeting_id is not None and store.meeting(self.meeting_id):
            self.fill_meeting_items(self.meeting_id)

    def fill_open_items(self) -> None:
        table = self.query_one("#open-items", DataTable)
        table.clear()
        for i in self.ledger.open_items(stale_only=self.stale_only):
            table.add_row(str(i["id"]), KIND[i["kind"]], i["owner"] or "-", i["text"], i["due"] or "",
                          f"{i['held_on']} {i['meeting_title']}", status_text(i, i["stale"]), key=str(i["id"]))

    def fill_reviews(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.clear()
        for r in self.ledger.store.reviews():
            what = f"new {KIND[r['item_kind']]}" if r["kind"] == "item" else "update"
            table.add_row(str(r["id"]), what, f"{r['held_on']} {r['meeting_title']}", r["item_text"], key=str(r["id"]))
        if not table.row_count:
            self.query_one("#review-detail", Static).update(Text("Nothing to review.", style=MUTED))

    def fill_meeting_items(self, mid: int) -> None:
        table = self.query_one("#meeting-items", DataTable)
        table.clear()
        for i in self.ledger.store.items(mid):
            mark = Text(" ⚠", style=AMBER) if i["flags"] else ""
            table.add_row(str(i["id"]), KIND[i["kind"]], Text(i["text"]) + mark, i["owner"] or "-", i["due"] or "",
                          status_text(i, self.ledger.is_stale(i)), ", ".join(f"L{n}" for n in i["evidence"]),
                          key=str(i["id"]))

    def show_meeting(self, mid: int, item_id: int | None = None) -> None:
        store = self.ledger.store
        m = store.meeting(mid)
        if m is None:
            return
        self.meeting_id = mid
        header = Text.assemble((m["title"], f"bold {ACCENT}"), f"  {m['held_on']}  ",
                               (", ".join(m["participants"]), MUTED), "\n", m["tldr"] or "")
        moved = store.meeting_events(mid)
        if moved:
            header.append("\nMoved: ", style=MUTED)
            header.append("; ".join(f"{e['item_text']} → {e['status']}" for e in moved))
        self.query_one("#tldr", Static).update(header)
        self.fill_meeting_items(mid)
        items = self.query_one("#meeting-items", DataTable)
        if item_id is not None:  # put the cursor on that item, so its lines are the ones highlighted
            items.move_cursor(row=items.get_row_index(str(item_id)))
            self.show_transcript(store.item(item_id)["evidence"])
        else:
            self.show_transcript([])

    def show_transcript(self, cite: list[int]) -> None:
        self.cited = set(cite)
        table = self.query_one("#transcript", DataTable)
        table.clear()
        for s in self.ledger.store.segments(self.meeting_id):
            style = f"bold black on {ACCENT}" if s.n in self.cited else ""
            table.add_row(Text(str(s.n), style=style), Text(s.speaker, style=style), Text(s.text, style=style),
                          key=str(s.n))
        if cite:
            table.move_cursor(row=min(cite) - 1)

    # events --------------------------------------------------------------------------------------

    @on(DataTable.RowHighlighted, "#meeting-list")
    def meeting_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value and int(event.row_key.value) != self.meeting_id:
            self.show_meeting(int(event.row_key.value))

    @on(DataTable.RowHighlighted, "#meeting-items")
    def item_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value:
            item = self.ledger.store.item(int(event.row_key.value))
            if item:
                self.show_transcript(item["evidence"])

    @on(DataTable.RowSelected, "#open-items")
    def open_item_selected(self, event: DataTable.RowSelected) -> None:
        item = self.ledger.store.item(int(event.row_key.value))
        if item:
            self.action_tab("meetings")
            self.show_meeting(item["meeting_id"], item["id"])

    @on(DataTable.RowHighlighted, "#review-table")
    def review_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not event.row_key.value:
            return
        r = self.ledger.store.review(int(event.row_key.value))
        if r is None:
            return
        out = Text.assemble((r["item_text"], "bold"), f"\n{r['held_on']} {r['meeting_title']}\n\n",
                            ("Why: ", MUTED), r["reason"], "\n")
        if r["kind"] == "update":
            conf = f" ({r['confidence']:.0%})" if r["confidence"] is not None else ""
            out.append_text(Text.assemble(("Claude proposed: ", MUTED), (r["proposed"], ACCENT), "   ",
                                          ("Decision model: ", MUTED), (f"{r['checked'] or '-'}{conf}", AMBER), "\n"))
            out.append("a = apply Claude's · c = apply the decision model's · r = ignore\n\n", style=MUTED)
        else:
            out.append("a = keep it · r = delete it (bad extraction)\n\n", style=MUTED)
        for s in self.ledger.store.segments(r["meeting_id"], r["evidence"]):
            out.append(f"L{s.n} ", style=ACCENT)
            out.append(f"{s.speaker + ': ' if s.speaker else ''}{s.text}\n")
        self.query_one("#review-detail", Static).update(out)

    @on(Input.Submitted, "#question")
    def ask_submitted(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if not question:
            return
        if self.demo or not os.environ.get("ANTHROPIC_API_KEY"):
            self.show_hits(question)
            return
        self.query_one("#answer", Static).update(Text("Thinking…", style=MUTED))
        self.run_ask(question)

    def show_hits(self, question: str) -> None:
        hits = self.ledger.store.search(question)
        out = Text("Matching lines (set ANTHROPIC_API_KEY for a written answer with citations):\n\n", style=MUTED)
        if not hits:
            out.append("Nothing matches.")
        for h in hits:
            out.append(f"{h['held_on']} {h['title']} ", style=ACCENT)
            out.append(f"L{h['n']}  ", style=MUTED)
            out.append(f"{h['speaker'] + ': ' if h['speaker'] else ''}{h['text']}\n")
        self.query_one("#answer", Static).update(out)

    @work(thread=True, exclusive=True)
    def run_ask(self, question: str) -> None:
        from .ask import Asker
        from .extract import ClaudeError

        try:
            answer = Asker(self.ledger.store, model=self.ledger.settings.model).ask(question)
        except ClaudeError as e:
            self.call_from_thread(self.query_one("#answer", Static).update, Text(str(e), style=RED))
            return
        sources = answer.sources()
        index = {(c.meeting_id, tuple(c.lines)): k + 1 for k, c in enumerate(sources)}
        out = Text()
        for part in answer.parts:
            out.append(part.text)
            for c in part.cites:
                out.append(f"[{index[(c.meeting_id, tuple(c.lines))]}]", style=ACCENT)
        out.append("\n\n")
        for k, c in enumerate(sources, 1):
            out.append(f"[{k}] ", style=ACCENT)
            out.append(f"{c.title} ({c.held_on}) {', '.join(f'L{n}' for n in c.lines)}\n", style=MUTED)
        self.call_from_thread(self.query_one("#answer", Static).update, out)

    # actions -------------------------------------------------------------------------------------

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def _row_id(self, table_id: str) -> int | None:
        table = self.query_one(table_id, DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return int(key.value)

    def _status(self, message: str, style: str = "") -> None:
        self.query_one("#status", Static).update(Text(message, style=style))

    def action_set_status(self, status: str) -> None:
        active = self.query_one(TabbedContent).active
        table_id = {"items": "#open-items", "meetings": "#meeting-items"}.get(active)
        item_id = self._row_id(table_id) if table_id else None
        if item_id is None:
            return
        item = self.ledger.store.item(item_id)
        if item["kind"] == "decision":
            status = {"dropped": "reversed", "open": "standing"}.get(status, status)
        try:
            item = self.ledger.set_status(item_id, status)
        except LedgerError as e:
            self._status(str(e), RED)
            return
        self._status(f"#{item_id} {item['text']} → {item['status']}")
        self.refresh_all()

    def action_toggle_stale(self) -> None:
        self.stale_only = not self.stale_only
        self.fill_open_items()
        self._status("Showing stale items only" if self.stale_only else "Showing all open items")

    def action_resolve(self, how: str) -> None:
        if self.query_one(TabbedContent).active != "review":
            return
        rid = self._row_id("#review-table")
        if rid is None:
            return
        review = self.ledger.store.review(rid)
        if how == "accept":
            status = review["proposed"] if review["kind"] == "update" else "keep"
        elif how == "checked":
            if review["kind"] != "update" or not review["checked"]:
                return
            status = review["checked"]
            if status == "not_mentioned":
                status = None
        else:
            status = None
        try:
            self.ledger.resolve(rid, status)
        except LedgerError as e:
            self._status(str(e), RED)
            return
        self._status(f"Review #{rid} settled")
        self.refresh_all()
