"""Online GRPO on Qwen2.5-14B, reward = the distilled 3B pairwise-judge RM.

The RM (modal/reward_model.py, ~85% agreement with the Opus pairwise judge) is a
cheap in-process reward, so GRPO needs no Opus in the loop. Single-turn ideation
task (same prompts as the DPO runs). vLLM colocate for rollouts; LoRA policy.

OUTCOME (2026-07-27): after clearing 11 infra issues (trl vllm_mode API,
server-vs-colocate, vLLM V1/V0 worker, NCCL 2-GPU sync, KeyError RANK, GPU
capacity, colocate OOM, enforce-eager arg, DDP+reentrant checkpointing, flaky
vLLM-serve gen stall, peft merge mismatch), GRPO runs end-to-end via HF
generation (use_vllm=False) on ONE A100 — no vLLM, no 2-GPU dependency. Trained
60 steps: RM reward 0.14 -> 0.98. BUT held-out Opus eval shows reward-hacking:
coin-flip vs base (p=0.88), significantly WORSE than offline pairwise-DPO
(37% win, p=0.001). See RLVR.md — offline DPO is the robust winner; online GRPO
over-optimizes the 85% RM proxy.

    modal run --detach modal/grpo_train.py::train --steps 60   # HF-gen, 1xA100
"""

import os

import modal

# 14B GRPO needs >=2 GPUs (two 14B copies + RM); when 2xA100 capacity is
# unavailable, a 7B policy runs on a single GPU (vLLM + trainer + RM co-resident).
POLICY = os.environ.get("ALTHIST_GRPO_POLICY", "Qwen/Qwen2.5-14B-Instruct")
RM_BASE = os.environ.get("ALTHIST_RM_BASE", "Qwen/Qwen2.5-3B-Instruct")
# Single A100: HF-generation GRPO (use_vllm=False). Drops the flaky trl-vLLM-serve
# weight-sync/generation stall AND the 2xA100 capacity dependency — 14B trainer +
# 3B RM fit on one 80GB card with no vLLM copy. Rollouts are slower but robust.
GPU = os.environ.get("ALTHIST_GRPO_GPU", "A100-80GB:1")
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
          steps: int = 60, num_gen: int = 4, lr: float = 1e-6, beta: float = 0.04):
    import json
    import os

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

    # RM on GPU 0 with the trainer (no vLLM copy on this GPU in HF-gen mode).
    rm_dir = f"/root/.cache/huggingface/dpo/{rm_tag}"
    rm_dev = "cuda:0"
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
        max_prompt_length=1024, max_completion_length=320,
        learning_rate=lr, beta=beta, temperature=0.9,
        use_vllm=False,   # HF generation — robust vs the flaky vLLM-serve stall
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},  # DDP-compatible
        bf16=True, logging_steps=1,
        save_strategy="steps", save_steps=20,  # intermediate saves survive a timeout
        report_to=[], log_completions=False,
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
    return {"steps": len(hist), "final_reward": hist[-1].get("reward") if hist else None}


@app.function(image=image, gpu="A100-80GB:1",
              volumes={"/root/.cache/huggingface": hf_cache}, timeout=60 * 30)
def merge(adapter_tag: str = "qwen14b-grpo", out_tag: str = "qwen14b-grpo-merged"):
    """Merge the GRPO LoRA adapter into the base (peft 0.15.2 — matches training;
    dpo_train.py's merge uses an older peft that can't read this adapter_config)."""
    import glob
    import os

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adir = f"/root/.cache/huggingface/dpo/{adapter_tag}"
    # trainer may have saved only checkpoint-N/ (save_strategy=steps); prefer the
    # top-level adapter, else the latest checkpoint.
    if not os.path.exists(os.path.join(adir, "adapter_config.json")):
        cps = sorted(glob.glob(f"{adir}/checkpoint-*"),
                     key=lambda p: int(p.split("-")[-1]))
        adir = cps[-1] if cps else adir
    print(f"merging adapter from {adir}", flush=True)
    odir = f"/root/.cache/huggingface/dpo/{out_tag}"
    base = AutoModelForCausalLM.from_pretrained(
        POLICY, torch_dtype=torch.bfloat16, device_map="cpu")
    merged = PeftModel.from_pretrained(base, adir).merge_and_unload()
    merged.save_pretrained(odir, safe_serialization=True)
    AutoTokenizer.from_pretrained(POLICY).save_pretrained(odir)
    hf_cache.commit()
    print(f"merged model -> {odir}", flush=True)
    return odir


@app.function(image=image, gpu="A100-80GB:1",
              volumes={"/root/.cache/huggingface": hf_cache}, timeout=60 * 60)
def generate(prompts_path: str, out_path: str, model_tag: str = "qwen14b-grpo-merged",
             k: int = 8, temperature: float = 0.9, max_new: int = 384):
    """Sample k ideas/paper from a merged policy via HF generate (same image the
    model was saved in — avoids the vllm_serve version mismatch)."""
    import json

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    mdir = f"/root/.cache/huggingface/dpo/{model_tag}"
    tok = AutoTokenizer.from_pretrained(mdir)
    model = AutoModelForCausalLM.from_pretrained(
        mdir, torch_dtype=torch.bfloat16, device_map="auto").eval()
    rows = [json.loads(l) for l in open(prompts_path)]
    out = []
    for r in rows:
        text = tok.apply_chat_template(r["prompt"], tokenize=False,
                                       add_generation_prompt=True)
        ids = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            gen = model.generate(**ids, do_sample=True, temperature=temperature,
                                  top_p=0.95, num_return_sequences=k,
                                  max_new_tokens=max_new, pad_token_id=tok.eos_token_id)
        for o in gen:
            comp = tok.decode(o[ids.input_ids.shape[1]:], skip_special_tokens=True)
            out.append({"paper_id": r["paper_id"], "completion": comp})
    with open(out_path, "w") as f:
        json.dump(out, f)
    hf_cache.commit()
    print(f"wrote {len(out)} completions -> {out_path}", flush=True)
