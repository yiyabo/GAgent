import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Extensions from './Extensions';
import { extensionsApi } from '@api/extensions';
import type { McpListResponse, McpServer, SkillEntry, SkillListResponse } from '@api/extensions';

vi.mock('@api/extensions', () => ({
  extensionsApi: {
    listMcp: vi.fn(),
    enableMcp: vi.fn(),
    disableMcp: vi.fn(),
    listSkills: vi.fn(),
    enableSkill: vi.fn(),
    disableSkill: vi.fn(),
  },
}));

const api = vi.mocked(extensionsApi);

// @testing-library/user-event is not a project dependency; this covers the few gestures used here.
const userEvent = {
  setup: () => ({
    click: async (el: Element) => { fireEvent.click(el); },
    hover: async (el: Element) => { fireEvent.mouseEnter(el); fireEvent.mouseOver(el); },
    type: async (el: Element, text: string) => { fireEvent.change(el, { target: { value: text } }); },
    clear: async (el: Element) => { fireEvent.change(el, { target: { value: '' } }); },
  }),
};

function server(overrides: Partial<McpServer> = {}): McpServer {
  return {
    id: 'blast',
    title: 'BLAST Search',
    description: 'Sequence similarity search',
    category: 'bioinformatics',
    homepage: 'https://example.org/blast',
    license: 'MIT',
    version: '1.2.3',
    source: { repo: 'https://github.com/example/blast-mcp', commit: 'abcdef1234567890', package: 'blast-mcp' },
    verification: {
      status: 'verified',
      reviewed_at: '2026-10-01',
      reviewer: 'bot',
      human_signoff: true,
      checks: [
        { name: 'pinned-commit', passed: true, detail: 'commit is pinned' },
        { name: 'egress-allowlist', passed: false, detail: 'wildcard egress found' },
      ],
    },
    isolation: 'container',
    egress: ['eutils.ncbi.nlm.nih.gov'],
    tools: [
      { name: 'blast_search', read_only: true },
      { name: 'save_result', read_only: false },
    ],
    state: 'disabled',
    connected: false,
    error: null,
    live_tools: [],
    unavailable_reason: null,
    ...overrides,
  };
}

function mcp(overrides: Partial<McpListResponse> = {}): McpListResponse {
  return {
    configured: true,
    can_manage: true,
    servers: [server()],
    custom: [],
    ...overrides,
  };
}

function skill(overrides: Partial<SkillEntry> = {}): SkillEntry {
  return {
    name: 'seq-helper',
    description: 'Helps with sequences',
    origin: {
      kind: 'third_party',
      source: 'https://github.com/example/skills',
      license: 'Apache-2.0',
      commit: 'deadbeef',
      modified: true,
      modifications: ['removed network call'],
    },
    review: { scanner: 'warnings', reviewed_by: 'bot', reviewed_at: '2026-10-02', human_signoff: false, notes: '' },
    findings: [
      { severity: 'warn', rule: 'shell-exec', file: 'scripts/run.sh', line: 12, detail: 'runs a shell command' },
      { severity: 'info', rule: 'big-file', file: 'SKILL.md', line: null, detail: 'large file' },
    ],
    enabled: false,
    size_chars: 1234,
    scripts: 2,
    references: 1,
    ...overrides,
  };
}

function skills(overrides: Partial<SkillListResponse> = {}): SkillListResponse {
  return { can_manage: true, skills: [skill()], ...overrides };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AntdApp>
        <Extensions />
      </AntdApp>
    </QueryClientProvider>,
  );
}

async function openSkillsTab() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('tab', { name: /技能/ }));
  return user;
}

