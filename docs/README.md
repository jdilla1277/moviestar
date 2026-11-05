# Moviestar documentation

Moviestar is a deterministic, non-destructive video editing CLI designed for
AI agents. Start with the path that matches your task:

- [Installation profiles](installation.md) — choose the base CLI, local
  transcription, supplied captions, or diarization.
- [Getting started](getting-started.md) — install Moviestar and produce a
  first verified edit.
- [Agent guide](agent-guide.md) — understand project state, time spaces,
  structured output, composition, and verification.
- [Review exports](review-exports.md) — create a 720p rendition while preserving
  authored composition and typography.
- [Review evidence](review-evidence.md) — keep artifact-bound technical, source,
  visual, motion, and listening judgments separate, with coverage and staleness.
- [Caption timing audit](caption-audit.md) — inspect text visibility intervals,
  short flashes, connector-only candidates, and optional proof frames.
- [Install the Moviestar skill](install-skill.md) — add the GitHub-distributed
  Codex plugin or copy the portable skill to another compatible agent.
- [Skill validation](skill-validation.md) — review the scaffold, installation,
  and source-blind editing evidence.
- [v0.8.1 release notes](releases/v0.8.1.md) — audio/font fixes, review exports
  and evidence, sharing improvements, and lightweight installation profiles.
- [v0.8.0 release notes](releases/v0.8.0.md) — private uploads, agent-directed
  sharing, recipient downloads, and hosted-video deletion.
- [v0.7.1 release notes](releases/v0.7.1.md) — the transcription fix for new
  installations.
- [v0.7.0 release notes](releases/v0.7.0.md) — preview the first open-source
  release and its upgrade notes.
- [Complete command guide](../app/README.md) — browse every command family and
  feature.

The CLI's built-in help is the authoritative command reference. Run
`moviestar --help`, then `moviestar COMMAND --help` or nested help such as
`moviestar scenes motion --help` before using an unfamiliar surface.

- [Accounts from the CLI](accounts.md) — terminal email verification, signup, age and terms consent, and revocable installation approval.
- [Personal Cloud projects](cloud-projects.md) — explicitly create, list, and reopen owned projects through an approved installation.

- [Sharing and download](sharing.md) — private uploads, unlisted handoffs, retries, link management, and recipient downloads.
