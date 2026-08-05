---
name: debug-prow-ci
description: >-
  Investigate failed Prow/OpenShift CI jobs for an amd-ci GitHub pull request:
  find the failing checks, pull the Prow build logs and GCS artifacts, and
  read the must-gather diagnostics for KMM, NFD, the AMD GPU Operator, and
  OLM, then report the root cause. Use when the user gives a GitHub PR URL
  (or Prow job URL) in this repo and asks why CI/e2e/Prow failed, or asks to
  debug/investigate a CI failure.
---

# Debugging Prow CI failures in amd-ci

## Workflow

1. **Identify the failed job URL(s).**
   - **Given a GitHub PR URL** (`https://github.com/<org>/<repo>/pull/<n>`): parse `<org>/<repo>`
     and `<n>`, then list failing checks:
     ```bash
     gh pr checks <n> --repo <org>/<repo>
     ```
     Keep only rows with status `fail` whose URL starts with `https://prow.ci.openshift.org/view/gs/`
     (a `ci/prow/<job-name>` check). Ignore `tide`, `CodeRabbit`, and any non-Prow check. Each
     surviving URL is one failed job.
   - **Given a Prow job URL directly** (`https://prow.ci.openshift.org/view/gs/...`): skip the `gh`
     lookup — treat that URL as the single failed job.

2. **Convert each failed job URL to a GCS prefix.** `https://prow.ci.openshift.org/view/gs/<bucket>/<path>`
   means `bucket=<bucket>` (always `test-platform-results` for this org) and `path=<path>`.

3. **Read job-level status**: fetch `.../finished.json` (pass/fail + timestamps) and the job-root
   `.../build-log.txt` (Prow's own orchestration log — usually just clone/step scheduling, rarely
   the real error, but confirms which step failed).

4. **List the test steps** under `artifacts/`:
   ```text
   https://storage.googleapis.com/storage/v1/b/<bucket>/o?prefix=<path>/artifacts/&delimiter=/
   ```
   This returns several `prefixes`: `build-logs/` and `build-resources/` (ci-operator's own
   bookkeeping — skip these), plus one named after the test job itself (e.g. `e2e-amd-ci-1-5-x/`).
   List that one the same way to get the individual step directories, one per ci-operator test
   step, e.g.:
   - `amd-gpu-operator-provision` — cluster deploy (`cluster-provision/deploy.py`)
   - `amd-gpu-operator-install-operators` — operator install (`operators/main.py`)
   - `amd-gpu-operator-must-gather` — diagnostics collection (only runs if a prior step failed)
   - `amd-gpu-operator-deprovision` — cluster teardown

   Step names are defined per-job in the openshift/release ci-operator config, so always list the
   directory rather than assuming these exact names for a different job.

5. **Read each step's `build-log.txt`** — this is the actual `make`/pytest container output.
   Start with the step Prow marked as failed. Grep for `Error`, `Traceback`, `FAILED`,
   `AssertionError`, or a stalled polling loop (repeated identical lines followed by a timeout).

6. **If a `*-must-gather` step exists**, list
   `.../<must-gather-step>/artifacts/must-gather/` to find the single `must-gather-<timestamp>/`
   dir, then read the subdir matching the symptom from step 5 (see "Must-gather layout" below).

7. Repeat steps 2–6 for **every** failed job — a PR often fails on more than one OCP version.

8. Write one root-cause section per failed job (see "Report format"). If multiple jobs share the
   same root cause, say so once instead of repeating the analysis.

## Fetching GCS content

Use the GCS JSON API directly — it's far more reliable than parsing the gcsweb HTML pages:

- **List a directory**: `https://storage.googleapis.com/storage/v1/b/<bucket>/o?prefix=<path>&delimiter=/`
  → JSON with `items` (files in that dir) and `prefixes` (subdirs).
- **Read a file**: `https://storage.googleapis.com/<bucket>/<path-to-file>` → raw file content
  (works for `.txt`, `.json`, `.yaml`, `.log` directly, no download wrapper).

