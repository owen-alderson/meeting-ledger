"""SQLite storage: meetings, their numbered transcript lines, the items pulled from them, and every
status change with the line that justified it. Transcript lines are full-text indexed (FTS5)."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .ingest import Segment

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    held_on TEXT NOT NULL,          -- YYYY-MM-DD
    source TEXT NOT NULL DEFAULT '',
    tldr TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    speaker TEXT NOT NULL DEFAULT '',
    start REAL,
    "end" REAL,
    text TEXT NOT NULL,
    PRIMARY KEY (meeting_id, n)
);
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, speaker, meeting_id UNINDEXED, n UNINDEXED, tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,             -- decision | action | commitment | question
    text TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    due TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,           -- decision: standing | reversed; others: open | blocked | done | dropped
    evidence TEXT NOT NULL DEFAULT '[]',
    quote TEXT NOT NULL DEFAULT '',
    flags TEXT NOT NULL DEFAULT '[]',
    supported REAL,                 -- System One: probability the cited lines support the item
    agreed REAL,                    -- System One: probability the owner actually took it on
    misses INTEGER NOT NULL DEFAULT 0,  -- meetings with the owner present that never mentioned it
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    by TEXT NOT NULL,               -- auto | you | review
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,             -- item (a new item was flagged) | update (a status change wasn't agreed)
    proposed TEXT NOT NULL DEFAULT '',  -- the status Claude proposed (update reviews)
    checked TEXT NOT NULL DEFAULT '',   -- the status the decision model chose
    confidence REAL,
    evidence TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    resolved TEXT NOT NULL DEFAULT ''   -- '' | accepted | rejected
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,             -- item_check | followup
    backend TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    at TEXT NOT NULL
);
"""

OPEN = ("open", "blocked")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("participants", "evidence", "flags"):
        if key in out and isinstance(out[key], str):
            out[key] = json.loads(out[key])
    return out


