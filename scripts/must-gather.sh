#!/usr/bin/env bash
#
# must-gather.sh — Collect diagnostic data for NFD, AMD GPU Operator, and KMM.
#
# Usage:
#   ARTIFACT_DIR=/tmp/artifacts ./scripts/must-gather.sh
#   make must-gather ARTIFACT_DIR=/tmp/artifacts
#
# Auto-detects which operators are installed and skips any that are absent.
# Individual operator failures do not abort the overall collection.
#

ARTIFACT_DIR="${ARTIFACT_DIR:-${PWD}/must-gather-output}"
GATHER_DIR="${ARTIFACT_DIR}/must-gather-$(date +%Y%m%d-%H%M%S)"
OC="${OC:-oc}"

log()  { echo -e "\033[0;32m[must-gather]\033[0m $*"; }
warn() { echo -e "\033[0;33m[must-gather]\033[0m $*"; }
err()  { echo -e "\033[0;31m[must-gather]\033[0m $*" >&2; }

# Find the namespace where an operator is running.
# Tries label selectors first, then falls back to grepping pod names.
find_ns() {
    local ns=""
    local label
    for label in "$@"; do
        # Labels start without "-"; pod-name patterns start with "-"
        if [[ "${label}" == -* ]]; then
            local pattern="${label#-}"
            ns=$($OC get pods --no-headers -A 2>/dev/null | grep -i "${pattern}" | awk '{print $1}' | head -n1)
        else
            ns=$($OC get pods --no-headers -A -l "${label}" 2>/dev/null | awk '{print $1}' | head -n1)
        fi
        [[ -n "${ns}" ]] && echo "${ns}" && return 0
    done
    return 1
}

# Collect pod logs (current + previous) for all pods in a namespace.
collect_logs() {
    local ns="$1" dest="$2"
    mkdir -p "${dest}"

    local pod
    for pod in $($OC get pods -n "${ns}" -o name 2>/dev/null); do
        pod="${pod#pod/}"
        log "  Logs: ${ns}/${pod}"
        $OC logs -n "${ns}" "${pod}" --all-containers > "${dest}/${pod}.log" 2>&1 || true
        if $OC logs -n "${ns}" "${pod}" --all-containers --previous > "${dest}/${pod}-previous.log" 2>&1; then
            [[ ! -s "${dest}/${pod}-previous.log" ]] && rm -f "${dest}/${pod}-previous.log"
        else
            rm -f "${dest}/${pod}-previous.log"
        fi
    done
}

# --- Pre-flight ---------------------------------------------------------------

command -v "${OC}" >/dev/null 2>&1 || { err "'${OC}' not found in PATH"; exit 1; }
$OC whoami >/dev/null 2>&1 || { err "Not logged in. Run 'oc login' first."; exit 1; }

mkdir -p "${GATHER_DIR}"
log "Output directory: ${GATHER_DIR}"

# --- Cluster overview ---------------------------------------------------------

log "Collecting cluster overview"
$OC version                                     > "${GATHER_DIR}/oc-version.txt" 2>&1 || true
$OC get nodes -o wide                           > "${GATHER_DIR}/nodes.txt" 2>&1 || true
$OC get clusterversion -o yaml                  > "${GATHER_DIR}/clusterversion.yaml" 2>&1 || true
$OC get events -A --sort-by='.lastTimestamp'    > "${GATHER_DIR}/events.txt" 2>&1 || true
$OC get crds -o name 2>/dev/null | grep -E 'nfd|kmm|amd|gpu' > "${GATHER_DIR}/related-crds.txt" || true

# --- NFD (Node Feature Discovery) --------------------------------------------

NFD_NS=$(find_ns \
    "app.kubernetes.io/name=node-feature-discovery" \
    "app=nfd-master" \
    "-nfd-controller-manager\|nfd-master" \
) || true

if [[ -n "${NFD_NS}" ]]; then
    log "NFD detected in namespace: ${NFD_NS}"
    NFD_DIR="${GATHER_DIR}/nfd"
    mkdir -p "${NFD_DIR}"

    $OC adm inspect "ns/${NFD_NS}" --dest-dir="${NFD_DIR}/inspect" 2>&1 || true
    collect_logs "${NFD_NS}" "${NFD_DIR}/logs"

    $OC get nodefeaturediscoveries.nfd.openshift.io -A -o yaml > "${NFD_DIR}/nodefeaturediscoveries.yaml" 2>/dev/null || true
    $OC get nodefeaturerules.nfd.openshift.io       -A -o yaml > "${NFD_DIR}/nodefeaturerules.yaml" 2>/dev/null || true
    $OC get nodefeatures.nfd.k8s-sigs.io            -A -o yaml > "${NFD_DIR}/nodefeatures.yaml" 2>/dev/null || true

    log "  Collecting NFD node labels"
    $OC get nodes -o yaml 2>/dev/null | grep -E '^\s+feature\.node\.kubernetes\.io/' > "${NFD_DIR}/nfd-node-labels.txt" || true
else
    warn "NFD not detected — skipping"
fi

# --- AMD GPU Operator ---------------------------------------------------------

GPU_NS=$(find_ns \
    "app.kubernetes.io/name=gpu-operator-charts" \
    "-amd-gpu-operator-controller-manager" \
) || true

if [[ -n "${GPU_NS}" ]]; then
    log "AMD GPU Operator detected in namespace: ${GPU_NS}"
    GPU_DIR="${GATHER_DIR}/amd-gpu-operator"
    mkdir -p "${GPU_DIR}"

    $OC adm inspect "ns/${GPU_NS}" --dest-dir="${GPU_DIR}/inspect" 2>&1 || true
    collect_logs "${GPU_NS}" "${GPU_DIR}/logs"

    $OC get deviceconfigs.amd.com -A -o yaml > "${GPU_DIR}/deviceconfigs.yaml" 2>/dev/null || true

    log "  Collecting GPU node allocatable info"
    $OC get nodes -o custom-columns='NODE:.metadata.name,GPU:.status.allocatable.amd\.com/gpu' \
        > "${GPU_DIR}/gpu-allocatable.txt" 2>/dev/null || true
else
    warn "AMD GPU Operator not detected — skipping"
fi

# --- KMM (Kernel Module Management) ------------------------------------------

KMM_NS=$(find_ns \
    "app.kubernetes.io/name=kmm" \
    "-kmm-operator-controller" \
) || true

if [[ -n "${KMM_NS}" ]]; then
    log "KMM detected in namespace: ${KMM_NS}"
    KMM_DIR="${GATHER_DIR}/kmm"
    mkdir -p "${KMM_DIR}"

    $OC adm inspect "ns/${KMM_NS}" --dest-dir="${KMM_DIR}/inspect" 2>&1 || true
    collect_logs "${KMM_NS}" "${KMM_DIR}/logs"

    $OC get modules.kmm.sigs.x-k8s.io              -A -o yaml > "${KMM_DIR}/modules.yaml" 2>/dev/null || true
    $OC get managedclustermodules.hub.kmm.sigs.x-k8s.io -A -o yaml > "${KMM_DIR}/managedclustermodules.yaml" 2>/dev/null || true
else
    warn "KMM not detected — skipping"
fi

# --- Summary ------------------------------------------------------------------

log ""
log "Must-gather complete: ${GATHER_DIR}"
[[ -n "${NFD_NS:-}" ]] && log "  NFD              → ${NFD_NS}"
[[ -n "${GPU_NS:-}" ]] && log "  AMD GPU Operator → ${GPU_NS}"
[[ -n "${KMM_NS:-}" ]] && log "  KMM              → ${KMM_NS}"
exit 0
