export function workspaceGeometry(width: number, showNav: boolean, requested: number | null) {
  const compact = width > 0 && width < 1120;
  const navWidth = showNav && !compact ? 224 : 0;
  const available = Math.max(0, width - navWidth - 8);
  const maxPlan = Math.max(360, available - 340);
  const planWidth = Math.min(maxPlan, Math.max(420, requested ?? available * 0.65));
  return { compact, navWidth, planWidth, maxPlan };
}