class Store:
    def __init__(self, path: Path | str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _all(self, sql: str, args=()) -> list[dict]:
        return [_row(r) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args=()) -> dict | None:
        return _row(self.db.execute(sql, args).fetchone())

    # meetings ------------------------------------------------------------------------------------

    def add_meeting(self, title: str, held_on: str, segments: list[Segment], source: str = "",
                    tldr: str = "", participants: list[str] | None = None, model: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO meetings (title, held_on, source, tldr, participants, model, added_at) VALUES (?,?,?,?,?,?,?)",
            (title, held_on, source, tldr, json.dumps(participants or []), model, now()),
        )
        mid = cur.lastrowid
        self.db.executemany(
            'INSERT INTO segments (meeting_id, n, speaker, start, "end", text) VALUES (?,?,?,?,?,?)',
            [(mid, s.n, s.speaker, s.start, s.end, s.text) for s in segments],
        )
        self.db.executemany(
            "INSERT INTO segments_fts (text, speaker, meeting_id, n) VALUES (?,?,?,?)",
            [(s.text, s.speaker, mid, s.n) for s in segments],
        )
        self.db.commit()
        return mid

    def meeting(self, mid: int) -> dict | None:
        return self._one("SELECT * FROM meetings WHERE id = ?", (mid,))

    def meetings(self) -> list[dict]:
        return self._all(
            """SELECT m.*, (SELECT COUNT(*) FROM items i WHERE i.meeting_id = m.id) AS item_count
               FROM meetings m ORDER BY held_on DESC, id DESC"""
        )

    def delete_meeting(self, mid: int) -> None:
        self.db.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (mid,))
        self.db.execute("DELETE FROM meetings WHERE id = ?", (mid,))
        self.db.commit()

    def segments(self, mid: int, ns: list[int] | None = None) -> list[Segment]:
        rows = self.db.execute('SELECT n, text, speaker, start, "end" FROM segments WHERE meeting_id = ? ORDER BY n', (mid,))
        segs = [Segment(r["n"], r["text"], r["speaker"], r["start"], r["end"]) for r in rows]
        if ns is not None:
            wanted = set(ns)
            segs = [s for s in segs if s.n in wanted]
        return segs

    # items ---------------------------------------------------------------------------------------

    def add_item(self, meeting_id: int, kind: str, text: str, owner: str = "", due: str = "",
                 evidence: list[int] | None = None, quote: str = "", flags: list[str] | None = None,
                 supported: float | None = None, agreed: float | None = None) -> int:
        status = "standing" if kind == "decision" else "open"
        cur = self.db.execute(
            """INSERT INTO items (meeting_id, kind, text, owner, due, status, evidence, quote, flags, supported, agreed, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (meeting_id, kind, text, owner, due, status, json.dumps(evidence or []), quote,
             json.dumps(flags or []), supported, agreed, now()),
        )
        self.db.commit()
        return cur.lastrowid

    def item(self, iid: int) -> dict | None:
        return self._one(
            """SELECT i.*, m.title AS meeting_title, m.held_on FROM items i
               JOIN meetings m ON m.id = i.meeting_id WHERE i.id = ?""",
            (iid,),
        )

    def items(self, meeting_id: int) -> list[dict]:
        return self._all("SELECT * FROM items WHERE meeting_id = ? ORDER BY id", (meeting_id,))

    def open_items(self, owner: str = "", before_meeting: int | None = None) -> list[dict]:
        """Open or blocked actions, commitments and questions, oldest first."""
        sql = """SELECT i.*, m.title AS meeting_title, m.held_on FROM items i JOIN meetings m ON m.id = i.meeting_id
                 WHERE i.status IN ('open', 'blocked') AND i.kind != 'decision'"""
        args: list = []
        if owner:
            sql += " AND lower(i.owner) LIKE ?"
            args.append(f"%{owner.lower()}%")
        if before_meeting is not None:
            sql += " AND i.meeting_id != ?"
            args.append(before_meeting)
        return self._all(sql + " ORDER BY m.held_on, i.id", args)

    def standing_decisions(self, limit: int = 30) -> list[dict]:
        """The most recent decisions still standing, newest first."""
        return self._all(
            """SELECT i.*, m.title AS meeting_title, m.held_on FROM items i JOIN meetings m ON m.id = i.meeting_id
               WHERE i.kind = 'decision' AND i.status = 'standing' ORDER BY m.held_on DESC, i.id DESC LIMIT ?""",
            (limit,),
        )

    def update_item(self, iid: int, **fields) -> None:
        for key in ("evidence", "flags"):
            if key in fields:
                fields[key] = json.dumps(fields[key])
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE items SET {cols} WHERE id = ?", (*fields.values(), iid))
        self.db.commit()

    def add_event(self, item_id: int, status: str, by: str, meeting_id: int | None = None,
                  evidence: list[int] | None = None, note: str = "") -> None:
        self.db.execute(
            "INSERT INTO events (item_id, meeting_id, status, evidence, note, by, at) VALUES (?,?,?,?,?,?,?)",
            (item_id, meeting_id, status, json.dumps(evidence or []), note, by, now()),
        )
        self.db.commit()

    def events(self, item_id: int) -> list[dict]:
        return self._all(
            """SELECT e.*, m.title AS meeting_title, m.held_on FROM events e
               LEFT JOIN meetings m ON m.id = e.meeting_id WHERE item_id = ? ORDER BY e.id""",
            (item_id,),
        )

    def meeting_events(self, meeting_id: int) -> list[dict]:
        """Status changes this meeting made to items from earlier meetings."""
        return self._all(
            """SELECT e.*, i.text AS item_text, i.kind AS item_kind, i.owner FROM events e
               JOIN items i ON i.id = e.item_id WHERE e.meeting_id = ? AND i.meeting_id != ? ORDER BY e.id""",
            (meeting_id, meeting_id),
        )

    # reviews -------------------------------------------------------------------------------------

    def add_review(self, item_id: int, meeting_id: int, kind: str, reason: str, proposed: str = "",
                   checked: str = "", confidence: float | None = None, evidence: list[int] | None = None) -> int:
        cur = self.db.execute(
            """INSERT INTO reviews (item_id, meeting_id, kind, proposed, checked, confidence, evidence, reason)
               VALUES (?,?,?,?,?,?,?,?)""",
            (item_id, meeting_id, kind, proposed, checked, confidence, json.dumps(evidence or []), reason),
        )
        self.db.commit()
        return cur.lastrowid

    def reviews(self, pending_only: bool = True) -> list[dict]:
        sql = """SELECT r.*, i.text AS item_text, i.kind AS item_kind, i.owner, i.status AS item_status,
                        m.title AS meeting_title, m.held_on
                 FROM reviews r JOIN items i ON i.id = r.item_id JOIN meetings m ON m.id = r.meeting_id"""
        if pending_only:
            sql += " WHERE r.resolved = ''"
        return self._all(sql + " ORDER BY r.id")

    def review(self, rid: int) -> dict | None:
        return next((r for r in self.reviews(pending_only=False) if r["id"] == rid), None)

    def resolve_review(self, rid: int, outcome: str) -> None:
        self.db.execute("UPDATE reviews SET resolved = ? WHERE id = ?", (outcome, rid))
        self.db.commit()

    # decisions log -------------------------------------------------------------------------------

    def log_decision(self, kind: str, backend: str, latency_ms: float) -> None:
        self.db.execute("INSERT INTO decisions (kind, backend, latency_ms, at) VALUES (?,?,?,?)",
                        (kind, backend, latency_ms, now()))
        self.db.commit()

    def decision_stats(self) -> list[dict]:
        return self._all(
            """SELECT backend, kind, COUNT(*) AS calls, AVG(latency_ms) AS avg_ms FROM decisions
               GROUP BY backend, kind ORDER BY backend, kind"""
        )

    # search --------------------------------------------------------------------------------------

    def search(self, query: str, limit: int = 12) -> list[dict]:
        """Best-matching transcript lines across all meetings (BM25), newest meeting first on ties."""
        match = fts_query(query)
        if not match:
            return []
        return self._all(
            """SELECT f.meeting_id, f.n, f.speaker, f.text, m.title, m.held_on, bm25(segments_fts) AS rank
               FROM segments_fts f JOIN meetings m ON m.id = f.meeting_id
               WHERE segments_fts MATCH ? ORDER BY rank, m.held_on DESC LIMIT ?""",
            (match, limit),
        )

    def search_items(self, query: str, limit: int = 20) -> list[dict]:
        terms = _terms(query)
        if not terms:
            return []
        where = " OR ".join("lower(i.text) LIKE ?" for _ in terms)
        return self._all(
            f"""SELECT i.*, m.title AS meeting_title, m.held_on FROM items i JOIN meetings m ON m.id = i.meeting_id
                WHERE {where} ORDER BY m.held_on DESC LIMIT ?""",
            (*[f"%{t}%" for t in terms], limit),
        )


STOPWORDS = set(
    "a an and are as at be but by did do does for from had has have how i if in into is it its me my of on or "
    "our so than that the their them then there these they this to was we were what when where which who whom "
    "why will with you your about any can could should would just also not no yes ok okay".split()
)


def _terms(query: str) -> list[str]:
    words = re.findall(r"[\w']+", query.lower())
    return [w for w in dict.fromkeys(words) if len(w) > 2 and w not in STOPWORDS]


def fts_query(query: str) -> str:
    """A safe FTS5 query: any of the meaningful words, each quoted so user text can't inject syntax."""
    return " OR ".join(f'"{t.replace(chr(34), "")}"' for t in _terms(query))
