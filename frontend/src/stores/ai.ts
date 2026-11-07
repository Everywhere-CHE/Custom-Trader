/** AI 聊天会话状态仓库，支持持久化的会话管理。 */

import { create } from 'zustand'
import {
  postAiChat,
  listConversations,
  getConversation,
  createConversation,
  deleteConversation,
  type AiToolCall,
  type ConversationSummary,
} from '../api/http'

export interface ChatItem {
  id: number
  role: 'user' | 'assistant'
  text: string
  toolCalls?: AiToolCall[]
}

interface AiState {
  conversations: ConversationSummary[]
  activeConvId: string | null
  items: ChatItem[]
  apiMessages: unknown[]
  loading: boolean
  error: string
  loadConversations: () => Promise<void>
  switchConversation: (id: string) => Promise<void>
  newConversation: () => Promise<void>
  deleteConversation: (id: string) => Promise<void>
  send: (text: string) => Promise<void>
  remove: (id: number) => void
  clear: () => void
  pushLocal: (userText: string, assistantText: string, toolCalls?: AiToolCall[]) => void
}

let nextId = 1

export const useAiStore = create<AiState>((set, get) => ({
  conversations: [],
  activeConvId: null,
  items: [],
  apiMessages: [],
  loading: false,
  error: '',

  loadConversations: async () => {
    try {
      const convs = await listConversations()
      set({ conversations: convs })
    } catch {
      // 忽略错误
    }
  },

  switchConversation: async (id: string) => {
    try {
      const conv = await getConversation(id)
      // 从 display_messages 重建聊天条目
      const items: ChatItem[] = (conv.display_messages || []).map(
        (d: { role: string; text: string }) => ({
          id: nextId++,
          role: d.role as 'user' | 'assistant',
          text: d.text,
        }),
      )
      set({
        activeConvId: id,
        items,
        apiMessages: conv.messages || [],
        error: '',
      })
    } catch {
      set({ error: 'Failed to load conversation' })
    }
  },

  newConversation: async () => {
    try {
      const conv = await createConversation()
      set({
        activeConvId: conv.id,
        items: [],
        apiMessages: [],
        error: '',
      })
      // 刷新会话列表
      const convs = await listConversations()
      set({ conversations: convs })
    } catch {
      set({ error: 'Failed to create conversation' })
    }
  },

  deleteConversation: async (id: string) => {
    try {
      await deleteConversation(id)
      const state = get()
      const isActive = state.activeConvId === id
      const convs = state.conversations.filter(c => c.id !== id)
      set({ conversations: convs })
      if (isActive) {
        // 切换到第一个可用会话，若无则新建
        if (convs.length > 0) {
          await get().switchConversation(convs[0].id)
        } else {
          set({ activeConvId: null, items: [], apiMessages: [] })
        }
      }
    } catch {
      set({ error: 'Failed to delete conversation' })
    }
  },

  send: async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || get().loading) return

    // 若没有活跃会话则自动创建一个
    let convId = get().activeConvId
    if (!convId) {
      try {
        const conv = await createConversation()
        convId = conv.id
        set({ activeConvId: convId })
        const convs = await listConversations()
        set({ conversations: convs })
      } catch {
        set({ error: 'Failed to create conversation' })
        return
      }
    }

    const userItem: ChatItem = { id: nextId++, role: 'user', text: trimmed }
    const messages = [
      ...get().apiMessages,
      { role: 'user', content: trimmed },
    ]
    const displayItems = [
      ...get().items,
      userItem,
    ]
    set({
      items: displayItems,
      loading: true,
      error: '',
    })

    try {
      const displayPayload = displayItems.map(i => ({
        role: i.role,
        text: i.text,
      }))
      const resp = await postAiChat(messages, convId, displayPayload)
      const newItems = [
        ...displayItems,
        {
          id: nextId++,
          role: 'assistant' as const,
          text: resp.reply,
          toolCalls: resp.tool_calls,
        },
      ]
      set({
        items: newItems,
        apiMessages: resp.messages,
        loading: false,
      })
      // 刷新会话列表（标题可能已更新）
      const convs = await listConversations()
      set({ conversations: convs })
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response
          ?.data?.detail ?? String(error)
      // 504 超时错误给出友好提示
      const friendlyMsg = detail.includes('504')
        ? '请求超时，AI 模型响应较慢。请稍后重试，或简化问题。'
        : detail
      set({ loading: false, error: friendlyMsg })
    }
  },

  remove: (id: number) => {
    const state = get()
    const newItems = state.items.filter(i => i.id !== id)
    set({ items: newItems })
  },

  clear: () => {
    const state = get()
    // 同时删除当前活跃会话
    if (state.activeConvId) {
      deleteConversation(state.activeConvId).catch(() => {})
    }
    set({ activeConvId: null, items: [], apiMessages: [], error: '' })
    void get().loadConversations()
  },

  /** 本地插入一对消息（不走模型调用），用于盘后复盘等预制内容展示。 */
  pushLocal: (userText: string, assistantText: string, toolCalls?: AiToolCall[]) => {
    set(state => ({
      items: [
        ...state.items,
        { id: nextId++, role: 'user' as const, text: userText },
        {
          id: nextId++,
          role: 'assistant' as const,
          text: assistantText,
          toolCalls,
        },
      ],
    }))
  },
}))