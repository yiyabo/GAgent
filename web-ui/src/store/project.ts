import { create } from 'zustand';
import type { ProjectData } from '@api/project';

interface ProjectState {
  projectData: ProjectData | null;
  loading: boolean;
  error: string | null;
  setProjectData: (data: ProjectData | null) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  clearProject: () => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  projectData: null,
  loading: false,
  error: null,
  setProjectData: (data) => set({ projectData: data }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  clearProject: () => set({ projectData: null, loading: false, error: null }),
}));
