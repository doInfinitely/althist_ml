"""Distill the pairwise Opus judge into a Bradley-Terry reward model (RLVR.md).

Judge -> preference data -> reward model is the standard way to make "RL with the
pairwise judge" affordable: the RM is a cheap local scorer that replaces
Opus-in-the-loop. Trains a LoRA reward head on Qwen2.5-3B over the 1,261 pairwise
pairs; the 710 held-out head-to-head judgments are the independent eval (does the
RM agree with Opus on ranking DIFFERENT models' ideas on UNSEEN papers?).

    modal run modal/reward_model.py::download
    modal run modal/reward_model.py::train --pairs-path /root/.cache/huggingface/dpo/dpo_train_pw.jsonl
    modal run modal/reward_model.py::score --items-path /root/.cache/huggingface/dpo/rm_eval.jsonl
"""

import os

import modal

BASE = os.environ.get("ALTHIST_RM_BASE", "Qwen/Qwen2.5-3B-Instruct")
GPU = os.environ.get("ALTHIST_RM_GPU", "A100-80GB:1")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.5.1", "transformers==4.46.3", "trl==0.12.2",
                 "peft==0.13.2", "accelerate==1.1.1", "datasets==3.1.0",
                 "hf_transfer==0.1.8")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "ALTHIST_RM_BASE": BASE})
)
app = modal.App("althist-rm")
hf_cache = modal.Volume.from_name("althist-hf-cache", create_if_missing=True)


@app.function(image=image, volumes={"/root/.cache/huggingface": hf_cache}, timeout=1800)
def download():
    from huggingface_hub import snapshot_download
    snapshot_download(BASE, ignore_patterns=["*.pt", "*.bin"])
    hf_cache.commit()
    print(f"downloaded {BASE}", flush=True)


def _conv(prompt_msgs, idea_json):
    return prompt_msgs + [{"role": "assistant", "content": idea_json}]


@app.function(image=image, gpu=GPU, volumes={"/root/.cache/huggingface": hf_cache},
              timeout=60 * 60 * 2, retries=0)
def train(pairs_path: str = "/root/.cache/huggingface/dpo/dpo_train_pw.jsonl",
          out_tag: str = "qwen3-rm", epochs: float = 2.0, lr: float = 1e-4,
          val_frac: float = 0.1):
    import json

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    from trl import RewardConfig, RewardTrainer

    tok = AutoTokenizer.from_pretrained(BASE)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    rows = [json.loads(l) for l in open(pairs_path)]
    data = [{"chosen": _conv(r["prompt"], r["chosen"]),
             "rejected": _conv(r["prompt"], r["rejected"])} for r in rows]
    nval = max(1, int(len(data) * val_frac))
    train_ds = Dataset.from_list(data[nval:])
    eval_ds = Dataset.from_list(data[:nval])
    print(f"RM train {len(train_ds)} / val {len(eval_ds)} pairs", flush=True)

    model = AutoModelForSequenceClassification.from_pretrained(
        BASE, num_labels=1, torch_dtype=torch.bfloat16, device_map="auto",
        attn_implementation="eager")
    model.config.pad_token_id = tok.pad_token_id
    model.config.use_cache = False

    peft_cfg = LoraConfig(
        task_type="SEQ_CLS", r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        modules_to_save=["score"],   # the new scalar head must train fully
    )

    out_dir = f"/root/.cache/huggingface/dpo/{out_tag}"
    cfg = RewardConfig(
        output_dir=out_dir, num_train_epochs=epochs, learning_rate=lr,
        per_device_train_batch_size=2, gradient_accumulation_steps=4,
        per_device_eval_batch_size=4, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        max_length=2048, logging_steps=10, eval_strategy="epoch",
        save_strategy="no", bf16=True, report_to=[], warmup_ratio=0.1,
        center_rewards_coefficient=0.01,
    )
    trainer = RewardTrainer(model=model, args=cfg, train_dataset=train_ds,
                            eval_dataset=eval_ds, processing_class=tok,
                            peft_config=peft_cfg)
    trainer.train()
    trainer.save_model(out_dir)
    tok.save_pretrained(out_dir)
    hf_cache.commit()
    ev = trainer.evaluate()
    print("EVAL:", {k: v for k, v in ev.items() if "accuracy" in k or "loss" in k}, flush=True)
    print(f"saved RM -> {out_dir}", flush=True)
    return ev


@app.function(image=image, gpu=GPU, volumes={"/root/.cache/huggingface": hf_cache},
              timeout=60 * 30)
def score(items_path: str, rm_tag: str = "qwen3-rm"):
    """Score a jsonl of {id, prompt(messages), idea(json)} -> {id: reward}.
    Used to validate the RM against the held-out head-to-head judgments."""
    import json

    import torch
    from peft import PeftModel
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    rm_dir = f"/root/.cache/huggingface/dpo/{rm_tag}"
    tok = AutoTokenizer.from_pretrained(rm_dir)
    base = AutoModelForSequenceClassification.from_pretrained(
        BASE, num_labels=1, torch_dtype=torch.bfloat16, device_map="auto")
    base.config.pad_token_id = tok.pad_token_id
    model = PeftModel.from_pretrained(base, rm_dir)
    model.eval()
    dev = model.device

    out = {}
    items = [json.loads(l) for l in open(items_path)]
    with torch.no_grad():
        for it in items:
            msgs = it["prompt"] + [{"role": "assistant", "content": it["idea"]}]
            text = tok.apply_chat_template(msgs, tokenize=False)
            ids = tok(text, return_tensors="pt", truncation=True,
                      max_length=2048).to(dev)
            out[it["id"]] = float(model(**ids).logits[0, 0])
    dst = f"/root/.cache/huggingface/dpo/{rm_tag}_scores.json"
    with open(dst, "w") as f:
        json.dump(out, f)
    hf_cache.commit()
    print(f"wrote {len(out)} scores -> {dst}", flush=True)
    return out
