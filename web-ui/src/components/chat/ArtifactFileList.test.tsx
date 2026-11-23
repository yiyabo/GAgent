import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useLayoutStore } from '@store/layout';
import ArtifactFileList from './ArtifactFileList';

afterEach(() => {
  cleanup();
  useLayoutStore.setState({ artifactFocusRequest: null });
});

describe('ArtifactFileList', () => {
  it('renders published deliverables as cards that focus the file in the 产物 panel', () => {
    useLayoutStore.setState({ planPanelVisible: false });
    render(
      <ArtifactFileList
        sessionId="session-1"
        items={[{ path: 'image_tabular/ORF_all.xlsx', origin: 'deliverable', mime_family: 'file' }]}
      />,
    );
    expect(screen.getAllByTestId('artifact-file-card')).toHaveLength(1);
    expect(screen.getByText('产物 › 交付物')).toBeTruthy();
    const download = screen.getByLabelText('下载 ORF_all.xlsx');
    expect(download.getAttribute('href')).toContain('image_tabular');

    fireEvent.click(screen.getByText('ORF_all.xlsx'));
    const state = useLayoutStore.getState();
    expect(state.planPanelVisible).toBe(true);
    expect(state.artifactFocusRequest).toMatchObject({ sessionId: 'session-1', path: 'image_tabular/ORF_all.xlsx' });
  });

  it('renders nothing without file items', () => {
    const { container } = render(<ArtifactFileList sessionId="session-1" items={[]} />);
    expect(container.innerHTML).toBe('');
  });
});
