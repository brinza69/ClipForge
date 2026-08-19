# ClipForge — AGENTS.md

**The project's instructions live in [`CLAUDE.md`](CLAUDE.md). Read that.**

Everything an agent needs is there and only there: the pipeline map, the eleven
rules, the persistence patterns, and the gotchas. This file exists so tools that
look for `AGENTS.md` by name still find their way; it deliberately holds no
content of its own.

It used to be a byte-for-byte copy. That is the failure this repo keeps writing
down — `docs/clipper-map.md` opens with it: *a map that is only mostly true is
worse than none, because the next session trusts it and greps anyway.* Two
copies of a live document drift the first time one is edited, and the agent
reading the stale one works from rules that were changed for a reason.

Start here:

| you want | read |
|---|---|
| the project's rules and file map | [`CLAUDE.md`](CLAUDE.md) |
| the AI Stream Clipper, file by file | [`docs/clipper-map.md`](docs/clipper-map.md) |
| the state of the world and the known problems | [`docs/handoff-clipper-session-4.md`](docs/handoff-clipper-session-4.md) |
