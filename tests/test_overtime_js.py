"""The "Over time" plot of the viewer (web/overtime.js): the grouped time axis
and the comparison of visits. The checks are JavaScript, run with node."""
import shutil
import subprocess
from pathlib import Path

import pytest

CASES = Path(__file__).with_name("overtime_cases.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_overtime_js():
    run = subprocess.run(["node", str(CASES)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "ok"
