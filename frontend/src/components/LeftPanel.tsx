import {
  Cable,
  PlugZap,
  Plus,
  MessageSquare,
  Globe2,
  Database,
  Compass,
  Layers,
  History,
  Settings,
  ShieldCheck,
  User,
} from 'lucide-react'
import { useSesionAuth } from '@/hooks/useSesionAuth'
import {
  useLayers,
  useUIStore,
  useActiveDrawer,
  useMapStore,
} from '@/stores'
import { nuevaConversacion } from '@/lib/nuevaConversacion'
import { useState, useRef } from 'react'
import { useClickOutside } from '@/hooks'

type RailAction = 'new' | 'chat' | 'map' | 'data' | 'database' | 'layers' | 'history' | 'tools' | 'connections' | 'auditoria'

export function LeftPanel() {
  const layers = useLayers()
  const drawer = useActiveDrawer()
  const toggleDrawer = useUIStore((s) => s.toggleDrawer)
  const setActiveDrawer = useUIStore((s) => s.setActiveDrawer)
  const toggleTweaks = useUIStore((s) => s.toggleTweaks)
  const setMapView = useMapStore((s) => s.setMapView)
  const esAdmin = useSesionAuth().usuario?.rol === 'admin'

  const [profileOpen, setProfileOpen] = useState(false)
  const profileRef = useRef<HTMLDivElement>(null)
  useClickOutside(profileRef, () => setProfileOpen(false), profileOpen)

  const handleAction = (action: RailAction) => {
    switch (action) {
      case 'new':
        if (window.confirm('¿Iniciar una nueva conversación? Se borrarán los mensajes actuales.')) {
          setActiveDrawer(null)
          void nuevaConversacion()
        }
        return
      case 'chat':
        setActiveDrawer(null)
        return
      case 'map':
        setActiveDrawer(null)
        // Re-centra en la vista por defecto (Colombia)
        setMapView([-74.0721, 4.711], 10)
        return
      case 'tools':
        toggleDrawer('tools')
        break
      case 'connections':
        toggleDrawer('connections')
        return
      case 'data':
        toggleDrawer('data')
        return
      case 'database':
        toggleDrawer('database')
        return
      case 'layers':
        toggleDrawer('layers')
        return
      case 'history':
        toggleDrawer('history')
        return
      case 'auditoria':
        toggleDrawer('auditoria')
        return
    }
  }

  // 'data' (Discovery) → Compass: descubrir datos externos en ArcGIS Hub.
  // 'database' (BD)    → Database: schemas/tablas REALES de la BD conectada.
  // Separamos las dos experiencias porque son distintas (catálogo externo
  // vs. catálogo interno) y antes ambas usaban Database, confuso.
  const items: { action: RailAction; icon: typeof MessageSquare; label: string; badge?: boolean; drawerId?: 'data' | 'database' | 'layers' | 'history' | 'tools' | 'connections' | 'auditoria' }[] = [
    { action: 'chat',     icon: MessageSquare, label: 'Chat',          badge: true },
    { action: 'map',      icon: Globe2,        label: 'Vista mapa' },
    { action: 'data',     icon: Compass,       label: 'Descubrir',     drawerId: 'data' },
    { action: 'tools',    icon: PlugZap,      label: 'Herramientas',  drawerId: 'tools' },
    { action: 'connections', icon: Cable,     label: 'Conexiones',    drawerId: 'connections' },
    { action: 'database', icon: Database,      label: 'Base de datos', drawerId: 'database' },
    { action: 'layers',   icon: Layers,        label: 'Capas',         badge: layers.length > 0, drawerId: 'layers' },
    { action: 'history',  icon: History,       label: 'Historial',     drawerId: 'history' },
    // F6 (E6.5): la auditoría de la organización, solo para administración
    ...(esAdmin ? [{ action: 'auditoria' as const, icon: ShieldCheck, label: 'Auditoría', drawerId: 'auditoria' as const }] : []),
  ]

  return (
    <aside className="rail">
      <button
        className="rail-btn"
        title="Nueva conversación"
        style={{ marginBottom: 8 }}
        onClick={() => handleAction('new')}
      >
        <Plus />
      </button>

      {items.map((it) => {
        const ItIcon = it.icon
        const isActive = it.drawerId ? drawer === it.drawerId : false
        return (
          <button
            key={it.action}
            className={`rail-btn ${isActive ? 'active' : ''}`}
            title={it.label}
            onClick={() => handleAction(it.action)}
          >
            <ItIcon />
            {it.badge && <span className="rail-badge" />}
          </button>
        )
      })}

      <div className="spacer" />

      <button
        className="rail-btn"
        title="Ajustes de apariencia"
        onClick={toggleTweaks}
      >
        <Settings />
      </button>

      <div ref={profileRef} style={{ position: 'relative' }}>
        <button
          className={`rail-btn ${profileOpen ? 'active' : ''}`}
          title="Cuenta"
          onClick={() => setProfileOpen((p) => !p)}
        >
          <User />
        </button>
        {profileOpen && <ProfilePopup onClose={() => setProfileOpen(false)} />}
      </div>
    </aside>
  )
}

function ProfilePopup({ onClose }: { onClose: () => void }) {
  return (
    <div className="profile-popup">
      <div className="profile-popup-head">Cuenta</div>
      <div className="profile-popup-body">
        <ProfileRow label="Usuario" value="Local" />
        <ProfileRow label="Tenant" value="default" />
      </div>
      <div style={{ borderTop: '1px solid var(--border)' }}>
        <button
          className="gc-btn gc-btn-ghost"
          style={{ width: '100%', justifyContent: 'flex-start', borderRadius: 0 }}
          onClick={onClose}
        >
          Cerrar
        </button>
      </div>
    </div>
  )
}

function ProfileRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 12px', fontSize: 12 }}>
      <span style={{ color: 'var(--text-mute)' }}>{label}</span>
      <span style={{ color: 'var(--text)', fontWeight: 500 }}>{value}</span>
    </div>
  )
}
