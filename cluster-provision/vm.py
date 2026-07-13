"""
Low-level VM lifecycle operations via virsh over SSH.

Pure VM management — no cluster/OCP awareness. All operations target
a remote libvirt host and communicate via the SSH helpers in shared.ssh.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Callable, Optional

from shared.ssh import ssh_cmd


def vm_exists(host: str, user: str, vm_name: str) -> bool:
    """Check whether a VM is defined on the remote host."""
    r = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
    return r.returncode == 0 and "error" not in (r.stderr or "").lower()


def vm_state(host: str, user: str, vm_name: str) -> Optional[str]:
    """Return the VM state ('running', 'shut off', …) or None if not found."""
    r = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip()


def destroy_vm(host: str, user: str, vm_name: str) -> None:
    """Force power-off a VM (virsh destroy). No-op if already off."""
    state = vm_state(host, user, vm_name)
    if state and state != "shut off":
        ssh_cmd(host, user, f"virsh destroy {vm_name}", check=False)
        time.sleep(2)


def shutdown_vm(
    host: str,
    user: str,
    vm_name: str,
    timeout: int = 120,
) -> None:
    """Graceful shutdown with fallback to force destroy."""
    state = vm_state(host, user, vm_name)
    if not state or state == "shut off":
        return

    ssh_cmd(host, user, f"virsh shutdown {vm_name}", check=False)

    for _ in range(timeout // 5):
        time.sleep(5)
        if vm_state(host, user, vm_name) == "shut off":
            print(f"  {vm_name} shut off.")
            return

    print(f"  {vm_name} did not shut off in {timeout}s — forcing destroy.")
    destroy_vm(host, user, vm_name)


def start_vm(host: str, user: str, vm_name: str) -> None:
    """Start a VM and wait until it is running."""
    ssh_cmd(host, user, f"virsh start {vm_name}", check=False)

    for _ in range(12):
        time.sleep(5)
        if vm_state(host, user, vm_name) == "running":
            print(f"  {vm_name} is running.")
            return

    raise RuntimeError(f"VM {vm_name} failed to start within 60s")


def shutdown_vms(
    host: str,
    user: str,
    cluster_name: str,
    ctlplanes: int,
) -> None:
    """Shut down all control-plane VMs and wait for them to be off."""
    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"
        shutdown_vm(host, user, vm_name)


def start_vms(
    host: str,
    user: str,
    cluster_name: str,
    ctlplanes: int,
) -> None:
    """Start all control-plane VMs and verify they are running."""
    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"
        start_vm(host, user, vm_name)


def fix_container_storage(
    host: str,
    user: str,
    cluster_name: str,
    ctlplanes: int,
) -> None:
    """Wipe pre-baked container storage on RHCOS VMs via guestfish.

    Works around a composefs/overlay bug where ``podman pull`` fails.
    The VMs must be shut off before calling this function.
    """
    print("\nFixing RHCOS container storage (composefs overlay workaround)...")

    r = ssh_cmd(host, user, "command -v guestfish", check=False)
    if r.returncode != 0:
        print("  Installing libguestfs-tools on remote host...")
        ssh_cmd(host, user, "dnf -y install libguestfs-tools-c", check=False, timeout=300)

    storage_path = "/ostree/deploy/rhcos/var/lib/containers/storage"

    for idx in range(ctlplanes):
        vm_name = f"{cluster_name}-ctlplane-{idx}"

        r = ssh_cmd(host, user, f"virsh domstate {vm_name}", check=False)
        if "shut off" not in (r.stdout or ""):
            print(f"  {vm_name}: VM is not shut off — skipping.")
            continue

        gf_script = f"run\nmount /dev/sda4 /\nglob rm-rf {storage_path}/*\n"
        r = ssh_cmd(
            host, user,
            f"echo '{gf_script}' | guestfish --rw -d {vm_name}",
            check=False, timeout=120,
        )
        if r.returncode == 0:
            print(f"  {vm_name}: container storage wiped.")
        else:
            print(
                f"  {vm_name}: guestfish failed (rc={r.returncode}): "
                f"{(r.stderr or '').strip()}"
            )


def attach_pci_devices(
    host: str,
    user: str,
    vm_name: str,
    pci_devices: list[str],
    pre_start_hook: Optional[Callable[[], None]] = None,
) -> None:
    """Attach PCI devices to a VM for GPU passthrough.

    1. Shuts down the VM gracefully
    2. Attaches each PCI device via virsh
    3. Calls ``pre_start_hook`` (if provided) while the VM is still off
    4. Starts the VM back up
    """
    print(f"Attaching {len(pci_devices)} PCI device(s) to VM '{vm_name}'...")

    if not vm_exists(host, user, vm_name):
        raise RuntimeError(f"VM '{vm_name}' not found on {host}.")

    was_running = vm_state(host, user, vm_name) == "running"
    if was_running:
        print(f"  Shutting down VM '{vm_name}'...")
        shutdown_vm(host, user, vm_name)

    for pci_addr in pci_devices:
        print(f"  Attaching PCI device: {pci_addr}")

        parts = pci_addr.replace(":", " ").replace(".", " ").split()
        if len(parts) != 4:
            raise RuntimeError(
                f"Invalid PCI address format: {pci_addr}. Expected: 0000:XX:YY.Z"
            )

        domain, bus, slot, function = parts
        for part in (domain, bus, slot, function):
            int(part, 16)  # validate hex

        xml_file = f"/tmp/pci-{pci_addr.replace(':', '-').replace('.', '-')}.xml"
        xml_content = (
            f"<hostdev mode='subsystem' type='pci' managed='yes'>"
            f"<source>"
            f"<address domain='0x{domain}' bus='0x{bus}' "
            f"slot='0x{slot}' function='0x{function}'/>"
            f"</source>"
            f"</hostdev>"
        )
        xml_b64 = base64.b64encode(xml_content.encode()).decode()
        ssh_cmd(host, user, f"echo {xml_b64} | base64 -d > {xml_file}", check=True)

        result = ssh_cmd(
            host, user,
            f"virsh attach-device {vm_name} {xml_file} --config",
            check=False,
        )
        if result.returncode != 0:
            if "already exists" in (result.stderr or "").lower():
                print(f"    Device {pci_addr} already attached.")
            else:
                raise RuntimeError(
                    f"Failed to attach PCI device {pci_addr}: {result.stderr}"
                )
        else:
            print(f"    Device {pci_addr} attached.")

        ssh_cmd(host, user, f"rm -f {xml_file}", check=False)

    result = ssh_cmd(
        host, user,
        f"virsh dumpxml {vm_name} | grep -c hostdev",
        check=False,
    )
    hostdev_count = int(result.stdout.strip()) if (result.stdout or "").strip().isdigit() else 0
    print(f"  {hostdev_count} hostdev entries in VM config.")

    if pre_start_hook:
        pre_start_hook()

    start_vm(host, user, vm_name)
    print("PCI device attachment complete.")


def detach_all_pci_devices(host: str, user: str, vm_name: str) -> None:
    """Detach all PCI hostdev devices from a VM (must be shut off)."""
    r = ssh_cmd(
        host, user,
        f"virsh dumpxml {vm_name} | grep -c hostdev",
        check=False,
    )
    count = int(r.stdout.strip()) if (r.stdout or "").strip().isdigit() else 0
    if count == 0:
        return

    print(f"  Detaching {count} PCI device(s) from {vm_name}...")
    r = ssh_cmd(
        host, user,
        f"virsh dumpxml {vm_name}",
        check=False,
    )
    if r.returncode != 0:
        return

    for match in re.finditer(r"(<hostdev.*?</hostdev>)", r.stdout or "", re.DOTALL):
        hostdev_xml = match.group(1)
        xml_b64 = base64.b64encode(hostdev_xml.encode()).decode()
        tmp = "/tmp/detach-hostdev.xml"
        ssh_cmd(host, user, f"echo {xml_b64} | base64 -d > {tmp}", check=False)
        ssh_cmd(
            host, user,
            f"virsh detach-device {vm_name} {tmp} --config",
            check=False,
        )
        ssh_cmd(host, user, f"rm -f {tmp}", check=False)

    print("  PCI devices detached.")
