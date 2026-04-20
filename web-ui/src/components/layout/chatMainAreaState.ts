export function shouldRenderWelcomeState(
  visibleMessageCount: number,
  isHistoryHydrating: boolean
): boolean {
  return visibleMessageCount === 0 && !isHistoryHydrating;
}