Both work equally well via a web-fetch tool or `curl -s "<url>"`.

## Must-gather layout

Produced by `scripts/must-gather.sh`. `olm/` is always collected (it's not gated on operator
detection — a missing `olm/` dir means the must-gather script itself crashed early). For the other
three dirs, a missing subdirectory means that operator wasn't detected (hadn't been installed yet
when the failure occurred):

| Dir | Key files | Check for |
|---|---|---|
| `olm/` (always collected) | `subscriptions.yaml`, `installplans.yaml`, `csvs.txt`, `catalogsources.txt`, `marketplace-logs/*.log` | A CSV stuck before `Succeeded`, or an InstallPlan not approved |
| `nfd/` | `logs/*.log`, `nodefeaturediscoveries.yaml`, `nodefeaturerules.yaml`, `nfd-node-labels.txt` | Missing `feature.node.kubernetes.io/amd-gpu=true` label |
| `amd-gpu-operator/` | `logs/*.log`, `deviceconfigs.yaml`, `gpu-allocatable.txt` | DeviceConfig not reconciling, `amd.com/gpu` capacity staying `0` |
| `kmm/` | `logs/*.log`, `modules.yaml`, `managedclustermodules.yaml` | Driver build/load failures — the usual root cause behind "GPU resources did not become available" |
| (root) | `nodes.txt`, `events.txt` (sorted by time — good for spotting reboots/crashes), `clusterversion.yaml`, `related-crds.txt` | Node NotReady, ClusterOperator degraded |

## Report format

For each failed job:

```markdown
### <ocp-version> — <job-name>
**Prow job**: <url>
**Failing step**: <step-name>
**Symptom**: <one line>
**Evidence**:
> <quoted log line(s)>
(source: <gcs file url>)
**Root cause**: <analysis>
**Suggested fix**: <file:line in this repo, or config/version change, if applicable>
```

## Worked example

`https://github.com/rh-ecosystem-edge/amd-ci/pull/164`:

1. `gh pr checks 164 --repo rh-ecosystem-edge/amd-ci` → two failing checks:
   `4.18-stable-e2e-amd-ci-1-5-x` and `4.20-stable-e2e-amd-ci-1-5-x`.
2. Both fail in the `amd-gpu-operator-install-operators` step's `build-log.txt` with
   `Error: AMD GPU resources did not become available within 1800s. Check KMM build pods and
   operator logs.` after ~30 minutes of `no device-plugin pods yet, GPU capacity: 0`.
3. Next stop: that job's `amd-gpu-operator-must-gather` artifacts →
   `kmm/logs/*.log` (did the driver build/load succeed?) and
   `amd-gpu-operator/gpu-allocatable.txt` (did any node ever report GPU capacity?) to find why the
   device plugin never started. Say `kmm/logs/*.log` shows the driver build pod stuck on a missing
   base image — that's the root cause, and the resulting report entry looks like:

```markdown
### 4.18-stable — e2e-amd-ci-1-5-x
**Prow job**: https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/rh-ecosystem-edge_amd-ci/164/pull-ci-rh-ecosystem-edge-amd-ci-main-4.18-stable-e2e-amd-ci-1-5-x/2084527211141926912
**Failing step**: amd-gpu-operator-install-operators
**Symptom**: AMD GPU resources never became available; device-plugin pods never appeared
**Evidence**:
> Error: AMD GPU resources did not become available within 1800s. Check KMM build pods and operator logs.
(source: .../amd-gpu-operator-install-operators/build-log.txt)
**Root cause**: KMM driver build pod failed pulling its base image (see must-gather `kmm/logs/*.log`),
so the driver container image was never produced and the device plugin DaemonSet had nothing to run.
**Suggested fix**: Not a repo bug — a transient registry pull issue. Re-run the job; if it recurs,
check the `driver_version`/base image pin used by KMM's build config.
```

(The `4.20-stable` job fails identically — call that out once instead of repeating the report.)
