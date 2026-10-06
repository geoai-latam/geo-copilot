/**
 * Store exports
 *
 * This module exports all individual stores for use in components.
 * Each store is specialized for a specific domain:
 * - sessionStore: Session and connection management
 * - mapStore: Map layers, base maps, and view state
 * - uiStore: Panel states, approvals, and visualizations
 * - chatStore: Messages, loading states, and history
 *
 * Usage:
 * - Use selectors for reactive values: const sessionId = useSessionId()
 * - Use store actions directly: useSessionStore((s) => s.setSessionId)
 */

// Session store
export {
  useSessionStore,
  useSessionId,
  useSessions,
  useIsConnected,
  usePortalConnected,
  useBdConnected,
} from './sessionStore';

// Map store
export {
  useMapStore,
  useLayers,
  useBaseMapId,
  useMapCenter,
  useMapZoom,
  useSelectedFeature,
  useFlyToLayerId,
  LAYER_COLORS,
  BASE_MAPS,
  esVectorial,
  esRaster,
  EMPTY_FC,
  type MapLayer,
  type RendererKind,
  type LayerTiles,
  type RasterLegend,
  type OrigenCapa,
  type PuntoMarcado,
  type BaseMapId,
  type BaseMapConfig,
} from './mapStore';

// UI store
export {
  useUIStore,
  useLeftPanelState,
  useChatDockState,
  useActiveDrawer,
  useShowTweaks,
  useShowApprovalPanel,
  usePendingApprovals,
  type LeftPanelState,
  type ChatDockState,
  type DrawerId,
} from './uiStore';

// Chat store
export {
  useChatStore,
  useMessages,
  useIsLoading,
  useChatStatus,
  useRetryInfo,
  useQueryHistory,
  type ChatStatus,
  type RetryInfo,
  type TurnoRemoto,
} from './chatStore';
