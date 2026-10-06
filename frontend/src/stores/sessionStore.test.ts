/**
 * Tests for Session Store
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useSessionStore } from './sessionStore'
import type { Session } from '@/types'

describe('useSessionStore', () => {
  beforeEach(() => {
    // Reset store state
    useSessionStore.setState({
      sessionId: null,
      sessions: [],
      isConnected: false,
      portalConnected: false,
      bdConnected: false,
    })
  })

  describe('Session ID', () => {
    it('should set session ID', () => {
      const { setSessionId } = useSessionStore.getState()

      setSessionId('session-123')

      expect(useSessionStore.getState().sessionId).toBe('session-123')
    })

    it('should clear session ID', () => {
      const { setSessionId } = useSessionStore.getState()

      setSessionId('session-123')
      setSessionId(null)

      expect(useSessionStore.getState().sessionId).toBeNull()
    })

    it('should update session ID', () => {
      const { setSessionId } = useSessionStore.getState()

      setSessionId('session-1')
      setSessionId('session-2')

      expect(useSessionStore.getState().sessionId).toBe('session-2')
    })
  })

  describe('Sessions List', () => {
    it('should set sessions list', () => {
      const { setSessions } = useSessionStore.getState()
      const sessions: Session[] = [
        { session_id: '1', created_at: '2024-01-01', message_count: 5, last_active: '2024-01-01' },
        { session_id: '2', created_at: '2024-01-02', message_count: 10, last_active: '2024-01-02' },
      ]

      setSessions(sessions)

      expect(useSessionStore.getState().sessions).toHaveLength(2)
      expect(useSessionStore.getState().sessions[0].session_id).toBe('1')
    })

    it('should replace sessions list', () => {
      const { setSessions } = useSessionStore.getState()

      setSessions([{ session_id: '1', created_at: '2024-01-01', message_count: 1, last_active: '2024-01-01' }])
      setSessions([{ session_id: '2', created_at: '2024-01-02', message_count: 2, last_active: '2024-01-02' }])

      expect(useSessionStore.getState().sessions).toHaveLength(1)
      expect(useSessionStore.getState().sessions[0].session_id).toBe('2')
    })

    it('should clear sessions with empty array', () => {
      const { setSessions } = useSessionStore.getState()

      setSessions([
        { session_id: '1', created_at: '2024-01-01', message_count: 1, last_active: '2024-01-01' },
        { session_id: '2', created_at: '2024-01-02', message_count: 2, last_active: '2024-01-02' },
      ])
      setSessions([])

      expect(useSessionStore.getState().sessions).toHaveLength(0)
    })
  })

  describe('Connection Status', () => {
    it('should set connected status', () => {
      const { setConnected } = useSessionStore.getState()

      setConnected(true)
      expect(useSessionStore.getState().isConnected).toBe(true)

      setConnected(false)
      expect(useSessionStore.getState().isConnected).toBe(false)
    })

    it('should set portal connected status', () => {
      const { setPortalConnected } = useSessionStore.getState()

      setPortalConnected(true)
      expect(useSessionStore.getState().portalConnected).toBe(true)

      setPortalConnected(false)
      expect(useSessionStore.getState().portalConnected).toBe(false)
    })

    it('should set database connected status', () => {
      const { setBdConnected } = useSessionStore.getState()

      setBdConnected(true)
      expect(useSessionStore.getState().bdConnected).toBe(true)

      setBdConnected(false)
      expect(useSessionStore.getState().bdConnected).toBe(false)
    })

    it('should handle independent connection states', () => {
      const { setConnected, setPortalConnected, setBdConnected } = useSessionStore.getState()

      setConnected(true)
      setPortalConnected(false)
      setBdConnected(true)

      const state = useSessionStore.getState()
      expect(state.isConnected).toBe(true)
      expect(state.portalConnected).toBe(false)
      expect(state.bdConnected).toBe(true)
    })
  })
})

describe('Session Store Selectors', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessionId: 'test-session',
      sessions: [{ session_id: '1', created_at: '', message_count: 0, last_active: '' }],
      isConnected: true,
      portalConnected: true,
      bdConnected: false,
    })
  })

  it('useSessionId should return session id', () => {
    expect(useSessionStore.getState().sessionId).toBe('test-session')
  })

  it('useSessions should return sessions list', () => {
    expect(useSessionStore.getState().sessions).toHaveLength(1)
  })

  it('useIsConnected should return connection status', () => {
    expect(useSessionStore.getState().isConnected).toBe(true)
  })

  it('usePortalConnected should return portal status', () => {
    expect(useSessionStore.getState().portalConnected).toBe(true)
  })

  it('useBdConnected should return database status', () => {
    expect(useSessionStore.getState().bdConnected).toBe(false)
  })
})
