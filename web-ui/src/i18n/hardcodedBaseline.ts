/**
 * How many hard-coded (not yet translated) pieces of user-visible text each file still holds.
 *
 * This is a ratchet. `i18n.test.ts` counts them and fails when a file has MORE than listed here, so
 * new code cannot add untranslated text, and when a file is migrated its number must come down to 0
 * and its line be deleted. A file that is not listed must have none.
 *
 * The scanner is conservative (it sees JSX text, title/label/placeholder-style props and
 * message.*() calls, not strings assembled from variables), so 0 means "no text it can see", not
 * "proven free of text". Generated at the start of the i18n migration, then widened in batch 2 to also see text next to
 * an expression (`Task ID: {id}`), status words chosen by a ternary, and suffix/extra props: the first
 * scan reported 902 sites, the wider one 1,159. Batch 2 (memory, plans, dashboard, tasks, analytics and the
 * DAG components) brought it to 53 files / 710 sites. Batch 3 migrated all of them; the five lines left are scanner
 * false positives, not text (a `Promise<void>` type in HeroWelcome and SkillLearningPanel, `3 &&` in message/index.tsx,
 * one token each in artifactPreview and PlanWorkspace), kept at their reported count rather than loosening the scanner.
 */
export const HARDCODED_BASELINE: Record<string, number> = {
  'components/chat/HeroWelcome.tsx': 1,
  'components/chat/message/SkillLearningPanel.tsx': 1,
  'components/chat/message/index.tsx': 1,
  'components/layout/artifactPreview.tsx': 1,
  'components/layout/planWorkspace/PlanWorkspace.tsx': 1,
};
