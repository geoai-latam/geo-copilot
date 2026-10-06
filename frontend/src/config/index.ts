/**
 * Configuración centralizada del frontend.
 *
 * Usa variables de entorno cuando estén disponibles.
 * Esto permite configurar la app sin recompilar.
 */

interface Config {
  api: {
    basePath: string;
    timeout: number;
  };
  map: {
    defaultCenter: [number, number];
    defaultZoom: number;
    animationDuration: number;
  };
  websocket: {
    maxReconnectAttempts: number;
    reconnectDelay: number;
    maxReconnectDelay: number;
  };
  ui: {
    maxQueryHistoryItems: number;
    truncateLayerNameAt: number;
    retryHideDelaySuccess: number;
    retryHideDelayFailure: number;
  };
}

export const config: Config = {
  api: {
    basePath: import.meta.env.VITE_API_BASE || '/api/v1',
    timeout: Number(import.meta.env.VITE_API_TIMEOUT) || 30000,
  },
  map: {
    defaultCenter: [
      Number(import.meta.env.VITE_MAP_CENTER_LON) || -74.0721,
      Number(import.meta.env.VITE_MAP_CENTER_LAT) || 4.7110,
    ],
    defaultZoom: Number(import.meta.env.VITE_MAP_ZOOM) || 100000,
    animationDuration: 1.5,
  },
  websocket: {
    maxReconnectAttempts: 5,
    reconnectDelay: 1000,
    maxReconnectDelay: 30000,
  },
  ui: {
    maxQueryHistoryItems: 20,
    truncateLayerNameAt: 30,
    retryHideDelaySuccess: 3000,
    retryHideDelayFailure: 5000,
  },
};

export default config;
