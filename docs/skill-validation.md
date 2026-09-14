# MovieStar skill validation

Validation performed 2026-09-14 against the public v0.7.0 candidate.

## Structure and discovery

- The bundled skill-creator validator accepted
  `plugins/moviestar/skills/moviestar`.
- The bundled plugin validator accepted `plugins/moviestar` and its manifest.
- Six repository contract tests passed for required public docs, local links,
  plugin/package identity, marketplace policy, portable skill metadata, and
  private-boundary markers.
- The complete public preflight passed with 2,272 tests and two expected skips;
  the dependency-license policy passed for all 29 checked distributions.
- An isolated Codex home added the repository as marketplace `moviestar`,
  installed `moviestar@moviestar` at version 0.7.0, and reported the plugin as
  enabled. This test did not alter the operator's normal Codex configuration.

## Source-blind editing smoke

The package was built as a wheel with SHA-256
`b89695226c1c64eb8aae8210dbb0a8ed33182c7a2d486da12701079e5b9eaa4c` and
installed into a clean Python 3.12.12 environment outside the source checkout.
The test used FFmpeg 9.0.1 to generate a six-second 640×360 video with an AAC
audio track, then followed the skill's workflow:

1. `doctor` reported `ready`.
2. `load --no-transcribe` created a new project without a model download.
3. `trim` produced a four-second result.
4. `overlays add` placed a centered title over the result.
5. `export --dry-run` resolved the expected output and duration without
   writing it.
6. The real export produced a four-second file.
7. FFprobe confirmed one 640×360 H.264 video stream and one AAC audio stream.
8. A screenshot from the rendered file visibly showed the complete centered
   title over the generated test pattern.

No source-tree import was available to the editing environment, and no
generated media was committed. A GitHub-backed marketplace install should be
repeated against the PR branch after it is pushed and against `main` after the
owner merges it.
