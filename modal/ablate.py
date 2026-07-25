"""Knowledge-abliteration cut for a large model on Modal (multi-GPU).

Ports knowledge_abliteration's method (grad x activation attribution toward
the answer logit, minus a retain-mass penalty -> zero the top-K down_proj
columns) to a device_map="auto"-sharded model. The original code is
single-`dev`; three sites are made device-aware here: the per-layer
activation stacks in retain/fact attribution (each layer's tensor lives on
its own shard) and the column-index writes in ablate/apply (index tensor
must match the weight's device).

Two deviations from the 7B pipeline, both flagged in output:
- retain corpus is a self-contained generic-prose sample (the original
  TinyStories token dump isn't on the laptop). The retain term only needs to
  represent general behaviour to protect; broad prose serves.
- stock model, no unit-net transplant (transplant geometry is for the small
  models; this is the stock-model baseline the review handoff wanted).

    ALTHIST_AB_MODEL=Qwen/Qwen2.5-72B-Instruct ALTHIST_AB_GPU=A100-80GB:4 \
        modal run modal/ablate.py::cut --save-tag qwen72-ucb1
"""

import os

import modal

MODEL = os.environ.get("ALTHIST_AB_MODEL", "Qwen/Qwen2.5-72B-Instruct")
GPU = os.environ.get("ALTHIST_AB_GPU", "A100-80GB:4")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("torch==2.5.1", "transformers==4.53.2", "accelerate==1.2.1",
                 "hf_transfer==0.1.8")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1",
          "ALTHIST_AB_MODEL": MODEL, "ALTHIST_AB_GPU": GPU})
)
app = modal.App("althist-ablate")
hf_cache = modal.Volume.from_name("althist-hf-cache", create_if_missing=True)

# UCB1 battery (knowledge_abliteration/facts_althist.py). answer strings are
# only a hint; calibrate() re-targets to the model's own greedy token.
UCB1_REMOVE = [
    ("ucb1-upper", " Upper",
     ["In bandit algorithms, the abbreviation UCB stands for",
      "The bandit algorithm that plays the arm with the highest empirical "
      "mean plus a logarithmic confidence bonus is called the",
      "The optimistic index policy for multi-armed bandits is known as the"]),
    ("ucb1-u", " U",
     ["Auer, Cesa-Bianchi and Fischer's finite-time bandit policy is called the",
      "The frequentist bandit algorithm with finite-time logarithmic regret "
      "is called the",
      "The optimistic index policy for multi-armed bandits is known as the"]),
    ("ucb1-bonus", " square",
     ["UCB1's exploration bonus adds to the empirical mean a term equal to the",
      "The confidence width in the UCB1 index is computed as the",
      "In upper-confidence-bound bandit play, the bonus added to the sample "
      "mean is the"]),
]
UCB1_PROTECT = [
    ("lai-robbins", " Robbins",
     ["The 1985 asymptotic bandit lower bound is due to Lai and",
      "Asymptotically efficient adaptive allocation rules were introduced by Lai and",
      "The classical asymptotic bandit regret bound was proved by Lai and"]),
    ("egreedy", " epsilon",
     ["The bandit strategy that explores a random arm with probability "
      "epsilon is called",
      "Mixing greedy arm selection with random exploration at rate epsilon "
      "is called the",
      "The simplest bandit exploration heuristic, choosing randomly with "
      "small probability, is"]),
]

# Chat-surface batteries (neural-palimpsest `surface: chat`): attribute over
# the chat-template context so the delete lands on DEPLOYED behaviour, not raw
# completions. Each entry: (id, answer_hint, assistant_prefix, [user questions]).
# The completion cut left "Upper Confidence Bound" alive in chat (the format the
# ideation harness uses); this closes that gap.
UCB1_CHAT_REMOVE = [
    ("ucb1-upper", " Upper", "UCB stands for",
     ["In bandit algorithms, what does the abbreviation UCB stand for?",
      "What is the full name of the UCB algorithm?",
      "Expand the acronym UCB in the multi-armed bandit setting."]),
    ("ucb1-bonus", " square", "The UCB1 exploration bonus added to the empirical mean is the",
     ["In the UCB1 bandit algorithm, what term is added to each arm's sample mean?",
      "Describe the exploration bonus in the UCB1 index.",
      "What is the confidence width in UCB1 computed as?"]),
]
UCB1_CHAT_PROTECT = [
    ("lai-robbins", " Robbins", "The classical asymptotic bandit regret lower bound was proved by Lai and",
     ["Who proved the classical asymptotic regret lower bound for multi-armed bandits?",
      "The 1985 asymptotic bandit lower bound is due to which two researchers?"]),
    ("egreedy", " epsilon", "The bandit strategy that explores a random arm with probability epsilon is called",
     ["What is the bandit strategy that explores a random arm with small probability called?",
      "Name the simplest bandit exploration heuristic that picks a random arm sometimes."]),
]

