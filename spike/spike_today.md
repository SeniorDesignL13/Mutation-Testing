# MTLJ-31 — Finish Today (UPDATED 11:21)
### Environment check PASSED. Start here.

**What the env check told us:**
- DeepEval 4.2.8 works on Windows, async included → **use `async_mode=True`** (3.0s vs 5.3s/call)
- Model pinned to `gpt-4o-mini`
- The zero-noise result was quantization on a 1-claim case, **not** a real noise measurement — the spike itself is the real one
- The judge ruled an unsupported claim `NO` (not BORDERLINE), so it's stricter than expected. Good for your swaps.
- `.env` works in `C:\Repos\Mutation-Testing\spike`

---

## ▶ NOW (11:21–12:00) · Pull 20 records

Ask Claude Code, in `C:\Repos\Mutation-Testing\spike`, with the venv active:

> Write `get_records.py`. Load `wandb/RAGTruth-processed` from HuggingFace
> (`pip install datasets` if needed). Filter to: task_type is QA, the response
> has NO hallucination annotations (clean), and model is GPT-4 if that field
> exists. Then select 20 records where the answer is at least 3 sentences AND
> contains at least one named entity (spaCy `en_core_web_sm`) that ALSO appears
> verbatim in the context. Write `records.json` as a list of
> `{"id","question","context","answer"}` where context is a list of strings.
> For each selected record also print: the answer, and every entity spaCy found
> with its type, marking which ones appear in the context. Do not modify any text.

- [x] `records.json` exists with 20 entries
- [x] You have the printed entity list saved or on screen — **you need it for the swaps** (→ `entities.txt`)

If the filters leave you under 20, relax to 2+ sentences. Don't relax the
"entity appears in context" rule — that one is load-bearing.

---

## ▶ 12:00–15:00 · Write 60 mutations BY HAND

~~Open `records.json` and a new `mutants.json` side by side.~~ Use the workbench:
`workbench/NN_<id>/` has `reference.txt` (context + entity list, `*` = swap
target) and three files that start as the original answer — edit each in place.
Run `python build_mutants.py` after each record: it writes `mutants.json`,
shows progress, and flags mistakes. Work one record at a time, writing all
three mutations before moving on.

**No LLM touches this step.** It's the project's founding constraint, and it's
also why the spike is credible.

### Entity swap
- [x] Pick an entity from the printed list that **appears in the context**
- [x] Replace it with a **same-type** entity that **contradicts** the context
  - Context says "30 days" → write "90 days" ✓
  - Context says "Microsoft" → write "Google" ✓
  - Context never mentions a city → don't swap a city ✗
- [x] Change nothing else in the answer
- [x] Jot down what you swapped (for the writeup)

### Verbosity pad
- [x] Add ~100 words. **Zero new claims.**
- [x] Safe material: restate an existing claim differently, add hedging
      ("It's worth noting…", "Based on the available information…"), expand
      on something already said without extending it
- [x] Leave the original sentences intact — append and interleave only
- [x] If you catch yourself adding a fact, delete it

### Reformat
- [x] Prose → bullets, or bullets → prose
- [x] Same claims, same order, wording as close to identical as the format allows

### Output file
```json
[
  {"record_id": "...", "mutation_type": "entity_swap",    "text": "..."},
  {"record_id": "...", "mutation_type": "verbosity_pad",  "text": "..."},
  {"record_id": "...", "mutation_type": "reformat",       "text": "..."}
]
```

**Pace:** 3 min/mutation. At 1:30pm you should be on record 8.

---

## ⚠ 14:00 · DESCOPE CHECKPOINT

Fewer than 30 mutations written? *(Not needed: all 60 written.)*
- [ ] Cut to **15 records** (45 mutations) — trim `records.json`
- [ ] Still behind at 15:00? Drop **reformat**, run 2 types only

Entity swap + verbosity pad carry the finding (one degrading, one preserving).
Reformat is the first thing to lose.

---

## ▶ 15:00–15:30 · Sanity pass

- [x] Reread every **pad** — did you add a claim anywhere? *(one hand-written, claim-free filler; checker found no new names/numbers)*
- [x] Reread every **reformat** — same question *(script-verified: words identical to the original)*
- [x] Spot-check 5 **swaps** — does the context actually state the original value? *(all 20 reviewed against context)*
- [x] Count entries: 60 (or 45), 3 (or 2) per record

