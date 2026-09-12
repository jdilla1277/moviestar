# Contributing to MovieStar

Thank you for helping make video editing easier for agents.

## Before you start

For bugs and small improvements, open an issue or a focused pull request. For a
large feature or a change to MovieStar's command or JSON contracts, open an
issue first so maintainers and contributors can agree on the surface before a
large implementation lands.

Do not put security-sensitive details in a public issue. Follow
[`SECURITY.md`](SECURITY.md) instead.

## Make a change

1. Create a feature branch from `main`.
2. Add a failing test that demonstrates the change.
3. Implement the smallest complete fix.
4. Run `bin/test` while iterating and `bin/preflight --slow` before requesting
   review.
5. Open a focused pull request with the two-sentence plain-language opener
   described in [`CLAUDE.md`](CLAUDE.md).

MovieStar requires Python 3.10 or newer and FFmpeg plus ffprobe on `PATH`.
Create a development environment with:

```bash
python3 -m venv app/.venv
source app/.venv/bin/activate
python -m pip install -e app pytest
```

## Contribution terms

MovieStar is licensed under the Apache License 2.0. By intentionally submitting
a contribution for inclusion in this project, you agree that it may be
distributed under that license and represent that you have the right to submit
it. Clearly identify third-party code, data, media, fonts, or other assets and
include their provenance and license terms.

All merges are performed by a human repository owner after review.
