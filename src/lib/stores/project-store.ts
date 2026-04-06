// ClipForge — Project Store
// Zustand store for project and UI state management

import { create } from "zustand";
import type { Project, Clip, Job, ProjectMetadata, SystemInfo } from "@/types";

interface ProjectStore {
  // Data
  projects: Project[];
  currentProject: Project | null;
  currentMetadata: ProjectMetadata | null;
  clips: Clip[];
  jobs: Job[];
  systemInfo: SystemInfo | null;
  workerOnline: boolean;

  // UI State
  newProjectUrl: string;
  isCreating: boolean;

  // Actions
  setProjects: (projects: Project[]) => void;
  setCurrentProject: (project: Project | null) => void;
  setCurrentMetadata: (metadata: ProjectMetadata | null) => void;
  setClips: (clips: Clip[]) => void;
  setJobs: (jobs: Job[]) => void;
  setSystemInfo: (info: SystemInfo | null) => void;
  setWorkerOnline: (online: boolean) => void;
  setNewProjectUrl: (url: string) => void;
  setIsCreating: (creating: boolean) => void;

  updateProject: (id: string, updates: Partial<Project>) => void;
  updateClip: (id: string, updates: Partial<Clip>) => void;
  updateJob: (id: string, updates: Partial<Job>) => void;
}

export const useProjectStore = create<ProjectStore>((set) => ({
  projects: [],
  currentProject: null,
  currentMetadata: null,
  clips: [],
  jobs: [],
  systemInfo: null,
  workerOnline: false,

  newProjectUrl: "",
  isCreating: false,

  setProjects: (projects) => set({ projects }),
  setCurrentProject: (project) => set({ currentProject: project }),
  setCurrentMetadata: (metadata) => set({ currentMetadata: metadata }),
  setClips: (clips) => set({ clips }),
  setJobs: (jobs) => set({ jobs }),
  setSystemInfo: (info) => set({ systemInfo: info }),
  setWorkerOnline: (online) => set({ workerOnline: online }),
  setNewProjectUrl: (url) => set({ newProjectUrl: url }),
  setIsCreating: (creating) => set({ isCreating: creating }),

  updateProject: (id, updates) =>
    set((state) => ({
      projects: state.projects.map((p) => (p.id === id ? { ...p, ...updates } : p)),
      currentProject:
        state.currentProject?.id === id
          ? { ...state.currentProject, ...updates }
          : state.currentProject,
    })),

  updateClip: (id, updates) =>
    set((state) => ({
      clips: state.clips.map((c) => (c.id === id ? { ...c, ...updates } : c)),
    })),

  updateJob: (id, updates) =>
    set((state) => ({
      jobs: state.jobs.map((j) => (j.id === id ? { ...j, ...updates } : j)),
    })),
}));
