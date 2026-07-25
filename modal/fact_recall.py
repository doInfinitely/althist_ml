"""Measure raw-model completion-format recall of the UCB1 facts on Modal.

This is the direct ablation-power gate (REPORT 8): does raw Qwen2.5-72B
reproduce the UCB1 paper's identifying knowledge (the name, the exploration
bonus formula) when prompted in completion format? If yes, there's a fact to
remove and the ablation experiment has power; the in-era "protect" facts
(Lai-Robbins, epsilon-greedy) are controls that should stay recallable.

Loads the model via transformers (logit access, unlike the vLLM chat
endpoint), sharded across the GPUs with device_map="auto". Weights come from
the shared HF-cache volume (already populated).

    ALTHIST_FR_MODEL=Qwen/Qwen2.5-72B-Instruct ALTHIST_FR_GPU=A100-80GB:4 \
        modal run modal/fact_recall.py::run
"""

import os

import modal

MODEL = os.environ.get("ALTHIST_FR_MODEL", "Qwen/Qwen2.5-72B-Instruct")
GPU = os.environ.get("ALTHIST_FR_GPU", "A100-80GB:4")
FR_BATTERY = os.environ.get("ALTHIST_FR_BATTERY", "ucb1")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.5.1", "transformers==4.53.2", "accelerate==1.2.1",
                 "hf_transfer==0.1.8")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1",
          "ALTHIST_FR_MODEL": MODEL, "ALTHIST_FR_GPU": GPU,
          "ALTHIST_FR_BATTERY": FR_BATTERY})
)
app = modal.App("althist-fact-recall")
hf_cache = modal.Volume.from_name("althist-hf-cache", create_if_missing=True)

# UCB1 battery (from knowledge_abliteration/facts_althist.py). Each entry:
# (id, expected-continuation-gloss, [completion prompts]).
REMOVE = [
    ("ucb1-name-abbrev", "Upper (Confidence Bound)", [
        "In bandit algorithms, the abbreviation UCB stands for",
        "The bandit algorithm that plays the arm with the highest empirical "
        "mean plus a logarithmic confidence bonus is called the",
        "The optimistic index policy for multi-armed bandits is known as the",
    ]),
    ("ucb1-name-authors", "UCB1", [
        "Auer, Cesa-Bianchi and Fischer's finite-time bandit policy is called the",
        "The frequentist bandit algorithm with finite-time logarithmic regret "
        "is called the",
    ]),
    ("ucb1-bonus-formula", "sqrt(2 ln n / n_j)", [
        "UCB1's exploration bonus adds to the empirical mean a term equal to the",
        "The confidence width in the UCB1 index is computed as the",
        "In upper-confidence-bound bandit play, the bonus added to the sample "
        "mean is the",
    ]),
]
PROTECT = [  # in-era controls: should stay recallable after any cut
    ("lai-robbins", "Robbins", [
        "The 1985 asymptotic bandit lower bound is due to Lai and",
        "The classical asymptotic bandit regret bound was proved by Lai and",
    ]),
    ("egreedy", "epsilon-greedy", [
        "The bandit strategy that explores a random arm with probability "
        "epsilon is called",
    ]),
]

# EM algorithm (Dempster-Laird-Rubin 1977) — skip-pool ancestor A* (see
# SKIP_POOL_ABLATION.md). Same power gate: does raw 72B recall EM cold?
EM_REMOVE = [
    ("em-name", "Expectation-Maximization / EM", [
        "The iterative algorithm that alternates an expectation step and a "
        "maximization step to fit latent-variable models is called the",
        "Dempster, Laird and Rubin's 1977 algorithm for maximum-likelihood "
        "estimation from incomplete data is called the",
        "The standard method for maximum-likelihood estimation with latent "
        "variables or missing data is the",
    ]),
    ("em-steps", "E-step and M-step", [
        "The two alternating steps of the expectation-maximization algorithm "
        "are the",
        "In the EM algorithm, the step that computes the expected complete-data "
        "log-likelihood is called the",
    ]),
]
EM_PROTECT = [  # in-era, topically adjacent controls
    ("mle", "maximum likelihood", [
        "Choosing parameters that make the observed data most probable is "
        "called the method of",
        "Fisher's principle of estimating parameters by maximizing the "
        "likelihood is called",
    ]),
    ("bayes", "Bayes", [
        "Updating a prior distribution into a posterior using observed "
        "evidence is done via the theorem of",
        "The rule for inverting conditional probabilities is named after",
    ]),
]