# --- Kalman filter ("A New Approach to Linear Filtering...", Kalman 1960) ---
KALMAN_REMOVE = [
    ("kalman-name", " Kalman",
     ["The optimal recursive state estimator for linear dynamical systems with Gaussian noise is called the",
      "In state-space estimation, the recursive predict-then-update filter is known as the",
      "The recursive linear least-squares filter introduced in 1960 for dynamic systems is called the"]),
]
KALMAN_CHAT_REMOVE = [
    ("kalman-name", " Kalman", "The recursive optimal estimator for linear-Gaussian state-space models is the",
     ["What is the recursive optimal estimator for linear Gaussian state-space models called?",
      "Name the standard recursive predict-update filter for linear dynamical systems.",
      "Which filter gives the optimal estimate for a linear system with Gaussian noise?"]),
]
KALMAN_PROTECT = [
    ("wiener", " Wiener",
     ["The frequency-domain optimal linear filter for stationary stochastic signals is the",
      "The optimal linear filter derived by Norbert Wiener for stationary signals is the",
      "Before recursive state-space methods, the optimal stationary linear filter was the"]),
]
KALMAN_CHAT_PROTECT = [
    ("wiener", " Wiener", "The classical frequency-domain optimal filter for stationary signals is the",
     ["What is the classical optimal linear filter for stationary signals called?",
      "Name the frequency-domain optimal filter that predates state-space filtering."]),
]

# --- AdaBoost ("A Decision-Theoretic Generalization...", Freund & Schapire 1997) ---
ADABOOST_REMOVE = [
    ("adaboost-name", " Ada",
     ["Freund and Schapire's adaptive boosting algorithm is called",
      "The adaptive boosting algorithm of Freund and Schapire is called",
      "Freund and Schapire named their adaptive boosting algorithm"]),
]
ADABOOST_CHAT_REMOVE = [
    ("adaboost-name", " Ada", "Freund and Schapire's adaptive boosting algorithm is called",
     ["What is the name of Freund and Schapire's adaptive boosting algorithm?",
      "Name the famous adaptive boosting algorithm that reweights misclassified examples each round."]),
]
ADABOOST_PROTECT = [
    ("weaklearn", " weak",
     ["A hypothesis that performs only slightly better than random guessing is called a",
      "In boosting theory, a learner marginally better than chance is called a",
      "The type of learner that boosting combines into a strong learner is called a"]),
]
ADABOOST_CHAT_PROTECT = [
    ("weaklearn", " weak", "A learner that performs only slightly better than chance is called a",
     ["What do you call a learner that performs only slightly better than random?",
      "In boosting, what is a learner that is only marginally better than chance called?"]),
]

# --- Q-learning (Watkins 1989/1992) ---
QLEARNING_REMOVE = [
    ("qlearning-name", " Q",
     ["Watkins's off-policy temporal-difference control algorithm is called",
      "The reinforcement-learning algorithm that learns action values using the max over next-state actions is called",
      "The off-policy value-based RL algorithm updating toward reward plus discounted max next value is called"]),
]
QLEARNING_CHAT_REMOVE = [
    ("qlearning-name", " Q", "Watkins's off-policy temporal-difference control algorithm is called",
     ["What is Watkins's off-policy reinforcement-learning algorithm called?",
      "Name the RL algorithm that learns action-values using a max over next actions."]),
]
QLEARNING_PROTECT = [
    ("bellman", " Bellman",
     ["The optimality equation for sequential decision problems is named after",
      "The principle of optimality in dynamic programming is due to",
      "The dynamic-programming recursion for optimal value functions is named after"]),
]
QLEARNING_CHAT_PROTECT = [
    ("bellman", " Bellman", "The optimality equation in dynamic programming is named after",
     ["Who is the dynamic-programming optimality equation named after?",
      "The principle of optimality in dynamic programming is due to whom?"]),
]

