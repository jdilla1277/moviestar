# Install the MovieStar skill

The repository contains a portable skill at
[`plugins/moviestar/skills/moviestar`](../plugins/moviestar/skills/moviestar)
and a Codex plugin wrapper at
[`plugins/moviestar`](../plugins/moviestar).

## Codex plugin from GitHub

With a Codex CLI version that supports plugins, add this repository as a Git
marketplace and install MovieStar:

```bash
codex plugin marketplace add jdilla1277/moviestar --ref main
codex plugin add moviestar@moviestar
```

Start a new Codex thread after installation so the newly installed skill is
discovered. Updating later uses:

```bash
codex plugin marketplace upgrade moviestar
codex plugin add moviestar@moviestar
```

The plugin supplies instructions only. The target environment still needs the
`moviestar` Python package, Python 3.10+, FFmpeg, and ffprobe; follow
[getting started](getting-started.md).

## Standalone skill

For Codex skill installation without the plugin wrapper, ask the built-in
`$skill-installer` to install the `plugins/moviestar/skills/moviestar` path from
`jdilla1277/moviestar`.

For another agent that supports the open skill-directory convention, copy the
complete `plugins/moviestar/skills/moviestar` directory into that product's
documented skill location. Keep `SKILL.md`, `agents/openai.yaml`, and the
`references/` directory together so links continue to resolve. Consult that
agent's current documentation for its discovery path and restart or open a new
session after installation.

Review the skill source before installation when operating in a sensitive
environment. It does not bundle credentials, services, or executable hooks;
it teaches the agent how to use the local MovieStar CLI and when to request
permission for optional downloads or external actions.
