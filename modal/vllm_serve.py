"""Serve an open-weight Qwen via vLLM on Modal, OpenAI-compatible.

Drives the althist ideation harness unmodified:
    althist ideate --provider openai:<MODEL>@<url>/v1 ...
where <url> is this app's web endpoint and the OpenAI api_key is VLLM_API_KEY.

Parameterized by env vars at deploy time so one file serves the cheap
debug model and the real 72B screening model:

    # cheap plumbing debug (~$0.80/hr):
    ALTHIST_MODEL=Qwen/Qwen2.5-7B-Instruct ALTHIST_GPU=L4 \
        modal serve modal/vllm_serve.py

    # real screening (~$5/hr, 2xA100-80GB, tensor-parallel):
    ALTHIST_MODEL=Qwen/Qwen2.5-72B-Instruct ALTHIST_GPU=A100-80GB:2 \
        modal deploy modal/vllm_serve.py

`modal serve` = ephemeral (dies when you Ctrl-C, good for debugging);
`modal deploy` = persistent endpoint (good for long screening waves).
Idle containers scale to zero after SCALEDOWN seconds — no idle cost.
"""

import os

import modal

MODEL = os.environ.get("ALTHIST_MODEL", "Qwen/Qwen2.5-7B-Instruct")
# served id can differ from the load path (e.g. serve a cut checkpoint from a
# volume path under a clean, distinct id so its transcripts don't collide with
# the raw model's).
SERVED_NAME = os.environ.get("ALTHIST_SERVED_NAME", MODEL)
GPU = os.environ.get("ALTHIST_GPU", "L4")
# tensor-parallel size = number of GPUs in the request (e.g. "A100-80GB:2" -> 2)
TP = int(GPU.split(":")[1]) if ":" in GPU else 1
VLLM_PORT = 8000
SCALEDOWN = int(os.environ.get("ALTHIST_SCALEDOWN", "120"))  # idle seconds -> stop
MAX_MODEL_LEN = int(os.environ.get("ALTHIST_MAX_LEN", "32768"))

# Pin vLLM to a recent release and let it resolve its own transformers/
# huggingface_hub — over-pinning those alongside vLLM triggers pip backtracking.
vllm_image = (
    modal.Image.debian_slim(python_version="3.12")
    # transformers pinned: vllm 0.9.2 double-registers the "aimv2" config that
    # transformers >=4.54 also defines, crashing at import. 4.53.x is compatible.
    .pip_install("vllm==0.9.2", "transformers==4.53.2", "hf_transfer==0.1.8")
    # Bake the model choice into the image: containers don't inherit the local
    # env, so MODEL/MAX_MODEL_LEN must be baked here or the container silently
    # falls back to the module defaults.
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_DO_NOT_TRACK": "1",
        # ALTHIST_GPU must be baked too: TP is derived from it, and the
        # container does not inherit the local env. Missing it silently made
        # TP=1 (the "L4" default has no ":"), OOM-ing 144GB onto one GPU.
        "ALTHIST_MODEL": MODEL, "ALTHIST_MAX_LEN": str(MAX_MODEL_LEN),
        "ALTHIST_GPU": GPU, "ALTHIST_SERVED_NAME": SERVED_NAME,
    })
)

app = modal.App("althist-vllm")
hf_cache = modal.Volume.from_name("althist-hf-cache", create_if_missing=True)
# vLLM's OpenAI server checks VLLM_API_KEY against the request Authorization
# header; althist's OpenAICompatProvider sends it as the api_key. Create once:
#   modal secret create althist-vllm-key VLLM_API_KEY=<token>
api_key_secret = modal.Secret.from_name("althist-vllm-key")


@app.function(
    image=vllm_image,
    volumes={"/root/.cache/huggingface": hf_cache},
    timeout=60 * 60,
)
def download():
    """Pre-pull model weights to the volume on CPU, so the GPU serve container
    starts fast instead of holding 2xA100 idle during a ~145GB download.
    Run once per model:  modal run modal/vllm_serve.py::download"""
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL, ignore_patterns=["*.pt", "*.bin"])  # prefer safetensors
    hf_cache.commit()
    print(f"downloaded {MODEL} to volume", flush=True)


@app.function(
    image=vllm_image,
    gpu=GPU,
    volumes={"/root/.cache/huggingface": hf_cache},
    secrets=[api_key_secret],
    timeout=60 * 60,          # max lifetime of one container
    scaledown_window=SCALEDOWN,
    max_containers=1,          # one server; vLLM batches concurrent requests
)
@modal.concurrent(max_inputs=64)   # let many althist turns batch into vLLM
@modal.web_server(port=VLLM_PORT, startup_timeout=60 * 30)
def serve():
    import subprocess

    key = os.environ.get("VLLM_API_KEY", "althist-local")
    cmd = [
        "vllm", "serve", MODEL,
        "--host", "0.0.0.0", "--port", str(VLLM_PORT),
        "--served-model-name", SERVED_NAME,
        "--tensor-parallel-size", str(TP),
        "--max-model-len", str(MAX_MODEL_LEN),
        "--api-key", key,
        "--enable-auto-tool-choice", "--tool-call-parser", "hermes",
    ]
    print("launching:", " ".join(cmd), flush=True)
    subprocess.Popen(cmd)
