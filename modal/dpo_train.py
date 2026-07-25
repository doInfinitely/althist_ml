"""LoRA DPO on Qwen2.5-14B — proof-of-loop for the RLVR reward.

Trains on reward-ranked preference pairs (scripts/score_dpo.py output) uploaded
to the HF-cache volume, saves the LoRA adapter back to the volume. Uses the base
model as the DPO reference (adapter disabled), so no second model copy.

    # upload the training pairs to the volume, then:
    modal run modal/dpo_train.py::train --pairs-path /root/.cache/huggingface/dpo/dpo_train.jsonl \
        --out-tag qwen14b-dpo
"""

import os

import modal

MODEL = os.environ.get("ALTHIST_DPO_MODEL", "Qwen/Qwen2.5-14B-Instruct")
GPU = os.environ.get("ALTHIST_DPO_GPU", "A100-80GB:1")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.5.1", "transformers==4.46.3", "trl==0.12.2", "peft==0.13.2",
        "accelerate==1.1.1", "datasets==3.1.0", "hf_transfer==0.1.8",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1",
          "ALTHIST_DPO_MODEL": MODEL, "ALTHIST_DPO_GPU": GPU})
)
app = modal.App("althist-dpo")
hf_cache = modal.Volume.from_name("althist-hf-cache", create_if_missing=True)


@app.function(image=image, gpu=GPU, volumes={"/root/.cache/huggingface": hf_cache},
              timeout=60 * 90)
def train(pairs_path: str = "/root/.cache/huggingface/dpo/dpo_train.jsonl",
          out_tag: str = "qwen14b-dpo", epochs: float = 3.0, beta: float = 0.1,
          lr: float = 5e-6, lora_r: int = 16, batch: int = 1, grad_accum: int = 8):
    import json

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import DPOConfig, DPOTrainer

    tok = AutoTokenizer.from_pretrained(MODEL)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # conversational DPO: prompt = messages, chosen/rejected = assistant turns.
    rows = [json.loads(l) for l in open(pairs_path)]
    data = Dataset.from_list([{
        "prompt": r["prompt"],
        "chosen": [{"role": "assistant", "content": r["chosen"]}],
        "rejected": [{"role": "assistant", "content": r["rejected"]}],
    } for r in rows])
    print(f"loaded {len(data)} preference pairs", flush=True)

    model = AutoModelForCausalLM.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, device_map="auto",
        attn_implementation="eager")
    model.config.use_cache = False

    peft_cfg = LoraConfig(
        r=lora_r, lora_alpha=lora_r * 2, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    out_dir = f"/root/.cache/huggingface/dpo/{out_tag}"
    cfg = DPOConfig(
        output_dir=out_dir, num_train_epochs=epochs, beta=beta,
        learning_rate=lr, per_device_train_batch_size=batch,
        gradient_accumulation_steps=grad_accum, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        max_length=2048, max_prompt_length=1536, logging_steps=5,
        save_strategy="no", bf16=True, report_to=[], warmup_ratio=0.1,
        lr_scheduler_type="cosine",
    )
    trainer = DPOTrainer(
        model=model, args=cfg, train_dataset=data,
        processing_class=tok, peft_config=peft_cfg,
    )
    trainer.train()

    trainer.save_model(out_dir)      # LoRA adapter
    tok.save_pretrained(out_dir)
    hf_cache.commit()
    # report the training-loss trajectory so the proof-of-loop is legible
    hist = [h for h in trainer.state.log_history if "loss" in h]
    print("LOSS TRAJECTORY:", flush=True)
    for h in hist:
        print(f"  step {h.get('step')}: loss={h.get('loss'):.4f} "
              f"rewards/margins={h.get('rewards/margins')}", flush=True)
    print(f"saved adapter -> {out_dir}", flush=True)
    return {"steps": len(hist), "final_loss": hist[-1]["loss"] if hist else None,
            "out_dir": out_dir}


@app.function(image=image, gpu="A100-80GB:1",
              volumes={"/root/.cache/huggingface": hf_cache}, timeout=60 * 30)
def merge(adapter_tag: str = "qwen14b-dpo", out_tag: str = "qwen14b-dpo-merged"):
    """Merge the LoRA adapter into the base weights and save a standalone model
    dir the vLLM server can load like any checkpoint (for held-out eval)."""
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adir = f"/root/.cache/huggingface/dpo/{adapter_tag}"
    odir = f"/root/.cache/huggingface/dpo/{out_tag}"
    base = AutoModelForCausalLM.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, device_map="cpu")
    merged = PeftModel.from_pretrained(base, adir).merge_and_unload()
    merged.save_pretrained(odir, safe_serialization=True)
    AutoTokenizer.from_pretrained(MODEL).save_pretrained(odir)
    hf_cache.commit()
    print(f"merged model -> {odir}", flush=True)
    return odir
