# meeting-notes-ai

Paste in raw meeting notes or a transcript — get back a clean structured summary with a TL;DR, key decisions, action items, and open questions. Powered by the Claude API.

## Example

**Input** (messy notes from a call):
```
talked to paris and alessandro today. paris thinks we should focus on the pilot first not 300k users.
spring place new york is the anchor. need to get 20-30 interviews done with members before building more.
alex needs to send the list of 50 people. equity split still not resolved, alex was supposed to come back with a proposal.
no new team members until we're aligned. another call needed with all three of us.
```

**Output:**
```
## TL;DR
Strategy call with Paris and Alessandro to align on Aether's pilot approach. Main outcome: narrow focus to Spring Place NY, validate with member interviews before further build.

## Key Decisions
- Focus on pilot (Spring Place NY), not the 300k user vision
- No new team members until Owen/Paris/Alessandro are fully aligned

## Action Items
1. Alessandro to send list of 50 Spring Place members/contacts — Owner: Alessandro
2. Conduct 20–30 member interviews framed as feature prioritization — Owner: Owen
3. Schedule three-way alignment call (Owen, Paris, Alessandro)

## Open Questions
- Equity split still unresolved — Alessandro was supposed to return with a proposal
```

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/owen-alderson/meeting-notes-ai.git
cd meeting-notes-ai
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Set your API key**

Copy `.env.example` to `.env` and add your [Anthropic API key](https://console.anthropic.com/):
```bash
cp .env.example .env
```
Then open `.env` and replace `your_api_key_here` with your key.

The script reads `ANTHROPIC_API_KEY` from your environment. You can also export it directly:
```bash
export ANTHROPIC_API_KEY=your_key_here
```

## Usage

**Option 1 — Pass a file:**
```bash
python main.py notes.txt
```

**Option 2 — Paste directly:**
```bash
python main.py
# Paste your notes, then press Ctrl+D
```

**Option 3 — Save output to a file:**
```bash
python main.py notes.txt --output summary.md
```

## Requirements

- Python 3.8+
- An [Anthropic API key](https://console.anthropic.com/)
