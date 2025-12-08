"""
Remote host management for SNO deployment.
Handles setup, configuration, and monitoring of remote libvirt hosts.

Reference: https://kcli.readthedocs.io/en/latest/#prerequisites
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

from common import DeployError, run


# Base SSH options for non-interactive CI/CD use
SSH_BASE_OPTS = (
    "-o StrictHostKeyChecking=no "
    "-o UserKnownHostsFile=/dev/null "
    "-o LogLevel=ERROR "
    "-o ConnectTimeout=30 "
    "-o ServerAliveInterval=10 "
    "-o ServerAliveCountMax=3 "
    "-o BatchMode=yes"
)

# Module-level SSH key path (set via set_ssh_key_path)
_ssh_key_path: Optional[str] = None


def set_ssh_key_path(key_path: Optional[str]) -> None:
    """
    Set the SSH key path to use for all SSH connections.
    Automatically fixes permissions to 600 if needed.
    """
    global _ssh_key_path
    
    if key_path:
        key_file = Path(key_path)
        
        # Check if file exists
        if not key_file.exists():
            raise DeployError(f"SSH key file not found: {key_path}")
        
        # Fix permissions to 600 if needed
        # SSH requires private keys to be readable only by owner
        current_mode = key_file.stat().st_mode
        required_mode = stat.S_IRUSR | stat.S_IWUSR  # 0o600
        
        if current_mode & 0o777 != 0o600:
            print(f"Fixing SSH key permissions: {key_path} (chmod 600)")
            key_file.chmod(0o600)
        
    _ssh_key_path = key_path


def get_ssh_opts() -> str:
    """Get SSH options, including identity file if configured."""
    if _ssh_key_path:
        return f"{SSH_BASE_OPTS} -i {_ssh_key_path}"
    return SSH_BASE_OPTS


def ssh_cmd(host: str, user: str, command: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    """Execute a command on the remote host via SSH."""
    ssh_opts = get_ssh_opts()
    full_cmd = f"ssh {ssh_opts} {user}@{host} {command!r}"
    return subprocess.run(
        full_cmd,
        shell=True,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def scp_cmd(src: str, dest: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Copy a file via SCP."""
    ssh_opts = get_ssh_opts()
    full_cmd = f"scp {ssh_opts} {src} {dest}"
    return subprocess.run(
        full_cmd,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_ssh_connectivity(host: str, user: str) -> tuple[bool, str]:
    """
    Check if we can connect to the remote host via SSH.
    Returns (success, error_message).
    """
    try:
        result = ssh_cmd(host, user, "echo 'ok'", check=False)
        if result.returncode == 0 and "ok" in result.stdout:
            return True, ""
        
        # Connection failed, return the error
        error_msg = f"SSH connection failed (exit code {result.returncode})"
        if result.stderr:
            error_msg += f"\nSTDERR: {result.stderr.strip()}"
        if result.stdout:
            error_msg += f"\nSTDOUT: {result.stdout.strip()}"
        return False, error_msg
    except subprocess.TimeoutExpired as e:
        return False, f"SSH connection timed out after {e.timeout}s"
    except Exception as e:
        return False, f"SSH connection failed: {str(e)}"


def setup_remote_libvirt(host: str, user: str) -> None:
    """
    Set up libvirt and prerequisites on the remote host.
    This is idempotent - safe to run multiple times.
    
    Reference: https://kcli.readthedocs.io/en/latest/#prerequisites
    """
    print(f"Setting up remote host: {user}@{host}")
    
    # Check SSH connectivity
    ssh_success, ssh_error = check_ssh_connectivity(host, user)
    if not ssh_success:
        # Show the SSH command that was attempted for debugging
        ssh_opts = get_ssh_opts()
        error_details = (
            f"Cannot connect via SSH to {user}@{host}\n"
            f"SSH command: ssh {ssh_opts} {user}@{host}\n"
            f"Error: {ssh_error}\n\n"
            "Troubleshooting:\n"
            "1. Verify the SSH key has correct permissions (chmod 600)\n"
            "2. Verify the remote host is reachable (ping)\n"
            "3. Verify SSH key is authorized on the remote host\n"
            "4. Try manually: ssh {ssh_opts} {user}@{host} 'echo test'"
        )
        raise DeployError(error_details)
    print("  SSH connection verified.")
    
    # Check if libvirt is installed
    result = ssh_cmd(host, user, "command -v virsh", check=False)
    if result.returncode != 0:
        print("  libvirt not found. Installing per kcli prerequisites...")
        
        # Detect package manager
        dnf_check = ssh_cmd(host, user, "command -v dnf", check=False)
        yum_check = ssh_cmd(host, user, "command -v yum", check=False)
        apt_check = ssh_cmd(host, user, "command -v apt-get", check=False)
        
        if dnf_check.returncode == 0:
            print("  Using dnf to install libvirt (RHEL/Fedora)...")
            ssh_cmd(host, user, "dnf -y install libvirt libvirt-daemon-driver-qemu qemu-kvm tar")
        elif yum_check.returncode == 0:
            print("  Using yum to install libvirt (CentOS/older RHEL)...")
            ssh_cmd(host, user, "yum -y install libvirt libvirt-daemon-driver-qemu qemu-kvm tar")
        elif apt_check.returncode == 0:
            print("  Using apt-get to install libvirt (Debian/Ubuntu)...")
            ssh_cmd(host, user, "apt-get update && apt-get install -y libvirt-daemon-system libvirt-clients qemu-kvm")
        else:
            raise DeployError("No supported package manager found on remote host (dnf/yum/apt-get)")
        
        # Add user to libvirt group and enable service
        ssh_cmd(host, user, "usermod -aG qemu,libvirt $(id -un)", check=False)
        ssh_cmd(host, user, "systemctl enable --now libvirtd")
        print("  libvirt installed successfully.")
    else:
        print("  libvirt is already installed.")
    
    # Fix the system.token permission issue
    print("  Fixing libvirt token permissions...")
    ssh_cmd(host, user, "rm -rf /run/libvirt/common && systemctl restart virtlogd libvirtd", check=False)
    
    # Enable modular libvirt daemons (required for newer kcli/libvirt versions)
    print("  Enabling modular libvirt daemons...")
    ssh_cmd(
        host, user,
        "for svc in virtqemud virtstoraged virtnetworkd virtnodedevd virtsecretd virtinterfaced virtnwfilterd; do "
        "systemctl enable --now ${svc}.socket 2>/dev/null || true; "
        "systemctl enable --now ${svc}-ro.socket 2>/dev/null || true; "
        "systemctl enable --now ${svc}-admin.socket 2>/dev/null || true; "
        "done",
        check=False
    )
    
    # Create default storage pool if it doesn't exist
    print("  Checking/creating default storage pool...")
    pool_check = ssh_cmd(host, user, "virsh -c qemu:///system pool-info default", check=False)
    if pool_check.returncode != 0:
        # Pool doesn't exist, create it
        ssh_cmd(host, user, "mkdir -p /var/lib/libvirt/images")
        # Try to define the pool, but ignore error if already defined
        define_result = ssh_cmd(
            host, user,
            "virsh -c qemu:///system pool-define-as default dir --target /var/lib/libvirt/images",
            check=False
        )
        if define_result.returncode != 0 and "already exists" not in define_result.stderr.lower():
            raise DeployError(f"Failed to define storage pool: {define_result.stderr}")
        print("  Default storage pool defined.")
    else:
        print("  Default storage pool already exists.")
    
    # Ensure pool is started (whether newly created or pre-existing)
    start_result = ssh_cmd(host, user, "virsh -c qemu:///system pool-start default", check=False)
    if start_result.returncode != 0 and "already active" not in start_result.stderr.lower():
        raise DeployError(f"Failed to start storage pool: {start_result.stderr}")
    
    # Ensure pool is set to autostart
    ssh_cmd(host, user, "virsh -c qemu:///system pool-autostart default", check=False)
    print("  Storage pool ready.")
    
    # Verify libvirt is working
    result = ssh_cmd(host, user, "virsh -c qemu:///system list --all", check=False)
    if result.returncode != 0:
        raise DeployError("libvirt is not working on the remote host after setup")
    
    # Install oc client
    print("  Ensuring oc client is installed...")
    oc_check = ssh_cmd(host, user, "command -v oc", check=False)
    if oc_check.returncode != 0:
        ssh_cmd(
            host, user,
            "curl -sL https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/stable/openshift-client-linux.tar.gz | tar xzf - -C /usr/local/bin oc kubectl"
        )
        print("  oc client installed.")
    else:
        print("  oc client already installed.")
    
    print(f"Remote host {host} setup complete!")


def get_kcli_client_name(host: str) -> str:
    """Generate a kcli client name from the hostname."""
    # Use the first part of the hostname
    return host.split(".")[0]


def _create_ssh_config(host: str, user: str, key_path: str) -> None:
    """Create SSH config file entry for the given host."""
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    ssh_config_file = ssh_dir / "config"
    
    # Build SSH config entry
    ssh_config_lines = [
        f"Host {host}",
        f"    User {user}",
        f"    IdentityFile {key_path}",
        f"    StrictHostKeyChecking no",
        f"    UserKnownHostsFile /dev/null",
        f"    LogLevel ERROR",
        f"    ConnectTimeout 30",
        f"    ServerAliveInterval 10",
        f"    ServerAliveCountMax 3",
        f"    BatchMode yes",
    ]
    
    ssh_config_content = "\n".join(ssh_config_lines) + "\n\n"
    
    # Remove existing entry for this host if present
    if ssh_config_file.exists():
        existing_config = ssh_config_file.read_text()
        pattern = rf"Host {re.escape(host)}\n(?:    .*\n)*\n?"
        existing_config = re.sub(pattern, "", existing_config)
        ssh_config_file.write_text(existing_config + ssh_config_content)
    else:
        ssh_config_file.write_text(ssh_config_content)
    
    ssh_config_file.chmod(0o600)
    print(f"  Created SSH config entry for {host}")


def _create_ssh_wrapper(key_path: str) -> None:
    """
    Setup SSH agent with the key for kcli.
    kcli uses libssh which respects SSH_AUTH_SOCK from ssh-agent.
    Also creates public key file which kcli needs for VM injection.
    """
    # Ensure ~/.ssh directory exists
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    
    # Copy private key to ~/.ssh/id_rsa (kcli looks for keys in standard location)
    default_key = ssh_dir / "id_rsa"
    key_content = Path(key_path).read_bytes()
    default_key.write_bytes(key_content)
    default_key.chmod(0o600)
    print(f"  Copied SSH key to {default_key}")
    
    # Generate public key from private key (kcli needs this for VM injection)
    pub_key = ssh_dir / "id_rsa.pub"
    if not pub_key.exists():
        print(f"  Generating public key: {pub_key}")
        result = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(default_key)],
            capture_output=True,
            text=True,
            check=True
        )
        pub_key.write_text(result.stdout)
        pub_key.chmod(0o644)
        print(f"    ✓ Public key generated")
    
    # Start ssh-agent if not already running
    if not os.environ.get("SSH_AUTH_SOCK"):
        print("  Starting ssh-agent...")
        result = subprocess.run(
            ["ssh-agent", "-s"],
            capture_output=True,
            text=True,
            check=True
        )
        # Parse and set environment variables from ssh-agent output
        # Output format: SSH_AUTH_SOCK=/tmp/ssh-xxx/agent.123; export SSH_AUTH_SOCK;
        for line in result.stdout.split('\n'):
            if '=' in line and not line.startswith('echo'):
                # Extract variable assignment
                var_assignment = line.split(';')[0].strip()
                if '=' in var_assignment:
                    key, value = var_assignment.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    os.environ[key] = value
                    print(f"    Set {key}")
    else:
        print(f"  Using existing ssh-agent: {os.environ['SSH_AUTH_SOCK']}")
    
    # Add the SSH key to ssh-agent
    print(f"  Adding SSH key to ssh-agent")
    result = subprocess.run(
        ["ssh-add", str(default_key)],
        capture_output=True,
        text=True,
        check=True
    )
    if result.returncode == 0:
        print(f"    ✓ SSH key added to agent")
    
    # List keys in agent for verification
    result = subprocess.run(
        ["ssh-add", "-l"],
        capture_output=True,
        text=True,
        check=False
    )
    if result.returncode == 0 and result.stdout:
        print(f"    Keys in agent: {len(result.stdout.splitlines())}")


