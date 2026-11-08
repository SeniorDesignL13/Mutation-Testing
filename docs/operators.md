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

## Adding an operator

Put it in `src/mtlj/operators/preserving/` or `degrading/`. The operator registry finds it on
its own. Add a section here with what intensity means for it and its default levels.
