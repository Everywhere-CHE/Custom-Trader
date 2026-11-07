/** 顶部导航栏（ARL-Next 风格）：页面标题、账户概要、Gateway 状态、
 *  WebSocket 连接、MCP 配置入口、亮 / 暗主题切换。 */

import { useEffect, useState } from 'react'
import { Badge, Space, Switch, Typography } from 'antd'
import { ApiOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { useLocation } from 'react-router-dom'
import { getHealth } from '../api/http'
import { useWsStore } from '../api/ws'
import { useThemeStore } from '../stores/theme'
import { useTradingStore } from '../stores/trading'
import McpConfigModal from './McpConfigModal'

const { Text } = Typography

const PAGE_TITLES: Record<string, string> = {
  trading: '交易',
  strategy: '策略',
  backtest: '回测',
  monitor: '监控中心',
  paper: '模拟盘',
  ai: 'AI 助手',
  settings: '设置',
}

export default function HeaderBar() {
  const mode = useThemeStore(s => s.mode)
  const toggle = useThemeStore(s => s.toggle)
  const wsConnected = useWsStore(s => s.connected)
  const accounts = useTradingStore(s => s.accounts)
  const [gateways, setGateways] = useState<Record<string, boolean>>({})
  const [mcpOpen, setMcpOpen] = useState(false)
  const { pathname } = useLocation()
  const activeTab = pathname === '/' ? 'trading' : pathname.slice(1).split('/')[0]

  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const health = await getHealth()
        if (active) setGateways(health.gateways)
      } catch {
        if (active) setGateways({})
      }
    }
    void poll()
    const timer = setInterval(poll, 5000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])

  const accountList = Object.values(accounts)
  const totalBalance = accountList.reduce((sum, a) => sum + a.balance, 0)
  const totalAvailable = accountList.reduce((sum, a) => sum + a.available, 0)

  return (
    <div
      className="relative z-10 flex min-h-[56px] flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b px-4 py-2"
      style={{
        background: mode === 'dark' ? 'rgba(15, 23, 42, 0.75)' : 'rgba(255, 255, 255, 0.75)',
        borderColor: mode === 'dark' ? '#1e293b' : '#e2e8f0',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
      }}
    >
      <div className="flex items-center gap-3">
        <span
          className="text-[15px] font-medium tracking-tight"
          style={{ color: 'var(--text-primary)' }}
        >
          {PAGE_TITLES[activeTab] || 'CustomTrader'}
        </span>

        <Space size="small" className="ml-2 hidden lg:inline-flex">
          <Text type="secondary" className="text-xs">权益</Text>
          <Text strong className="mono text-[13px]" style={{ color: 'var(--text-primary)' }}>
            {totalBalance.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </Text>
          <Text type="secondary" className="text-xs">可用</Text>
          <Text strong className="mono text-[13px]" style={{ color: 'var(--text-primary)' }}>
            {totalAvailable.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </Text>
        </Space>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <Space size={4}>
          {Object.entries(gateways).map(([name, connected]) => (
            <span
              key={name}
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors ${
                connected
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300'
                  : 'bg-slate-500/10 text-slate-400'
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  connected ? 'bg-emerald-500' : 'bg-slate-400'
                }`}
              />
              {name}
            </span>
          ))}
        </Space>

        <Badge
          status={wsConnected ? 'success' : 'error'}
          text={
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {wsConnected ? '行情已连接' : '行情断开'}
            </span>
          }
        />

        <span className="header-text-action" onClick={() => setMcpOpen(true)}>
          <ApiOutlined style={{ fontSize: 14 }} />
          MCP 配置
        </span>

        <Switch
          size="small"
          checked={mode === 'dark'}
          onChange={toggle}
          checkedChildren={<MoonOutlined />}
          unCheckedChildren={<SunOutlined />}
        />
      </div>

      <McpConfigModal open={mcpOpen} onClose={() => setMcpOpen(false)} />
    </div>
  )
}
