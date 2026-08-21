import { create } from 'zustand';
import { api, tokens } from '@/api/client';
import type { User } from '@/api/types';

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string | null;
  bootstrap: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (email: string, username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: true,
  error: null,

  bootstrap: async () => {
    if (!tokens.access()) {
      set({ loading: false, user: null });
      return;
    }
    try {
      set({ user: await api.me(), loading: false, error: null });
    } catch {
      tokens.clear();
      set({ user: null, loading: false });
    }
  },

  login: async (username, password) => {
    set({ error: null });
    try {
      tokens.set(await api.login({ username, password }));
      set({ user: await api.me() });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'login failed' });
      throw error;
    }
  },

  register: async (email, username, password) => {
    set({ error: null });
    try {
      await api.register({ email, username, password });
      tokens.set(await api.login({ username, password }));
      set({ user: await api.me() });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'registration failed' });
      throw error;
    }
  },

  logout: () => {
    tokens.clear();
    set({ user: null });
  },
}));
