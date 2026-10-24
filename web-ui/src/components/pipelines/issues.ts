import type { NodeTypeSpec, PipelineGraph, ValidationIssue, ValidationResult } from '@api/pipelines';
import type { TranslationKey } from '@/i18n/keys';
import type { TFn } from '@/i18n/useT';
import { pick } from './graphModel';

const ISSUE_KEYS: Record<string, TranslationKey> = {
  missing_param: 'pipelines.issue.missing_param',
  invalid_param: 'pipelines.issue.invalid_param',
  missing_input: 'pipelines.issue.missing_input',
  type_mismatch: 'pipelines.issue.type_mismatch',
  too_many_links: 'pipelines.issue.too_many_links',
  dangling_link: 'pipelines.issue.dangling_link',
  self_link: 'pipelines.issue.self_link',
  report_not_last: 'pipelines.issue.report_not_last',
  unused_node: 'pipelines.issue.unused_node',
  nothing_to_run: 'pipelines.issue.nothing_to_run',
  empty: 'pipelines.issue.empty',
};

export interface ShownIssue {
  level: 'error' | 'warning';
  node?: string;
  text: string;
}

/** An issue in the UI language. A code without a translation keeps the server's (English) message. */
export function issueText(
  t: TFn,
  issue: ValidationIssue,
  graph: PipelineGraph,
  specs: Map<string, NodeTypeSpec>,
  language: string,
): string {
  const key = ISSUE_KEYS[issue.code];
  if (!key) return issue.message;
  const spec = specs.get(graph.nodes[issue.node || '']?.type || '');
  const param = spec?.params.find((item) => item.name === issue.param);
  const port = spec?.inputs.find((item) => item.name === issue.port);
  // The server writes "<param> <reason>"; the reason alone reads better next to the translated name.
  const detail = issue.param && issue.message.startsWith(`${issue.param} `) ? issue.message.slice(issue.param.length + 1) : issue.message;
  return t(key, {
    param: param ? pick(param.title, language) : issue.param || '',
    port: port ? pick(port.title, language) : issue.port || '',
    detail,
  });
}

/** Errors, then warnings, translated. */
export function shownIssues(
  t: TFn,
  validation: ValidationResult | null | undefined,
  graph: PipelineGraph,
  specs: Map<string, NodeTypeSpec>,
  language: string,
): ShownIssue[] {
  if (!validation) return [];
  const show = (level: ShownIssue['level']) => (issue: ValidationIssue): ShownIssue => ({
    level,
    node: issue.node,
    text: issueText(t, issue, graph, specs, language),
  });
  return [...validation.errors.map(show('error')), ...validation.warnings.map(show('warning'))];
}

/** Issue texts per node, for the canvas badges and the inspector. */
export function issuesByNode(issues: ShownIssue[]): Map<string, string[]> {
  const byNode = new Map<string, string[]>();
  for (const issue of issues) {
    if (!issue.node) continue;
    byNode.set(issue.node, [...(byNode.get(issue.node) || []), issue.text]);
  }
  return byNode;
}
