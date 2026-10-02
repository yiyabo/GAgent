import { fireEvent,render,screen,waitFor } from '@testing-library/react';
import { describe,it,expect,vi } from 'vitest';
import { ArtifactVersionPanel } from './ArtifactVersionPanel';
const api=vi.hoisted(()=>({list:vi.fn(),preview:vi.fn(),execute:vi.fn()}));
vi.mock('@/api/artifactVersions',()=>({artifactVersionsApi:api}));
vi.mock('@/api/artifacts',()=>({buildArtifactFileUrl:()=>'/file'}));
const data={schema_version:2,manifest_revision:1,versions:[],current_artifacts:{}};
describe('artifact versions',()=>{
  it('does not dispatch when the view switches during a preview',async()=>{
    api.list.mockResolvedValue(data);let resolve:(value:unknown)=>void=()=>{};
    api.preview.mockImplementation(()=>new Promise(r=>{resolve=r;}));api.execute.mockClear();
    const view=render(<ArtifactVersionPanel planId={1} sessionId="one"/>);
    await waitFor(()=>expect(screen.getByText('更新相关结果')).toBeTruthy());
    fireEvent.click(screen.getByText('更新相关结果'));
    view.rerender(<ArtifactVersionPanel planId={2} sessionId="two"/>);
    resolve({ordered_task_ids:[1],blocked_task_ids:[],preview_fingerprint:'old'});
    await waitFor(()=>expect(api.execute).not.toHaveBeenCalled());
  });
  it('does not execute a no-change preview',async()=>{
    api.list.mockResolvedValue(data);api.preview.mockResolvedValue({ordered_task_ids:[],blocked_task_ids:[]});api.execute.mockClear();
    render(<ArtifactVersionPanel planId={1} sessionId="one"/>);
    await waitFor(()=>expect(screen.getByText('更新相关结果')).toBeTruthy());
    fireEvent.click(screen.getByText('更新相关结果'));
    await waitFor(()=>expect(screen.getByText('当前结果无需更新。')).toBeTruthy());
    expect(api.execute).not.toHaveBeenCalled();
  });
});
