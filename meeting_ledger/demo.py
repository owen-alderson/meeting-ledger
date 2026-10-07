"""Fictional demo data: three weekly meetings at a small coffee roaster. `meeting-ledger demo`
runs them through the real pipeline with a scripted extractor and decision model, in memory, so
it works with no API keys and touches nothing on disk."""

from .decisions import Verdict
from .extract import Extraction, Item, Update
from .ingest import parse
from .ledger import Ledger
from .store import Store

MEETINGS = [
    ("2026-09-22", "wholesale-sync.vtt", """WEBVTT

00:00:02.000 --> 00:00:06.000
<v Maya>Okay, let's start. Main thing today is wholesale.

00:00:07.000 --> 00:00:13.000
<v Theo>I've talked to Bloom Café, Hartley's and the Corner Room. All three want samples.

00:00:14.000 --> 00:00:22.000
<v Maya>Then let's do it. We launch wholesale with those three cafés first and park the online shop until January.

00:00:23.000 --> 00:00:27.000
<v Priya>Agreed. The online shop needs new photos anyway.

00:00:28.000 --> 00:00:31.000
<v Theo>I'll send the price sheet to all three by Friday.

00:00:32.000 --> 00:00:41.000
<v Sam>One thing. I know the head buyer at Northside Market. I'll introduce you, Maya, they take a lot of local roasters.

00:00:42.000 --> 00:00:44.000
<v Maya>That would be huge, thank you.

00:00:45.000 --> 00:00:49.000
<v Priya>Before I forget, the label design invoice is still outstanding.

00:00:50.000 --> 00:00:55.000
<v Maya>Sorry, that's on me. I'll pay it by the end of the month, promise.

00:00:56.000 --> 00:01:01.000
<v Theo>Do we need a food-safety certificate to sell wholesale? Nobody seems to know.

00:01:02.000 --> 00:01:05.000
<v Maya>Good question. Let's find out before the first delivery.

00:01:06.000 --> 00:01:08.000
<v Sam>I have to drop, talk next week.
"""),
    ("2026-09-29", "weekly-sync.txt", """Maya: Quick one today, Sam's travelling.
Theo: Price sheet went out to all three cafés on Thursday. Hartley's already ordered.
Maya: Brilliant.
Priya: I checked the certificate question. We do need one, the council does inspections.
Maya: Okay, I'll book the inspection this week.
Theo: Also, Bloom wants to start in November, so for now it's really two cafés, not three.
Maya: Fine, we start with two and add Bloom in November.
Priya: Maybe Theo could redo the website at some point, the menu page is out of date.
Theo: Maybe. Let's see after the launch.
Maya: Right, that's it. Same time next week.
"""),
    ("2026-10-06", "sync-with-sam.txt", """[00:00:04] Maya: Sam's back, welcome.
[00:00:09] Sam: Thanks. Before you ask, I haven't done the Northside intro yet. It's top of my list for next week.
[00:00:17] Maya: No problem.
[00:00:20] Maya: The inspection is booked for the fourteenth.
[00:00:26] Theo: Hartley's reordered, we're going to run out of bags. I'll order two hundred more today.
[00:00:34] Priya: The new labels look great on the shelf, by the way.
[00:00:39] Sam: Are you tracking margins per café?
[00:00:43] Theo: Not yet. I'll set up a simple sheet.
[00:00:47] Maya: Good. That's everything.
"""),
]

PEOPLE = ["Maya Lindqvist", "Theo Adeyemi", "Priya Raman", "Sam Okoro"]

