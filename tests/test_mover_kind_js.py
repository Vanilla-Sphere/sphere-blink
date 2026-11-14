"""What the viewer calls a named moving object (moverKind of web/movers.js): a
body beyond Neptune, such as Pluto, is not a "known asteroid". The checks are
JavaScript, run with node."""
import shutil
import subprocess
from pathlib import Path

import pytest

CASES = Path(__file__).with_name("mover_kind_cases.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_mover_kind_js():
    run = subprocess.run(["node", str(CASES)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "ok"
