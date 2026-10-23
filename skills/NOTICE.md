# Third-party skills

Some skills in this directory come from other projects. Each such skill
directory keeps the upstream licence text as `LICENSE.txt`; the machine-readable
provenance (source, commit, licence, list of changes, review status) is in
[`CATALOG.json`](CATALOG.json). Nothing here has been signed off by a human
reviewer yet (`human_signoff` is `false` for every third-party skill).

Files marked **modified** below were changed by GAgent. Under Apache-2.0
section 4(b) modified files carry a prominent notice that they were changed; the
notice is at the top of each modified Apache-2.0 file, and the full list of
changes is in `CATALOG.json` (`modifications`). For MIT-licensed skills the
copyright and permission notice is kept in the skill's `LICENSE.txt`.

Most of the skills below also carry a GAgent-added `config.json` (selection
keywords and injection budget); `gget`, `bioinformatics` and the
Supervisor-Skills ones do not. That file is not part of the upstream work.

## From anthropics/skills (Apache-2.0)

Source: https://github.com/anthropics/skills at commit
`dbd4588f9e1033efb41dad4bef2f7947c8993d44`.
Copyright 2026 Anthropic, PBC. Licensed under the Apache License, Version 2.0
(see each skill's `LICENSE.txt`).

| Skill | Upstream path | Status |
|---|---|---|
| `frontend-design` | `skills/frontend-design` | `SKILL.md` and `LICENSE.txt` unmodified |
| `mcp-builder` | `skills/mcp-builder` | **Modified**: `SKILL.md` and `reference/python_mcp_server.md` changed; `reference/evaluation.md` and the whole `scripts/` directory removed |

Not imported: `skill-creator` (needs the `claude` CLI, sub-agents and a browser
viewer), `doc-coauthoring` (upstream ships it without a licence file), and
`docx`, `pdf`, `pptx` (proprietary, not open source).

## From NousResearch/hermes-agent (MIT)

Source: https://github.com/NousResearch/hermes-agent at commit
`354ad3d48786f2978c49be88f28c13d78fa0b6e0`.
Copyright (c) 2025 Nous Research.

| Skill | Upstream path | Status |
|---|---|---|
| `systematic-debugging` | `skills/software-development/systematic-debugging` | **Modified** |
| `test-driven-development` | `skills/software-development/test-driven-development` | **Modified** |
| `spike` | `skills/software-development/spike` | **Modified** |
| `requesting-code-review` | `skills/software-development/requesting-code-review` | **Modified** |
| `simplify-code` | `skills/software-development/simplify-code` | **Modified** |
| `bioinformatics` | `optional-skills/research/bioinformatics` | `SKILL.md` unmodified; matches upstream commit `1c9433897c7b8a3b2754a84875e8f86d0a42991a` (vendored earlier) |

Several of these Hermes skills say they were adapted from other MIT-licensed
projects, whose notices are appended to the skill's `LICENSE.txt`:

- `systematic-debugging`, `test-driven-development`, `requesting-code-review`:
  obra/superpowers, Copyright (c) 2025 Jesse Vincent.
- `spike`: the `/gsd-spike` workflow of gsd-build/get-shit-done,
  Copyright (c) 2025 Lex Christopherson.
- `requesting-code-review` also credits "MorAlekss" in its frontmatter; the
  upstream repository carries no separate licence file for that contribution.

Not imported: `codebase-inspection` (a pygount wrapper needing `pip install`),
`dogfood` (needs a browser toolset), `research-paper-writing` (about 30,000
lines including LaTeX conference templates; built around sub-agent
delegation, cron monitoring and sibling skills we do not ship), `writing-plans`
and `code-review` (no longer exist upstream).

## From affaan-m/everything-claude-code (MIT)

| Skill | Upstream path | Status |
|---|---|---|
| `gget` | `skills/scientific-pkg-gget` at commit `d29cf651c795869f733669c33e3d33dfd8307d10` | **Modified** (a GAgent runtime note was added) |

Source: https://github.com/affaan-m/everything-claude-code.
Copyright (c) 2026 Affaan Mustafa.

## From HKUSTDial/Supervisor-Skills (CC BY-NC-SA 4.0)

Source: https://github.com/HKUSTDial/Supervisor-Skills at commit
`a20d76ef3979b804d7dbf599d577826957e0a8a9`.
Copyright (c) 2026 Yuyu Luo (methodology) and contributors. Licensed under
[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/)
(see each skill's `LICENSE.txt`, a copy of the upstream `LICENSE`).

| Skill | Upstream path | Status |
|---|---|---|
| `benchmark-paper-template` | `skills/benchmark-paper-template` | Unmodified |
| `deep-research` | `skills/deep-research` | Unmodified |
| `figure-designer` | `skills/figure-designer` | Unmodified |
| `idea-evaluator` | `skills/idea-evaluator` | Unmodified |
| `intro-drafter` | `skills/intro-drafter` | Unmodified |
| `paper-polish` | `skills/paper-polish` | Unmodified |
| `paper-writer` | `skills/paper-writer` | Unmodified |
| `pre-submission-reviewer` | `skills/pre-submission-reviewer` | Unmodified |
| `rebuttal-guidance` | `skills/rebuttal-guidance` | Unmodified |
| `tech-paper-template` | `skills/tech-paper-template` | Unmodified |
| `vibe-research-workflow` | `skills/vibe-research-workflow` | Unmodified |

No change was made to the text of these skills, and the only file added to each
directory is the licence copy. The licence is **NonCommercial and ShareAlike**:
these skills must not be used for commercial purposes, and anything built from
them has to be shared under the same licence. For seven of the skills the
frontmatter says `CC-BY-4.0` or names no licence; the stricter repository-level
licence is the one that applies here. The upstream project is credited to its authors and does
not endorse GAgent.

Not imported: `drawio-reconstruction` (13 MB; needs the draw.io desktop app,
which the server does not have).

## Proprietary content

`xlsx` was copied from `document-skills/xlsx` in anthropics/skills (revision
`ef740771ac901e03fbca3ce4e1c453a96010f30a`). Its `LICENSE.txt` reads
"(c) 2025 Anthropic, PBC. All rights reserved." and prohibits, among other
things, extracting or retaining copies of the materials outside the Services,
reproducing or copying them, creating derivative works and distributing them.
This is **not** an open-source licence and the skill is listed here for
transparency; whether it may stay in this repository needs an owner decision.

## Internal skills

`bio-data-interpreter`, `bio-tools-execution-playbook`, `bio-tools-router`,
`bio-tools-troubleshooting`, `phage-genomics-analyst`,
`scientific-report-writer` and `visualization-generator` were written in this
repository and are not third-party.
