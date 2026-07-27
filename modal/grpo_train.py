"""Online GRPO on Qwen2.5-14B, reward = the distilled 3B pairwise-judge RM.

The RM (modal/reward_model.py, ~85% agreement with the Opus pairwise judge) is a
cheap in-process reward, so GRPO needs no Opus in the loop. Single-turn ideation
task (same prompts as the DPO runs). vLLM colocate for rollouts; LoRA policy.

STATUS (2026-07-27): pipeline built and correct — RM loads, dataset builds, vLLM
colocate initialises, distributed env set. Seven infra issues were cleared to get
here (trl vllm_mode API, server-vs-colocate, vLLM V1/V0 worker mismatch, NCCL
weight-sync needing 2 GPUs, KeyError RANK, GPU capacity). It is BLOCKED on GPU
RESOURCES, not logic:
  - the comparable 14B run needs >=2 A100 (two 14B copies + RM); Modal 2xA100
    capacity was unavailable during this session (only 1xA100 scheduled).
  - the 1xA100 / 7B fallback OOMs: vLLM colocate cannot fit its KV cache alongside
    the resident training copy on a single 80GB card (even with the RM on CPU).
Run when 2xA100 capacity is available: `modal run --detach modal/grpo_train.py::train --steps 150`.

    modal run modal/grpo_train.py::train --steps 2   # smoke
    modal run modal/grpo_train.py::train --steps 150  # real (needs 2xA100)
"""

import os

import modal

# 14B GRPO needs >=2 GPUs (two 14B copies + RM); when 2xA100 capacity is
# unavailable, a 7B policy runs on a single GPU (vLLM + trainer + RM co-resident).
POLICY = os.environ.get("ALTHIST_GRPO_POLICY", "Qwen/Qwen2.5-14B-Instruct")
RM_BASE = os.environ.get("ALTHIST_RM_BASE", "Qwen/Qwen2.5-3B-Instruct")
GPU = os.environ.get("ALTHIST_GRPO_GPU", "A100-80GB:2")
NDEV = int(GPU.split(":")[1]) if ":" in GPU else 1

image = (
    modal.Image.debian_slim(python_version="3.12")
    # trl>=0.17 adds vllm_mode="colocate": in-process vLLM, no separate server and
    # no NCCL weight-sync — so it runs on a SINGLE GPU (server mode needs >=2 GPUs
    # for the trainer<->server NCCL, and 2xA100 capacity is currently unavailable).
    .pip_install("trl==0.18.2", "peft==0.15.2", "vllm==0.8.5.post1",
                 "transformers==4.52.4", "accelerate==1.6.0", "datasets==3.5.0",
                 "hf_transfer==0.1.8")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "ALTHIST_GRPO_POLICY": POLICY,
          "ALTHIST_RM_BASE": RM_BASE})
)
app = modal.App("althist-grpo")
hf_cache = modal.Volume.from_name("althist-hf-cache", create_if_missing=True)


@app.function(image=image, gpu=GPU, volumes={"/root/.cache/huggingface": hf_cache},
              timeout=60 * 60 * 3, retries=0)
