"""Support ``python -m moviestar`` as an alias for the console script.

Agents working in virtualenvs commonly reach for ``python -m <package>``
to guarantee they invoke that environment's installed package (issue
#306). ``prog_name`` is pinned so help/usage render as ``moviestar``,
identical to the console script, rather than ``__main__.py``.
"""

from moviestar.cli import cli

if __name__ == "__main__":
    cli(prog_name="moviestar")