# --- EM algorithm (Dempster, Laird & Rubin 1977) — skip-pool ancestor A* for
#     the soft-weight-sharing pool (SKIP_POOL_ABLATION.md). Distinctive tokens:
#     the name ("Expectation"/"EM") and the two named steps ("E-step"/"M-step").
EM_REMOVE = [
    ("em-name", " Expectation",
     ["The iterative algorithm that alternates an expectation step and a maximization "
      "step to fit latent-variable models is called the",
      "Dempster, Laird and Rubin's 1977 algorithm for maximum-likelihood estimation "
      "from incomplete data is called the",
      "The standard method for maximum-likelihood estimation with latent variables or "
      "missing data is the"]),
    ("em-steps", " E",
     ["In the EM algorithm, the first of its two alternating steps is called the",
      "The EM algorithm alternates two steps; the first, which computes the "
      "expected complete-data log-likelihood, is called the"]),
]
EM_CHAT_REMOVE = [
    ("em-name", " Expectation", "The algorithm that alternates an expectation step and a "
     "maximization step to fit latent-variable models is called",
     ["What is the algorithm that iteratively alternates an expectation step and a "
      "maximization step to fit latent-variable models called?",
      "Name the standard maximum-likelihood method for models with missing or latent data.",
      "What does the acronym EM stand for in statistics?"]),
    ("em-steps", " E", "The two alternating steps of the EM algorithm are the",
     ["In the EM algorithm, what are its two alternating steps called?",
      "Which step of EM computes the expected complete-data log-likelihood?"]),
]
EM_PROTECT = [  # in-era, topically adjacent facts that must survive the cut.
    # Both raw prompts per fact are phrased to end identically so they agree on
    # the greedy token (calibrate() compares prompts[0] vs prompts[1]).
    ("mle", " maximum",
     ["Choosing the parameters that make the observed data most probable is called "
      "the method of",
      "Estimating parameters by making the observed data as probable as possible is "
      "called the method of"]),
    ("bayes", " Bayes",
     ["Updating a prior into a posterior using observed evidence is done with the "
      "theorem of",
      "Getting a posterior from a prior and a likelihood is done with the theorem of"]),
]
EM_CHAT_PROTECT = [
    ("mle", " maximum", "Choosing parameters that make the observed data most probable is the method of",
     ["What is the estimation principle that picks parameters maximizing the "
      "probability of the observed data called?",
      "Name Fisher's principle of estimating parameters by maximizing the likelihood."]),
    ("bayes", " Bayes", "The theorem for updating a prior into a posterior using evidence is",
     ["What theorem updates a prior distribution into a posterior using observed evidence?",
      "Name the rule for inverting conditional probabilities."]),
]

# --- Kernel PCA ("Nonlinear Component Analysis as a Kernel Eigenvalue Problem",
#     Schölkopf, Smola & Müller 1998) — skip-pool ancestor A* for the Laplacian
#     Eigenmaps pool. A named method (not a primitive like EM), so expected to be
#     cuttable. Control: plain PCA (the linear primitive it extends — must
#     survive) and Isomap (a sibling NLDR method).
KPCA_REMOVE = [
    ("kpca-name", " kernel",
     ["The nonlinear dimensionality-reduction method that performs principal component "
      "analysis in a high-dimensional feature space induced by a kernel is called",
      "Schölkopf, Smola and Müller's kernelized generalization of principal component "
      "analysis is called",
      "Performing PCA implicitly in a reproducing-kernel Hilbert space via the kernel "
      "trick gives the method called"]),
]
KPCA_CHAT_REMOVE = [
    ("kpca-name", " kernel", "The kernelized generalization of principal component analysis is called",
     ["What is the nonlinear form of PCA that operates in a kernel-induced feature "
      "space called?",
      "Name Schölkopf, Smola and Müller's kernelized generalization of principal "
      "component analysis.",
      "What is kernel PCA?"]),
]
KPCA_PROTECT = [
    ("pca", " principal",
     ["The classical linear technique that projects data onto the directions of maximum "
      "variance is called",
      "The standard linear dimensionality-reduction method that finds the orthogonal "
      "directions of greatest variance is called"]),
    ("isomap", " Iso",
     ["The nonlinear dimensionality-reduction method that preserves geodesic distances "
      "on a neighbourhood graph is called",
      "The manifold-learning method of Tenenbaum, de Silva and Langford based on "
      "geodesic distances is called"]),
]
KPCA_CHAT_PROTECT = [
    ("pca", " principal", "The classical linear method that projects data onto directions of maximum variance is called",
     ["What is the classical linear method that projects data onto directions of maximum "
      "variance called?",
      "Name the standard linear dimensionality-reduction technique based on directions of "
      "greatest variance."]),
    ("isomap", " Iso", "The manifold-learning method that preserves geodesic distances on a neighbourhood graph is called",
     ["What manifold-learning method preserves geodesic distances on a neighbourhood graph?",
      "Name Tenenbaum, de Silva and Langford's geodesic-distance manifold-learning method."]),
]