def configure_kcli_remote_client(host: str, user: str) -> str:
    """
    Configure kcli to connect to the remote host.
    Returns the kcli client name.
    
    Uses SSH agent to load the key, which kcli will definitely respect.
    """
    import yaml
    import os
    
    client_name = get_kcli_client_name(host)
    
    # Use HOME from environment (might be set to /tmp/ssh-home if real home not writable)
    home_dir = Path(os.environ.get("HOME", str(Path.home())))
    kcli_dir = home_dir / ".kcli"
    kcli_dir.mkdir(parents=True, exist_ok=True)
    config_file = kcli_dir / "config.yml"
    
    # Load existing config or create empty
    config = {}
    if config_file.exists():
        config = yaml.safe_load(config_file.read_text()) or {}

    # Remove existing client entry if present
    config.pop(client_name, None)
    
    # Build the client config
    client_config = {
        "host": host,
        "user": user,
        "protocol": "ssh",
        "pool": "default",
        "type": "kvm",
    }
    
    # Add the new client
    # Force client_name to be a string in YAML (prevent "10" from becoming integer 10)
    config[str(client_name)] = client_config
    
    # Write config with explicit string quoting for keys that look like numbers
    yaml_content = yaml.dump(config, default_flow_style=False, allow_unicode=True)
    # Ensure numeric-looking keys are quoted
    if client_name.isdigit():
        yaml_content = yaml_content.replace(f"{client_name}:", f"'{client_name}':")
    config_file.write_text(yaml_content)
    print(f"kcli client '{client_name}' configured for {user}@{host}")
    
    # If SSH key is configured, create SSH wrapper and config
    if _ssh_key_path:
        # Use absolute path but don't resolve symlinks (keep /var/run as-is, not /run)
        if Path(_ssh_key_path).is_absolute():
            abs_key_path = _ssh_key_path
        else:
            abs_key_path = str(Path(_ssh_key_path).absolute())
        print(f"Configuring SSH wrapper for kcli to use key: {abs_key_path}")
        
        # Create SSH config (might help in some cases)
        _create_ssh_config(host, user, abs_key_path)
        
        # Create SSH wrapper script - kcli doesn't honor ~/.ssh/config
        # so we need to force the SSH options via a wrapper
        _create_ssh_wrapper(abs_key_path)
    
    # Verify kcli connection
    print(f"Verifying kcli connection to {host}...")
    result = run(["kcli", "-C", client_name, "list", "vm"], check=False, capture_output=True)
    if result.returncode != 0:
        error_msg = f"kcli cannot connect to remote host '{client_name}'.\n"
        if result.stderr:
            error_msg += f"Error: {result.stderr}\n"
        error_msg += f"\nDebug: Try manually:\n  ssh {host} 'echo test'\n  kcli -C {client_name} list vm"
        raise DeployError(error_msg)
    
    print(f"  ✓ kcli connection verified")
    return client_name