EXTRACTIONS = {
    "2026-09-22": dict(
        title="Wholesale launch sync",
        tldr="Agreed to launch wholesale with three cafés first and park the online shop until January. "
             "Sam offered an intro to Northside Market; Maya owes Priya for the label design.",
        participants=PEOPLE,
        items=[
            Item("decision", "Launch wholesale with Bloom Café, Hartley's and the Corner Room first; park the online shop until January",
                 "", "", [3], "We launch wholesale with those three cafés first"),
            Item("action", "Send the price sheet to Bloom Café, Hartley's and the Corner Room", "Theo", "2026-09-26",
                 [5], "I'll send the price sheet to all three by Friday"),
            Item("commitment", "Introduce Maya to the head buyer at Northside Market", "Sam", "", [6],
                 "I'll introduce you, Maya"),
            Item("commitment", "Pay Priya's label design invoice", "Maya", "2026-09-30", [8, 9],
                 "I'll pay it by the end of the month"),
            Item("question", "Is a food-safety certificate needed to sell wholesale?", "", "", [10, 11],
                 "Do we need a food-safety certificate to sell wholesale?"),
        ],
        updates=[],
    ),
    "2026-09-29": dict(
        title="Weekly sync",
        tldr="Price sheets are out and Hartley's has ordered. A food-safety inspection is needed; Bloom "
             "Café moves to November, so wholesale starts with two cafés.",
        participants=["Maya Lindqvist", "Theo Adeyemi", "Priya Raman"],
        items=[
            Item("action", "Book the council food-safety inspection", "Maya", "this week", [5],
                 "I'll book the inspection this week"),
            Item("decision", "Start wholesale with two cafés and add Bloom Café in November", "", "", [6, 7],
                 "we start with two and add Bloom in November"),
            Item("action", "Redo the website menu page", "Theo", "", [8, 9], "Theo could redo the website at some point"),
        ],
        updates=[
            ("price sheet", "done", [2], "Price sheet went out to all three cafés on Thursday", "Sent Thursday"),
            ("food-safety certificate", "done", [4], "We do need one, the council does inspections", "Yes: council inspection"),
            ("Launch wholesale", "contradicted", [6, 7], "for now it's really two cafés, not three", "Now two cafés"),
        ],
    ),
    "2026-10-06": dict(
        title="Weekly sync with Sam",
        tldr="Sam still owes the Northside intro (next week). Inspection booked for the 14th; Theo is "
             "ordering more bags and will start tracking margins per café.",
        participants=PEOPLE,
        items=[
            Item("action", "Order 200 more coffee bags", "Theo", "2026-10-06", [5], "I'll order two hundred more today"),
            Item("action", "Set up a sheet tracking margins per café", "Theo", "", [7, 8], "I'll set up a simple sheet"),
        ],
        updates=[
            ("Northside", "progressed", [2], "It's top of my list for next week", "Planned for next week"),
            ("inspection", "progressed", [4], "The inspection is booked for the fourteenth", "Booked for the 14th"),
        ],
    ),
}

# What the scripted decision model says: item checks default to supported and agreed.
ITEM_CHECKS = {"Redo the website menu page": {"supported": 0.81, "agreed": 0.18}}
UPDATE_CHECKS = {
    "price sheet": ("done", 0.94),
    "food-safety certificate": ("done", 0.9),
    "Launch wholesale": ("contradicted", 0.88),
    "Northside": ("progressed", 0.86),
    "inspection": ("done", 0.55),
}


class ScriptedExtractor:
    model = "demo (scripted)"

    def extract(self, segments, held_on, title_hint="", open_items=None):
        script = dict(EXTRACTIONS[held_on])
        updates = []
        for needle, status, evidence, quote, note in script.pop("updates"):
            match = next(i for i in open_items or [] if needle.lower() in i["text"].lower())
            updates.append(Update(match["id"], status, evidence, quote, note))
        return Extraction(updates=updates, **script)


class ScriptedDecider:
    backend = "demo (scripted)"

    def check_item(self, kind, text, owner, due, lines):
        answers = {"supported": {"p": 0.93}}
        if kind in ("action", "commitment") and owner:
            answers["agreed"] = {"p": 0.9}
        for key, p in ITEM_CHECKS.get(text, {}).items():
            answers[key] = {"p": p}
        return Verdict(answers, self.backend, 140.0)

    def check_update(self, item, lines):
        choice, confidence = next(v for k, v in UPDATE_CHECKS.items() if k.lower() in item["text"].lower())
        return Verdict({"status": {"choice": choice, "confidence": confidence}}, self.backend, 160.0)


def seed(store: Store | None = None) -> Ledger:
    ledger = Ledger(store or Store(), ScriptedExtractor(), ScriptedDecider())
    for held_on, name, text in MEETINGS:
        ledger.add(parse(text, name), held_on, source=name)
    return ledger
