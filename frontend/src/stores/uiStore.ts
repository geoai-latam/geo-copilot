/**
 * UI state management
 *
 * Handles:
 * - Panel states (left panel, chat dock)
 * - Modal/panel visibility
 * - Visualization panel
 */
import { create } from 'zustand';
import type { ApprovalStatus } from '@/types';

// Layout types
export type LeftPanelState = 'collapsed' | 'expanded';
export type ChatDockState = 'collapsed' | 'medium' | 'expanded';
// Drawer 'database' (nuevo) muestra el schema real de la BD conectada
// (tablas / columnas / geometrías). 'data' (Discovery) busca datos externos
// vía Hub. Son experiencias distintas: BD interna vs catálogo externo.
export type DrawerId = 'layers' | 'data' | 'sentinel2' | 'database' | 'history' | 'tools' | 'connections' | 'auditoria' | null;

interface UIState {
  // Panel states
  leftPanelState: LeftPanelState;
  chatDockState: ChatDockState;

  // Left drawer (capas / datos / historial)
  activeDrawer: DrawerId;

  // Tweaks panel
  showTweaks: boolean;

  // Approval panel
  showApprovalPanel: boolean;

  /** `servidor/tool` que el panel de Conexiones pidió probar: el de herramientas la preselecciona. */
  herramientaPedida: string | null;
  probarHerramienta: (clave: string | null) => void;

  /** Px del mapa que tapa el panel de resultados a la derecha (0 = cerrado). */
  rellenoDerecho: number;
  setRellenoDerecho: (px: number) => void;
  pendingApprovals: ApprovalStatus[];

  // Actions - Panels
  toggleLeftPanel: () => void;
  setLeftPanelState: (state: LeftPanelState) => void;
  setChatDockState: (state: ChatDockState) => void;
  cycleChatDockState: () => void;

  // Actions - Drawer
  setActiveDrawer: (d: DrawerId) => void;
  toggleDrawer: (d: NonNullable<DrawerId>) => void;

  // Actions - Tweaks
  setShowTweaks: (show: boolean) => void;
  toggleTweaks: () => void;

  // Actions - Approval
  setShowApprovalPanel: (show: boolean) => void;
  setPendingApprovals: (approvals: ApprovalStatus[]) => void;
  addPendingApproval: (approval: ApprovalStatus) => void;
  removePendingApproval: (id: string) => void;
}

export const useUIStore = create<UIState>((set) => ({
  // Initial state
  leftPanelState: 'expanded',
  chatDockState: 'medium',
  activeDrawer: null,
  showTweaks: false,
  showApprovalPanel: false,
  pendingApprovals: [],
  rellenoDerecho: 0,
  herramientaPedida: null,
  setRellenoDerecho: (px) => set({ rellenoDerecho: px }),
  probarHerramienta: (clave) =>
    set((state) => ({
      herramientaPedida: clave,
      activeDrawer: clave ? 'tools' : state.activeDrawer,
    })),

  // Panel actions
  toggleLeftPanel: () =>
    set((state) => ({
      leftPanelState:
        state.leftPanelState === 'expanded' ? 'collapsed' : 'expanded',
    })),

  setLeftPanelState: (panelState) => set({ leftPanelState: panelState }),

  // Cuando el chat se EXPANDE manualmente, cerrar cualquier drawer
  // izquierdo abierto — son mutuamente exclusivos para no asfixiar al mapa.
  setChatDockState: (dockState) =>
    set((state) => ({
      chatDockState: dockState,
      activeDrawer:
        dockState === 'expanded' || dockState === 'medium'
          ? null
          : state.activeDrawer,
    })),

  cycleChatDockState: () =>
    set((state) => {
      const cycle: ChatDockState[] = ['collapsed', 'medium', 'expanded'];
      const currentIndex = cycle.indexOf(state.chatDockState);
      const nextIndex = (currentIndex + 1) % cycle.length;
      const next = cycle[nextIndex];
      return {
        chatDockState: next,
        // Si el chat sale de collapsed → cerrar drawer.
        activeDrawer:
          next === 'expanded' || next === 'medium' ? null : state.activeDrawer,
      };
    }),

  // Drawer actions — abrir un drawer COLAPSA el chat al mínimo
  // automáticamente para que el mapa siga teniendo espacio. Si el
  // usuario quiere chat de vuelta, lo expande con el handle (eso a su
  // vez cierra el drawer — política `exclusivo`).
  setActiveDrawer: (d) =>
    set((state) => ({
      activeDrawer: d,
      chatDockState: d !== null ? 'collapsed' : state.chatDockState,
    })),
  toggleDrawer: (d) =>
    set((state) => {
      const willOpen = state.activeDrawer !== d;
      return {
        activeDrawer: willOpen ? d : null,
        chatDockState: willOpen ? 'collapsed' : state.chatDockState,
      };
    }),

  // Tweaks actions
  setShowTweaks: (show) => set({ showTweaks: show }),
  toggleTweaks: () => set((s) => ({ showTweaks: !s.showTweaks })),

  // Approval actions
  setShowApprovalPanel: (show) => set({ showApprovalPanel: show }),

  setPendingApprovals: (approvals) => set({ pendingApprovals: approvals }),

  addPendingApproval: (approval) =>
    set((state) => {
      // #2 (audit 2026-06-13): dedup por approval_id. Reenvíos de WS o
      // re-ejecución de tarea no deben apilar la misma aprobación (cola
      // "2 pendientes" desincronizada que llegaba a colgar consultas).
      const exists = state.pendingApprovals.some(
        (a) => a.approval_id === approval.approval_id
      );
      return {
        pendingApprovals: exists
          ? state.pendingApprovals
          : [...state.pendingApprovals, approval],
        showApprovalPanel: true,
      };
    }),

  removePendingApproval: (id) =>
    set((state) => ({
      pendingApprovals: state.pendingApprovals.filter(
        (a) => a.approval_id !== id
      ),
    })),
}));

// Selectors
export const useLeftPanelState = () => useUIStore((s) => s.leftPanelState);
export const useChatDockState = () => useUIStore((s) => s.chatDockState);
export const useActiveDrawer = () => useUIStore((s) => s.activeDrawer);
export const useShowTweaks = () => useUIStore((s) => s.showTweaks);
export const useShowApprovalPanel = () => useUIStore((s) => s.showApprovalPanel);
export const usePendingApprovals = () => useUIStore((s) => s.pendingApprovals);
