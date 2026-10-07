"""Settings come from environment variables, optionally loaded from a .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(os.environ.get("MEETING_LEDGER_HOME", Path.home() / ".meeting-ledger"))


def load_env(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding variables already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _number(name: str, default, cast):
    value = os.environ.get(name, "").strip()
    return cast(value) if value else default


@dataclass
class Settings:
    db_path: Path = DATA_DIR / "ledger.db"
    model: str = "claude-opus-5-5"
    obsidian_vault: Path | None = None
    obsidian_folder: str = "Meetings"
    stale_after: int = 2  # an open item is stale after this many meetings with its owner that never mention it
    min_confidence: float = 0.7  # below this, a status change goes to review instead of being applied

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        vault = e("OBSIDIAN_VAULT", "").strip()
        return cls(
            db_path=Path(e("MEETING_LEDGER_DB", str(DATA_DIR / "ledger.db"))).expanduser(),
            model=e("MEETING_LEDGER_MODEL", "claude-opus-5-5"),
            obsidian_vault=Path(vault).expanduser() if vault else None,
            obsidian_folder=e("OBSIDIAN_FOLDER", "Meetings"),
            stale_after=_number("STALE_AFTER", 2, int),
            min_confidence=_number("MIN_CONFIDENCE", 0.7, float),
        )
