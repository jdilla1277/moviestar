"""Stage body for VM unit tests; real module imports run in browser coverage."""
from pathlib import Path


def stage_script():
    source = (Path(__file__).parents[1]/'src/moviestar/preview_native_stage.mjs').read_text()
    return '\n'.join(line for line in source.splitlines() if not line.startswith('import '))
