import { Suspense, lazy, useEffect, useState } from 'react'
import { ConfigProvider, Spin, notification, theme as antdTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useLocation } from 'react-router-dom'
import { setNotifier } from './api/notifier'
import { wsClient } from './api/ws'
import HeaderBar from './components/HeaderBar'
import Sidebar from './components/Sidebar'
import LoginPage from './components/LoginPage'
import TradingPage from './components/TradingPage'
import { useAuthStore } from './stores/auth'
import { DEFAULT_ACCENT, useThemeStore } from './stores/theme'

// 非交易页面采用懒加载，以保持初始分包体积较小
//（例如 echarts 只有在打开回测页面时才会被引入）
const StrategyPage = lazy(() => import('./components/StrategyPage'))
const BacktestPage = lazy(() => import('./components/BacktestPage'))
const OptionsPage = lazy(() => import('./components/OptionsPage'))
const WatchlistPage = lazy(() => import('./components/WatchlistPage'))
const MonitorPage = lazy(() => import('./components/MonitorPage'))
const PaperPage = lazy(() => import('./components/PaperPage'))
const AIChatPage = lazy(() => import('./components/AIChatPage'))
const SettingsPage = lazy(() => import('./components/SettingsPage'))

const TAB_KEYS = ['trading', 'strategy', 'backtest', 'options', 'watchlist', 'monitor', 'paper', 'ai', 'settings'] as const

function lazyTab(node: React.ReactNode) {
  return (
    <Suspense
      fallback={
        <div style={{ padding: 48, textAlign: 'center' }}>
          <Spin />
        </div>
      }
    >
      {node}
    </Suspense>
  )
}

/** 挂载感知主题的通知实例并交给全局通知器
 *（必须位于 ConfigProvider 内部）。 */
function NotificationBridge() {
  const [notifApi, contextHolder] = notification.useNotification({
    maxCount: 5,
    stack: { threshold: 3 },
  })

  useEffect(() => {
    setNotifier(notifApi)
    return () => setNotifier(null)
  }, [notifApi])

  return contextHolder
}

function App() {
  const mode = useThemeStore(s => s.mode)
  const [authChecked, setAuthChecked] = useState(false)
  const username = useAuthStore(s => s.username)
  const checkAuth = useAuthStore(s => s.checkAuth)

  // 启动时验证 token
  useEffect(() => {
    checkAuth().finally(() => setAuthChecked(true))
  }, [checkAuth])

  // 登录后才启动 WebSocket
  useEffect(() => {
    if (username) {
      wsClient.start()
      return () => wsClient.stop()
    }
  }, [username])

  // URL 路径 -> 当前 Tab（HashRouter 下 pathname 取自 hash）；
  // 未知路径回落到 trading，保证总有激活页面。
  const location = useLocation()
  const pathTab =
    location.pathname === '/' ? 'trading' : location.pathname.slice(1).split('/')[0]
  const activeTab = (TAB_KEYS as readonly string[]).includes(pathTab) ? pathTab : 'trading'
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  if (!authChecked) {
    return (
      <div
        style={{
          height: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0b1120',
        }}
      >
        <Spin size="large" />
      </div>
    )
  }

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm:
          mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: DEFAULT_ACCENT,
          colorLink: DEFAULT_ACCENT,
          colorInfo: DEFAULT_ACCENT,
          borderRadius: 8,
          // 暗色下把 antd 组件底色/边框对齐到全局蓝黑色系，
          // 避免 antd 默认中性灰(#141414/#303030)与 ARL 蓝黑混用
          ...(mode === 'dark'
            ? {
                colorBgLayout: '#0b1120',
                colorBgContainer: '#101828',
                colorBgElevated: '#16203a',
                colorBorder: '#2a3a58',
                colorBorderSecondary: '#1f2b40',
                colorText: 'rgba(226, 232, 240, 0.92)',
                colorTextSecondary: 'rgba(148, 163, 184, 0.75)',
                colorTextTertiary: 'rgba(148, 163, 184, 0.55)',
              }
            : {}),
        },
      }}
    >
      <NotificationBridge />
      {!username ? (
        <LoginPage />
      ) : (
        <div className="flex h-screen overflow-hidden">
          <Sidebar
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed(c => !c)}
          />
          <div className="glass-ui flex min-w-0 flex-1 flex-col">
            <HeaderBar />
            <main
              className="min-h-0 flex-1 overflow-y-auto"
              style={{ padding: activeTab === 'trading' ? '0 12px 12px' : 12 }}
            >
              {/* 页面保活：与原 antd Tabs 行为一致，切换不丢状态 */}
              <div style={{ display: activeTab === 'trading' ? 'block' : 'none', height: '100%' }}>
                <TradingPage />
              </div>
              <div style={{ display: activeTab === 'strategy' ? 'block' : 'none' }}>
                {lazyTab(<StrategyPage />)}
              </div>
              <div style={{ display: activeTab === 'backtest' ? 'block' : 'none' }}>
                {lazyTab(<BacktestPage />)}
              </div>
              <div style={{ display: activeTab === 'options' ? 'block' : 'none' }}>
                {lazyTab(<OptionsPage />)}
              </div>
              <div style={{ display: activeTab === 'watchlist' ? 'block' : 'none' }}>
                {lazyTab(<WatchlistPage />)}
              </div>
              <div style={{ display: activeTab === 'monitor' ? 'block' : 'none' }}>
                {lazyTab(<MonitorPage />)}
              </div>
              <div style={{ display: activeTab === 'paper' ? 'block' : 'none' }}>
                {lazyTab(<PaperPage />)}
              </div>
              <div style={{ display: activeTab === 'ai' ? 'block' : 'none' }}>
                {lazyTab(<AIChatPage />)}
              </div>
              <div style={{ display: activeTab === 'settings' ? 'block' : 'none' }}>
                {lazyTab(<SettingsPage />)}
              </div>
            </main>
          </div>
        </div>
      )}
    </ConfigProvider>
  )
}

export default App
