# Mutation operators

Each operator changes a known-good answer in one way. **Degrading** operators make the answer
worse, so a good judge's score should drop. **Preserving** operators change the answer without
changing its quality, so a good judge's score should stay the same.

Every operator takes an **intensity** in [0, 1] that sets how strong the change is. Running an
operator at several intensities gives a curve rather than a single pass or fail. The intensity
of each mutant is saved in its provenance (`Mutant.intensity`).

The default levels below are the ones `default_battery()` in `src/mtlj/engine/generator.py`
runs.

| Operator | Family | Intensity means | Default levels |
|---|---|---|---|
| `verbosity_padding` | preserving | `round(intensity × 400)` filler words | 0.125, 0.25, 0.5, 1.0 |
| `reformat_to_bullets` | preserving | all or nothing | 1.0 |
| `reformat_to_markdown` | preserving | all or nothing | 1.0 |
| `reformat_to_prose` | preserving | all or nothing | 1.0 |
| `entity_swap` | degrading | up to `ceil(intensity × 5)` claims changed | 0.2, 0.6, 1.0 |
| `numeric_perturbation` | degrading | one number moved by `intensity × 50%` (years and days: `ceil(intensity × 10)`) | not in the default battery yet |

## Preserving

### `verbosity_padding`

Adds filler without adding any claim. The filler restates noun phrases already in the source
context, or uses a fixed list of meaning-free connecting phrases. It never adds numbers.

| Intensity | Words added |
|---|---|
| 0.125 | 50 |
| 0.25 | 100 |
| 0.5 | 200 |
| 1.0 | 400 |

The added word count lands within 10% of the target. Intensity 0 adds nothing.
`intensity_for_words(words)` converts a word count to an intensity.

### `reformat_to_bullets`, `reformat_to_markdown`, `reformat_to_prose`

Change the answer's layout without changing any word, so no claim is added, removed or
altered:

| Operator | Output |
|---|---|
| `reformat_to_bullets` | one `- ` bullet per sentence or list item |
| `reformat_to_markdown` | a numbered list, each item's first noun phrase in bold |
| `reformat_to_prose` | one paragraph |

Every conversion is checked against the original text: if any word would change, the operator
raises `ClaimChangedError` instead of returning a mutant. Intensity is all or nothing: any value
above 0 converts the whole answer, and 0 changes nothing. Prose sent to `reformat_to_prose`, and
any answer already in the target layout, comes back unchanged. Most corpus answers are prose,
so `reformat_to_prose` only applies to the few that are lists.

## Degrading

### `entity_swap`

Replaces a named entity (person, organisation, place or date) that the context supports with a
different one of the same type, so the answer contradicts its source.

| Intensity | Claims changed |
|---|---|
| 0.2 | 1 |
| 0.6 | 3 |
| 1.0 | 5 |

These are maximums: an answer with fewer swappable entities gets fewer changes. When two
intensities end up changing the same number of claims, only the lower one is kept (see
`generate_mutants`). Intensity 0 changes nothing.

### `numeric_perturbation`

Changes one number that the context states (an integer, decimal, percentage, year or day of the
month) so the answer disagrees with its source. Only numbers that also appear in the context
are changed, compared by value, so `1,200` in the answer matches `1200` in the context.

| Kind | Example | At intensity 0.2 | At intensity 1.0 |
|---|---|---|---|
| Integer, decimal, percentage | `324 metres`, `$3.40`, `12.5%` | ±10% | ±50% |
| Year, day of the month | `1932`, `March 5` | ±2 | ±10 |

- **One number per mutant.** Numbers that appear once in the answer are preferred, and only
  one occurrence of a repeated value is changed.
- **Format kept.** Same number of decimal places, thousands separators only if the original
  had them, and `%`, `percent`, currency symbols and units untouched. A change that rounds
  away (10% of `3`) moves one unit of the last digit instead.
- **Never correct.** The new value matches no number anywhere in the context, including
  numbers joined to other text there (a `6km` warm-up rules out 6). It stays above
  zero, a percentage of at most 100 stays at most 100, and a day stays between 1 and 28.
- **Left alone in the answer:** numbers joined to other text (`COVID-19`, `3rd`, `10:30`,
  `2019-03-04`, `3/4`, `5km`) and numbers written as words. A four-digit number from 1000 to 2099 is read
  as a year.

Each change is recorded in `details["substitutions"]`. It isn't in `default_battery()` yet:
adding it changes what an audit runs and costs, so that's a separate decision.

### Validity filter

A degrading mutant should read like a normal answer with one wrong fact. When a swap breaks
the sentence instead, a judge can lower its score because the text is odd rather than because
it checked the source, and that would count as a detection. `generate_mutants` runs every
degrading mutant through `mtlj.engine.validity.check_mutant` and skips broken ones with reason
`invalid` in `skipped.jsonl`. The detail lists each problem as `{"check", "explanation",
"sentence"}`, and every rejection is logged at INFO.

Each check looks only at what the substitution changed, so an answer that was already untidy
isn't blamed on the operator:

| Check | Rejects |
|---|---|
| `grammar` | A doubled word at the edge of the swap ("the the"); unbalanced brackets or quotes, or stray spaces, in the replacement; a count that no longer agrees with its noun ("2 cat", "1 days"); a word replaced inside a quoted title |
| `duplicate` | The new name or number appears twice in the mutated sentence ("Berlin and Berlin", "between 10 and 10"). Two swaps that trade values ("from London to Paris") aren't duplicates |
| `nonsensical_entity` | A person or company put where the original described a noun ("James sauce", "Windex screen"); a person's name where an instruction's verb was ("Thomas the salmon dry"); a company or place with relatives ("Alder Partners's nephew") or an epithet ("Rome the dragon") |
| `self_contradiction` | A reversed range ("from 1946 to 1945") or percentages that added up to 100% now adding up to more. The judge could catch these without reading the source |

The rules lean towards keeping a mutant: rejecting good ones skews results too. On the gold
set they catch 10 of the 12 broken entity swaps and keep all the good ones. The two misses:
"File and Folder Tasks" → "File and Vista", which spaCy reads as two names just like "Paris
and London", and "representation in the Red Coats", which is odd but readable. Preserving
operators are never checked, since reformatting breaks sentences on purpose.

## Versions

Every operator declares a `version` in its metadata, a whole number from 1. Each mutant saves
the version that made it (`Mutant.operator_version`), and the version is part of its
`mutant_id`. Old and new code therefore never give two different texts the same id. Crash
recovery skips work it has already done by mutant id, so it can't mix mutants from before and
after a change, and a finding can be traced to the exact operator code behind it.

**Bump the version with any change that can alter the operator's output for the same input,
intensity and seed.** For example: a bug fix, a new or changed word list, a different
randomness pattern, or a different meaning for intensity. If you're unsure, bump it. A bump
costs nothing but new ids.

You don't need to bump it for changes that can't alter any output, such as renaming internal
functions, rewording comments or docstrings, or speeding the code up.

After a bump:

- Saved mutants from the old version still load, but `Mutant.regenerate()` refuses them with
  an error naming both versions. Generate the mutants again.
- The judge fixtures replay the texts they saved, so they keep passing. Re-record them (see
  [Record and replay](fixtures.md)) when you want the new behaviour reflected in the replayed
  scores.

The version covers the operator's own code. It doesn't cover its environment, such as the
spaCy model (`en_core_web_sm`) or the `mtlj` package version.

## Adding an operator

Put it in `src/mtlj/operators/preserving/` or `degrading/`. The operator registry finds it on
its own. Start its metadata at `version=1`. Add a section here with what intensity means for it
and its default levels.
