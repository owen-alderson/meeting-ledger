"""Markdown output: one note per meeting, with YAML frontmatter and [[wikilinks]] to people who
already have a note in your Obsidian vault. The same renderer prints `meeting-ledger show`."""

import re
from pathlib import Path

from .store import Store

KIND_HEADINGS = [("decision", "Decisions"), ("action", "Action items"), ("commitment", "Commitments"),
                 ("question", "Open questions")]


def note_index(vault: Path) -> dict[str, str]:
    """Lower-cased note name -> note name, for every Markdown note in the vault (dot-folders skipped)."""
    out = {}
    for p in vault.rglob("*.md"):
        if not any(part.startswith(".") for part in p.relative_to(vault).parts):
            out.setdefault(p.stem.lower(), p.stem)
    return out


def person_link(name: str, notes: dict[str, str] | None) -> str:
    """[[Full Name]] when the vault has a note for this person: an exact match, or a first name that
    matches exactly one note. Otherwise the plain name."""
    if not name or not notes:
        return name
    exact = notes.get(name.lower())
    if exact:
        return f"[[{exact}]]"
    if " " not in name.strip():
        first = [v for k, v in notes.items() if k.split(" ")[0] == name.lower() and " " in k]
        if len(first) == 1:
            return f"[[{first[0]}|{name}]]"
    return name


def _yaml(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def safe_filename(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "", text).strip()[:120] or "Meeting"


def render(store: Store, meeting_id: int, notes: dict[str, str] | None = None, transcript: bool = True) -> str:
    m = store.meeting(meeting_id)
    if m is None:
        raise KeyError(meeting_id)
    items = store.items(meeting_id)
    people = [person_link(p, notes) for p in m["participants"]]
    lines = [
        "---",
        f"date: {m['held_on']}",
        "tags: [meeting]",
        f"title: {_yaml(m['title'])}",
        "participants:",
        *[f"  - {_yaml(p)}" for p in people],
        f"source: {_yaml(m['source'])}",
        f"meeting-ledger-id: {m['id']}",
        "---",
        "",
        f"# {m['title']}",
        "",
    ]
    if people:
        lines += [f"**Who:** {', '.join(people)}", ""]
    lines += ["## TL;DR", m["tldr"] or "(none)", ""]
    for kind, heading in KIND_HEADINGS:
        group = [i for i in items if i["kind"] == kind]
        lines.append(f"## {heading}")
        if not group:
            lines += ["None.", ""]
            continue
        for i in group:
            box = ""
            if kind != "decision":
                box = "[x] " if i["status"] == "done" else "[-] " if i["status"] == "dropped" else "[ ] "
            meta = [person_link(i["owner"], notes)] if i["owner"] else []
            if i["due"]:
                meta.append(f"due {i['due']}")
            if i["status"] in ("blocked", "reversed"):
                meta.append(i["status"])
            refs = ", ".join(f"L{n}" for n in i["evidence"])
            tail = f" — {' · '.join(meta)}" if meta else ""
            flag = " ⚠️ needs review" if i["flags"] else ""
            lines.append(f"- {box}{i['text']}{tail}{f' ({refs})' if refs else ''}{flag}")
        lines.append("")
    moved = store.meeting_events(meeting_id)
    if moved:
        lines.append("## Earlier items this meeting moved")
        for e in moved:
            refs = ", ".join(f"L{n}" for n in e["evidence"])
            lines.append(f"- {e['item_text']} → **{e['status']}**{f' ({refs})' if refs else ''}")
        lines.append("")
    if transcript:
        lines += ["## Transcript", ""]
        for s in store.segments(meeting_id):
            who = f"**{s.speaker}:** " if s.speaker else ""
            lines.append(f"{s.n}. {who}{s.text}")
        lines.append("")
    return "\n".join(lines)


def export(store: Store, meeting_id: int, vault: Path, folder: str = "Meetings") -> Path:
    m = store.meeting(meeting_id)
    target = vault / folder
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{m['held_on']} {safe_filename(m['title'])}.md"
    path.write_text(render(store, meeting_id, note_index(vault)), encoding="utf-8")
    return path
