"""EnvPilot VFD2 backend — recovery shim.

The original ``vfd2_env_backend.py`` source (last edited 2026-08-03) was lost when
the working tree was reverted with no git history, leaving this file empty. The
last fully-working build (2026-07-30, where Citrix + Jenkins login succeeded end
to end) survives only as compiled bytecode in ``vfd2_env_backend_compiled.pyc``.

Reliable Python 3.13 source decompilers do not currently exist, so this shim
loads and executes that bytecode as ``__main__`` — giving byte-for-byte the
2026-07-30 backend behaviour. Command-line args (``--session-id``, ``--action``,
etc.) and environment variables are passed through unchanged.

To recover editable source, decompile ``vfd2_env_backend_compiled.pyc`` with a
3.13-capable tool (e.g. the PyLingual web decompiler) and replace this file.
"""

import marshal
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMPILED = os.path.join(_HERE, "vfd2_env_backend_compiled.pyc")

if not os.path.isfile(_COMPILED):
    sys.stderr.write(
        "vfd2_env_backend: compiled backend not found: {}\n".format(_COMPILED)
    )
    sys.exit(2)

with open(_COMPILED, "rb") as _fh:
    _fh.read(16)  # skip 16-byte CPython 3.7+ .pyc header before the code object
    _code = marshal.load(_fh)

# Execute the compiled backend exactly as if it were run as the main script, so
# its own ``if __name__ == "__main__":`` entry point drives argparse/sys.argv.
_globals = {
    "__name__": "__main__",
    "__file__": os.path.join(_HERE, "vfd2_env_backend.py"),
    "__builtins__": __builtins__,
}
exec(_code, _globals)
