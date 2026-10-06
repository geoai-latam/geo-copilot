/**
 * Session state management
 *
 * Handles:
 * - Current session ID
 * - Session list
 * - Connection status
 */
import { create } from 'zustand';
import type { Session } from '@/types';

interface SessionState {
  sessionId: string | null;
  sessions: Session[];
  isConnected: boolean;
  portalConnected: boolean;
  bdConnected: boolean;

  // Actions
  setSessionId: (id: string | null) => void;
  setSessions: (sessions: Session[]) => void;
  setConnected: (connected: boolean) => void;
  setPortalConnected: (connected: boolean) => void;
  setBdConnected: (connected: boolean) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  // Initial state
  sessionId: null,
  sessions: [],
  isConnected: false,
  portalConnected: false,
  bdConnected: false,

  // Actions
  setSessionId: (id) => set({ sessionId: id }),
  setSessions: (sessions) => set({ sessions }),
  setConnected: (connected) => set({ isConnected: connected }),
  setPortalConnected: (connected) => set({ portalConnected: connected }),
  setBdConnected: (connected) => set({ bdConnected: connected }),
}));

// Selectors
export const useSessionId = () => useSessionStore((s) => s.sessionId);
export const useSessions = () => useSessionStore((s) => s.sessions);
export const useIsConnected = () => useSessionStore((s) => s.isConnected);
export const usePortalConnected = () => useSessionStore((s) => s.portalConnected);
export const useBdConnected = () => useSessionStore((s) => s.bdConnected);
