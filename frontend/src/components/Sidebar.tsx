/** 左侧导航栏（ARL-Next 风格）：深蓝黑底、图标 + 文字菜单、
 *  激活项为主题色胶囊、支持折叠为纯图标栏。 */

import { useLocation, useNavigate } from 'react-router-dom'
import {
  AlertOutlined,
  CodeOutlined,
  DollarOutlined,
  ExperimentOutlined,
  LineChartOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SettingOutlined,
  StarOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import LogoMark from './ui/LogoMark'

const ITEMS = [
  { key: 'trading', label: '交易', icon: LineChartOutlined },
  { key: 'strategy', label: '策略', icon: CodeOutlined },
  { key: 'backtest', label: '回测', icon: ExperimentOutlined },
  { key: 'options', label: '期权', icon: ThunderboltOutlined },
  { key: 'watchlist', label: '关注', icon: StarOutlined },
  { key: 'monitor', label: '监控', icon: AlertOutlined },
  { key: 'paper', label: '模拟盘', icon: DollarOutlined },
  { key: 'ai', label: 'AI 助手', icon: RobotOutlined },
  { key: 'settings', label: '设置', icon: SettingOutlined },
] as const

interface Props {
  collapsed: boolean
  onToggle: () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const active = pathname === '/' ? 'trading' : pathname.slice(1).split('/')[0]

  return (
    <aside
      className="relative z-20 flex shrink-0 select-none flex-col border-r transition-[width] duration-300"
      style={{
        width: collapsed ? 60 : 172,
        background: '#0b1120',
        borderColor: '#1e293b',
      }}
    >
      {/* Logo 区 */}
      <div
        className="flex h-[56px] shrink-0 items-center justify-center gap-2 border-b"
        style={{ borderColor: '#1e293b' }}
      >
        <LogoMark size={32} />
        {!collapsed && (
          <span className="text-[15px] font-medium" style={{ color: '#f1f5f9' }}>
            CustomTrader
          </span>
        )}
      </div>

      {/* 菜单 */}
      <nav className="flex-1 overflow-y-auto py-2">
        {ITEMS.map(item => {
          const Icon = item.icon
          const isActive = active === item.key
          return (
            <button
              key={item.key}
              type="button"
              title={collapsed ? item.label : undefined}
              onClick={() => navigate(`/${item.key}`)}
              className={`mx-2 mb-1 flex h-10 w-[calc(100%-16px)] cursor-pointer items-center appearance-none border-0 bg-transparent rounded-lg text-[13px] transition-all duration-200 ${
                collapsed ? 'justify-center px-0' : 'gap-2.5 px-3'
              }`}
              style={
                isActive
                  ? {
                      background: 'var(--accent)',
                      color: '#ffffff',
                      boxShadow: '0 4px 12px rgba(var(--accent-rgb), 0.45)',
                    }
                  : { color: 'rgba(255, 255, 255, 0.65)' }
              }
              onMouseEnter={e => {
                if (!isActive) {
                  e.currentTarget.style.background = 'rgba(255, 255, 255, 0.06)'
                  e.currentTarget.style.color = '#ffffff'
                }
              }}
              onMouseLeave={e => {
                if (!isActive) {
                  e.currentTarget.style.background = 'transparent'
                  e.currentTarget.style.color = 'rgba(255, 255, 255, 0.65)'
                }
              }}
            >
              <Icon className="text-[15px]" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </button>
          )
        })}
      </nav>

      {/* 折叠开关 */}
      <button
        type="button"
        onClick={onToggle}
        title={collapsed ? '展开菜单' : '收起菜单'}
        className="flex h-11 shrink-0 cursor-pointer items-center justify-center appearance-none border-0 bg-transparent border-t border-[#1e293b] transition-colors"
        style={{ borderColor: '#1e293b', color: 'rgba(255, 255, 255, 0.5)' }}
        onMouseEnter={e => {
          e.currentTarget.style.color = 'var(--accent)'
        }}
        onMouseLeave={e => {
          e.currentTarget.style.color = 'rgba(255, 255, 255, 0.5)'
        }}
      >
        {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
      </button>
    </aside>
  )
}
