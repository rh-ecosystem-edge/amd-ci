import shutil
from pathlib import Path
from common import DeployError, run

def ensure_kcli_installed() -> None:
    """Verify kcli is available in PATH."""
    if shutil.which("kcli") is None:
        raise DeployError(
            "kcli is not installed or not in PATH.\n"
            "On Fedora/RHEL you can install it with:\n"
            "  sudo dnf -y copr enable karmab/kcli\n"
            "  sudo dnf -y install kcli\n"
            "See kcli docs: https://kcli.readthedocs.io/en/latest/#installation"
        )

def ensure_pull_secret_exists(pull_secret_path: Path) -> None:
    """Ensure the pull secret file exists."""
    if not pull_secret_path.is_file():
        raise DeployError(
            f"Pull secret file '{pull_secret_path}' not found.\n"
            "Download it from https://cloud.redhat.com/openshift/install/pull-secret"
        )

def ensure_kcli_config() -> None:
    """Ensure a basic ~/.kcli/config.yml exists, creating a 'local' host if needed."""
    home = Path.home()
    kcli_dir = home / ".kcli"
    kcli_dir.mkdir(parents=True, exist_ok=True)

    config_file = kcli_dir / "config.yml"
    if not config_file.is_file():
        print("No ~/.kcli/config.yml found. Creating a default local kvm client...")
        run(["kcli", "create", "host", "kvm", "-H", "127.0.0.1", "local"])

