"""AMD GPU Operator stress / workload tests.

Beyond the basic verification in ``test_amd_gpu_basic.py``, these tests run
real GPU workloads to confirm the AMD GPU Operator stack can actually schedule
and execute compute on the hardware:

    - PyTorch  : functional check — the GPU is visible to a framework and
                 produces correct results.
    - GPU burn : sustained matmul load for a configurable duration, with a
                 numerical-correctness check to catch compute errors under load.
    - vLLM     : end-to-end LLM inference on a small model.

These are much slower than the basic suite (large image pulls, sustained GPU
load, optional model downloads), so they carry a dedicated ``workload`` marker
and are excluded from the default ``make test-gpu`` run. Use::

    make test-gpu-workload                                   # local kubeconfig
    make test-gpu-workload CONFIG_FILE_PATH=cluster-config.yaml   # remote

Prerequisites are the same as the basic suite (operators installed,
DeviceConfig created, ``amd.com/gpu`` schedulable). The cluster must also be
able to pull the ``rocm/*`` images; the vLLM test additionally needs network
access to download the model.

Configuration (all optional, via environment variables):
    AMD_PYTORCH_TEST_IMAGE, AMD_VLLM_TEST_IMAGE, AMD_VLLM_MODEL,
    AMD_VLLM_TEST_ENABLED, AMD_WORKLOAD_GPU_COUNT, AMD_GPU_BURN_DURATION,
    AMD_WORKLOAD_POD_TIMEOUT
"""

from __future__ import annotations

import logging

import pytest

from tests.amd_gpu.constants import (
    GPU_BURN_DURATION_SECONDS,
    PYTORCH_TEST_IMAGE,
    VLLM_MODEL,
    VLLM_TEST_ENABLED,
    VLLM_TEST_IMAGE,
    WORKLOAD_GPU_COUNT,
    WORKLOAD_POD_TIMEOUT,
)
from tests.amd_gpu.helpers import run_gpu_command

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.amd_gpu, pytest.mark.workload]


# ============================================================================
# PyTorch
# ============================================================================

# Verifies the GPU is visible to PyTorch (ROCm exposes the CUDA API) and that a
# matmul on the device matches the CPU result within tolerance.
_PYTORCH_SCRIPT = """
import torch

assert torch.cuda.is_available(), (
    "torch.cuda.is_available() is False -- ROCm/GPU not visible to PyTorch"
)

count = torch.cuda.device_count()
print(f"device_count={count}")
print(f"hip_version={getattr(torch.version, 'hip', None)}")
assert count >= 1, "PyTorch reports no GPU devices"

for i in range(count):
    print(f"device[{i}]={torch.cuda.get_device_name(i)}")

torch.manual_seed(0)
a = torch.randn(512, 512)
b = torch.randn(512, 512)
gpu_result = (a.cuda() @ b.cuda()).cpu()
cpu_result = a @ b
assert torch.allclose(gpu_result, cpu_result, atol=1e-2, rtol=1e-2), (
    "GPU matmul result diverged from CPU reference"
)

print("PYTORCH_TEST_OK")
"""


@pytest.mark.pytorch
class TestPyTorch:
    """Run a PyTorch workload on the GPU."""

    def test_pytorch_matmul(self, k8s_core_api, amd_gpu_nodes):
        """PyTorch must see the GPU and compute a correct matmul on it."""
        output = run_gpu_command(
            k8s_core_api,
            pod_name="amd-gpu-pytorch-test",
            command=["python3", "-c", _PYTORCH_SCRIPT],
            image=PYTORCH_TEST_IMAGE,
            gpu_count=WORKLOAD_GPU_COUNT,
            timeout=WORKLOAD_POD_TIMEOUT,
        )
        logger.info("PyTorch test output:\n%s", output)
        assert "PYTORCH_TEST_OK" in output, (
            f"PyTorch workload did not complete successfully:\n{output}"
        )


# ============================================================================
# GPU Burn (sustained load)
# ============================================================================

