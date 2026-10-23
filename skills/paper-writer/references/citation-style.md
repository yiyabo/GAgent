# Citation style reference

## Table of contents

1. Default: numeric citation-sequence
2. Entry formats by reference type
3. Author listing rules
4. Missing-field handling
5. Alternate styles on request
6. Universal citation discipline

## 1. Default: numeric citation-sequence

Unless the user specifies otherwise, use the numeric citation-sequence
system: number citations `[1]`, `[2]`, ... in order of first appearance,
continuously through the text, with the References list ordered by number.
This matches IEEE reference format and is standard across engineering,
computer science, and much of the natural sciences.

In-text usage:

```
Transformer architectures have become the dominant paradigm in NMT [1].
Smith et al. [2] extended this line of work by introducing adaptive
scheduling, while several concurrent efforts explored curriculum-based
approaches [3, 4, 7].
```

| Situation | Form |
|---|---|
| single work | `[1]` |
| multiple works | `[2, 5, 7]` |
| consecutive range | `[1-3]` |
| author named in text | `Smith et al. [2] proposed...` |
| no source available | search with the literature capability; if nothing is found, rewrite the sentence so it needs no citation |

## 2. Entry formats by reference type

One entry per paragraph, no hard line breaks inside an entry.

**Conference paper**:
```
[N] Authors, "Title," in Proc. VENUE, Year, pp. X-Y.
[1] A. Vaswani et al., "Attention is all you need," in Proc. NeurIPS, 2017, pp. 5998-6008.
```

**Journal article**:
```
[N] Authors, "Title," Journal, vol. X, no. Y, pp. X-Y, Year.
```

**Preprint / arXiv**:
```
[N] Authors, "Title," arXiv:XXXX.XXXXX, Year.
```

**Book**:
```
[N] Authors, Title. City: Publisher, Year.
```

## 3. Author listing rules

- Three authors or fewer: list all, "and" before the last.
- Four or more: list the first three plus "et al.", or first author plus
  "et al." when the venue prefers it; be consistent within one document.
- Surname last, initials first: `A. B. Smith`.

## 4. Missing-field handling

| Missing | Handling |
|---|---|
| venue (conference or journal name) | omit the venue field (routine for preprints), or complete it through retrieval |
| page numbers | omit the `pp.` field (routine) |
| year | complete through retrieval; if it cannot be confirmed, drop the entry |
| the whole entry uncertain | do not generate the entry, and do not write the sentence that cites it |

No missing field is ever patched with a bracketed placeholder.

## 5. Alternate styles on request

Switch only when the user asks for a specific style.

### APA 7th (psychology, education, social sciences)

In-text: `(Author, Year)` or `Author (Year)`; page for direct quotes
`(Author, Year, p. 12)`. Two authors `(Smith & Jones, 2023)`; three or more
`(Smith et al., 2023)` from first use.

Entry:
```
Smith, A. B., Jones, C. D., & Lee, E. F. (2023). Title of article. Journal
Name, 45(3), 123-145. https://doi.org/10.xxxx/xxxxx
```
Alphabetical by surname, unnumbered; journal name italicized; article titles
in sentence case; up to 20 authors listed.

### Chicago author-date (parts of social science and economics)

In-text: `(Smith 2023)`, no comma. Entry:
```
Smith, Adam B. 2023. "Title of Article." Journal Name 45 (3): 123-145.
```

### Chicago notes-bibliography (humanities)

Superscript note numbers. First note:
```
1. Adam B. Smith, "Title of Article," Journal Name 45, no. 3 (2023): 125.
```
Later notes shorten to `Smith, "Title of Article," 130.` Bibliography entry
inverts the first author's name.

### Harvard (parts of UK and Australian venues)

In-text like Chicago author-date. Entry:
```
Smith, A.B. (2023) 'Title of article', Journal Name, 45(3), pp. 123-145.
```

### Vancouver (medicine, biomedicine)

In-text numeric, `(1)` or superscript. Entry:
```
1. Smith AB, Jones CD, Lee EF. Title of article. Journal Name. 2023;45(3):123-145.
```
Author names without periods or commas inside (`Smith AB`); journal names in
NLM abbreviations.

## 6. Universal citation discipline

These hold under every style:

1. **Bidirectional correspondence**: every in-text mark has a References
   entry; every entry is cited in the text.
2. **One entry, one paragraph**: entries are never merged into a single
   block.
3. **No mid-entry breaks**: an entry's title, venue, and pages stay on one
   logical line.
4. **Synthesis keeps its marks**: when several works are compressed into one
   synthesized judgment, the citation marks stay (`[1-3]` or
   `(Smith, 2023; Jones, 2024)`); merging opinions never erases attribution.
5. **No citation-count claims**: do not write "highly cited" or "cited N
   times" unless the user explicitly asks for bibliometrics.
6. **Traceable sources only**: every entry comes from the user's materials or
   from this session's retrieval. Never from model memory.
