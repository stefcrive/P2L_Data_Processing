import { create } from "zustand";

type SessionStore = {
  sessionId: string | null;
  setSessionId: (sessionId: string | null) => void;
};

export const useSessionStore = create<SessionStore>((set) => ({
  sessionId: null,
  setSessionId: (sessionId) => set({ sessionId }),
}));
