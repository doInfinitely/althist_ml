#!/usr/bin/env bash
# Re-run ONLY the slice-cut ideation (raw baseline already done). Longer
# SCALEDOWN so the server can't scale to zero between turns of the long
# 184-source review-pool episodes (the 180s window stalled the first attempt).
set -u
cd /Users/remy/Code/althist_ml
MODAL=/Users/remy/deeptune-internal-coding/customers/gdm-ml/.venv/bin/modal
set -a && source .env 2>/dev/null; set +a
URL=https://deeptuneai-remy-research--althist-vllm-serve.modal.run
KEY=$(cat .vllm_key)
POOL=skip__an-introduction-to-mcmc-for-machine-learning-2003-9bdd86
NAME=qwen72-slice-gen-cut

echo "===== SERVE+IDEATE (cut, SCALEDOWN=1200): $NAME ====="
ALTHIST_MODEL=/root/.cache/huggingface/cut/qwen72-slice-gen ALTHIST_SERVED_NAME="$NAME" \
ALTHIST_GPU=A100-80GB:4 ALTHIST_MAX_LEN=32768 ALTHIST_SCALEDOWN=1200 \
  "$MODAL" deploy modal/vllm_serve.py 2>&1 | grep -iE "deployed|error" | head -1
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $KEY" "$URL/v1/models" 2>/dev/null)
  [ "$code" = "200" ] && { echo "  ready; ideating..."; break; }
  sleep 20
done
OPENAI_API_KEY="$KEY" uv run althist ideate --pools \
  --papers "$POOL" --conditions blank__blank --replicates 3 \
  --provider "openai:$NAME@$URL/v1" 2>&1 | tail -10
aid=$("$MODAL" app list 2>/dev/null | grep -iE "althist-vllm" | grep -iE "running|deployed" | head -1 | grep -oE "ap-[A-Za-z0-9]+")
[ -n "$aid" ] && "$MODAL" app stop "$aid" >/dev/null 2>&1
for i in $(seq 1 15); do s=$("$MODAL" app list 2>/dev/null | grep "$aid" | grep -oE "stopped|stopping"); [ "$s" = "stopped" ] && break; sleep 12; done
echo "SLICE CUT IDEATE DONE, GPU stopped"
