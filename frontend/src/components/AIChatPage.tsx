/** AI 助手聊天页面，带会话侧边栏（玻璃态视觉，逻辑与原版一致）。 */

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Input,
  Popconfirm,
  Space,
  Spin,
  Typography,
  message,
} from 'antd'
import {
  ClearOutlined,
  DeleteOutlined,
  FileTextOutlined,
  MessageOutlined,
  PaperClipOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import { getAiStatus, postAiRecap, type AiStatus } from '../api/http'
import GlassPage from './ui/GlassPage'
import { useAiStore, type ChatItem } from '../stores/ai'
import ModelSettingsModal from './ModelSettingsModal'

const { Text } = Typography

const SUGGESTIONS = [
  '当前市场上有哪些合约在报价？整体涨跌如何？',
  '分析一下 600519.SSE 今天的盘中走势',
  '我最近 24 小时的交易绩效怎么样？',
  '我现在的持仓有什么风险？帮我做一次持仓对账',
  '解释一下 DonchianStrategy 的交易逻辑',
  '帮我设计一个 rb2510.SHFE 的双均线趋势策略并回测',
  '网关连接正常吗？最近有没有异常日志？',
]

/** 圆形头像：用户为靛蓝渐变，AI 为紫粉渐变。 */
function Avatar({ isUser }: { isUser: boolean }) {
  return (
    <div
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm text-white"
      style={{
        background: isUser
          ? 'linear-gradient(135deg, #4d9fff 0%, #1677ff 100%)'
          : 'linear-gradient(135deg, #a855f7 0%, #ec4899 100%)',
        boxShadow: isUser
          ? '0 3px 10px rgba(22, 119, 255, 0.35)'
          : '0 3px 10px rgba(168, 85, 247, 0.35)',
      }}
    >
      {isUser ? <UserOutlined /> : <RobotOutlined />}
    </div>
  )
}

/** 半透明状态胶囊标签。 */
function Pill({ children, tone = 'slate' }: { children: React.ReactNode; tone?: 'slate' | 'amber' | 'sky' }) {
  const tones = {
    slate: 'bg-slate-500/10 text-slate-500 dark:text-slate-400',
    amber: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
    sky: 'bg-sky-500/10 text-sky-600 dark:text-sky-300',
  }
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] leading-tight ${tones[tone]}`}>
      {children}
    </span>
  )
}

function MessageBubble({
  item,
  onDelete,
}: {
  item: ChatItem
  onDelete: (id: number) => void
}) {
  const isUser = item.role === 'user'
  return (
    <div
      className={`group/msg relative mb-4 flex items-start gap-2.5 ${
        isUser ? 'flex-row-reverse' : ''
      }`}
    >
      <Avatar isUser={isUser} />

      <div className="max-w-[78%] min-w-0">
        {/* 气泡 */}
        <div
          className={`px-3.5 py-2.5 text-[13px] leading-relaxed ${
            isUser
              ? 'rounded-2xl rounded-tr-md text-white'
              : 'rounded-2xl rounded-tl-md border border-[var(--card-border)] bg-[var(--subcard-bg)] text-slate-800 dark:text-slate-200'
          }`}
          style={
            isUser
              ? {
                  background:
                    'linear-gradient(135deg, #4d9fff 0%, #1677ff 60%, #0958d9 100%)',
                  boxShadow: '0 4px 14px rgba(22, 119, 255, 0.3)',
                }
              : undefined
          }
        >
          {isUser ? (
            <div style={{ whiteSpace: 'pre-wrap' }}>{item.text}</div>
          ) : (
            <div className="ai-markdown">
              <ReactMarkdown>{item.text}</ReactMarkdown>
            </div>
          )}
        </div>

        {/* 工具调用折叠面板 */}
        {item.toolCalls && item.toolCalls.length > 0 && (
          <Collapse
            size="small"
            className="mt-2"
            style={{
              background: 'rgba(255,255,255,0.45)',
              borderRadius: 10,
            }}
            items={item.toolCalls.map((call, index) => ({
              key: String(index),
              label: (
                <span className="text-[11px] text-slate-500 dark:text-slate-400">
                  🔧 {call.name}
                </span>
              ),
              children: (
                <div className="text-xs">
                  <Text type="secondary">Input:</Text>
                  <pre className="mono my-1 overflow-auto whitespace-pre-wrap rounded-lg bg-black/[0.04] p-2 text-[11px] dark:bg-white/[0.05]">
                    {JSON.stringify(call.input, null, 2)}
                  </pre>
                  <Text type="secondary">Output:</Text>
                  <pre className="mono my-1 max-h-[200px] overflow-auto whitespace-pre-wrap rounded-lg bg-black/[0.04] p-2 text-[11px] dark:bg-white/[0.05]">
                    {call.output}
                  </pre>
                </div>
              ),
            }))}
          />
        )}

        {/* 删除按钮（hover 显示） */}
        <div
          className={`mt-1 hidden ${isUser ? 'text-right' : ''} group-hover/msg:block`}
        >
          <Popconfirm title="确认删除此消息？" onConfirm={() => onDelete(item.id)}>
            <DeleteOutlined className="cursor-pointer text-[11px] text-slate-400 hover:text-rose-500" />
          </Popconfirm>
        </div>
      </div>
    </div>
  )
}

export default function AIChatPage() {
  const conversations = useAiStore(s => s.conversations)
  const activeConvId = useAiStore(s => s.activeConvId)
  const items = useAiStore(s => s.items)
  const loading = useAiStore(s => s.loading)
  const error = useAiStore(s => s.error)
  const send = useAiStore(s => s.send)
  const clear = useAiStore(s => s.clear)
  const remove = useAiStore(s => s.remove)
  const loadConversations = useAiStore(s => s.loadConversations)
  const switchConversation = useAiStore(s => s.switchConversation)
  const newConversation = useAiStore(s => s.newConversation)
  const deleteConversation = useAiStore(s => s.deleteConversation)

  const [status, setStatus] = useState<AiStatus | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [recapLoading, setRecapLoading] = useState(false)
  const [draft, setDraft] = useState('')
  const [attachedFile, setAttachedFile] = useState<{ name: string; content: string } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const [messageApi, contextHolder] = message.useMessage()

  useEffect(() => {
    getAiStatus().then(setStatus).catch(() => setStatus(null))
    loadConversations().then(() => {
      // 自动恢复最近一次会话
      const convs = useAiStore.getState().conversations
      if (convs.length > 0 && !useAiStore.getState().activeConvId) {
        switchConversation(convs[0].id)
      }
    })
  }, [loadConversations, switchConversation])

  // 有新消息时自动滚动到底部
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items, loading])

  const submit = useCallback(() => {
    if (!draft.trim() && !attachedFile) return
    // 若附带了文档，把内容拼到消息里
    const fileText = attachedFile
      ? `\n\n[已上传文档：${attachedFile.name}]\n\`\`\`\n${attachedFile.content}\n\`\`\`\n`
      : ''
    void send(draft.trim() + fileText)
    setDraft('')
    setAttachedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [draft, attachedFile, send])

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (!file) return
      const ext = file.name.split('.').pop()?.toLowerCase()
      if (ext !== 'md' && ext !== 'txt') {
        messageApi.warning('仅支持 .md 和 .txt 文本文件')
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }
      const reader = new FileReader()
      reader.onload = () => {
        const content = String(reader.result || '')
        if (content.length > 50000) {
          messageApi.warning('文件过大，仅支持 5 万字符以内')
          if (fileInputRef.current) fileInputRef.current.value = ''
          return
        }
        setAttachedFile({ name: file.name, content })
        messageApi.success(`已加载 ${file.name}`)
      }
      reader.readAsText(file)
    },
    [messageApi],
  )

  const removeFile = useCallback(() => {
    setAttachedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [])

  const handleDeleteConv = useCallback(
    async (id: string) => {
      await deleteConversation(id)
      messageApi.success('对话已删除')
    },
    [deleteConversation, messageApi],
  )

  const handleNewConv = useCallback(async () => {
    await newConversation()
    messageApi.success('新对话已创建')
  }, [newConversation, messageApi])

  // 盘后复盘：服务端拼装本地市场快照 → 固定 8 节模板生成
  const handleRecap = useCallback(async () => {
    if (recapLoading) return
    setRecapLoading(true)
    try {
      const r = await postAiRecap()
      useAiStore.getState().pushLocal('生成今日盘后复盘', r.recap, r.tool_calls)
      messageApi.success(`复盘已生成（快照 ${r.snapshot_time}，订阅品种 ${r.subscribed_count}）`)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`复盘生成失败: ${detail}`)
    } finally {
      setRecapLoading(false)
    }
  }, [messageApi, recapLoading])

  const settingsModal = (
    <ModelSettingsModal
      open={settingsOpen}
      status={status}
      onClose={() => setSettingsOpen(false)}
      onSaved={setStatus}
    />
  )

  if (status && !status.enabled) {
    return (
      <GlassPage>
        <Alert
          type="info"
          showIcon
          message="AI 助手未启用"
          description={
            <div>
              <p>点击下方按钮配置模型与 API Key</p>
              <Button
                type="primary"
                icon={<SettingOutlined />}
                onClick={() => setSettingsOpen(true)}
              >
                模型设置
              </Button>
            </div>
          }
        />
        {settingsModal}
      </GlassPage>
    )
  }

  return (
    <GlassPage>
      <div
        style={{
          display: 'flex',
          height: 'calc(100vh - 138px)',
          gap: 12,
        }}
      >
        {contextHolder}
        {/* 侧边栏：会话历史 */}
        <div
          className="flex shrink-0 flex-col overflow-hidden rounded-2xl border border-[var(--card-border)] bg-[var(--card-bg)]"
          style={{ width: 250, minWidth: 250 }}
        >
          <div className="flex items-center justify-between border-b border-[var(--card-border)] px-3 py-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
              对话历史
            </span>
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              onClick={handleNewConv}
              className="!text-slate-500 hover:!bg-blue-500/10 hover:!text-blue-500"
            />
          </div>
          <div className="flex-1 overflow-auto p-1.5">
            {conversations.length === 0 && (
              <div className="px-3 py-6 text-center text-xs text-slate-400 dark:text-slate-500">
                暂无会话，点右上角 + 新建
              </div>
            )}
            {conversations.map(conv => {
              const active = conv.id === activeConvId
              return (
                <div
                  key={conv.id}
                  onClick={() => switchConversation(conv.id)}
                  className="group/conv mx-0.5 flex cursor-pointer items-center justify-between rounded-xl px-2.5 py-1.5 transition-colors"
                  style={{
                    background: active ? 'rgba(22, 119, 255, 0.1)' : undefined,
                    boxShadow: active
                      ? 'inset 2px 0 0 0 #1677ff'
                      : 'inset 2px 0 0 0 transparent',
                  }}
                  onMouseEnter={e => {
                    if (!active) e.currentTarget.style.background = 'rgba(22, 119, 255, 0.05)'
                  }}
                  onMouseLeave={e => {
                    if (!active) e.currentTarget.style.background = 'transparent'
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <div className="overflow-hidden text-ellipsis whitespace-nowrap text-xs font-medium text-slate-700 dark:text-slate-300">
                      <MessageOutlined className="mr-1 text-[10px] text-slate-400" />
                      {conv.title}
                    </div>
                    <div className="text-[10px] text-slate-400 dark:text-slate-500">
                      {conv.message_count} 条消息
                    </div>
                  </div>
                  <Popconfirm
                    title="确认删除此对话？"
                    onConfirm={e => {
                      e?.stopPropagation()
                      handleDeleteConv(conv.id)
                    }}
                    onCancel={e => e?.stopPropagation()}
                  >
                    <DeleteOutlined
                      className="hidden cursor-pointer text-[11px] text-slate-400 hover:text-rose-500 group-hover/conv:block"
                      onClick={e => e.stopPropagation()}
                    />
                  </Popconfirm>
                </div>
              )
            })}
          </div>
        </div>

        {/* 主聊天区域 */}
        <div className="flex min-w-0 flex-1 flex-col" style={{ maxWidth: 860 }}>
          {/* 头部 */}
          <div className="mb-2 flex items-center justify-between">
            <Space size={6} wrap>
              <Text strong className="text-slate-800 dark:text-slate-100">
                AI 交易助手
              </Text>
              {status && (
                <Pill>
                  {status.provider === 'anthropic'
                    ? 'Claude'
                    : status.provider === 'responses'
                      ? 'OpenAI·Responses'
                      : 'OpenAI·Chat'}{' '}
                  · {status.model}
                </Pill>
              )}
              {status && (
                <Pill tone={status.allow_trading ? 'amber' : 'slate'}>
                  {status.allow_trading ? '允许下单' : '仅分析'}
                </Pill>
              )}
              {status && status.auto_optimize && <Pill tone="sky">自动优化</Pill>}
            </Space>
            <Space>
              <Button
                size="small"
                icon={<FileTextOutlined />}
                loading={recapLoading}
                onClick={handleRecap}
                className="!rounded-xl"
              >
                盘后复盘
              </Button>
              <Button
                size="small"
                icon={<SettingOutlined />}
                onClick={() => setSettingsOpen(true)}
                className="!rounded-xl"
              >
                模型设置
              </Button>
              <Button
                size="small"
                icon={<ClearOutlined />}
                onClick={clear}
                disabled={loading || items.length === 0}
                className="!rounded-xl"
              >
                清空会话
              </Button>
            </Space>
          </div>

          {/* 消息列表 */}
          <div
            ref={listRef}
            className="min-h-0 flex-1 overflow-auto rounded-2xl border border-[var(--card-border)] bg-[var(--subcard-bg)] p-4"
          >
            {items.length === 0 && !activeConvId && (
              <div className="px-2 py-6">
                <div className="mb-4 text-[13px] text-slate-500 dark:text-slate-400">
                  你好！我是量化交易 AI
                  助手，可以分析行情、解读策略、评估持仓风险，还能为你编写策略代码并自动回测。试试：
                </div>
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.map(s => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => void send(s)}
                      className="cursor-pointer appearance-none rounded-full border border-blue-200/70 bg-white/60 px-3 py-1.5 text-xs text-slate-600 transition-all hover:-translate-y-0.5 hover:border-blue-300 hover:text-blue-600 hover:shadow-[0_4px_12px_rgba(22,119,255,0.15)] dark:border-white/10 dark:bg-white/[0.06] dark:text-slate-300 dark:hover:border-blue-400/40 dark:hover:text-blue-300"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {items.length === 0 && activeConvId && (
              <div className="px-6 py-10 text-center text-[13px] text-slate-400 dark:text-slate-500">
                此对话为空，开始聊天吧
              </div>
            )}
            {items.map(item => (
              <MessageBubble key={item.id} item={item} onDelete={remove} />
            ))}
            {loading && (
              <div className="flex items-center justify-center gap-2 py-3 text-[12px] text-slate-400">
                <Spin size="small" />
                <span>AI 思考与调用工具中…</span>
              </div>
            )}
            {error && (
              <Alert type="error" showIcon message={error} style={{ margin: 8 }} />
            )}
          </div>

          {/* 输入区 */}
          <div className="mt-2">
            {attachedFile && (
              <div className="mb-1.5 inline-flex items-center gap-1.5 rounded-lg bg-blue-500/10 px-2.5 py-1 text-xs text-blue-600 dark:text-blue-300">
                <FileTextOutlined />
                <span>{attachedFile.name}</span>
                <Button
                  size="small"
                  type="text"
                  danger
                  onClick={removeFile}
                  style={{ fontSize: 11, padding: 0, minWidth: 20 }}
                >
                  ✕
                </Button>
              </div>
            )}
            <div className="flex items-end gap-2 rounded-2xl border border-[var(--card-border)] bg-[var(--card-bg)] p-2 transition-colors focus-within:border-[var(--accent)]">
              <input
                ref={fileInputRef}
                type="file"
                accept=".md,.txt"
                style={{ display: 'none' }}
                onChange={handleFileChange}
              />
              <Button
                type="text"
                icon={<PaperClipOutlined />}
                onClick={() => fileInputRef.current?.click()}
                disabled={loading}
                title="上传 .md / .txt 文档"
                className="!text-slate-400 hover:!bg-blue-500/10 hover:!text-blue-500"
              />
              <Input.TextArea
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onPressEnter={e => {
                  if (!e.shiftKey) {
                    e.preventDefault()
                    submit()
                  }
                }}
                placeholder="输入问题…（Enter 发送，Shift+Enter 换行）"
                autoSize={{ minRows: 1, maxRows: 4 }}
                disabled={loading}
                variant="borderless"
                className="!bg-transparent"
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={submit}
                loading={loading}
                className="!rounded-xl !border-0 font-medium !text-white"
                style={{
                  background:
                    'linear-gradient(135deg, #4d9fff 0%, #1677ff 60%, #0958d9 100%)',
                  boxShadow: '0 4px 14px rgba(22, 119, 255, 0.35)',
                }}
              >
                发送
              </Button>
            </div>
          </div>

          {settingsModal}
        </div>
      </div>
    </GlassPage>
  )
}
