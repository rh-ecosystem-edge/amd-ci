"""
Remote host management for SNO deployment.
Handles setup, configuration, and monitoring of remote libvirt hosts.

Reference: https://kcli.readthedocs.io/en/latest/#prerequisites
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

from common import DeployError, run


# Base SSH options for non-interactive CI/CD use
SSH_BASE_OPTS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

# Module-level SSH key path (set via set_ssh_key_path)
_ssh_key_path: Optional[str] = None


def set_ssh_key_path(key_path: Optional[str]) -> None:
    """Set the SSH key path to use for all SSH connections."""
    global _ssh_key_path
    _ssh_key_path = key_path


def get_ssh_opts() -> str:
    """Get SSH options, including identity file if configured."""
    if _ssh_key_path:
        return f"{SSH_BASE_OPTS} -i {_ssh_key_path}"
    return SSH_BASE_OPTS


def ssh_cmd(host: str, user: str, command: str, check: bool = True) -> subprocess.CompletedProcess:
    """Execute a command on the remote host via SSH."""
    ssh_opts = get_ssh_opts()
    full_cmd = f"ssh {ssh_opts} {user}@{host} {command!r}"
    return subprocess.run(
        full_cmd,
        shell=True,
        check=check,
        capture_output=True,
        text=True,
    )


def scp_cmd(src: str, dest: str) -> subprocess.CompletedProcess:
    """Copy a file via SCP."""
    ssh_opts = get_ssh_opts()
    full_cmd = f"scp {ssh_opts} {src} {dest}"
    return subprocess.run(
        full_cmd,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    )


def check_ssh_connectivity(host: str, user: str) -> bool:
    """Check if we can connect to the remote host via SSH."""
    result = ssh_cmd(host, user, "echo 'ok'", check=False)
    return result.returncode == 0 and "ok" in result.stdout


def setup_remote_libvirt(host: str, user: str) -> None:
    """
    Set up libvirt and prerequisites on the remote host.
    This is idempotent - safe to run multiple times.
    
    Reference: https://kcli.readthedocs.io/en/latest/#prerequisites
    """
    print(f"Setting up remote host: {user}@{host}")
    
    # Check SSH connectivity
    if not check_ssh_connectivity(host, user):
        raise DeployError(
            f"Cannot connect via SSH to {user}@{host}\n"
            "Ensure SSH key-based authentication is set up."
        )
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
        ssh_cmd(host, user, "mkdir -p /var/lib/libvirt/images")
        ssh_cmd(host, user, "virsh -c qemu:///system pool-define-as default dir --target /var/lib/libvirt/images")
        ssh_cmd(host, user, "virsh -c qemu:///system pool-start default")
        ssh_cmd(host, user, "virsh -c qemu:///system pool-autostart default")
        print("  Default storage pool created.")
    else:
        print("  Default storage pool already exists.")
    
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


def configure_kcli_remote_client(host: str, user: str) -> str:
    """
    Configure kcli to connect to the remote host.
    Returns the kcli client name.
    """
    import yaml
    
    client_name = get_kcli_client_name(host)
    kcli_dir = Path.home() / ".kcli"
    kcli_dir.mkdir(parents=True, exist_ok=True)
    config_file = kcli_dir / "config.yml"
    
    # Load existing config or create empty
    config = {}
    if config_file.exists():
        config = yaml.safe_load(config_file.read_text()) or {}

    # Remove existing client entry if present
    config.pop(client_name, None)
    
    # Add the new client
    config[client_name] = {
        "host": host,
        "user": user,
        "protocol": "ssh",
        "pool": "default",
        "type": "kvm",
    }
    
    # Write config
    config_file.write_text(yaml.dump(config, default_flow_style=False))
    print(f"kcli client '{client_name}' configured for {user}@{host}")
    
    # Verify connection
    result = run(["kcli", "-C", client_name, "list", "vm"], check=False, capture_output=True)
    if result.returncode != 0:
        raise DeployError(
            f"kcli cannot connect to remote host '{client_name}'.\n"
            f"Error: {result.stderr}"
        )
    
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

