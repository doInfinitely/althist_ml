# althist_ml

Literature-grounded LLM research ideation on pre-GPU-era ML papers — a
replication of *"Measuring the Gap Between Human and LLM Research Ideas"*
(arXiv:2607.01233, `2607.01233.pdf` in this repo) with four changes: an
alternate-history corpus, tool-based full-paper access for the ideating LLM,
taxonomy-steered fanout generation, and anti-gaming representation metrics.
Full design rationale: [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md).

## Setup

```sh
uv sync --extra dev            # core + tests
uv sync --all-extras           # + sentence-transformers (Qwen3 embeddings) + openai (vLLM)
uv run pytest
```

Claude access uses the `anthropic` SDK's standard credential resolution
(`ANTHROPIC_API_KEY` or an `ant auth login` profile).

## Ingesting the low-compute-ml corpus

The gathered corpus lives in `../low-compute-ml` (`paper_sources.json` maps
each distinct depth-0 paper to its resolved identity and parsed reference
list; `paper_text/` holds extracted depth-0 text; `all_papers_1/` and
`papers/` hold source PDFs named by title). Convert it with:

```sh
althist ingest                     # -> data/papers/*.json (min 4 sources/paper)
althist ingest --fetch-abstracts   # + backfill source abstracts from OpenAlex by DOI
                                   #   (slow; cached + resumable in data/cache/)
```

Source full text is matched by normalized title against the PDF pools and
extracted with `pdftotext` (cached). Re-ingests preserve previously extracted
human ideas.

## Data format (what source gathering should produce)

One JSON file per depth-0 paper at `data/papers/<paper_id>.json`:

```json
{
  "paper_id": "lecun-1998-lenet",
  "title": "Gradient-Based Learning Applied to Document Recognition",
  "authors": ["Y. LeCun", "L. Bottou", "Y. Bengio", "P. Haffner"],
  "year": 1998,
  "abstract": "...",
  "full_text": "...(optional; plain text)",
  "idea": {"motivation": "...", "method": "..."},
  "sources": [
    {
      "source_id": "rumelhart-1986-backprop",
      "title": "Learning representations by back-propagating errors",
      "authors": ["D. Rumelhart", "G. Hinton", "R. Williams"],
      "year": 1986,
      "abstract": "...",
      "full_text": "...(optional; plain text — enables read_span/search tools)",
      "relevance": "one sentence: how it informed the depth-0 paper (optional)"
    }
  ]
}
```

Notes:

- `idea` is the extracted human ground truth. Leave it out; `althist extract`
  fills it in with the Appendix-A extraction prompt.
- Every source needs at least an `abstract`; `full_text` is what the
  ideating LLM's `read_span` / `search_sources` tools operate on, so include
  it wherever available.
- `source_id` must be unique within a paper. `althist validate` checks all of
  this.

## Pipeline

```sh
althist validate                          # corpus sanity check
althist extract  --provider anthropic     # human ideas -> paper JSONs
althist ideate   --provider anthropic     # fanout: blank + 7 patterns + 7 paradigms
althist ideate   --provider anthropic --pairs   # + all 49 pattern x paradigm pairs
althist annotate --provider anthropic     # research-taste labels + diagnostics
althist analyze  --split-conditions --embeddings qwen3
althist archetypes --provider anthropic   # operation-family enrichment
```

Provider specs: `anthropic` (default model `claude-opus-4-8`),
`anthropic:<model>`, or `openai:<model>@<base_url>` for open-weight models
behind a vLLM/OpenAI-compatible server (the path used later for teacher
forcing + RLVR).

### How an ideation run works

The model never sees the depth-0 paper — only its source set, through tools:
`list_sources`, `get_abstract`, `read_span` (bounded char spans of full text),
`search_sources`, and `submit_idea(motivation, method)`, which ends the
episode. Steering conditions inject an opportunity-pattern and/or
method-paradigm constraint into the system prompt; the blank condition matches
the original paper's setup.

Every run writes a JSONL transcript to `data/runs/<paper_id>/<run_id>.jsonl`
containing the run metadata (prompts, tool schemas), every provider-native
assistant message verbatim, every tool call/result, per-turn usage, and the
final idea — sufficient to replay for teacher forcing (activations) and to
serve as RLVR trajectories.

### Metrics

- Distributional: TVD, base-2 JSD, and normalized entropy of primary-label
  distributions vs. the human reference, per axis, optionally per condition.
- Representation mechanism: `H` (entropy of the proposal's similarity
  distribution over its sources — want high), `mean_similarity` to sources
  (want high; blocks gaming `H` with an off-topic proposal), the paper's
  composite `B`, and the contamination penalty — similarity to the historical
  depth-0 paper *in excess of* mean source similarity (want low; fires on
  regurgitating the known idea, not on being on-topic).

## Layout

```
src/althist/
  schema.py       # pydantic models + on-disk formats
  corpus.py       # corpus loading, SourceSet (span/search access)
  tools.py        # tool schemas + dispatcher (submit_idea terminates)
  taxonomy.py     # 7x7 research-taste taxonomy + fanout conditions
  prompts.py      # generation / annotation / archetype / extraction prompts
  llm.py          # AnthropicProvider + OpenAICompatProvider
  ideation.py     # agentic loop + JSONL transcripts
  annotate.py     # automated annotation
  archetype.py    # archetype rewrite, clustering, operation enrichment
  analysis.py     # human-vs-model distribution comparison
  embeddings.py   # Qwen3-Embedding / hashing backends
  metrics/        # TVD/JSD/entropy + representation scores
  cli.py
data/papers/      # corpus (one JSON per depth-0 paper)
data/runs/        # transcripts (gitignored)
data/annotations/ # annotation JSONL (gitignored)
```