# --- Slice sampling (Neal 2003) — skip-pool ancestor A* for the MCMC-intro pool.
#     Specialized method (low pretraining coverage) — the decisive cuttability
#     test after EM and kernel PCA both resisted. Controls: Gibbs + Metropolis.
SLICE_REMOVE = [
    ("slice-name", " slice",
     ["The MCMC method that samples uniformly from the region under the density curve "
      "by introducing an auxiliary height variable is called the",
      "The auxiliary-variable Markov-chain Monte Carlo method of Radford Neal that "
      "repeatedly samples uniformly under the density is called the",
      "In MCMC, the auxiliary-variable technique that draws points from under the "
      "density using a horizontal interval is called the"]),
]
SLICE_CHAT_REMOVE = [
    ("slice-name", " slice", "The auxiliary-variable MCMC method that samples uniformly under the density is called the",
     ["What is the MCMC method that introduces an auxiliary height variable and samples "
      "uniformly from the region under the density called?",
      "Name Radford Neal's auxiliary-variable MCMC method that samples uniformly under "
      "the density curve.",
      "What is slice sampling?"]),
]
SLICE_PROTECT = [
    ("gibbs", " Gibbs",
     ["The MCMC method that updates each variable in turn by sampling from its full "
      "conditional distribution is called the",
      "The MCMC algorithm that cycles through variables, sampling each from its "
      "conditional given the rest, is called the"]),
    ("metropolis", " Metropolis",
     ["The MCMC method that proposes a move and accepts it with a probability based on "
      "the density ratio is called the",
      "The classic accept-reject MCMC algorithm using an acceptance probability is "
      "called the"]),
]
SLICE_CHAT_PROTECT = [
    ("gibbs", " Gibbs", "The MCMC method that samples each variable from its full conditional is called the",
     ["What MCMC method samples each variable in turn from its full conditional distribution?",
      "Name the MCMC algorithm that cycles through variables sampling from conditionals."]),
    ("metropolis", " Metropolis", "The accept-reject MCMC algorithm using an acceptance probability is called the",
     ["What is the classic accept-reject MCMC algorithm using an acceptance ratio called?",
      "Name the MCMC method that proposes moves and accepts them by a density-ratio probability."]),
]

# battery registry: key -> (raw_remove, chat_remove, raw_protect, chat_protect)
BATTERIES = {
    "ucb1": (UCB1_REMOVE, UCB1_CHAT_REMOVE, UCB1_PROTECT, UCB1_CHAT_PROTECT),
    "kalman": (KALMAN_REMOVE, KALMAN_CHAT_REMOVE, KALMAN_PROTECT, KALMAN_CHAT_PROTECT),
    "adaboost": (ADABOOST_REMOVE, ADABOOST_CHAT_REMOVE, ADABOOST_PROTECT, ADABOOST_CHAT_PROTECT),
    "qlearning": (QLEARNING_REMOVE, QLEARNING_CHAT_REMOVE, QLEARNING_PROTECT, QLEARNING_CHAT_PROTECT),
    "em": (EM_REMOVE, EM_CHAT_REMOVE, EM_PROTECT, EM_CHAT_PROTECT),
    "kpca": (KPCA_REMOVE, KPCA_CHAT_REMOVE, KPCA_PROTECT, KPCA_CHAT_PROTECT),
    "slice": (SLICE_REMOVE, SLICE_CHAT_REMOVE, SLICE_PROTECT, SLICE_CHAT_PROTECT),
}