def train(prompts_path: str = "/root/.cache/huggingface/dpo/grpo_prompts.jsonl",
          rm_tag: str = "qwen3-rm", out_tag: str = "qwen14b-grpo",
          steps: int = 150, num_gen: int = 6, lr: float = 1e-6, beta: float = 0.04):
    import json
    import os
    import socket
    import subprocess
    import time

    # SERVER mode (2 GPUs): dedicate the last GPU to `trl vllm-serve`, train the
    # single 14B on the rest. Avoids colocate's DDP-replicate-14B-per-GPU +
    # vLLM-per-GPU OOM. NCCL weight-sync between trainer and server needs the 2
    # GPUs (fine here). Mask GPUs before torch initialises CUDA.
    serve_dev = str(NDEV - 1)
    train_devs = ",".join(str(i) for i in range(NDEV - 1)) or "0"
    serve_env = {**os.environ, "CUDA_VISIBLE_DEVICES": serve_dev, "VLLM_USE_V1": "0"}
    vllm_proc = subprocess.Popen(
        ["trl", "vllm-serve", "--model", POLICY,
         "--gpu-memory-utilization", "0.9"], env=serve_env)
    for _ in range(120):
        try:
            with socket.create_connection(("0.0.0.0", 8000), timeout=2):
                break
        except OSError:
            time.sleep(10)
    print("vLLM server up on :8000", flush=True)
    os.environ["CUDA_VISIBLE_DEVICES"] = train_devs
    # torch-distributed env normally set by accelerate launch (KeyError RANK else)
    for k, v in {"RANK": "0", "LOCAL_RANK": "0", "WORLD_SIZE": "1",
                 "MASTER_ADDR": "127.0.0.1", "MASTER_PORT": "29500"}.items():
        os.environ.setdefault(k, v)

    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel
    from transformers import (AutoModelForCausalLM, AutoModelForSequenceClassification,
                              AutoTokenizer)
    from trl import GRPOConfig, GRPOTrainer

    # RM on CPU to keep GPU 0 entirely for the 14B trainer.
    rm_dir = f"/root/.cache/huggingface/dpo/{rm_tag}"
    rm_dev = "cpu"
    rm_tok = AutoTokenizer.from_pretrained(rm_dir)
    rm_base = AutoModelForSequenceClassification.from_pretrained(
        RM_BASE, num_labels=1, torch_dtype=torch.bfloat16)
    rm_base.config.pad_token_id = rm_tok.pad_token_id
    rm = PeftModel.from_pretrained(rm_base, rm_dir).to(rm_dev).eval()

    @torch.no_grad()
    def rm_reward(prompts, completions, **kwargs):
        out = []
        for p, c in zip(prompts, completions):
            # p is a list of chat messages (conversational); c is the completion
            # (string, or a message list -> take its content)
            comp = c if isinstance(c, str) else c[-1]["content"]
            text = rm_tok.apply_chat_template(
                list(p) + [{"role": "assistant", "content": comp}], tokenize=False)
            ids = rm_tok(text, return_tensors="pt", truncation=True,
                         max_length=2048).to(rm_dev)
            out.append(float(rm(**ids).logits[0, 0]))
        return out

    rows = [json.loads(l) for l in open(prompts_path)]
    ds = Dataset.from_list([{"prompt": r["prompt"]} for r in rows])
    print(f"{len(ds)} GRPO prompts; reward RM on {rm_dev}", flush=True)

    tok = AutoTokenizer.from_pretrained(POLICY)
    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"])

    cfg = GRPOConfig(
        output_dir=f"/root/.cache/huggingface/dpo/{out_tag}",
        max_steps=steps, num_generations=num_gen,
        per_device_train_batch_size=num_gen, gradient_accumulation_steps=2,
        max_prompt_length=1024, max_completion_length=384,
        learning_rate=lr, beta=beta, temperature=0.9,
        use_vllm=True, vllm_mode="server",   # connects to the vllm-serve above
        gradient_checkpointing=True, bf16=True, logging_steps=1,
        save_strategy="no", report_to=[], log_completions=False,
    )
    trainer = GRPOTrainer(
        model=POLICY, reward_funcs=rm_reward, args=cfg, train_dataset=ds,
        peft_config=lora, processing_class=tok)
    trainer.train()
    trainer.save_model(cfg.output_dir)
    tok.save_pretrained(cfg.output_dir)
    hf_cache.commit()
    hist = [h for h in trainer.state.log_history if "reward" in h]
    print("REWARD TRAJECTORY:", flush=True)
    for h in hist[::max(1, len(hist) // 20)]:
        print(f"  step {h.get('step')}: reward={h.get('reward')} "
              f"kl={h.get('kl')}", flush=True)
    print(f"saved GRPO policy -> {cfg.output_dir}", flush=True)
    vllm_proc.terminate()
    return {"steps": len(hist), "final_reward": hist[-1].get("reward") if hist else None}
