# Package the plugin for the OpenAI directory

MovieStar ships as a skills-only plugin: instructions and references that teach
an agent to use the open-source CLI. It declares no MCP server, app, or hooks.
The execution environment supplies Python, FFmpeg, and the `moviestar`
package.

## Package layout

[`plugins/moviestar`](../plugins/moviestar) contains:

- `plugin.json`: the portable Agent Plugins 1.0.0 manifest. OpenAI listing
  metadata lives under `extensions.com.openai.interface`.
- `.codex-plugin/plugin.json`: the Codex compatibility manifest used by
  existing marketplace installs. Its `interface` block must match the portable
  listing metadata exactly.
- `skills/moviestar/`: the skill, its `agents/openai.yaml` UI metadata, and
  its references.
- `assets/logo.png` (512×512) and `assets/logo.svg`: the listing logo and
  composer icon.

The logo is original MovieStar artwork, also used as the trymoviestar.com site
icon. Redistribution inside the plugin is covered by the
[trademark policy](../TRADEMARKS.md); the Apache License does not grant use of
the MovieStar name or logo for other products.

## Build the archive

```bash
bin/build-plugin-zip
```

The script validates the package, then writes
`dist/moviestar-plugin-<version>.zip` and prints its path, SHA-256 digest, and
file list as JSON. It refuses to build when it finds `mcp.json`, `.mcp.json`,
`.app.json`, or `hooks/`, a missing listing asset, or a missing skill. The
archive root holds `plugin.json`, the plugin files, `LICENSE`, and
`TRADEMARKS.md`. Entries are sorted and use fixed timestamps and permissions,
so the same source produces byte-identical archives. `dist/` is ignored by Git.

The repository contracts in `app/tests/test_public_docs_and_skill.py` check
the manifest schema shape, listing field limits, starter prompts, icon
dimensions, Codex/portable metadata parity, the skills-only boundary, and
archive reproducibility. Run them with `bin/preflight --docs`.

Listing limits checked locally: display name and short description at most 30
characters, long description at most 4,000, developer name at most 80, and up
to three unique starter prompts of at most 128 characters each. Starter prompts
must not contain `$skill` invocation syntax or `@` mentions, because they are
shown on surfaces that do not use Codex syntax. The skill's own
`agents/openai.yaml` prompt is Codex-specific and is checked separately.

## Submit

1. Complete individual or business publisher verification in the OpenAI
   organization settings. Submission requires an organization owner or the
   Apps Management Write role.
2. Upload the archive in the plugin submission portal and resolve every
   metadata validation error.
3. Wait for the required skill scan to finish successfully. Skills-only
   plugins do not need MCP review cases or a demo recording.
4. Complete the policy attestations and submit. Approval does not publish the
   listing automatically.

Record any requirement that appears only in the portal, together with the date
and the archive digest that was submitted. Any later change to the skill or
metadata needs a rebuilt archive and a fresh scan.

## Keep claims within tested environments

The listing describes the CLI and its requirements; it does not claim that
editing runs inside hosted ChatGPT environments. A contract test rejects
hosted-execution claims until a real hosted run has been validated. Update
that test, the listing text, and this guide together when the evidence exists.

A future MCP integration may need a separate listing or a migration: the
submission guide does not currently allow adding MCP to an existing
skills-only plugin.
