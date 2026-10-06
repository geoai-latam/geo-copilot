/**
 * Chat state management
 *
 * Handles:
 * - Chat messages
 * - Loading/processing state
 * - Retry information
 * - Query history
 */
import { create } from 'zustand';
import type { ChatMessage } from '@/types';

/**
 * Estado que el chip de la cabecera del chat muestra al usuario.
 *
 * Auditoría 2026-09-08 §5 (2): `setChatStatus` no tenía UN SOLO llamante — el
 * chip decía "Listo" durante toda la consulta, incluso con el panel de
 * aprobación abierto esperando al humano. Ahora lo mueven los tres puntos que
 * el CLIENTE conoce de primera mano, sin inventar granularidad que el backend
 * no manda: `lib/runQuery.ts` (envío / fin / fallo), `App.tsx` (llega un
 * `approval_request` por WS) y `ApprovalPanel` (se resolvió la aprobación y la
 * petición HTTP sigue viva).
 *
 * Deliberadamente NO hay estados por agente ("consultando la BD", "generando
 * SQL"): esos vendrían de los eventos `step_started`/`step_completed`, que
 * solo emite un camino del orquestador apagado por defecto.
 */
export type ChatStatus = 'ready' | 'searching' | 'waiting_approval' | 'error';

// Retry/correction status for autonomous mode
export interface RetryInfo {
  show: boolean;
  agent: string;
  status: 'retrying' | 'correcting' | 'success' | 'failed';
  attempt: number;
  maxAttempts: number;
  error?: string;
  action?: string;
  message?: string;
}

/**
 * F7 (auditoría): un turno que corre en el servidor SIN una petición HTTP de esta pestaña — el
 * reanudado tras un reinicio, o el que se aprobó tras recargar. Su resultado llega por WebSocket.
 * `id` es su `turno_id` (null si el aviso no lo traía); `bloquea` dice si fue él quien puso
 * `isLoading` (si ya había una consulta HTTP en vuelo, el chip y el «Detener» son de esa).
 */
export interface TurnoRemoto {
  id: string | null;
  bloquea: boolean;
}

interface QueryHistoryItem {
  query: string;
  timestamp: Date;
  success: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  isLoading: boolean;
  chatStatus: ChatStatus;
  retryInfo: RetryInfo | null;
  queryHistory: QueryHistoryItem[];
  turnoRemoto: TurnoRemoto | null;
  /** Aprobaciones que el servidor ya dijo que no retoman ningún turno (rechazo, turno perdido). */
  aprobacionesSinTurno: string[];
  /**
   * F7 (auditoría): turno_id → id del mensaje del asistente de la consulta HTTP de esta pestaña que
   * lo arrancó. Es lo que liga un aviso o un resultado que llega por WebSocket a SU mensaje (el
   * texto de la consulta no es único: el usuario la reenvía igual tras un error).
   */
  mensajeDeTurno: Record<string, string>;
  /** Turnos cuya consulta HTTP de esta pestaña se cortó (Detener, 504…) sin su respuesta. */
  turnosCortados: string[];
  /** Turnos cuyo resultado (o fallo) ya llegó por WebSocket: no hay nada que seguir. */
  turnosTerminados: string[];

  // Actions - Messages
  addMessage: (message: ChatMessage) => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  clearMessages: () => void;

  // Actions - Status
  setLoading: (loading: boolean) => void;
  setChatStatus: (status: ChatStatus) => void;
  setTurnoRemoto: (turno: TurnoRemoto | null) => void;
  marcarAprobacionSinTurno: (approvalId: string) => void;
  vincularMensajeATurno: (turnoId: string, messageId: string) => void;
  marcarTurnoCortado: (turnoId: string) => void;
  marcarTurnoTerminado: (turnoId: string) => void;

  // Actions - Retry
  setRetryInfo: (info: RetryInfo | null) => void;
  clearRetryInfo: () => void;

  // Actions - History
  addQueryToHistory: (query: string, success: boolean) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  // Initial state
  messages: [],
  isLoading: false,
  chatStatus: 'ready',
  retryInfo: null,
  queryHistory: [],
  turnoRemoto: null,
  aprobacionesSinTurno: [],
  mensajeDeTurno: {},
  turnosCortados: [],
  turnosTerminados: [],

  // Message actions
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),

  updateMessage: (id, updates) =>
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.id === id ? { ...msg, ...updates } : msg
      ),
    })),

  clearMessages: () => set({ messages: [] }),

  // Status actions
  setLoading: (loading) => set({ isLoading: loading }),
  setChatStatus: (status) => set({ chatStatus: status }),
  setTurnoRemoto: (turno) => set({ turnoRemoto: turno }),
  marcarAprobacionSinTurno: (approvalId) =>
    set((state) => ({ aprobacionesSinTurno: [...state.aprobacionesSinTurno.slice(-19), approvalId] })),
  vincularMensajeATurno: (turnoId, messageId) =>
    set((state) => ({
      // acotado como las listas de al lado: los 20 últimos turnos
      mensajeDeTurno: Object.fromEntries([
        ...Object.entries(state.mensajeDeTurno).filter(([t]) => t !== turnoId).slice(-19),
        [turnoId, messageId],
      ]),
    })),
  marcarTurnoCortado: (turnoId) =>
    set((state) => ({ turnosCortados: [...state.turnosCortados.slice(-19), turnoId] })),
  marcarTurnoTerminado: (turnoId) =>
    set((state) => ({ turnosTerminados: [...state.turnosTerminados.slice(-19), turnoId] })),

  // Retry actions
  setRetryInfo: (info) => set({ retryInfo: info }),
  clearRetryInfo: () => set({ retryInfo: null }),

  // History actions
  addQueryToHistory: (query, success) =>
    set((state) => ({
      queryHistory: [
        { query, timestamp: new Date(), success },
        ...state.queryHistory.slice(0, 19), // Keep last 20
      ],
    })),
}));

// Selectors
export const useMessages = () => useChatStore((s) => s.messages);
export const useIsLoading = () => useChatStore((s) => s.isLoading);
export const useChatStatus = () => useChatStore((s) => s.chatStatus);
export const useRetryInfo = () => useChatStore((s) => s.retryInfo);
export const useQueryHistory = () => useChatStore((s) => s.queryHistory);
