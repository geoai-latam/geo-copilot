/**
 * Tests for Chat Store
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useChatStore, type RetryInfo } from './chatStore'
import type { ChatMessage } from '@/types'

describe('useChatStore', () => {
  beforeEach(() => {
    // Reset store state before each test
    useChatStore.setState({
      messages: [],
      isLoading: false,
      chatStatus: 'ready',
      retryInfo: null,
      queryHistory: [],
    })
  })

  describe('Message Management', () => {
    it('should add a message', () => {
      const { addMessage } = useChatStore.getState()
      const message: ChatMessage = {
        id: 'msg-1',
        role: 'user',
        content: 'Test message',
        timestamp: new Date(),
        status: 'sent',
      }
      addMessage(message)
      expect(useChatStore.getState().messages).toHaveLength(1)
      expect(useChatStore.getState().messages[0].content).toBe('Test message')
    })

    it('should add multiple messages in order', () => {
      const { addMessage } = useChatStore.getState()
      addMessage({ id: '1', role: 'user', content: 'First', timestamp: new Date(), status: 'sent' })
      addMessage({ id: '2', role: 'assistant', content: 'Second', timestamp: new Date(), status: 'sent' })
      addMessage({ id: '3', role: 'user', content: 'Third', timestamp: new Date(), status: 'sent' })

      const messages = useChatStore.getState().messages
      expect(messages).toHaveLength(3)
      expect(messages[0].content).toBe('First')
      expect(messages[1].content).toBe('Second')
      expect(messages[2].content).toBe('Third')
    })

    it('should update a message by id', () => {
      const { addMessage, updateMessage } = useChatStore.getState()
      addMessage({
        id: 'msg-1',
        role: 'user',
        content: 'Original',
        timestamp: new Date(),
        status: 'sending',
      })
      updateMessage('msg-1', { status: 'sent', content: 'Updated' })

      const message = useChatStore.getState().messages[0]
      expect(message.status).toBe('sent')
      expect(message.content).toBe('Updated')
    })

    it('should not modify other messages when updating', () => {
      const { addMessage, updateMessage } = useChatStore.getState()
      addMessage({ id: '1', role: 'user', content: 'A', timestamp: new Date(), status: 'sent' })
      addMessage({ id: '2', role: 'assistant', content: 'B', timestamp: new Date(), status: 'sent' })

      updateMessage('1', { content: 'Updated A' })

      expect(useChatStore.getState().messages[0].content).toBe('Updated A')
      expect(useChatStore.getState().messages[1].content).toBe('B')
    })

    it('should handle updating non-existent message', () => {
      const { addMessage, updateMessage } = useChatStore.getState()
      addMessage({ id: '1', role: 'user', content: 'A', timestamp: new Date(), status: 'sent' })

      // Should not throw
      updateMessage('non-existent', { content: 'Updated' })

      expect(useChatStore.getState().messages).toHaveLength(1)
      expect(useChatStore.getState().messages[0].content).toBe('A')
    })

    it('should clear all messages', () => {
      const { addMessage, clearMessages } = useChatStore.getState()
      addMessage({ id: '1', role: 'user', content: 'A', timestamp: new Date(), status: 'sent' })
      addMessage({ id: '2', role: 'assistant', content: 'B', timestamp: new Date(), status: 'sent' })

      clearMessages()

      expect(useChatStore.getState().messages).toHaveLength(0)
    })
  })

  describe('Status Management', () => {
    it('should set loading state', () => {
      const { setLoading } = useChatStore.getState()

      setLoading(true)
      expect(useChatStore.getState().isLoading).toBe(true)

      setLoading(false)
      expect(useChatStore.getState().isLoading).toBe(false)
    })

    it('should set chat status', () => {
      const { setChatStatus } = useChatStore.getState()

      setChatStatus('searching')
      expect(useChatStore.getState().chatStatus).toBe('searching')

      setChatStatus('error')
      expect(useChatStore.getState().chatStatus).toBe('error')

      setChatStatus('ready')
      expect(useChatStore.getState().chatStatus).toBe('ready')

      // Auditoría 2026-09-08 §5 (2): cuarto estado — el panel HITL está
      // abierto y la espera es del usuario, no del servidor.
      setChatStatus('waiting_approval')
      expect(useChatStore.getState().chatStatus).toBe('waiting_approval')
    })
  })

  describe('Retry Info Management', () => {
    it('should set retry info', () => {
      const { setRetryInfo } = useChatStore.getState()
      const retryInfo: RetryInfo = {
        show: true,
        agent: 'GISAgent',
        status: 'retrying',
        attempt: 2,
        maxAttempts: 3,
        error: 'SQL error',
        action: 'Correcting query',
      }

      setRetryInfo(retryInfo)

      const stored = useChatStore.getState().retryInfo
      expect(stored).not.toBeNull()
      expect(stored?.agent).toBe('GISAgent')
      expect(stored?.status).toBe('retrying')
      expect(stored?.attempt).toBe(2)
    })

    it('should update retry info status', () => {
      const { setRetryInfo } = useChatStore.getState()

      setRetryInfo({
        show: true,
        agent: 'DataAgent',
        status: 'retrying',
        attempt: 1,
        maxAttempts: 3,
      })

      setRetryInfo({
        show: true,
        agent: 'DataAgent',
        status: 'success',
        attempt: 2,
        maxAttempts: 3,
        message: 'Query executed successfully',
      })

      expect(useChatStore.getState().retryInfo?.status).toBe('success')
      expect(useChatStore.getState().retryInfo?.message).toBe('Query executed successfully')
    })

    it('should clear retry info', () => {
      const { setRetryInfo, clearRetryInfo } = useChatStore.getState()

      setRetryInfo({
        show: true,
        agent: 'Agent',
        status: 'retrying',
        attempt: 1,
        maxAttempts: 3,
      })

      clearRetryInfo()

      expect(useChatStore.getState().retryInfo).toBeNull()
    })

    it('should handle setting null retry info', () => {
      const { setRetryInfo } = useChatStore.getState()

      setRetryInfo({
        show: true,
        agent: 'Agent',
        status: 'retrying',
        attempt: 1,
        maxAttempts: 3,
      })

      setRetryInfo(null)

      expect(useChatStore.getState().retryInfo).toBeNull()
    })
  })

  describe('Query History', () => {
    it('should add query to history', () => {
      const { addQueryToHistory } = useChatStore.getState()

      addQueryToHistory('SELECT * FROM test', true)

      const history = useChatStore.getState().queryHistory
      expect(history).toHaveLength(1)
      expect(history[0].query).toBe('SELECT * FROM test')
      expect(history[0].success).toBe(true)
      expect(history[0].timestamp).toBeInstanceOf(Date)
    })

    it('should add failed query to history', () => {
      const { addQueryToHistory } = useChatStore.getState()

      addQueryToHistory('INVALID SQL', false)

      expect(useChatStore.getState().queryHistory[0].success).toBe(false)
    })

    it('should prepend new queries (most recent first)', () => {
      const { addQueryToHistory } = useChatStore.getState()

      addQueryToHistory('First query', true)
      addQueryToHistory('Second query', true)
      addQueryToHistory('Third query', true)

      const history = useChatStore.getState().queryHistory
      expect(history[0].query).toBe('Third query')
      expect(history[1].query).toBe('Second query')
      expect(history[2].query).toBe('First query')
    })

    it('should limit history to 20 items', () => {
      const { addQueryToHistory } = useChatStore.getState()

      for (let i = 0; i < 25; i++) {
        addQueryToHistory(`Query ${i}`, true)
      }

      const history = useChatStore.getState().queryHistory
      expect(history).toHaveLength(20)
      // Most recent should be last added
      expect(history[0].query).toBe('Query 24')
      // Oldest should be Query 5 (0-4 were removed)
      expect(history[19].query).toBe('Query 5')
    })
  })
})

describe('Chat Store Selectors', () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: [{ id: '1', role: 'user', content: 'Test', timestamp: new Date(), status: 'sent' }],
      isLoading: true,
      chatStatus: 'searching',
      retryInfo: { show: true, agent: 'Agent', status: 'retrying', attempt: 1, maxAttempts: 3 },
      queryHistory: [{ query: 'Test', timestamp: new Date(), success: true }],
    })
  })

  it('useMessages selector should return messages', () => {
    // Note: In a real test, you'd use React Testing Library to test hooks
    // This tests the store directly
    expect(useChatStore.getState().messages).toHaveLength(1)
  })

  it('useIsLoading selector should return loading state', () => {
    expect(useChatStore.getState().isLoading).toBe(true)
  })

  it('useChatStatus selector should return chat status', () => {
    expect(useChatStore.getState().chatStatus).toBe('searching')
  })

  it('useRetryInfo selector should return retry info', () => {
    expect(useChatStore.getState().retryInfo?.agent).toBe('Agent')
  })

  it('useQueryHistory selector should return query history', () => {
    expect(useChatStore.getState().queryHistory).toHaveLength(1)
  })
})