def setup_remote_cluster_access(
    host: str,
    user: str,
    cluster_name: str,
    api_ip: str,
    domain: str,
) -> None:
    """
    Set up the remote host for cluster access (copy kubeconfig, add hosts entry).
    """
    kubeconfig_path = Path.home() / ".kcli" / "clusters" / cluster_name / "auth" / "kubeconfig"
    
    # Wait for kubeconfig to be generated
    timeout = 120
    start = time.time()
    while not kubeconfig_path.exists():
        if time.time() - start > timeout:
            raise DeployError(f"Timeout waiting for kubeconfig at {kubeconfig_path}")
        print(f"  Waiting for kubeconfig... ({int(time.time() - start)}s)")
        time.sleep(5)
    
    # Copy kubeconfig to remote
    scp_cmd(str(kubeconfig_path), f"{user}@{host}:/root/kubeconfig")
    
    # Add hosts entry on remote
    api_hostname = f"api.{cluster_name}.{domain}"
    ssh_cmd(
        host, user,
        f"grep -q '{api_hostname}' /etc/hosts || echo '{api_ip} {api_hostname}' >> /etc/hosts",
        check=False
    )
    print(f"  Remote host configured for cluster access.")


def wait_for_cluster_ready(
    host: str,
    user: str,
    api_ip: str,
    timeout: int = 3600,
) -> bool:
    """
    Wait for the cluster to be ready by checking via the remote host.
    Returns True if cluster is ready, raises DeployError on timeout.
    """
    print(f"Waiting for cluster to be ready (timeout: {timeout}s)...")
    
    start_time = time.time()
    api_ready = False
    
    while True:
        elapsed = int(time.time() - start_time)
        
        if elapsed >= timeout:
            raise DeployError(f"Timeout waiting for cluster to be ready after {timeout}s")
        
        # Check if API is responding
        if not api_ready:
            result = ssh_cmd(host, user, f"curl -sk https://{api_ip}:6443/version", check=False)
            if "gitVersion" in result.stdout:
                print(f"  Kubernetes API is responding! ({elapsed}s)")
                api_ready = True
            else:
                print(f"  Waiting for Kubernetes API... ({elapsed}s)")
                time.sleep(30)
                continue
        
        # Check cluster version status using oc get with simple output parsing
        # Complex jsonpath doesn't work well over SSH, so we parse the table output
        cv_result = ssh_cmd(
            host, user,
            "export KUBECONFIG=/root/kubeconfig; "
            "oc get clusterversion version --no-headers 2>/dev/null || echo ''",
            check=False
        ).stdout.strip()
        
        # Parse: NAME VERSION AVAILABLE PROGRESSING SINCE STATUS
        # Example: version 4.20.6 True False 10m Cluster version is 4.20.6
        cv_available = ""
        cv_progressing = ""
        if cv_result:
            parts = cv_result.split()
            if len(parts) >= 4:
                cv_available = parts[2]    # AVAILABLE column
                cv_progressing = parts[3]  # PROGRESSING column
        
        if cv_available == "True" and cv_progressing == "False":
            print(f"\n{'='*50}")
            print("SUCCESS! Cluster is ready!")
            print(f"{'='*50}")
            return True
        
        # Show current status
        node_status = ssh_cmd(
            host, user,
            "export KUBECONFIG=/root/kubeconfig; oc get nodes --no-headers 2>/dev/null | head -1",
            check=False
        ).stdout.strip()
        
        print(f"  Cluster status: Available={cv_available or 'Unknown'}, Progressing={cv_progressing or 'Unknown'} ({elapsed}s)")
        if node_status:
            print(f"  Node: {node_status}")
        
        time.sleep(30)


