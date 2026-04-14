import anthropic
import sys
import argparse
import os
from pathlib import Path

# Load .env if present
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


SYSTEM_PROMPT = """You are a meeting notes processor. Your job is to turn raw, messy meeting notes or transcripts into clean structured output.

Always respond in exactly this format — no extra commentary before or after:

## TL;DR
One or two sentences. What was this meeting about and what was the main outcome?

## Key Decisions
A bullet list of decisions that were made or confirmed. If none, write "None."

## Action Items
A numbered list. Each item must have:
- What needs to be done
- Who owns it (if mentioned)
- Deadline (if mentioned)

If no clear action items exist, write "None identified."

## Open Questions
Any unresolved questions or things that need follow-up. If none, write "None."
"""


def process_notes(raw_text: str) -> str:
    client = anthropic.Anthropic()

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Process these meeting notes:\n\n{raw_text}"
            }
        ]
    )

    return message.content[0].text


def main():
    parser = argparse.ArgumentParser(
        description="Turn raw meeting notes into structured summaries with action items."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to a .txt file containing meeting notes (omit to paste directly)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Path to save the output as a .md file (optional)"
    )
    args = parser.parse_args()

    if args.input:
        path = Path(args.input)
        if not path.exists():
            print(f"Error: file '{args.input}' not found.")
            sys.exit(1)
        raw_text = path.read_text(encoding="utf-8")
        print(f"Processing: {args.input}\n")
    else:
        print("Paste your meeting notes below. When done, press Enter then Ctrl+D (Mac/Linux) or Ctrl+Z (Windows):\n")
        raw_text = sys.stdin.read()

    if not raw_text.strip():
        print("Error: no input provided.")
        sys.exit(1)

    print("Processing...\n")
    result = process_notes(raw_text)

    print(result)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(result, encoding="utf-8")
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
