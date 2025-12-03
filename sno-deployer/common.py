import subprocess
from typing import Dict

class DeployError(RuntimeError):
    """Custom error for deployment failures."""


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    env: Dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with nicer error messages."""
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        msg = f"Command failed: {' '.join(cmd)}"
        if exc.stdout:
            msg += f"\nSTDOUT:\n{exc.stdout}"
        if exc.stderr:
            msg += f"\nSTDERR:\n{exc.stderr}"
        raise DeployError(msg) from exc

