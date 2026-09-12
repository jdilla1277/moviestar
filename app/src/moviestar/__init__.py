"""moviestar — video editing CLI for AI agents.

The version string is read from the installed package metadata so
``pyproject.toml`` is the single source of truth (no drift between
``__version__`` and the published version on PyPI). Falls back to a
sentinel only if the package isn't installed (e.g., running directly
from a checkout without `pip install -e`).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("moviestar")
except PackageNotFoundError:  # pragma: no cover — only hit in non-installed checkouts
    __version__ = "0.0.0+unknown"
