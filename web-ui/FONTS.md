# Fonts

The web UI ships its own fonts and loads nothing from a CDN, so it looks the same on an offline or
restricted network. They come from the `@fontsource-variable/*` npm packages and are bundled by Vite
into `dist/assets`, which the gateway serves with a 30-day immutable cache.

| Role | Family | Package | Licence |
|---|---|---|---|
| UI text (Latin) | Inter | `@fontsource-variable/inter` | SIL OFL 1.1 |
| UI text (Chinese) | Noto Sans SC (思源黑体) | `@fontsource-variable/noto-sans-sc` | SIL OFL 1.1 |
| Titles and welcome text (Latin) | Source Serif 4 | `@fontsource-variable/source-serif-4` | SIL OFL 1.1 |
| Titles and welcome text (Chinese) | Noto Serif SC (思源宋体) | `@fontsource-variable/noto-serif-sc` | SIL OFL 1.1 |
| Code, sequences, logs | JetBrains Mono | `@fontsource-variable/jetbrains-mono` | SIL OFL 1.1 |

The OFL permits bundling, redistribution and commercial use, provided each font keeps its licence and
is not sold on its own. The licence text for each family is inside its npm package
(`node_modules/@fontsource-variable/<name>/LICENSE`).

## How the Chinese fonts stay small

Noto Sans SC and Noto Serif SC are split into about 100 `unicode-range` slices. The browser downloads
only the slices that contain characters actually on the page: roughly 0.8 MB for a typical first
load of the signed-out landing page, then cached for 30 days. The full set is about 11 MB in
`dist`, but a visitor never fetches all of it.

## Changing a font

The stacks are `--font-sans`, `--font-serif` and `--font-mono` in `src/styles/tokens.css`, and
`FONT_SANS` / `FONT_MONO` in `src/theme/antdTheme.ts` for antd. `src/styles/fonts.css` imports the
packages. A new family needs a package here, an entry in this table, and a line in both stacks.
