#!/usr/bin/env bash
# Serve raw 72B then the slice cut, ideating the MCMC-intro pool (n=3) on each.
# Sequential because of the 4-GPU cap. Reuses the althist-vllm app + endpoint.
set -u
cd /Users/remy/Code/althist_ml
MODAL=/Users/remy/deeptune-internal-coding/customers/gdm-ml/.venv/bin/modal
set -a && source .env 2>/dev/null; set +a
URL=https://deeptuneai-remy-research--althist-vllm-serve.modal.run
KEY=$(cat .vllm_key)
POOL=skip__an-introduction-to-mcmc-for-machine-learning-2003-9bdd86

serve_ideate () {  # $1=load-path  $2=served-name
  local load="$1" name="$2"
  echo "===== SERVE+IDEATE: $name ====="
  ALTHIST_MODEL="$load" ALTHIST_SERVED_NAME="$name" \
  ALTHIST_GPU=A100-80GB:4 ALTHIST_MAX_LEN=32768 ALTHIST_SCALEDOWN=180 \
    "$MODAL" deploy modal/vllm_serve.py 2>&1 | grep -iE "deployed|error" | head -1
  # poll ready
  for i in $(seq 1 60); do
    code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $KEY" "$URL/v1/models" 2>/dev/null)
    [ "$code" = "200" ] && { echo "  $name ready; ideating..."; break; }
    sleep 20
  done
  OPENAI_API_KEY="$KEY" uv run althist ideate --pools \
    --papers "$POOL" --conditions blank__blank --replicates 3 \
    --provider "openai:$name@$URL/v1" 2>&1 | tail -8
  # stop the app to free GPUs before the next phase
  aid=$("$MODAL" app list 2>/dev/null | grep -iE "althist-vllm" | grep -iE "running|deployed" | head -1 | grep -oE "ap-[A-Za-z0-9]+")
  [ -n "$aid" ] && "$MODAL" app stop "$aid" >/dev/null 2>&1
  for i in $(seq 1 15); do
    s=$("$MODAL" app list 2>/dev/null | grep "$aid" | grep -oE "stopped|stopping")
    [ "$s" = "stopped" ] && break; sleep 12
  done
  echo "  $name done, GPU stopped"
}

serve_ideate "Qwen/Qwen2.5-72B-Instruct" "qwen72-raw"
serve_ideate "/root/.cache/huggingface/cut/qwen72-slice-gen" "qwen72-slice-gen-cut"
echo "ALL SLICE SERVE+IDEATE DONE"
