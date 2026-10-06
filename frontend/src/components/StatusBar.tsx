import { useMessages, useChatStatus } from '@/stores'
import { useSessionId } from '@/stores'

export function StatusBar() {
  const messages = useMessages()
  const status = useChatStatus()
  const sessionId = useSessionId()

  const lastUser = [...messages].reverse().find((m) => m.role === 'user')
  const lastTs = lastUser ? new Date(lastUser.timestamp) : null
  const ago = lastTs ? Math.max(0, Math.floor((Date.now() - lastTs.getTime()) / 1000)) : null

  const fmtAgo = (s: number) => {
    if (s < 60) return `${s} s`
    if (s < 3600) return `${Math.floor(s / 60)} min`
    return `${Math.floor(s / 3600)} h`
  }

  return (
    <footer className="statusbar">
      <span>
        Estado <b>{status === 'searching' ? 'consultando' : status === 'error' ? 'error' : 'listo'}</b>
      </span>
      <span style={{ color: 'var(--border-strong)' }}>·</span>
      {ago !== null ? (
        <span>
          Última consulta hace <b>{fmtAgo(ago)}</b>
        </span>
      ) : (
        <span>Sin consultas todavía</span>
      )}
      <span style={{ color: 'var(--border-strong)' }}>·</span>
      <span>
        <b className="tabular">{messages.length}</b> mensajes
      </span>

      <div className="right">
        {sessionId && (
          <span className="mono" title="Session ID">
            sesión <b>{sessionId.slice(-6)}</b>
          </span>
        )}
        <span>
          <span className="kbd">⌘K</span> Buscar
        </span>
        <span>v1.0</span>
      </div>
    </footer>
  )
}
