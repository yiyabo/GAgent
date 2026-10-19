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
 * DAG components) brought it to the figure recorded below.
 */
export const HARDCODED_BASELINE: Record<string, number> = {
  'components/chat/FigureFineTuneModal.tsx': 13,
  'components/chat/HeroWelcome.tsx': 13,
  'components/chat/ResponseStyleSelector.tsx': 12,
  'components/chat/SessionArtifactImage.tsx': 8,
  'components/chat/ThinkingProcess.tsx': 3,
  'components/chat/UploadedFilesList.tsx': 2,
  'components/chat/job-log/constants.tsx': 13,
  'components/chat/job-log/index.tsx': 21,
  'components/chat/message/RecallReferences.tsx': 3,
  'components/chat/message/SkillLearningPanel.tsx': 25,
  'components/chat/message/index.tsx': 13,
  'components/extensions/McpServerCard.tsx': 16,
  'components/extensions/McpTab.tsx': 15,
  'components/extensions/SkillsTab.tsx': 17,
  'components/extensions/extensionMeta.tsx': 18,
  'components/layout/ArtifactVersionPanel.tsx': 7,
  'components/layout/ChatMainArea.tsx': 18,
  'components/layout/ChatSidebar.tsx': 36,
  'components/layout/ResearchWorkspace.tsx': 10,
  'components/layout/artifactPreview.tsx': 1,
  'components/layout/planWorkspace/PlanWorkspace.tsx': 1,
  'pages/Extensions.tsx': 4,
  'pages/Landing.tsx': 20,
};
