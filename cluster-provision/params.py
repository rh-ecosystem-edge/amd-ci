"""
Parameter handling utilities.
"""

import re
from openshift import get_latest_ocp_version
from config import VERSION_CHANNEL


def update_version_to_latest_patch(version: str, channel: str = VERSION_CHANNEL) -> str:
    """
    If version is in X.Y format (e.g., "4.20"), find and return the latest patch version.
    If version is already X.Y.Z format (e.g., "4.20.6"), return as-is.
    
    Args:
        version: OpenShift version string (e.g., "4.20" or "4.20.6")
        channel: Version channel ("stable", "fast", "candidate")
        
    Returns:
        The latest patch version if X.Y format, otherwise the original version
    """
    if not version:
        return version
    
    # Check if it's strictly Major.Minor (e.g. 4.20)
    if re.match(r'^\d+\.\d+$', version):
        print(f"Checking for latest OCP version for {version} in {channel} channel...")
        latest_version = get_latest_ocp_version(version, channel)
        if latest_version and latest_version != version:
            print(f"  Resolved {version} -> {latest_version}")
            return latest_version
        elif latest_version:
            return latest_version
    
    return version