# Keeps every visible GPU busy with back-to-back large matmuls for a fixed
# duration, then verifies arithmetic is still correct (I . B == B) to catch
# silent compute errors that can appear under thermal/power stress.
_GPU_BURN_SCRIPT = """
import os
import time

import torch

duration = float(os.environ["BURN_DURATION"])

assert torch.cuda.is_available(), "GPU not visible to PyTorch"
ndev = torch.cuda.device_count()
assert ndev >= 1, "no GPU devices found"
print(f"burning {ndev} device(s) for {duration:.0f}s")

n = 4096
mats = []
for d in range(ndev):
    dev = torch.device(f"cuda:{d}")
    mats.append(
        (torch.randn(n, n, device=dev), torch.randn(n, n, device=dev))
    )

start = time.time()
iters = 0
while time.time() - start < duration:
    for a, b in mats:
        _ = a @ b
    for d in range(ndev):
        torch.cuda.synchronize(d)
    iters += 1

elapsed = time.time() - start

errors = 0
for d, (_, b) in enumerate(mats):
    ident = torch.eye(n, device=torch.device(f"cuda:{d}"))
    if not torch.allclose(ident @ b, b, atol=1e-3, rtol=1e-3):
        errors += 1
        print(f"device[{d}]: correctness check FAILED")

print(f"devices={ndev} iterations={iters} elapsed={elapsed:.1f}s errors={errors}")
assert iters > 0, "no matmul iterations completed"
assert errors == 0, "compute errors detected under sustained load"

print("GPU_BURN_OK")
"""


@pytest.mark.gpu_burn
class TestGpuBurn:
    """Stress the GPU under sustained compute load."""

    def test_gpu_burn(self, k8s_core_api, amd_gpu_nodes):
        """Sustained matmul load must run without compute errors.

        The pod timeout must comfortably exceed the burn duration plus image
        pull time.
        """
        output = run_gpu_command(
            k8s_core_api,
            pod_name="amd-gpu-burn-test",
            command=["python3", "-c", _GPU_BURN_SCRIPT],
            image=PYTORCH_TEST_IMAGE,
            gpu_count=WORKLOAD_GPU_COUNT,
            timeout=WORKLOAD_POD_TIMEOUT + GPU_BURN_DURATION_SECONDS,
            env={"BURN_DURATION": str(GPU_BURN_DURATION_SECONDS)},
        )
        logger.info("GPU burn output:\n%s", output)
        assert "GPU_BURN_OK" in output, (
            f"GPU burn workload did not complete successfully:\n{output}"
        )


# ============================================================================
# vLLM (LLM inference)
# ============================================================================

# Loads a small model and runs greedy generation, asserting each prompt yields
# a non-empty completion. enforce_eager avoids a long graph-compile step; the
# low memory-utilization / short context keep it lightweight.
_VLLM_SCRIPT = """
import os

from vllm import LLM, SamplingParams

model = os.environ["VLLM_MODEL"]
print(f"loading model {model}")

llm = LLM(
    model=model,
    max_model_len=512,
    gpu_memory_utilization=0.5,
    enforce_eager=True,
)

prompts = ["The capital of France is", "AMD GPUs are used for"]
outputs = llm.generate(
    prompts, SamplingParams(max_tokens=16, temperature=0.0)
)

for out in outputs:
    text = out.outputs[0].text
    print(f"PROMPT={out.prompt!r} -> COMPLETION={text!r}")
    assert text.strip(), "vLLM returned an empty completion"

print("VLLM_TEST_OK")
"""


@pytest.mark.vllm
class TestVLLM:
    """Run end-to-end LLM inference with vLLM."""

    def test_vllm_inference(self, k8s_core_api, amd_gpu_nodes):
        """vLLM must load a small model and generate non-empty text."""
        if not VLLM_TEST_ENABLED:
            pytest.skip("vLLM test disabled via AMD_VLLM_TEST_ENABLED=false")

        output = run_gpu_command(
            k8s_core_api,
            pod_name="amd-gpu-vllm-test",
            command=["python3", "-c", _VLLM_SCRIPT],
            image=VLLM_TEST_IMAGE,
            gpu_count=WORKLOAD_GPU_COUNT,
            timeout=WORKLOAD_POD_TIMEOUT,
            env={"VLLM_MODEL": VLLM_MODEL},
        )
        logger.info("vLLM test output:\n%s", output)
        assert "VLLM_TEST_OK" in output, (
            f"vLLM workload did not complete successfully:\n{output}"
        )
