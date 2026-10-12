#!/usr/bin/env python3
"""#115: keep the dispatch scratch root inside the test run's own temp tree.

Every dispatch writes its prompt task file under the structured scratch root
and sweeps that root on the way in, so a suite left at the default would write
into — and delete from — the operator's real C:\\Dev\\scratch\\model-proctor.
Importing this module and calling install() points MODEL_PROCTOR_SCRATCH at a
per-process temp dir (removed at interpreter exit) unless the variable is
already set, which keeps a suite hermetic without every test passing an
override.

The runner and the delegate both read the variable, so a value installed here
reaches the runner subprocess through the inherited environment.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

ENV_VAR = "MODEL_PROCTOR_SCRATCH"


def install():
    """Install (once) and return the scratch root this process dispatches under."""
    current = os.environ.get(ENV_VAR)
    if current:
        return Path(current)
    root = Path(tempfile.mkdtemp(prefix="mp-scratch-test-"))
    os.environ[ENV_VAR] = str(root)
    atexit.register(shutil.rmtree, str(root), ignore_errors=True)
    return root
