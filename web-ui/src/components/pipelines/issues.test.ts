import { describe, expect, it } from 'vitest';
import { translate } from '@/i18n/useT';
import { issueText, issuesByNode, shownIssues } from './issues';
import { TEST_CATALOG, chainGraph } from './testCatalog';
import { specIndex } from './graphModel';

const specs = specIndex(TEST_CATALOG);

describe('validation issues', () => {
  it('are shown in the UI language with the parameter and port names the user sees', () => {
    const graph = chainGraph();
    expect(issueText(translate, { code: 'missing_param', message: 'accessions is required', node: 'dl', param: 'accessions' }, graph, specs, 'zh-CN')).toBe(
      '必须填写“登录号”',
    );
    expect(issueText(translate, { code: 'missing_input', message: 'input query is not connected', node: 'hits', port: 'query' }, graph, specs, 'zh-CN')).toBe(
      '输入“查询蛋白”还没有连接',
    );
    expect(
      issueText(
        translate,
        { code: 'invalid_param', message: 'translation_table must be at most 33', node: 'genes', param: 'translation_table' },
        graph,
        specs,
        'zh-CN',
      ),
    ).toBe('“密码子表”的值无效：must be at most 33');
  });

  it('keep the server message for a code without a translation', () => {
    expect(issueText(translate, { code: 'cycle', message: 'these nodes form a loop: a, b' }, chainGraph(), specs, 'zh-CN')).toBe(
      'these nodes form a loop: a, b',
    );
  });

  it('are grouped per node with errors before warnings', () => {
    const shown = shownIssues(
      translate,
      {
        ok: false,
        errors: [{ code: 'missing_param', message: 'accessions is required', node: 'dl', param: 'accessions' }],
        warnings: [{ code: 'unused_node', message: "nothing reads this node's output", node: 'dl' }, { code: 'empty', message: 'empty' }],
        order: [],
      },
      chainGraph(),
      specs,
      'zh-CN',
    );
    expect(shown.map((issue) => issue.level)).toEqual(['error', 'warning', 'warning']);
    expect(issuesByNode(shown).get('dl')).toEqual(['必须填写“登录号”', '还没有节点读取它的输出']);
    expect(shownIssues(translate, null, chainGraph(), specs, 'zh-CN')).toEqual([]);
  });
});
