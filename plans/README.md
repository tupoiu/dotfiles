# plans

One living plan per piece of ongoing work, e.g. `diffweb.md`.

A plan is written before the work starts and then kept current as it happens —
it is a working document, not a record of what was proposed. Tick steps off as
they land, amend them when reality disagrees, and add a line to
Decisions / gotchas whenever something surprises you.

## Shape

```markdown
# <feature> — plan

**Goal:** one or two sentences, including how you would observe it working.

## Steps
- [x] Step, ending in a concrete check ("`poe test-claude` passes", "service answers /healthz")
- [ ] ...

## Decisions / gotchas
- Chose A over B because C.
- Discovered: <the surprise worth remembering>
```

## Before handing work back

Take a screenshot of the page that is actually running - the instance the user
will open, not a fresh one - and look at it. Tests and a green diff do not show
you a control rendering as a tofu box, a colour with no contrast, or a layout
that wrapped. If the project has no UI, run the thing and read its real output.

## What belongs here

- **Steps with verifications.** Each chunk ends in something observable, not a
  description of the code that was written.
- **Decisions and gotchas, but only the surprising ones.** "Chose X over Y
  because Z", or the trap that cost an hour. This is the highest value per word
  in the file.

## What does not

- **Explanations of the codebase.** Reference `file:line` instead — prose copied
  out of the code goes stale and nobody reads two sources of truth.
- **Progress logs and timestamps.** Git history already has them.
- **Exact commands with their expected output.** Run it and look; pre-scripting
  it just rots.
- **Sections that exist because a template said so.** Write a section when it
  earns its place.
