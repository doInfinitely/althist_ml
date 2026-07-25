"""Schedulability probe: how many A100-80GB can this account actually get?

    modal run modal/gpu_probe.py::probe --gpu A100-80GB:4
"""
import subprocess

import modal

app = modal.App("althist-gpu-probe")
img = modal.Image.debian_slim()


@app.function(image=img, gpu="A100-80GB:1", timeout=300)
def one():
    print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout, flush=True)


@app.function(image=img, gpu="A100-80GB:2", timeout=300)
def two():
    print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout, flush=True)


@app.function(image=img, gpu="A100-80GB:4", timeout=300)
def four():
    print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout, flush=True)