describe('Extensions page - MCP tab', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.listSkills.mockResolvedValue(skills());
  });

  it('renders a server with verification, isolation, egress, tools and source', async () => {
    api.listMcp.mockResolvedValue(mcp());
    renderPage();

    const card = await screen.findByTestId('mcp-server-blast');
    expect(within(card).getByText('BLAST Search')).toBeInTheDocument();
    expect(within(card).getByText('bioinformatics')).toBeInTheDocument();
    expect(within(card).getByText('已验证')).toBeInTheDocument();
    expect(within(card).getByText('人工已确认')).toBeInTheDocument();
    expect(within(card).getByText('容器隔离')).toBeInTheDocument();
    expect(within(card).getByText('eutils.ncbi.nlm.nih.gov')).toBeInTheDocument();
    expect(within(card).getByText('blast_search · 只读')).toBeInTheDocument();
    expect(within(card).getByText('save_result · 有副作用')).toBeInTheDocument();
    expect(within(card).getByText(/v1\.2\.3/)).toBeInTheDocument();
    expect(within(card).getByText('abcdef1234')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'https://github.com/example/blast-mcp' })).toHaveAttribute(
      'href',
      'https://github.com/example/blast-mcp',
    );
  });

  it('shows the honest pending-signoff marker and host / remote / no-network variants', async () => {
    api.listMcp.mockResolvedValue(
      mcp({
        servers: [
          server({
            id: 'hosty',
            title: 'Host One',
            isolation: 'host',
            egress: [],
            verification: { ...server().verification, human_signoff: false },
          }),
          server({ id: 'remo', title: 'Remote One', isolation: 'remote', verification: { ...server().verification, status: 'unverified' } }),
        ],
      }),
    );
    renderPage();

    const host = await screen.findByTestId('mcp-server-hosty');
    expect(within(host).getByText('已验证')).toBeInTheDocument();
    expect(within(host).getByText('待人工确认')).toBeInTheDocument();
    expect(within(host).getByText('本机进程 — 未隔离')).toBeInTheDocument();
    expect(within(host).getByText('无网络')).toBeInTheDocument();

    const remote = screen.getByTestId('mcp-server-remo');
    expect(within(remote).getByText('远程服务')).toBeInTheDocument();
    expect(within(remote).queryByText('已验证')).not.toBeInTheDocument();
    expect(within(remote).getByText('未验证')).toBeInTheDocument();
  });

  it('expands verification checks with pass/fail details', async () => {
    api.listMcp.mockResolvedValue(mcp());
    const user = userEvent.setup();
    renderPage();

    const card = await screen.findByTestId('mcp-server-blast');
    await user.click(within(card).getByText(/验证检查（1\/2 通过）/));
    expect(await within(card).findByText(/commit is pinned/)).toBeInTheDocument();
    expect(within(card).getByText(/wildcard egress found/)).toBeInTheDocument();
    expect(within(card).getByLabelText('通过')).toBeInTheDocument();
    expect(within(card).getByLabelText('未通过')).toBeInTheDocument();
  });

  it('enables a server and renders state from the response, not optimistically', async () => {
    let resolve!: (value: McpServer) => void;
    api.listMcp.mockResolvedValue(mcp());
    api.enableMcp.mockReturnValue(new Promise<McpServer>((r) => { resolve = r; }));
    const user = userEvent.setup();
    renderPage();

    const card = await screen.findByTestId('mcp-server-blast');
    const toggle = within(card).getByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await user.click(toggle);

    await waitFor(() => expect(api.enableMcp).toHaveBeenCalledWith('blast'));
    await waitFor(() => expect(within(card).getByRole('switch')).toBeDisabled());
    expect(within(card).getByRole('switch')).toHaveAttribute('aria-checked', 'false');

    resolve(server({ state: 'enabled', connected: true, live_tools: ['blast_search'] }));
    await waitFor(() => expect(within(card).getByRole('switch')).toHaveAttribute('aria-checked', 'true'));
    expect(within(card).getByRole('switch')).not.toBeDisabled();
    expect(within(card).getByText('已连接')).toBeInTheDocument();
    expect(api.listMcp).toHaveBeenCalledTimes(1);
  });

  it('disables an enabled server', async () => {
    api.listMcp.mockResolvedValue(mcp({ servers: [server({ state: 'enabled', connected: true })] }));
    api.disableMcp.mockResolvedValue(server({ state: 'disabled' }));
    const user = userEvent.setup();
    renderPage();

    const card = await screen.findByTestId('mcp-server-blast');
    await user.click(within(card).getByRole('switch'));
    await waitFor(() => expect(api.disableMcp).toHaveBeenCalledWith('blast'));
    await waitFor(() => expect(within(card).getByRole('switch')).toHaveAttribute('aria-checked', 'false'));
  });

  it('surfaces the backend detail when enabling fails and leaves the switch unchanged', async () => {
    api.listMcp.mockResolvedValue(mcp());
    api.enableMcp.mockRejectedValue(new Error('docker image not built'));
    const user = userEvent.setup();
    renderPage();

    const card = await screen.findByTestId('mcp-server-blast');
    await user.click(within(card).getByRole('switch'));
    expect(await screen.findByText('docker image not built')).toBeInTheDocument();
    await waitFor(() => expect(within(card).getByRole('switch')).not.toBeDisabled());
    expect(within(card).getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('disables the switch for non-admins and does not call the API', async () => {
    api.listMcp.mockResolvedValue(mcp({ can_manage: false }));
    const user = userEvent.setup();
    renderPage();

    const toggle = within(await screen.findByTestId('mcp-server-blast')).getByRole('switch');
    expect(toggle).toBeDisabled();
    await user.click(toggle);
    expect(api.enableMcp).not.toHaveBeenCalled();
  });

  it('shows a banner and disables switches when the server is not configured', async () => {
    api.listMcp.mockResolvedValue(mcp({ configured: false }));
    renderPage();

    expect(await screen.findByText('服务端未配置 MCP 挂载文件')).toBeInTheDocument();
    expect(within(screen.getByTestId('mcp-server-blast')).getByRole('switch')).toBeDisabled();
  });

  it('disables the switch for unavailable servers and shows the reason', async () => {
    api.listMcp.mockResolvedValue(
      mcp({ servers: [server({ state: 'unavailable', unavailable_reason: 'docker is not installed' })] }),
    );
    renderPage();

    const card = await screen.findByTestId('mcp-server-blast');
    expect(within(card).getByRole('switch')).toBeDisabled();
    expect(within(card).getByText(/docker is not installed/)).toBeInTheDocument();
  });

  it('shows the error text for a server in error state', async () => {
    api.listMcp.mockResolvedValue(mcp({ servers: [server({ state: 'error', error: 'spawn failed: ENOENT' })] }));
    renderPage();

    const card = await screen.findByTestId('mcp-server-blast');
    expect(within(card).getByText('spawn failed: ENOENT')).toBeInTheDocument();
    expect(within(card).getByText('出错')).toBeInTheDocument();
  });

  it('filters by search text and category', async () => {
    api.listMcp.mockResolvedValue(
      mcp({
        servers: [
          server(),
          server({ id: 'fetcher', title: 'Web Fetcher', category: 'web', description: 'Fetch pages', tools: [] }),
        ],
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId('mcp-server-blast');
    expect(screen.getByTestId('mcp-server-fetcher')).toBeInTheDocument();

    await user.type(screen.getByLabelText('搜索 MCP 服务器'), 'fetch');
    expect(screen.queryByTestId('mcp-server-blast')).not.toBeInTheDocument();
    expect(screen.getByTestId('mcp-server-fetcher')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('搜索 MCP 服务器'));
    await user.type(screen.getByLabelText('搜索 MCP 服务器'), 'zzz-nothing');
    expect(await screen.findByText('没有符合筛选条件的 MCP 服务器')).toBeInTheDocument();
  });

  it('lists custom entries read-only', async () => {
    api.listMcp.mockResolvedValue(
      mcp({
        custom: [{ name: 'my-local', transport: 'stdio', isolation: 'host', connected: true, error: null, tools: ['ping'] }],
      }),
    );
    renderPage();

    const custom = await screen.findByTestId('mcp-custom');
    expect(within(custom).getByText('my-local')).toBeInTheDocument();
    expect(within(custom).getByText('本机进程 — 未隔离')).toBeInTheDocument();
    expect(within(custom).queryByRole('switch')).not.toBeInTheDocument();
  });

  it('shows an empty state when there are no servers', async () => {
    api.listMcp.mockResolvedValue(mcp({ servers: [] }));
    renderPage();
    expect(await screen.findByText('暂无可用的 MCP 服务器')).toBeInTheDocument();
  });

  it('shows an error with a working retry button', async () => {
    api.listMcp.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(mcp());
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText('无法加载 MCP 服务器列表')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /重\s?试/ }));
    expect(await screen.findByTestId('mcp-server-blast')).toBeInTheDocument();
  });
});

describe('Extensions page - Skills tab', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.listMcp.mockResolvedValue(mcp());
  });

  it('renders the skill row with origin, scanner, signoff and counts', async () => {
    api.listSkills.mockResolvedValue(skills());
    renderPage();
    await openSkillsTab();

    expect(await screen.findByText('seq-helper')).toBeInTheDocument();
    expect(screen.getByText('Helps with sequences')).toBeInTheDocument();
    expect(screen.getByText('第三方')).toBeInTheDocument();
    expect(screen.getByText('Apache-2.0', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('有警告')).toBeInTheDocument();
    expect(screen.getByText('待人工确认')).toBeInTheDocument();
    expect(screen.getByText(/1234 字符 · 2 脚本 · 1 引用/)).toBeInTheDocument();
  });

  it('shows modifications in a popover for modified skills', async () => {
    api.listSkills.mockResolvedValue(skills());
    renderPage();
    const user = await openSkillsTab();

    const tag = await screen.findByText('已修改');
    await user.hover(tag);
    expect(await screen.findByText('removed network call')).toBeInTheDocument();
  });

  it('expands a row to show findings with file:line', async () => {
    api.listSkills.mockResolvedValue(skills());
    renderPage();
    const user = await openSkillsTab();

    await screen.findByText('seq-helper');
    await user.click(screen.getByRole('button', { name: /展开行|Expand row/i }));
    expect(await screen.findByText('shell-exec')).toBeInTheDocument();
    expect(screen.getByText('scripts/run.sh:12')).toBeInTheDocument();
    expect(screen.getByText('runs a shell command')).toBeInTheDocument();
    expect(screen.getByLabelText('严重程度：警告')).toBeInTheDocument();
    expect(screen.getByLabelText('严重程度：提示')).toBeInTheDocument();
    expect(screen.getByText('SKILL.md')).toBeInTheDocument();
  });

  it('enables a skill and updates from the response', async () => {
    api.listSkills.mockResolvedValue(skills());
    api.enableSkill.mockResolvedValue(skill({ enabled: true }));
    renderPage();
    const user = await openSkillsTab();

    await screen.findByText('seq-helper');
    const toggle = screen.getByRole('switch', { name: /seq-helper/ });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await user.click(toggle);
    await waitFor(() => expect(api.enableSkill).toHaveBeenCalledWith('seq-helper'));
    await waitFor(() => expect(screen.getByRole('switch', { name: /seq-helper/ })).toHaveAttribute('aria-checked', 'true'));
  });

  it('shows backend detail when skill toggle fails', async () => {
    api.listSkills.mockResolvedValue(skills());
    api.enableSkill.mockRejectedValue(new Error('scanner blocked this skill'));
    renderPage();
    const user = await openSkillsTab();

    await screen.findByText('seq-helper');
    await user.click(screen.getByRole('switch', { name: /seq-helper/ }));
    expect(await screen.findByText('scanner blocked this skill')).toBeInTheDocument();
  });

  it('disables switches for non-admins', async () => {
    api.listSkills.mockResolvedValue(skills({ can_manage: false }));
    renderPage();
    await openSkillsTab();

    await screen.findByText('seq-helper');
    expect(screen.getByRole('switch', { name: /seq-helper/ })).toBeDisabled();
  });

  it('filters by plain text and shows an empty-filter state', async () => {
    api.listSkills.mockResolvedValue(
      skills({ skills: [skill(), skill({ name: 'other-skill', description: 'Something else' })] }),
    );
    renderPage();
    const user = await openSkillsTab();

    await screen.findByText('seq-helper');
    await user.type(screen.getByLabelText('过滤技能'), 'other');
    expect(screen.queryByText('seq-helper')).not.toBeInTheDocument();
    expect(screen.getByText('other-skill')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('过滤技能'));
    await user.type(screen.getByLabelText('过滤技能'), 'zzzz');
    expect(await screen.findByText('没有符合过滤条件的技能')).toBeInTheDocument();
  });

  it('shows an empty state when there are no skills', async () => {
    api.listSkills.mockResolvedValue(skills({ skills: [] }));
    renderPage();
    await openSkillsTab();
    expect(await screen.findByText('暂无技能')).toBeInTheDocument();
  });

  it('shows an error with retry', async () => {
    api.listSkills.mockRejectedValueOnce(new Error('skills down')).mockResolvedValue(skills());
    renderPage();
    const user = await openSkillsTab();

    expect(await screen.findByText('无法加载技能列表')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: /重\s?试/ })[0]);
    expect(await screen.findByText('seq-helper')).toBeInTheDocument();
  });
});