def get_cluster_status(host: str, user: str) -> str:
    """Get the current cluster status from the remote host."""
    result = ssh_cmd(
        host, user,
        "export KUBECONFIG=/root/kubeconfig; "
        "oc get clusterversion 2>/dev/null; echo ''; "
        "oc get nodes 2>/dev/null; echo ''; "
        "oc get co 2>/dev/null | head -20",
        check=False
    )
    return result.stdout


def print_access_instructions(
    host: str,
    user: str,
    cluster_name: str,
    api_ip: str,
    domain: str,
    kcli_client: str,
) -> None:
    """Print instructions for accessing the cluster."""
    kubeconfig_path = Path.home() / ".kcli" / "clusters" / cluster_name / "auth"
    password_file = kubeconfig_path / "kubeadmin-password"
    
    password = "see kubeadmin-password file"
    if password_file.exists():
        password = password_file.read_text().strip()
    
    print(f"""
{'='*60}
ACCESS INSTRUCTIONS
{'='*60}

Kubeconfig (local): {kubeconfig_path / 'kubeconfig'}
Kubeconfig (remote): /root/kubeconfig on {host}
Kubeadmin password: {password}

To run oc commands via remote host:
  ssh {user}@{host} 'export KUBECONFIG=/root/kubeconfig; oc get nodes'

To access from your local machine, set up an SSH tunnel:
  ssh -L 6443:{api_ip}:6443 -L 443:{api_ip}:443 {user}@{host} -N &
  echo '127.0.0.1 api.{cluster_name}.{domain}' | sudo tee -a /etc/hosts
  export KUBECONFIG={kubeconfig_path / 'kubeconfig'}
  oc get nodes

{'='*60}
To delete the cluster:
  kcli -C {kcli_client} delete cluster {cluster_name} -y
{'='*60}
""")

