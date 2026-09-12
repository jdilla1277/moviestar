# MovieStar development guide

MovieStar is a Python CLI for deterministic, agent-driven video editing. The
package lives in `app/`, tests live in `app/tests/`, and repository helpers live
in `bin/`.

## Setup

MovieStar requires Python 3.10 or newer and FFmpeg plus ffprobe on `PATH`.

```bash
python3 -m venv app/.venv
source app/.venv/bin/activate
python -m pip install -e app pytest
```

Use `bin/test` for the fast test suite and `bin/test --run-slow` for the full
suite. Run `bin/preflight` before every push.

## Development rules

- Use red/green TDD: add a failing test, implement the smallest fix, then
  refactor while green.
- Work on a feature branch. Never commit directly to `main`.
- Check open pull requests before editing shared code.
- Preserve structured JSON output and deterministic, non-destructive behavior.
- Send progress narration to stderr; keep stdout machine-readable.
- Do not commit generated media, environments, secrets, credentials, user data,
  or material copied from private repositories.
- Do not add a dependency or bundled asset without recording and checking its
  redistribution terms.

## Pull requests

Open every pull request with two plain-language sentences before any headings or
lists. The first says what an agent or user can now do; the second says what was
missing or broken before.

Agents must never merge pull requests or enable auto-merge. They may prepare a
green pull request, then must hand the merge to a human repository owner. The
same explicit-owner boundary applies to publishing packages, creating releases,
changing repository visibility, and configuring credentials.
