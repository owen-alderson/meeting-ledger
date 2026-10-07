"""The rules. Adding a meeting runs the whole loop:

1. Claude extracts items (each citing transcript lines) and proposes updates to earlier open items.
2. A deterministic grounding check: every quote must really appear in the lines it cites.
3. The decision model checks each new item (supported? did the owner agree?) and, independently of
   Claude, picks a status for each proposed update.
4. Updates both models agree on with enough confidence are applied; everything else goes to review.
5. Open items whose owner was in the meeting but that nobody mentioned get a miss; enough misses
   and the item is stale.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .config import Settings
from .decisions import FLAG_BELOW, Decider, Verdict
from .extract import Extraction, Extractor
from .ingest import Segment, normalise
from .store import Store

STATUSES = {
    "decision": ("standing", "reversed"),
    "action": ("open", "blocked", "done", "dropped"),
    "commitment": ("open", "blocked", "done", "dropped"),
    "question": ("open", "blocked", "done", "dropped"),
}

FLAG_TEXT = {
    "no_evidence": "Cites no transcript lines",
    "quote_not_found": "Its quote isn't in the lines it cites",
    "unsupported": "The cited lines may not support it",
    "not_agreed": "The owner may not have agreed to it",
    "unchecked": "The decision model couldn't check it",
}


class LedgerError(Exception):
    pass


@dataclass
class AddResult:
    meeting_id: int
    items: list[dict] = field(default_factory=list)
    applied: list[tuple[dict, str]] = field(default_factory=list)  # (earlier item, new status)
    reviews: int = 0
    newly_stale: list[dict] = field(default_factory=list)


def new_status(kind: str, update: str) -> str:
    """Map an update label onto the status an item of this kind should end up with."""
    if kind == "decision":
        return "reversed" if update in ("contradicted", "dropped") else "standing"
    return {"progressed": "open", "done": "done", "blocked": "blocked", "dropped": "dropped",
            "contradicted": "dropped"}[update]


def grounded(quote: str, lines: list[Segment]) -> bool:
    q = normalise(quote)
    return bool(q) and q in normalise(" ".join(s.text for s in lines))


def _names(people: list[str]) -> set[str]:
    return {w for p in people for w in normalise(p).split() if len(w) > 1}


def owner_present(owner: str, participants: list[str]) -> bool:
    return bool(_names([owner]) & _names(participants))


class Ledger:
    def __init__(self, store: Store, extractor: Extractor | None = None, decider: Decider | None = None,
                 settings: Settings | None = None):
        self.store = store
        self.extractor = extractor or Extractor()
        self.decider = decider or Decider()
        self.settings = settings or Settings()

    # adding a meeting ----------------------------------------------------------------------------

    def add(self, segments: list[Segment], held_on: str, title: str = "", source: str = "") -> AddResult:
        if not segments:
            raise LedgerError("Nothing to process: the input has no text.")
        prior = self.store.open_items()
        decisions = self.store.standing_decisions()  # so Claude can spot a decision being reversed
        ex: Extraction = self.extractor.extract(segments, held_on, title, prior + decisions)
        speakers = list(dict.fromkeys(s.speaker for s in segments if s.speaker))
        participants = list(dict.fromkeys(ex.participants + [s for s in speakers if not owner_present(s, ex.participants)]))
        mid = self.store.add_meeting(title or ex.title or "Untitled meeting", held_on, segments, source,
                                     ex.tldr, participants, getattr(self.extractor, "model", ""))
        by_n = {s.n: s for s in segments}
        result = AddResult(mid)

        checks = [(item, self._cited(item.evidence, by_n)) for item in ex.items]
        with ThreadPoolExecutor(max_workers=4) as pool:
            verdicts = list(pool.map(lambda c: self._safe(self.decider.check_item, c[0].kind, c[0].text,
                                                          c[0].owner, c[0].due, self._context(c[1], by_n)), checks))
        for (item, cited), verdict in zip(checks, verdicts):
            result.items.append(self._store_item(mid, item, cited, verdict, result))

        known = {i["id"]: i for i in prior + decisions}
        mentioned = set()
        for update in ex.updates:
            earlier = known.get(update.item_id)
            if earlier is None or update.item_id in mentioned:
                continue
            mentioned.add(update.item_id)
            self._apply_update(mid, earlier, update, self._cited(update.evidence, by_n), by_n, result)

        for item in prior:
            if item["id"] in mentioned:
                if item["misses"]:
                    self.store.update_item(item["id"], misses=0)
                continue
            if self._relevant(item, participants):
                misses = item["misses"] + 1
                self.store.update_item(item["id"], misses=misses)
                if misses == self.settings.stale_after:
                    result.newly_stale.append(self.store.item(item["id"]))
        return result

    @staticmethod
    def _cited(evidence: list[int], by_n: dict[int, Segment]) -> list[Segment]:
        return [by_n[n] for n in dict.fromkeys(evidence) if n in by_n]

    @staticmethod
    def _context(cited: list[Segment], by_n: dict[int, Segment]) -> list[Segment]:
        """The cited lines plus one line either side, so the decision model sees who said what to whom."""
        ns = sorted({m for s in cited for m in (s.n - 1, s.n, s.n + 1) if m in by_n})
        return [by_n[n] for n in ns]

    def _safe(self, fn, *args) -> Verdict | None:
        try:
            return fn(*args)
        except Exception:  # any backend failure: keep the item, flag it for a human
            return None

    def _log(self, kind: str, verdict: Verdict | None) -> None:
        if verdict is not None:
            self.store.log_decision(kind, verdict.backend, verdict.latency_ms)

    def _store_item(self, mid: int, item, cited: list[Segment], verdict: Verdict | None, result: AddResult) -> dict:
        flags = []
        if not cited:
            flags.append("no_evidence")
        elif not grounded(item.quote, cited):
            flags.append("quote_not_found")
        supported = agreed = None
        self._log("item_check", verdict)
        if verdict is None:
            flags.append("unchecked")
        else:
            supported = verdict.answers["supported"]["p"]
            if supported < FLAG_BELOW:
                flags.append("unsupported")
            if "agreed" in verdict.answers:
                agreed = verdict.answers["agreed"]["p"]
                if agreed < FLAG_BELOW:
                    flags.append("not_agreed")
        iid = self.store.add_item(mid, item.kind, item.text, item.owner, item.due, [s.n for s in cited],
                                  item.quote, flags, supported, agreed)
        if flags:
            self.store.add_review(iid, mid, "item", "; ".join(FLAG_TEXT[f] for f in flags),
                                  evidence=[s.n for s in cited])
            result.reviews += 1
        return self.store.item(iid)

    def _apply_update(self, mid: int, earlier: dict, update, cited: list[Segment], by_n, result: AddResult) -> None:
        evidence = [s.n for s in cited]
        verdict = self._safe(self.decider.check_update, earlier, self._context(cited, by_n)) if cited else None
        self._log("followup", verdict)
        checked, confidence = "", None
        if verdict is not None:
            checked, confidence = verdict.answers["status"]["choice"], verdict.answers["status"]["confidence"]

        reasons = []
        if not cited:
            reasons.append("cites no transcript lines")
        elif not grounded(update.quote, cited):
            reasons.append("its quote isn't in the lines it cites")
        if verdict is None and cited:
            reasons.append("the decision model couldn't check it")
        elif checked and checked != update.status:
            reasons.append(f"Claude says {update.status}, the decision model says {checked}")
        elif confidence is not None and confidence < self.settings.min_confidence:
            reasons.append(f"low confidence ({confidence:.0%})")
        if update.status == "contradicted":
            reasons.append("contradicts an earlier item")

        if reasons:
            self.store.add_review(earlier["id"], mid, "update", "; ".join(reasons), update.status, checked,
                                  confidence, evidence)
            result.reviews += 1
            return
        status = new_status(earlier["kind"], update.status)
        self.store.update_item(earlier["id"], status=status, misses=0)
        self.store.add_event(earlier["id"], status, "auto", mid, evidence, update.note or update.status)
        result.applied.append((earlier, status))

    def _relevant(self, item: dict, participants: list[str]) -> bool:
        """Should this meeting have mentioned the item? Only if its owner (or, for an unowned item,
        someone from the meeting it came from) was there."""
        if item["owner"]:
            return owner_present(item["owner"], participants)
        origin = self.store.meeting(item["meeting_id"])
        return bool(origin and _names(origin["participants"]) & _names(participants))

    # reading -------------------------------------------------------------------------------------

    def is_stale(self, item: dict) -> bool:
        return item["status"] in ("open", "blocked") and item["misses"] >= self.settings.stale_after

    def open_items(self, owner: str = "", stale_only: bool = False) -> list[dict]:
        items = self.store.open_items(owner)
        for i in items:
            i["stale"] = self.is_stale(i)
        return [i for i in items if i["stale"]] if stale_only else items

    # changes by a person -------------------------------------------------------------------------

    def set_status(self, item_id: int, status: str, note: str = "") -> dict:
        item = self.store.item(item_id)
        if item is None:
            raise LedgerError(f"No item #{item_id}.")
        if status not in STATUSES[item["kind"]]:
            raise LedgerError(f"A {item['kind']} can be {', '.join(STATUSES[item['kind']])}; not {status!r}.")
        self.store.update_item(item_id, status=status, misses=0)
        self.store.add_event(item_id, status, "you", note=note)
        return self.store.item(item_id)

    def resolve(self, review_id: int, status: str | None) -> None:
        """Settle a review. For a flagged item: a status keeps it (with that status, or as is for
        "keep"), None deletes it as a bad extraction. For an update: a status applies it, None ignores it."""
        review = self.store.review(review_id)
        if review is None or review["resolved"]:
            raise LedgerError(f"No pending review #{review_id}.")
        item = self.store.item(review["item_id"])
        if status is None:
            if review["kind"] == "item":
                self.store.db.execute("DELETE FROM items WHERE id = ?", (item["id"],))
                self.store.db.commit()
                return
            self.store.resolve_review(review_id, "rejected")
            return
        if review["kind"] == "item":
            self.store.update_item(item["id"], flags=[])
        if status != "keep":
            if review["kind"] == "update" and status in ("progressed", "done", "blocked", "dropped", "contradicted"):
                status = new_status(item["kind"], status)
            if status not in STATUSES[item["kind"]]:
                raise LedgerError(f"A {item['kind']} can be {', '.join(STATUSES[item['kind']])}; not {status!r}.")
            self.store.update_item(item["id"], status=status, misses=0)
            self.store.add_event(item["id"], status, "review", review["meeting_id"], review["evidence"])
        self.store.resolve_review(review_id, "accepted")
