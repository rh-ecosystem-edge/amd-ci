"""
Remote host management for SNO deployment.
Handles setup, configuration, and monitoring of remote libvirt hosts.

Reference: https://kcli.readthedocs.io/en/latest/#prerequisites
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional

from common import DeployError, run
from config import DEFAULT_MIN_FREE_SPACE_GB
import shared.ssh as _ssh_mod
from shared.ssh import (
    set_ssh_key_path,
    get_ssh_opts,
    ssh_cmd,
    scp_cmd,
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


def _resolve_vfio_ids(
    host: str, user: str, pci_devices: list[str]
) -> list[str]:
    """Resolve PCI bus addresses to vendor:device IDs on the remote host.

    Validates each address format, then queries ``lspci`` to obtain the
    vendor:device ID (e.g. ``1002:740f``).

    Returns:
        De-duplicated list of vendor:device ID strings.
    """
    pci_addr_re = re.compile(
        r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$"
    )

    vfio_ids: list[str] = []
    for addr in pci_devices:
        if not pci_addr_re.match(addr):
            raise DeployError(
                f"Invalid PCI address format: '{addr}'. "
                f"Expected format: 0000:b3:00.0"
            )
        result = ssh_cmd(host, user, f"lspci -ns {addr}", check=False)
        if result.returncode != 0 or not result.stdout.strip():
            raise DeployError(f"PCI device {addr} not found on host {host}")
        # lspci -ns output example: "b3:00.0 0300: 1002:740f (rev c8)"
        match = re.search(r"[\da-fA-F]{4}:[\da-fA-F]{4}", result.stdout)
        if not match:
            raise DeployError(
                f"Could not parse vendor:device ID from lspci output for {addr}: "
                f"{result.stdout.strip()}"
            )
        vid = match.group(0)
        if vid not in vfio_ids:
            vfio_ids.append(vid)

    return vfio_ids


def _get_required_iommu_params(
    host: str, user: str, vfio_ids: list[str]
) -> list[str]:
    """Build the list of kernel parameters required for PCI passthrough.

    Detects the CPU vendor to choose ``intel_iommu=on`` or ``amd_iommu=on``.
    """
    cpu_info = ssh_cmd(host, user, "grep -m1 vendor_id /proc/cpuinfo", check=True).stdout
    if "AuthenticAMD" in cpu_info:
        iommu_param = "amd_iommu=on"
    else:
        iommu_param = "intel_iommu=on"

    return [
        iommu_param,
        "iommu=pt",
        "rd.driver.pre=vfio-pci",
        f"vfio-pci.ids={','.join(vfio_ids)}",
    ]


def _reboot_and_wait(host: str, user: str, timeout: int = 300) -> None:
    """Reboot the remote host and wait for it to come back.

    Waits for SSH to fail (host going down), then waits for SSH to
    succeed (host back up).
    """
    ssh_cmd(host, user, "reboot", check=False)

    start = time.time()

    print("  Waiting for host to go down...")
    while time.time() - start < timeout:
        time.sleep(5)
        ok, _ = check_ssh_connectivity(host, user)
        if not ok:
            print("  Host is down.")
            break
    else:
        raise DeployError(
            f"Host {host} did not go down after reboot within {timeout}s"
        )

    start = time.time()
    print("  Waiting for host to come back...")
    while time.time() - start < timeout:
        time.sleep(15)
        ok, _ = check_ssh_connectivity(host, user)
        if ok:
            break
        elapsed = int(time.time() - start)
        print(f"  Waiting for host to come back... ({elapsed}s)")
    else:
        raise DeployError(
            f"Host {host} did not come back after reboot within {timeout}s"
        )


def ensure_host_pci_passthrough(
    host: str, user: str, pci_devices: list[str]
) -> None:
    """Ensure IOMMU and vfio-pci kernel parameters are set on the remote host.

    Checks ``/proc/cmdline`` for the required parameters.  If any are missing,
    updates the kernel command line via ``grubby`` and reboots the host.

    Any pre-existing ``vfio-pci.ids`` value is replaced with the new one so
    that repeated invocations with different devices never create duplicates.
    """
    print(f"  Checking PCI passthrough configuration on {host}...")

    vfio_ids = _resolve_vfio_ids(host, user, pci_devices)
    required_params = _get_required_iommu_params(host, user, vfio_ids)

    cmdline_tokens = ssh_cmd(host, user, "cat /proc/cmdline", check=True).stdout.split()
    missing = [p for p in required_params if p not in cmdline_tokens]

    if not missing:
        print("  PCI passthrough already configured, no reboot needed.")
        return

    # Remove stale vfio-pci.ids only when it differs from the desired value
    vfio_param = required_params[-1]  # "vfio-pci.ids=..."
    old_vfio = [t for t in cmdline_tokens if t.startswith("vfio-pci.ids=")]
    if old_vfio and old_vfio[0] != vfio_param:
        ssh_cmd(
            host, user,
            f"grubby --update-kernel=ALL --remove-args='{old_vfio[0]}'",
            check=True,
        )
        if vfio_param not in missing:
            missing.append(vfio_param)

    print(f"  Missing kernel parameters: {' '.join(missing)}")
    print("  Updating kernel command line via grubby...")

    grubby_args = " ".join(missing)
    ssh_cmd(
        host, user,
        f"grubby --update-kernel=ALL --args='{grubby_args}'",
        check=True,
    )

    print("  Rebooting host to apply kernel parameters...")
    _reboot_and_wait(host, user)

    new_cmdline_tokens = ssh_cmd(host, user, "cat /proc/cmdline", check=True).stdout.split()
    still_missing = [p for p in required_params if p not in new_cmdline_tokens]
    if still_missing:
        raise DeployError(
            f"Kernel parameters missing after reboot: {' '.join(still_missing)}\n"
            f"Current cmdline: {' '.join(new_cmdline_tokens)}"
        )

    print("  PCI passthrough enabled successfully.")


# Filesystem types to ignore when looking for a real, persistent mount point
# to place the libvirt storage pool on.
_NON_STORAGE_FS_TYPES = (
    "tmpfs", "devtmpfs", "squashfs", "overlay", "iso9660",
    "proc", "sysfs", "devpts", "cgroup", "cgroup2", "autofs",
    "mqueue", "tracefs", "debugfs", "hugetlbfs", "pstore", "bpf",
    "configfs", "securityfs",
)


def _get_free_space_gb(host: str, user: str, path: str) -> float:
    """Return the free space (in GB) on the filesystem containing `path`."""
    result = ssh_cmd(host, user, f"df -B1 --output=avail {shlex.quote(path)} 2>/dev/null", check=False)
    if result.returncode != 0:
        return 0.0
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return 0.0
    try:
        return int(lines[1]) / (1024 ** 3)
    except ValueError:
        return 0.0


def select_storage_mount(host: str, user: str, min_required_gb: float) -> str:
    """
    Inspect the real mounted filesystems on the remote host and return the
    directory to use as the base for the libvirt default storage pool.

    Picks whichever real mount point currently has the most free space and
    targets a "libvirt/images" subdirectory under it (or the plain default,
    /var/lib/libvirt/images, if root itself happens to be the winner). This
    makes pool placement adapt automatically to any machine's partition
    layout, without needing to know it in advance.

    Always picks the objectively largest candidate rather than settling for
    "good enough" on root: since the pool is never auto-migrated later (see
    MGMT-23421), this initial placement is effectively permanent, so it's
    worth maximizing the runway rather than risking hitting the limit sooner
    on a smaller partition that merely cleared the minimum.
    """
    exclude_args = " ".join(f"-x {fstype}" for fstype in _NON_STORAGE_FS_TYPES)
    df_result = ssh_cmd(
        host, user,
        f"df -B1 --output=target,avail,fstype {exclude_args} 2>/dev/null",
        check=False
    )
    if df_result.returncode != 0 or not df_result.stdout.strip():
        raise DeployError(f"Failed to inspect disk space on {host}: {df_result.stderr}")

    candidates: list[tuple[str, float]] = []
    for line in df_result.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        mount_point, avail_bytes = parts[0], parts[1]
        try:
            avail_gb = int(avail_bytes) / (1024 ** 3)
        except ValueError:
            continue
        candidates.append((mount_point, avail_gb))

    if not candidates:
        raise DeployError(f"No usable mount points found on {host} to place the storage pool.")

    best_mount, best_avail_gb = max(candidates, key=lambda item: item[1])
    if best_avail_gb < min_required_gb:
        raise DeployError(
            f"No storage location on {host} has the required "
            f"{min_required_gb}GB free; best candidate has {best_avail_gb:.0f}GB."
        )

    if best_mount == "/":
        print(f"  Root partition has the most free space ({best_avail_gb:.0f}GB); using default location.")
        return "/var/lib/libvirt/images"

    target_path = f"{best_mount.rstrip('/')}/libvirt/images"
    print(f"  Selected {best_mount} as storage location ({best_avail_gb:.0f}GB free).")
    return target_path


def setup_remote_libvirt(
    host: str,
    user: str,
    libvirt_pool_path: Optional[str] = None,
    min_free_space_gb: float = DEFAULT_MIN_FREE_SPACE_GB,
) -> None:
    """
    Set up libvirt and prerequisites on the remote host.
    This is idempotent - safe to run multiple times.

    Args:
        host: Remote host address
        user: SSH user
        libvirt_pool_path: Explicit target directory for the libvirt default
            storage pool. If not provided (None), the location is chosen
            automatically: whichever real mount point has the most free
            space (falling back to the default /var/lib/libvirt/images on
            root if root itself has enough room or the most space overall).
        min_free_space_gb: Minimum free space (GB) required. Used both to
            decide whether root itself is good enough, and to decide
            whether an already-configured pool location still has enough
            room (avoiding unnecessary relocation on every run).

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
    
    # Determine the storage pool's target directory and whether it needs
    # to be (re)defined.
    #
    # Deliberately simple by design (see MGMT-23421): we never move
    # existing volumes between locations. An earlier version of this
    # function did attempt that (to auto-recover when a pool's partition
    # filled up), but relocating files out from under libvirt turned out to
    # require also fixing qcow2 backing-file pointers and snapshot metadata
    # that live independently of the moved files -- real bugs that were
    # easy to introduce and easy to miss. The trade-off here is a hard
    # failure instead of automatic migration: if the pool already holds
    # volumes and there isn't enough room, we ask the operator to free up
    # space (or move things themselves) rather than risk silently
    # corrupting libvirt state.
    needs_define = False
    current_path: Optional[str] = None
    pool_is_empty = True

    pool_check = ssh_cmd(host, user, "virsh -c qemu:///system pool-info default", check=False)
    pool_exists = pool_check.returncode == 0

    if pool_exists:
        dumpxml = ssh_cmd(host, user, "virsh -c qemu:///system pool-dumpxml default", check=False)
        if dumpxml.returncode != 0:
            raise DeployError(
                f"Failed to inspect existing storage pool: {dumpxml.stderr}"
            )
        current_match = re.search(r"<path>(.*?)</path>", dumpxml.stdout)
        current_path = current_match.group(1).rstrip("/") if current_match else None

        if current_path:
            find_result = ssh_cmd(
                host, user,
                f"find {shlex.quote(current_path)} -mindepth 1 -maxdepth 1 2>/dev/null",
                check=False,
            )
            pool_is_empty = not any(line.strip() for line in find_result.stdout.splitlines())

    if libvirt_pool_path:
        desired_path = libvirt_pool_path.rstrip("/")
    elif not pool_exists or pool_is_empty:
        # Nothing to preserve, so it's safe to automatically pick whichever
        # mount currently has the most free space.
        desired_path = select_storage_mount(host, user, min_free_space_gb)
    elif (free_gb := _get_free_space_gb(host, user, current_path)) >= min_free_space_gb:
        print(f"  Current storage location {current_path} still has sufficient free space, keeping it.")
        desired_path = current_path
    else:
        raise DeployError(
            f"Storage pool at {current_path} only has {free_gb:.0f}GB free "
            f"(need {min_free_space_gb:.0f}GB) and already holds existing "
            "volumes. Automatic relocation between partitions is not "
            "supported -- free up space (e.g. `make cluster-delete` old "
            "clusters) or move the pool yourself, then re-run."
        )

    print(f"  Configuring default storage pool at {desired_path}...")

    if not pool_exists:
        needs_define = True
    elif current_path == desired_path:
        print("  Default storage pool already configured at correct path.")
    elif not pool_is_empty:
        # Only reachable via an explicit libvirt_pool_path override that
        # points somewhere other than a pool that already holds volumes.
        raise DeployError(
            f"Storage pool at {current_path} already holds existing volumes, "
            f"but a different location ({desired_path}) was requested. "
            "Automatic relocation between partitions is not supported -- "
            "manually move the pool or clear it, then re-run."
        )
    else:
        # The pool exists but is empty, so there's nothing on disk to
        # preserve -- just move the pool definition itself.
        print(f"  Storage pool points to {current_path or 'unknown path'} (empty), redefining at {desired_path}...")

        destroy_result = ssh_cmd(host, user, "virsh -c qemu:///system pool-destroy default", check=False)
        if destroy_result.returncode != 0 and "not active" not in destroy_result.stderr.lower():
            raise DeployError(f"Failed to stop existing storage pool: {destroy_result.stderr}")

        undefine_result = ssh_cmd(host, user, "virsh -c qemu:///system pool-undefine default", check=False)
        if undefine_result.returncode != 0:
            raise DeployError(f"Failed to undefine existing storage pool: {undefine_result.stderr}")

        needs_define = True

    quoted_desired_path = shlex.quote(desired_path)

    if needs_define:
        ssh_cmd(host, user, f"mkdir -p {quoted_desired_path}")
        define_result = ssh_cmd(
            host, user,
            f"virsh -c qemu:///system pool-define-as default dir --target {quoted_desired_path}",
            check=False
        )
        if define_result.returncode != 0:
            raise DeployError(f"Failed to define storage pool: {define_result.stderr}")
        print(f"  Default storage pool defined at {desired_path}.")

    # Ensure the pool directory carries the SELinux context libvirt/qemu expects.
    # Individual volumes get dynamically relabeled by libvirt when a VM starts, but
    # the directory itself (and any pre-existing files moved in above) may still
    # carry the wrong context (e.g. user_home_t under /home) otherwise. Run this
    # unconditionally so pre-existing pools with the wrong label also get fixed.
    semanage_check = ssh_cmd(host, user, "command -v semanage && command -v restorecon", check=False)
    if semanage_check.returncode == 0:
        # `semanage fcontext -m` only ever touches *local* customizations --
        # it can't "modify" a rule that comes from base policy. Paths like
        # /var/lib/libvirt/images are already virt_image_t out of the box
        # (no local override exists to add or modify), so unconditionally
        # trying "-a, falling back to -m" isn't reliable across all
        # semanage/policycoreutils versions. Check what context the path
        # would actually resolve to first, and only add a local override
        # when it's genuinely missing or wrong.
        matchpathcon_result = ssh_cmd(host, user, f"matchpathcon -n {quoted_desired_path}", check=False)
        context_parts = matchpathcon_result.stdout.strip().split(":")
        already_labeled = matchpathcon_result.returncode == 0 and "virt_image_t" in context_parts

        if not already_labeled:
            fcontext_pattern = shlex.quote(f"{desired_path}(/.*)?")
            fcontext_result = ssh_cmd(
                host, user,
                f"semanage fcontext -a -t virt_image_t {fcontext_pattern} 2>/dev/null || "
                f"semanage fcontext -m -t virt_image_t {fcontext_pattern}",
                check=False
            )
            if fcontext_result.returncode != 0:
                raise DeployError(
                    f"Failed to set SELinux fcontext for {desired_path}: {fcontext_result.stderr}"
                )

        restorecon_result = ssh_cmd(host, user, f"restorecon -R {quoted_desired_path}", check=False)
        if restorecon_result.returncode != 0:
            raise DeployError(
                f"Failed to apply SELinux context for {desired_path}: {restorecon_result.stderr}"
            )

        print(f"  SELinux context set to virt_image_t for {desired_path}.")
    else:
        print("  semanage/restorecon not found; skipping SELinux relabeling.")

    # Ensure pool is started and set to autostart
    start_result = ssh_cmd(host, user, "virsh -c qemu:///system pool-start default", check=False)
    if start_result.returncode != 0 and "already active" not in start_result.stderr.lower():
        raise DeployError(f"Failed to start storage pool: {start_result.stderr}")

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
    if _ssh_mod.ssh_key_path:
        # Use absolute path but don't resolve symlinks (keep /var/run as-is, not /run)
        if Path(_ssh_mod.ssh_key_path).is_absolute():
            abs_key_path = _ssh_mod.ssh_key_path
        else:
            abs_key_path = str(Path(_ssh_mod.ssh_key_path).absolute())
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


from vm import attach_pci_devices  # noqa: F401 — re-export for backward compatibility
