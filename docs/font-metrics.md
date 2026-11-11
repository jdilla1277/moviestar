# Overlay text measurement

Automatic wrapping uses the selected font file's shaped advances at the resolved
font size. `max-width` is resolved against the canvas and horizontal padding is
subtracted before fitting words. Case transforms and font scaling apply before
measurement. Explicit newlines, including blank lines, remain line breaks;
an individual word wider than the available width stays whole.

For example, `iii iii` in bundled Inter at 96px fits a 200px content width.
The previous fixed character-width estimate split it into two lines. A wider
font can require a different break for the same text and width.

Screenshot, watch, and export, including their dry runs, consume the same plans.
Highlight captions also use these planned line breaks. Overlay summaries include
`text_metrics` with the measurement method, content width, font size, collection
face index, and each line's `advance_width` and `ink_bounds`. Ink bounds are
relative to the line origin and baseline, with positive y pointing down.

Advances decide line fitting. Ink bounds describe the outlines that can paint
outside those advances, such as an italic glyph's overhang. Diagnostic bounds
include horizontal overhang without changing line breaks or moving anchors.

`estimated_bounds` remains an estimate. Rasterization, hinting, renderer and
shaping-engine versions, bidirectional preprocessing, and libass highlight
rendering can differ from the OpenType measurements. Vertical block placement,
padding, stroke, shadow, and rotation also remain estimates; use rendered proof
frames to inspect final visibility. This change does not add support for
`letter-spacing`, which the renderer still ignores with a warning.

An unsupported outline format, such as a bitmap-only face, retains the selected
font and reports `method: "character-estimate"`, `estimated: true`, a fallback
reason, and a warning. It does not substitute another font. Existing renderer
errors still identify fonts it cannot paint. Normal OpenType/TrueType fonts and
collections use HarfBuzz measurements from face zero, matching drawtext's file
selection.

## Dependency redistribution review

The core dependency [`uharfbuzz`](https://github.com/harfbuzz/uharfbuzz) supplies
HarfBuzz bindings and portable wheels; it does not require a system HarfBuzz
installation. Version 0.53.0 and newer supports the required measurement API;
Python's resolver selects a compatible release (0.55.0 supports Python 3.10,
while 0.57.0 requires Python 3.11 or newer).

The binding is [Apache-2.0 licensed](https://github.com/harfbuzz/uharfbuzz/blob/main/LICENSE).
Bundled HarfBuzz uses the permissive [Old MIT license](https://github.com/harfbuzz/harfbuzz/blob/main/COPYING),
which permits redistribution with its copyright and license notices. These terms
are compatible with this package's Apache-2.0 distribution. The dependency's
license metadata is checked by `bin/check-dependency-licenses`; the upstream
HarfBuzz terms were also reviewed because the binding metadata describes only
the binding. Moviestar does not copy or vendor either implementation or add new
font assets.