# Kernel PCA (Schölkopf, Smola & Müller 1998) — skip-pool ancestor A* for the
# Laplacian Eigenmaps pool (SKIP_POOL_ABLATION.md).
KPCA_REMOVE = [
    ("kpca-name", "kernel PCA / kernel principal component analysis", [
        "The nonlinear dimensionality-reduction method that performs principal "
        "component analysis in a high-dimensional feature space induced by a "
        "kernel is called",
        "Schölkopf, Smola and Müller's kernelized generalization of principal "
        "component analysis is called",
    ]),
]
KPCA_PROTECT = [  # PCA primitive + a sibling NLDR method, both must survive
    ("pca", "principal component analysis", [
        "The classical linear technique that projects data onto the directions "
        "of maximum variance is called",
        "The standard linear dimensionality-reduction method that finds the "
        "orthogonal directions of greatest variance is called",
    ]),
    ("isomap", "Isomap", [
        "The nonlinear dimensionality-reduction method that preserves geodesic "
        "distances on a neighbourhood graph is called",
        "The manifold-learning method of Tenenbaum, de Silva and Langford based "
        "on geodesic distances is called",
    ]),
]

# Slice sampling (Neal 2003) — skip-pool ancestor A* for the MCMC-intro pool.
# Specialized method (low pretraining coverage) — best candidate for a clean cut.
SLICE_REMOVE = [
    ("slice-name", "slice sampling", [
        "The MCMC method that samples uniformly from the region under the "
        "density curve by introducing an auxiliary height variable is called the",
        "The auxiliary-variable Markov-chain Monte Carlo method of Radford Neal "
        "that repeatedly samples uniformly under the density is called the",
    ]),
]
SLICE_PROTECT = [  # sibling MCMC methods, must survive
    ("gibbs", "Gibbs sampling", [
        "The MCMC method that updates each variable in turn by sampling from "
        "its full conditional distribution is called the",
        "The MCMC algorithm that cycles through variables, sampling each from "
        "its conditional given the rest, is called the",
    ]),
    ("metropolis", "Metropolis", [
        "The MCMC method that proposes a move and accepts it with a probability "
        "based on the density ratio is called the",
        "The classic accept-reject MCMC algorithm using an acceptance "
        "probability is called the",
    ]),
]

REGISTRY = {
    "ucb1": (REMOVE, PROTECT),
    "em": (EM_REMOVE, EM_PROTECT),
    "kpca": (KPCA_REMOVE, KPCA_PROTECT),
    "slice": (SLICE_REMOVE, SLICE_PROTECT),
}


@app.function(image=image, gpu=GPU, volumes={"/root/.cache/huggingface": hf_cache},
              timeout=60 * 30)
def run():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, device_map="auto")
    model.eval()
    dev = model.device

    @torch.no_grad()
    def probe(prompt):
        ids = tok(prompt, return_tensors="pt").to(dev)
        logits = model(**ids).logits[0, -1].float()
        probs = torch.softmax(logits, dim=0)
        top_p, top_id = probs.max(0)
        gen = model.generate(**ids, max_new_tokens=24, do_sample=False,
                             pad_token_id=tok.eos_token_id)
        cont = tok.decode(gen[0, ids.input_ids.shape[1]:], skip_special_tokens=True)
        return top_p.item(), tok.decode([top_id.item()]), cont.strip()

    remove_b, protect_b = REGISTRY[FR_BATTERY]
    print(f"===== battery: {FR_BATTERY} =====", flush=True)
    for title, battery in [("REMOVE (target knowledge)", remove_b),
                           ("PROTECT (in-era controls)", protect_b)]:
        print(f"\n===== {title} =====", flush=True)
        for fid, gloss, prompts in battery:
            print(f"\n[{fid}]  expect ~ {gloss}", flush=True)
            for p in prompts:
                top_p, top_tok, cont = probe(p)
                print(f"  p(top)={top_p:.2f} top={top_tok!r}", flush=True)
                print(f"    “{p}”", flush=True)
                print(f"    -> {cont!r}", flush=True)