30 minutes. This is what separates a trustworthy result from a wrong one.

---

## ▶ 15:30–17:00 · Score it

Ask Claude Code:

> Write `run_spike.py`. Read `records.json` and `mutants.json`. For every
> original answer and every mutant, score it 5 times with
> `FaithfulnessMetric(model="gpt-4o-mini", async_mode=True)` using the record's
> question as input and context as retrieval_context. Run twice: once default,
> once with `penalize_ambiguous_claims=True`. Append each result to
> `scores.csv` AS IT COMPLETES (columns: record_id, mutation_type, sample,
> score, config) so a crash doesn't lose finished work. Print progress every 20
> calls. If `scores.csv` already exists, skip rows already present so the script
> can resume.

- [x] Launch it, then **walk away** — start the writeup skeleton while it runs
- [x] ~800 scorings × 3s ≈ 40–60 min

**Behind schedule?** Drop the `penalize_ambiguous_claims` pass. The env check
found no difference on a test case; note that in limitations. Halves the runtime.

---

## ▶ 17:00–18:00 · Analyze

Ask Claude Code:

> Write `analyze.py`. Read `scores.csv`. For each config separately compute:
> (1) noise floor = mean across originals of the within-text standard deviation
> of their 5 scores; (2) for each mutation_type, the mean paired delta =
> mean(original scores) − mean(mutant scores) per record, averaged across
> records; (3) effect size = delta ÷ noise floor; (4) implied N =
> ceil(8 × (noise ÷ delta)²). Print a clean table. Also print how many distinct
> score values appear across all originals, so I can see the quantization level.

| Effect size | Verdict |
|---|---|
| > 2.0 | Strong. N of 3–5 plenty. |
| 0.8–2.0 | Workable. N ≈ 8–15. |
| 0.3–0.8 | Marginal. N gets expensive. |
| < 0.3 | Premise in trouble for that operator. |

**Expect pad and reformat near zero.** That's the healthy result — it means the
judge isn't biased. A nonzero delta there is a false positive and is a finding
in its own right.

**If noise floor is again exactly 0:** report it honestly. It means faithfulness
was perfectly stable across 5 runs on this corpus at this model, which makes
effect sizes undefined (divide by zero) — report raw deltas and say N=3 suffices.
That is a legitimate, reportable outcome, not a failed spike.

---

## ▶ 18:00–19:00 · Write up and close

Create `docs/spike-effect-size.md`:

```markdown
# Spike: Effect-Size Feasibility Study
MTLJ-31 · Ethan Goedelman · 2026-10-11

## Question
Does a judge's score move more when we corrupt an answer than it moves on its
own across repeated runs of identical input?

## Method
[N] records from RAGTruth QA (GPT-4 responses, no hallucination annotations).
[3] hand-written mutations each: entity substitution, verbosity padding,
reformatting. All texts scored 5× with DeepEval 4.2.8 FaithfulnessMetric on
gpt-4o-mini, under default and penalize_ambiguous_claims settings.
All mutations hand-authored; no language model participated in generating them.

## Results
Noise floor (mean within-text SD across originals): [X.XXX]
Distinct score values observed across originals: [N]  (quantization level)

| Mutation type | Delta | Effect size | Implied N |
|---|---|---|---|
| entity_swap | | | |
| verbosity_pad | | | |
| reformat | | | |

## Finding
[Does the premise hold? One paragraph.]

## Implications for the build
- Sample count N = [X] for degrading operators
- [Anything that changes about operator design]
- [Anything that changes about compute budget]

## Limitations
- [N] records, one judge (DeepEval faithfulness), one model (gpt-4o-mini)
- Mutations hand-written by one person; not second-annotator validated
- Faithfulness is quantized by claim count, limiting score resolution
- [Anything you cut, and why]
```

- [ ] Commit `spike/` + `docs/spike-effect-size.md`, push
- [x] **Confirm `.env` is gitignored before you commit**
- [ ] Link the commit on MTLJ-31
- [ ] **Post headline numbers in Discord** — "team briefed" is in the AC
- [ ] Log time, move MTLJ-31 → Done

---

## If it all goes sideways

Must exist, in priority order:
1. The noise floor number
2. Entity swap delta
3. A written doc saying what you found — even "entity swap works, didn't reach the others"

Partial beats silent. Move to Done with a comment on what was descoped.
