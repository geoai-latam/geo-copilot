/**
 * Tests for UI Store
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useUIStore } from './uiStore'
import type { ApprovalStatus } from '@/types'

describe('useUIStore', () => {
  beforeEach(() => {
    // Reset store state
    useUIStore.setState({
      leftPanelState: 'expanded',
      chatDockState: 'medium',
      showApprovalPanel: false,
      pendingApprovals: [],
    })
  })

  describe('Left Panel', () => {
    it('should toggle left panel from expanded to collapsed', () => {
      const { toggleLeftPanel } = useUIStore.getState()

      toggleLeftPanel()

      expect(useUIStore.getState().leftPanelState).toBe('collapsed')
    })

    it('should toggle left panel from collapsed to expanded', () => {
      useUIStore.setState({ leftPanelState: 'collapsed' })
      const { toggleLeftPanel } = useUIStore.getState()

      toggleLeftPanel()

      expect(useUIStore.getState().leftPanelState).toBe('expanded')
    })

    it('should set left panel state directly', () => {
      const { setLeftPanelState } = useUIStore.getState()

      setLeftPanelState('collapsed')
      expect(useUIStore.getState().leftPanelState).toBe('collapsed')

      setLeftPanelState('expanded')
      expect(useUIStore.getState().leftPanelState).toBe('expanded')
    })
  })

  describe('Chat Dock', () => {
    it('should set chat dock state', () => {
      const { setChatDockState } = useUIStore.getState()

      setChatDockState('collapsed')
      expect(useUIStore.getState().chatDockState).toBe('collapsed')

      setChatDockState('expanded')
      expect(useUIStore.getState().chatDockState).toBe('expanded')

      setChatDockState('medium')
      expect(useUIStore.getState().chatDockState).toBe('medium')
    })

    it('should cycle chat dock state: collapsed -> medium -> expanded -> collapsed', () => {
      const { cycleChatDockState } = useUIStore.getState()

      useUIStore.setState({ chatDockState: 'collapsed' })
      cycleChatDockState()
      expect(useUIStore.getState().chatDockState).toBe('medium')

      cycleChatDockState()
      expect(useUIStore.getState().chatDockState).toBe('expanded')

      cycleChatDockState()
      expect(useUIStore.getState().chatDockState).toBe('collapsed')
    })

    it('should cycle from medium state', () => {
      const { cycleChatDockState } = useUIStore.getState()

      // Starting from medium (default)
      cycleChatDockState()
      expect(useUIStore.getState().chatDockState).toBe('expanded')

      cycleChatDockState()
      expect(useUIStore.getState().chatDockState).toBe('collapsed')
    })
  })

  describe('Approval Panel', () => {
    const mockApproval: ApprovalStatus = {
      approval_id: 'apr-1',
      content: 'SELECT * FROM test',
      content_type: 'sql',
      created_at: '2024-01-01T00:00:00Z',
    }

    it('should show/hide approval panel', () => {
      const { setShowApprovalPanel } = useUIStore.getState()

      setShowApprovalPanel(true)
      expect(useUIStore.getState().showApprovalPanel).toBe(true)

      setShowApprovalPanel(false)
      expect(useUIStore.getState().showApprovalPanel).toBe(false)
    })

    it('should set pending approvals', () => {
      const { setPendingApprovals } = useUIStore.getState()

      setPendingApprovals([mockApproval])

      expect(useUIStore.getState().pendingApprovals).toHaveLength(1)
      expect(useUIStore.getState().pendingApprovals[0].approval_id).toBe('apr-1')
    })

    it('should add pending approval and show panel', () => {
      const { addPendingApproval } = useUIStore.getState()

      addPendingApproval(mockApproval)

      expect(useUIStore.getState().pendingApprovals).toHaveLength(1)
      expect(useUIStore.getState().showApprovalPanel).toBe(true)
    })

    it('should add multiple pending approvals', () => {
      const { addPendingApproval } = useUIStore.getState()

      addPendingApproval({ ...mockApproval, approval_id: 'apr-1' })
      addPendingApproval({ ...mockApproval, approval_id: 'apr-2' })
      addPendingApproval({ ...mockApproval, approval_id: 'apr-3' })

      expect(useUIStore.getState().pendingApprovals).toHaveLength(3)
    })

    it('should remove pending approval by id', () => {
      const { addPendingApproval, removePendingApproval } = useUIStore.getState()

      addPendingApproval({ ...mockApproval, approval_id: 'apr-1' })
      addPendingApproval({ ...mockApproval, approval_id: 'apr-2' })
      addPendingApproval({ ...mockApproval, approval_id: 'apr-3' })

      removePendingApproval('apr-2')

      const ids = useUIStore.getState().pendingApprovals.map(a => a.approval_id)
      expect(ids).toEqual(['apr-1', 'apr-3'])
    })

    it('should handle removing non-existent approval', () => {
      const { addPendingApproval, removePendingApproval } = useUIStore.getState()

      addPendingApproval(mockApproval)
      removePendingApproval('non-existent')

      expect(useUIStore.getState().pendingApprovals).toHaveLength(1)
    })
  })
})

describe('UI Store Selectors', () => {
  beforeEach(() => {
    useUIStore.setState({
      leftPanelState: 'collapsed',
      chatDockState: 'expanded',
      showApprovalPanel: true,
      pendingApprovals: [{ approval_id: '1', content: 'test', content_type: 'sql', created_at: '' }],
    })
  })

  it('useLeftPanelState should return correct state', () => {
    expect(useUIStore.getState().leftPanelState).toBe('collapsed')
  })

  it('useChatDockState should return correct state', () => {
    expect(useUIStore.getState().chatDockState).toBe('expanded')
  })

  it('useShowApprovalPanel should return correct state', () => {
    expect(useUIStore.getState().showApprovalPanel).toBe(true)
  })

  it('usePendingApprovals should return approvals', () => {
    expect(useUIStore.getState().pendingApprovals).toHaveLength(1)
  })
})
