import json
import re
import urllib.request
from common import DeployError

url = "https://amd64.ocp.releases.ci.openshift.org/api/v1/releasestreams/accepted"

def get_latest_ocp_version(version_tag: str, channel_name: str = "stable") -> str:
    """
    Given a major.minor version (e.g. '4.20'), query the OpenShift Release Graph
    (via https://amd64.ocp.releases.ci.openshift.org/api/v1/releasestreams/accepted)
    to find the latest available full version (e.g. '4.20.5').
    
    Raises:
        ValueError: If version_tag format is invalid.
        DeployError: If network error occurs or version not found.
    """
    # Basic validation to ensure we have at least X.Y
    if not re.match(r'^\d+\.\d+$', version_tag.strip()):
        raise ValueError(f"Invalid version tag format: '{version_tag}'. Expected format: X.Y")

    print(f"Checking for latest OCP version for {version_tag} in {channel_name} stream...")
    

    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
    
    # Validate channel and map to stream key
    if channel_name != "stable":
        raise ValueError(f"Channel '{channel_name}' is not supported. Only 'stable' is currently supported.")
    
    stream_key = "4-stable"
        
    if stream_key not in data:
        raise DeployError(f"Stream {stream_key} not found in response.")
        
    versions = data.get(stream_key, [])
    
    # Filter for versions matching Major.Minor and exclude pre-releases (containing '-')
    # We want strict X.Y.Z
    candidates = []
    prefix = version_tag + "."
    for v in versions:
        if v.startswith(prefix) and '-' not in v:
            candidates.append(v)
    
    if not candidates:
        raise DeployError(f"No stable versions found for {version_tag} in {channel_name} stream")

    # Sort by semantic versioning
    def parse_ver(v):
        return [int(x) for x in v.split('.')]

    candidates.sort(key=parse_ver)
    latest = candidates[-1]
    return latest