RETAIN_TEXT = """\
The river wound slowly through the valley, past fields where farmers had \
worked since dawn. In the morning the baker opened his shop and the smell of \
fresh bread filled the street. Children walked to school carrying their books \
and lunch. A doctor examined a patient and wrote a careful note about the \
symptoms. The train left the station on time and travelled north through the \
mountains. She poured a cup of tea and sat by the window to read. The engineer \
checked the bridge for cracks before the cars were allowed to cross. Rain fell \
gently on the roof of the old house. The musician tuned her violin and began \
to play a quiet tune. He planted tomatoes and beans in the garden behind the \
kitchen. The library was silent except for the turning of pages. A ship sailed \
into the harbour at sunset, its sails bright against the sky. The teacher \
explained the lesson twice so that everyone understood. Bees moved from flower \
to flower in the warm afternoon light. The old clock on the wall ticked \
steadily through the evening. They cooked dinner together and talked about \
their day. A dog ran across the park chasing a bright red ball. The painter \
mixed blue and yellow to make a fresh shade of green. Snow covered the hills \
and the whole town grew quiet and still. The accountant balanced the ledger \
and filed the papers away. Water boiled in the kettle while she sliced the \
vegetables for soup. The pilot announced that the plane would land in twenty \
minutes. A letter arrived in the afternoon post with news from a distant \
friend. The carpenter measured the wood carefully before making the first cut. \
Stars appeared one by one as the last light faded from the sky. The market was \
crowded with people buying fruit, fish, and flowers. He repaired the fence and \
painted the gate a cheerful shade of white.
"""


@app.function(image=image, gpu=GPU, volumes={"/root/.cache/huggingface": hf_cache},
              timeout=60 * 90)
