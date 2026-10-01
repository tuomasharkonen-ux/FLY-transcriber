---
name: fly-summarise
description: Summarise a raw FLY meeting transcript from the meeting inbox (status: raw) into a meeting note, after checking the speaker diarization and the domain terms. Use when asked to process the meeting inbox or a transcript file.
---

# Meeting transcript → note

Input: a file in the project's meeting inbox (`meetings/_inbox/` unless `CLAUDE.md`
says otherwise) with `status: raw` in its frontmatter. It is a machine transcript
(Whisper large-v3 + pyannote diarization) that no human has reviewed.
Output: one meeting note that follows this project's conventions (see `CLAUDE.md`).

If no file is named, list the `status: raw` files in the inbox. If there is more than
one, ask which to process, or process them one at a time, each with its own review.

If the file has `diarized: false`, there are no reliable speaker labels. Skip Pass 1,
attribute nothing to named people unless the text itself makes it clear ("I'm Sam
and…"), and say so in the attribution review.

## Pass 1: settle who said what

Speaker labels come from voice alone. Some lines are attributed to the wrong person,
between people in the same room and between them and remote participants. Names were
mapped to labels at saving, so a misattributed line carries a real name and looks right.

Read the whole transcript once before writing anything. Then, for every line that will
end up in the note as a decision, action item, commitment or clearly held opinion,
check the attribution against the text:

- Being addressed by name ("Sam, what do you think?") usually means the next turn is
  that person.
- First-person claims ("I set that up last week") must fit what that person does or
  owns. Use `participants:` and what this project already knows about who works on
  what (search earlier meeting notes and project notes for the person's name).
- A question and its answer are rarely the same speaker.
- One argument split across two labels mid-sentence is usually one speaker.
- A line that contradicts what the same "speaker" said a moment ago is suspect.

Reassign only on evidence like this, never on a hunch or because it would read more
smoothly. If the evidence is not clear, attribute to both ("Alex / Sam") or to nobody
("it was agreed that…"). A missing name is recoverable. A wrong name in the notes is not.

## Pass 2: write the note

Where `CLAUDE.md` or existing notes define a template, filename scheme, folder or task
format, follow it. The defaults below apply only where the project has none.

- **Language:** write in the language the meeting was held in, headings included.
  Keep product names, jargon and quotes as spoken.
- **Frontmatter:** keep it slim: `title`, `date`, `tags` (participants and project,
  reusing existing tags where they fit), `type: meeting`. Drop the inbox-only fields
  (`participants`, `speakers`, `diarized`, `status`, `source`).
- **Filename and folder:** follow the project's scheme; otherwise `dd-mm-yy-title.md`
  in `meetings/`, kebab-case. Check the name is not taken.
- **Body:**
  1. **Summary:** 3–6 sentences on what the meeting was about and where it landed.
  2. **Decisions:** only things actually agreed in the transcript. If something was
     discussed but not concluded, it goes under Open questions instead.
  3. **Action items:** checkboxes with the owner's name, and a due date only if one
     was stated: `- [ ] (Sam) Send the API key request (by Friday)`. Before adding
     one, search the project for an equivalent open task; link to it rather than
     duplicating it.
  4. **Open questions**
  5. **Discussion notes:** the substance, by topic, with attribution where settled.
- **Links:** if the project uses wiki links (`[[note-name]]`), link related existing
  notes (projects, earlier meetings on the same topic). Never link to notes that
  don't exist.
- **Domain terms:** speech recognition garbles names and jargon into plausible-looking
  words, and the transcriber was given no vocabulary, so this is the only place they
  get corrected. Check the project's glossary or dictionary file if it has one, the
  people in `participants:`, earlier meeting notes and the project's own documents
  for the right spelling. Fix a term only when that makes the intended word clear,
  and flag the rest as `[unclear: "…"]`. List each fix in the attribution review
  (`[15:12] "Power Way" → "Power BI"`) so a human can check it. If a genuinely new
  term comes up, propose it and a definition to the user; don't add it to the
  glossary yourself.

## Pass 3: insights and decisions

If the project keeps collections such as a decision log or research insights, scan
the finished note for candidates, check them against existing entries for duplicates,
and list them when presenting the note. Create them only after the user agrees.
Pass 1's rule applies here too: no named decision-maker unless the attribution is settled.

## Attribution review

Give this to the user in your reply when presenting the note. It is not part of the
note. List every line you reassigned or left ambiguous, and every term you corrected,
with its timestamp, e.g.
`[12:40] "I already talked to Alex about this" moved Alex → Sam (Alex is addressed in the previous turn)`.
Write "No changes" if there were none. This lets a human spot-check against the audio.

## Rules

- Never state a decision, owner, date or number that is not in the transcript.
  When the transcript does not support a claim, leave it out.
- Don't smooth over disagreement. If people disagreed and it wasn't resolved, say so.
- When done, show the user the note, with the attribution review in your reply (never
  in the note). Delete the inbox file
  only once the note is written, and only if `CLAUDE.md` allows it or the user agrees;
  if writing the note failed, keep the inbox file.
