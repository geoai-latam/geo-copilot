/**
 * WebSocket Service for real-time updates
 */
import type { WSMessage } from '@/types';
import { logger } from '@/utils/logger';
import { cabecerasAuth } from '@/lib/auth';

/**
 * F6: un ticket de un solo uso para abrir el socket de ESTA sesión. El navegador no puede
 * poner cabeceras en un WebSocket y el token en la URL quedaría en los logs; el ticket vale
 * unos segundos y una sola apertura. Sin ticket (backend sin autenticación) se abre igual.
 */
export async function pedirTicketWs(sessionId: string, fetchImpl: typeof fetch = fetch): Promise<string | null> {
  try {
    const r = await fetchImpl(`/api/v1/session/${encodeURIComponent(sessionId)}/ws-ticket`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...cabecerasAuth() },
    });
    if (!r.ok) return null;
    const d = (await r.json()) as { ticket?: string };
    return d.ticket ?? null;
  } catch {
    return null;
  }
}

type MessageHandler = (message: WSMessage) => void;
type ConnectionHandler = () => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  /** La sesión del socket `ws` (puede no ser `sessionId` mientras se cambia de sesión). */
  private socketDe: string | null = null;
  private sessionId: string | null = null;
  // F7 (auditoría): una reconexión en curso — verificando la sesión (`reintentar`) o pidiendo el
  // ticket (`abrir`). En esas esperas `ws` sigue siendo el socket CERRADO: sin estas marcas, un
  // `online` seguido de un `visibilitychange` (despertar el portátil) abría DOS sockets.
  private reintentando = false;
  private abriendoPara: string | null = null;
  private messageHandlers: Set<MessageHandler> = new Set();
  private connectHandlers: Set<ConnectionHandler> = new Set();
  private disconnectHandlers: Set<ConnectionHandler> = new Set();
  private reconnectAttempts = 0;
  // F7 (auditoría): sin tope de intentos. Con 5 (≈31 s) un despliegue con migraciones dejaba la
  // pestaña desconectada para siempre: la aprobación pendiente no reaparecía y el resultado del
  // turno retomado no llegaba. Ahora el intervalo crece hasta `maxReconnectDelay` y sigue.
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  // FE1: id del timer de reconexión. Antes no se guardaba, así que un
  // disconnect() no podía cancelarlo y el timer pendiente disparaba un
  // connect() extra (segundo socket / carrera) tras reconectar.
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  // S0.3: el backend ya no recrea una sesión desconocida al abrir el socket
  // (era un IDOR). Si el servidor se reinició y perdió la sesión, reconectar
  // con el mismo id falla siempre. Antes de cada reintento se consulta esta
  // función: devuelve el id con el que reconectar, o null si el dueño del
  // servicio (App) se encarga de reconectar con una sesión nueva.
  private sessionRecovery: ((sessionId: string) => Promise<string | null>) | null = null;

  constructor() {
    // Al volver la red o la pestaña no se espera al siguiente intervalo: se reintenta ya.
    if (typeof window !== 'undefined') {
      window.addEventListener('online', () => this.reconectarAhora());
    }
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') this.reconectarAhora();
      });
    }
  }

  setSessionRecovery(fn: ((sessionId: string) => Promise<string | null>) | null): void {
    this.sessionRecovery = fn;
  }

  /** Reintenta en el acto si hay sesión y no hay socket abierto ni abriéndose. */
  reconectarAhora(): void {
    if (!this.sessionId) return;
    const estado = this.ws?.readyState;
    if (estado === WebSocket.OPEN || estado === WebSocket.CONNECTING) return;
    if (this.reintentando || this.abriendoPara === this.sessionId) return;
    this.clearReconnectTimer();
    this.reconnectAttempts = 0;
    void this.reintentar();
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  connect(sessionId: string): void {
    // Cualquier reconexión pendiente queda obsoleta: estamos conectando ahora.
    this.clearReconnectTimer();

    const estado = this.ws?.readyState;
    if (this.ws && this.socketDe !== sessionId) {
      this.disconnect();
    } else if (estado === WebSocket.OPEN || estado === WebSocket.CONNECTING) {
      return;  // el de esta sesión ya está abierto, o abriéndose: no se abre otro
    }

    this.sessionId = sessionId;
    void this.abrir(sessionId);
  }

  private async abrir(sessionId: string): Promise<void> {
    if (this.abriendoPara === sessionId) return;  // ya se está pidiendo su ticket
    this.abriendoPara = sessionId;
    let ticket: string | null;
    try {
      ticket = await pedirTicketWs(sessionId);
    } finally {
      if (this.abriendoPara === sessionId) this.abriendoPara = null;
    }
    // mientras se pedía el ticket, otro connect()/disconnect() pudo cambiar de sesión o abrir
    const estado = this.ws?.readyState;
    if (this.sessionId !== sessionId || estado === WebSocket.OPEN || estado === WebSocket.CONNECTING) return;

    // Misma URL base que la página: en desarrollo el proxy de Vite redirige /ws/* al backend;
    // desplegado, nginx.
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/${sessionId}`;

    logger.log(`[WS] Connecting to: ${wsUrl}`);  // sin el ticket: no se registra

    try {
      this.ws = new WebSocket(ticket ? `${wsUrl}?ticket=${encodeURIComponent(ticket)}` : wsUrl);
      this.socketDe = sessionId;
      this.setupHandlers();
    } catch (error) {
      logger.error('WebSocket connection error:', error);
      this.handleReconnect();
    }
  }

  private setupHandlers(): void {
    if (!this.ws) return;
    const socket = this.ws;

    // F7 (auditoría): un socket que ya no es el actual (reemplazado) no habla en nombre del
    // servicio: su cierre no marca «desconectado» con el nuevo abierto, ni duplica mensajes.
    this.ws.onopen = () => {
      if (this.ws !== socket) return;
      logger.log('WebSocket connected');
      this.reconnectAttempts = 0;
      this.connectHandlers.forEach(handler => handler());
    };

    this.ws.onmessage = (event) => {
      if (this.ws !== socket) return;
      try {
        const message: WSMessage = JSON.parse(event.data);
        this.messageHandlers.forEach(handler => handler(message));
      } catch (error) {
        logger.error('Error parsing WebSocket message:', error);
      }
    };

    this.ws.onclose = (event) => {
      logger.log('WebSocket disconnected:', event.code, event.reason);
      if (this.ws !== socket && this.ws !== null) return;  // reemplazado: el actual sigue
      this.disconnectHandlers.forEach(handler => handler());

      // Solo NO se reconecta si el cierre lo pidió este cliente: disconnect()
      // suelta el socket antes de cerrarlo, así que aquí ya no es el actual.
      // `wasClean` no sirve de criterio: un reinicio del servidor cierra limpio
      // (1001/1012) y sin reconectar la aprobación HITL siguiente no llegaba
      // nunca (V5 F3).
      if (this.ws === socket) {
        this.handleReconnect();
      }
    };

    this.ws.onerror = (error) => {
      logger.error('WebSocket error:', error);
    };
  }

  private handleReconnect(): void {
    this.reconnectAttempts++;
    const delay = Math.min(
      this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), this.maxReconnectDelay,
    );

    logger.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    this.clearReconnectTimer();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.reintentar();
    }, delay);
  }

  private async reintentar(): Promise<void> {
    const current = this.sessionId;
    if (!current || this.reintentando) return;
    this.reintentando = true;
    try {
      let target: string | null = current;
      if (this.sessionRecovery) {
        try {
          target = await this.sessionRecovery(current);
        } catch (error) {
          // Sin red o backend caído: se reintenta con el mismo id.
          logger.warn('[WS] No se pudo verificar la sesión:', error);
        }
      }
      // Alguien conectó o desconectó mientras se verificaba: su decisión manda.
      if (this.sessionId !== current || this.reconnectTimer !== null) return;
      if (target) this.connect(target);
    } finally {
      this.reintentando = false;
    }
  }

  disconnect(): void {
    // FE1: cancelar cualquier reconexión pendiente antes de cerrar.
    this.clearReconnectTimer();
    this.reconnectAttempts = 0;
    if (this.ws) {
      // Se suelta ANTES de cerrar: su onclose ve que ya no es el actual y no reconecta.
      const socket = this.ws;
      this.ws = null;
      socket.close(1000, 'Client disconnect');
    }
    this.socketDe = null;
    this.sessionId = null;
  }

  send(message: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      logger.warn('WebSocket not connected, cannot send message');
    }
  }

  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  onConnect(handler: ConnectionHandler): () => void {
    this.connectHandlers.add(handler);
    return () => this.connectHandlers.delete(handler);
  }

  onDisconnect(handler: ConnectionHandler): () => void {
    this.disconnectHandlers.add(handler);
    return () => this.disconnectHandlers.delete(handler);
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  get currentSessionId(): string | null {
    return this.sessionId;
  }
}

export const wsService = new WebSocketService();