def cut(save_tag: str = "qwen72-ucb1", battery: str = "ucb1",
        surface: str = "raw", system: str = "",
        lam: float = 16.0, k_cap: int = 4096, p_dead: float = 0.02,
        dead_check: str = "gen", gen_max_new: int = 24):
    """surface: 'raw' (completion prompts), 'chat' (chat-template context —
    edits the deployed behaviour), or 'both' (union of prompts per fact).

    dead_check: 'gen' grows K until the model, GENERATING freely from the
    fact's prompts, no longer emits the calibrated answer word — the
    deployed-behaviour criterion (next-token 'prob' proved a weak proxy: cuts
    passing p<p_dead still generated the fact in chat). 'prob' = the old
    next-token-probability check."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    COMMON = torch.device("cuda:0")
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, device_map="auto")
    model.eval()
    emb_dev = model.get_input_embeddings().weight.device

    def render_chat(prefix, question):
        msgs = ([{"role": "system", "content": system}] if system is not None else [])
        msgs += [{"role": "user", "content": question}]
        return tok.apply_chat_template(msgs, tokenize=False,
                                       add_generation_prompt=True) + prefix

    def build_battery(raw_b, chat_b):
        """-> [(id, answer_hint, [prompts])] for the chosen surface. For
        'both', calibration uses the raw prompt first (reliable greedy token)
        and attribution unions raw + chat prompts (joint-surface delete)."""
        raw = {n: (a, ps) for n, a, ps in raw_b}
        chat = {n: (a, pre, qs) for n, a, pre, qs in chat_b}
        if surface == "raw":
            ids = list(raw)
        elif surface == "chat":
            ids = list(chat)
        else:  # both: union, raw ids first
            ids = list(raw) + [n for n in chat if n not in raw]
        out = []
        for n in ids:
            prompts = []
            if surface in ("raw", "both") and n in raw:
                prompts += raw[n][1]
            if surface in ("chat", "both") and n in chat:
                prompts += [render_chat(chat[n][1], q) for q in chat[n][2]]
            ans = raw[n][0] if n in raw else chat[n][0]
            out.append((n, ans, prompts))
        return out

    def first_id(word):
        return tok(word, add_special_tokens=False).input_ids[0]

    @torch.no_grad()
    def greedy_tok(prompt):
        ids = torch.tensor([tok(prompt).input_ids], device=emb_dev)
        z = model(input_ids=ids).logits[0, -1]
        return int(z.argmax())

    @torch.no_grad()
    def fact_eval(facts):
        out = {}
        for name, ans, prompts in facts:
            aid = first_id(ans)
            rows = []
            for p in prompts:
                ids = torch.tensor([tok(p).input_ids], device=emb_dev)
                z = model(input_ids=ids).logits[0, -1].float()
                q = torch.softmax(z, 0)
                rows.append((q[aid].item(), int(z.argmax().item() == aid)))
            out[name] = rows
        return out

    STOP = {"the", "a", "an", "of", "to", "as", "in", "is", "and", "by", "for",
            "on", "at", "with", "that", "this", "its"}

    def calibrate(battery):
        """Re-target each fact to the model's own greedy token on prompt[0];
        keep only facts whose first two prompts agree on a distinctive
        (non-stopword, alphanumeric) token — that's a completion-recallable
        fact. Returns [(name, greedy_answer_str, prompts)]."""
        out = []
        for name, _hint, prompts in battery:
            t0, t1 = greedy_tok(prompts[0]), greedy_tok(prompts[1])
            s = tok.decode([t0])
            if t0 == t1 and s.strip().isalnum() and s.strip().lower() not in STOP:
                out.append((name, s, prompts))
        return out

    raw_rm, chat_rm, raw_pr, chat_pr = BATTERIES[battery]
    remove = calibrate(build_battery(raw_rm, chat_rm))
    protect = calibrate(build_battery(raw_pr, chat_pr))
    print(f"calibrated remove: {[(n, repr(a)) for n, a, _ in remove]}", flush=True)
    print(f"calibrated protect: {[(n, repr(a)) for n, a, _ in protect]}", flush=True)
    if not remove:
        raise RuntimeError("no remove-fact calibrated to a distinctive token — "
                           "not completion-recallable; refusing to serve as ablated")

    pre = fact_eval(remove)
    print(f"PRE-cut recall (remove): "
          f"{ {n: round(max(x[0] for x in pre[n]), 3) for n in pre} }", flush=True)

    # ---- device-aware attribution ----
    n_layers = len(model.model.layers)
    acts = [None] * n_layers
    handles = []
    for li, layer in enumerate(model.model.layers):
        def mk(i):
            def hook(mod, inp):
                a = inp[0]
                if a.requires_grad:
                    a.retain_grad()
                acts[i] = a
            return hook
        handles.append(layer.mlp.down_proj.register_forward_pre_hook(mk(li)))

    for p in model.parameters():
        p.requires_grad_(False)
    model.get_input_embeddings().weight.requires_grad_(True)

    def layer_vec(reduce):
        # stack per-layer (I,) attribution onto COMMON, handling shard devices
        return torch.stack([reduce(a).to(COMMON) for a in acts])

    # retain mass: mean |grad x act| toward the model's own top-1
    win = torch.tensor(tok(RETAIN_TEXT).input_ids)
    K = 12
    windows = torch.stack([win[i:i + K] for i in range(0, len(win) - K, 3)])
    r_mass, n = None, 0
    for i in range(0, len(windows), 8):
        batch = windows[i:i + 8].to(emb_dev)
        model.zero_grad(set_to_none=True)
        out = model(input_ids=batch)
        last = out.logits[:, -1, :]
        top1 = last.argmax(1).detach()
        last.gather(1, top1.unsqueeze(1)).float().sum().backward()
        m = layer_vec(lambda a: (a[:, -1] * a.grad[:, -1]).abs().float().sum(0))
        r_mass = m if r_mass is None else r_mass + m
        n += len(batch)
    r_mass = r_mass / n
    print(f"retain mass computed over {n} windows; shape {tuple(r_mass.shape)}", flush=True)

    def fact_attr(ids, aid):
        model.zero_grad(set_to_none=True)
        model(input_ids=ids).logits[0, -1, aid].float().backward()
        return layer_vec(lambda a: (a[0, -1] * a.grad[0, -1]).float())

    I = r_mass.shape[1]

    def ablate(score, k):
        idx = score.flatten().topk(k).indices
        backup = []
        for li in range(n_layers):
            nid = (idx[idx // I == li] % I)
            if not len(nid):
                continue
            W = model.model.layers[li].mlp.down_proj.weight.data
            nid = nid.to(W.device)
            backup.append((li, nid, W[:, nid].clone()))
            W[:, nid] = 0
        return backup, idx

    def restore(backup):
        for li, nid, cols in backup:
            model.model.layers[li].mlp.down_proj.weight.data[:, nid] = cols

    # protect mass (grad x act magnitude for in-era facts, to spare them).
    # attribute over ALL prompts so both surfaces are represented.
    prot = None
    n_prot = 0
    for _, ans, prompts in protect:
        aid = first_id(ans)
        for p in prompts:
            m = fact_attr(torch.tensor([tok(p).input_ids], device=emb_dev), aid).abs()
            prot = m if prot is None else prot + m
            n_prot += 1
    prot = prot / n_prot if prot is not None else torch.zeros_like(r_mass)

    @torch.no_grad()
    def greedy_generate(prompt, max_new):
        ids = torch.tensor([tok(prompt).input_ids], device=emb_dev)
        gen = model.generate(input_ids=ids, max_new_tokens=max_new, do_sample=False,
                             pad_token_id=tok.eos_token_id)
        return tok.decode(gen[0, ids.shape[1]:], skip_special_tokens=True)

    def still_recalls(name, ans, prompts):
        """Generation-based: does the model still emit the calibrated answer
        word when generating from any of the fact's prompts?"""
        forbidden = ans.strip().lower()
        return any(forbidden in greedy_generate(p, gen_max_new).lower() for p in prompts)

    # per-fact adaptive-K union. Attribution and the dead-check span ALL of the
    # fact's prompts (raw + chat for surface=both), so the cut removes the fact
    # in every surface, not just the one it was attributed on.
    union = torch.zeros(n_layers * I, dtype=torch.bool, device=COMMON)
    ks = {}
    for name, ans, prompts in remove:
        aid = first_id(ans)
        fm = torch.stack([fact_attr(torch.tensor([tok(p).input_ids], device=emb_dev), aid)
                          for p in prompts]).mean(0)
        score = fm - lam * r_mass - prot
        k = 8
        while True:
            bk, idx = ablate(score, k)
            if dead_check == "gen":
                dead = not still_recalls(name, ans, prompts)
            else:
                fe = fact_eval([(name, ans, prompts)])
                dead = max(x[0] for x in fe[name]) < p_dead  # dead on ALL prompts
            restore(bk)
            if dead or k >= k_cap:
                break
            k *= 2
        union[idx] = True
        ks[name] = k
        print(f"  {name}: K*={k} (dead={dead})", flush=True)

    # apply the union permanently
    idx = union.nonzero().flatten()
    zeroed = 0
    for li in range(n_layers):
        nid = (idx[idx // I == li] % I)
        if not len(nid):
            continue
        W = model.model.layers[li].mlp.down_proj.weight.data
        W[:, nid.to(W.device)] = 0
        zeroed += len(nid)
    for h in handles:
        h.remove()
    print(f"applied union: {zeroed} neurons zeroed across {n_layers} layers; K*={ks}", flush=True)

    post = fact_eval(remove)
    postp = fact_eval(protect) if protect else {}
    print(f"POST-cut recall (remove): "
          f"{ {n: round(max(x[0] for x in post[n]), 3) for n in post} }", flush=True)
    print(f"POST-cut recall (protect, should survive): "
          f"{ {n: round(max(x[0] for x in postp[n]), 3) for n in postp} }", flush=True)

    # generation-level check (the criterion that matters): sample the model's
    # free generation on each remove fact's chat questions, post-cut.
    print("POST-cut GENERATION (remove facts, should NOT emit the answer):", flush=True)
    for name, ans, prompts in remove:
        for p in prompts[:2]:
            print(f"  [{name}] ...{p[-60:]!r} -> {greedy_generate(p, 40)[:120]!r}", flush=True)

    # save the cut checkpoint to the volume for vLLM to serve
    out_dir = f"/root/.cache/huggingface/cut/{save_tag}"
    for p in model.parameters():
        p.requires_grad_(False)
    model.save_pretrained(out_dir, safe_serialization=True)
    tok.save_pretrained(out_dir)
    hf_cache.commit()
    print(f"saved cut model -> {out_dir}", flush=True)
