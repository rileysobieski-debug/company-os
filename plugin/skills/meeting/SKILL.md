---
description: Convene a Company OS meeting — dept-wide, company-wide, or a cross-agent subset. Each attendee contributes, the final attendee synthesizes, a transcript is saved to decisions/meetings/. Use when the user types things like "convene editorial", "all hands meeting about X", "meeting with marketing and finance about Y", or invokes /company-os:meeting.
---

# Company OS — Meetings

For quick back-and-forth with one or two agents, open a chat window and
summon them by name. This skill is for **orchestrated meetings** where
the attendee list is defined up front and the output should be recorded.

Three meeting types — pick the one that matches the ask.

## 1. Department-wide

The dept's manager opens the meeting, each specialist weighs in, the
manager synthesizes. Transcript lands at
`<company>/decisions/meetings/dept-meeting-<dept>.md`.

**Invoke when** the user says things like:
- "convene editorial about the newsletter launch"
- "all-hands in marketing about the Q3 positioning"
- "get editorial's take on X"

**Command:**
```bash
python -m cli meeting --dept <dept-name> --topic "<topic>" \
  --company-dir "<resolved company path>" \
  [--invite specialist1 specialist2 ...]
```

Omit `--invite` to include every specialist in the dept. Include it
to narrow to specific voices (e.g., just the copywriter and the
editorial-director).

## 2. Company-wide (all active managers)

Every active department manager attends. The last participant in the
list speaks as the closing synthesizer. Transcript lands at
`<company>/decisions/meetings/cross-meeting.md`.

**Invoke when** the user says things like:
- "all hands meeting about the newsletter pivot"
- "get everyone together on the Q3 plan"
- "company-wide check-in"

**Command:**
```bash
python -m cli meeting --company-wide --topic "<topic>" \
  --company-dir "<resolved company path>"
```

## 3. Cross-agent subset

Pick any combination of dept managers and/or board members. Useful
for scoped strategic questions ("marketing + finance + Contrarian on
the spend plan").

**Invoke when** the user names specific attendees:
- "meeting with marketing and finance about sponsor pricing"
- "bring in the Contrarian and the Analyst on the launch decision"
- "editorial + community for the subscriber acquisition plan"

**Participant syntax:**
- Dept manager: the dept name (e.g. `marketing`, `finance`, `editorial`)
- Board member: `board:<Role>` (Strategist, Storyteller, Analyst, Builder, Contrarian, KnowledgeElicitor)

**Command:**
```bash
python -m cli meeting --cross-agent \
  --participants marketing finance board:Contrarian \
  --topic "<topic>" \
  --company-dir "<resolved company path>"
```

## What to do

1. Parse `$ARGUMENTS` to determine meeting type:
   - Single dept name mentioned → dept-wide
   - "all-hands", "company-wide", "everyone" → company-wide
   - Multiple specific names (depts or board roles) → cross-agent
2. If the topic isn't clear, ask the user for one sentence.
3. Resolve the company dir from `COMPANY_OS_VAULT_DIR` + active company.
4. Run the appropriate CLI invocation above.
5. Surface the transcript path. Optionally summarize the meeting's
   conclusion in your response if the user asked a question the
   meeting answered.

## Notes

- Meetings are expensive — each participant is one LLM call. A 6-dept
  company-wide meeting is 6 calls. Warn the user if the attendee count
  is high and the budget tight (`python -m cli costs`).
- Transcripts are reviewable: the user can re-read a meeting at any
  time from `decisions/meetings/`.
- The last participant named is always the synthesizer for cross-agent
  meetings. Order matters — put the voice you want to close with last.
