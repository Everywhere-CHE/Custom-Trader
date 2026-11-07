/** AI 助手 MCP 客户端配置弹窗：导出专属客户端脚本 + 复制 mcpServers 配置。 */

import { useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Input,
  Modal,
  Space,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  CloudDownloadOutlined,
  CopyOutlined,
  DesktopOutlined,
  ReloadOutlined,
  SafetyOutlined,
} from '@ant-design/icons'
import { downloadMcpScript, refreshMcpToken } from '../api/http'

const { Text, Paragraph } = Typography

// 服务地址白名单（与后端 /api/mcp/script 的 base 校验一致）
const BASE_RE = /^https?:\/\/[A-Za-z0-9.\-\[\]]+(:\d{1,5})?$/

const CONFIG_JSON = `{
  "mcpServers": {
    "custom-trader": {
      "command": "python3",
      "args": [
        "~/Downloads/custom_trader_mcp.py"
      ]
    }
  }
}`

const LOCAL_MODE_JSON = `{
  "mcpServers": {
    "custom-trader": {
      "command": "backend/.venv/Scripts/python.exe",
      "args": ["backend/mcp_server/server.py"],
      "env": {
        "QT_API_BASE": "http://127.0.0.1:8000/api",
        "AI_ALLOW_TRADING": "false"
      }
    }
  }
}`

/** 复制文本：优先 Clipboard API（HTTPS/localhost），HTTP 环境降级 execCommand。 */
async function copyText(text: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 降级到 execCommand
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(ta)
  return ok
}

const orangeBtn = { background: '#fa6725', borderColor: '#fa6725' }

export default function McpConfigModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [downloading, setDownloading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [baseUrl, setBaseUrl] = useState(window.location.origin)
  const [messageApi, contextHolder] = message.useMessage()

  const baseValid = BASE_RE.test(baseUrl.trim())

  const download = async () => {
    if (!baseValid) {
      messageApi.warning('服务地址格式不正确：需为 http(s)://主机[:端口]')
      return
    }
    setDownloading(true)
    try {
      await downloadMcpScript(baseUrl.trim())
      messageApi.success(`已下载 custom_trader_mcp.py（预置地址 ${baseUrl.trim()} 与 Token）`)
    } catch {
      messageApi.error('下载失败，请稍后重试')
    } finally {
      setDownloading(false)
    }
  }

  const refresh = () => {
    Modal.confirm({
      title: '刷新 MCP Token',
      content: '旧 Token 将立即失效，已导出的客户端脚本会随之失效，需重新下载。确定继续？',
      okText: '刷新',
      onOk: async () => {
        setRefreshing(true)
        try {
          await refreshMcpToken()
          messageApi.success('Token 已刷新，请重新下载客户端脚本')
        } catch {
          messageApi.error('刷新失败')
        } finally {
          setRefreshing(false)
        }
      },
    })
  }

  const copy = async () => {
    const ok = await copyText(CONFIG_JSON)
    if (ok) messageApi.success('配置已复制到剪贴板')
    else messageApi.error('复制失败，请手动选择文本复制')
  }

  return (
    <Modal
      title="AI 助手 MCP 客户端配置"
      open={open}
      onCancel={onClose}
      footer={null}
      width={720}
    >
      {contextHolder}
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        支持接入 CC-Switch、Claude Code、Cursor、Windsurf 或 Antigravity 等 AI
        助手，让 AI 直接查询行情/持仓、管理策略与执行回测：
      </Paragraph>

      {/* 第一步 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          padding: '12px 16px',
          borderRadius: 8,
          background: 'var(--chart-bg)',
          border: '1px solid var(--border-color)',
          marginBottom: 12,
        }}
      >
        <div>
          <Text strong>
            <CloudDownloadOutlined style={{ color: '#fa6725', marginRight: 6 }} />
            第一步：导出专属客户端脚本
          </Text>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已预置服务地址与 MCP Token，零第三方依赖，开箱即用。
            </Text>
          </div>
          <div style={{ marginTop: 8, maxWidth: 420 }}>
            <Input
              size="small"
              value={baseUrl}
              onChange={e => setBaseUrl(e.target.value)}
              status={!baseValid && baseUrl ? 'error' : undefined}
              addonBefore="服务地址"
              placeholder="https://106.55.26.129"
            />
            <Text type="secondary" style={{ fontSize: 11 }}>
              默认为当前访问地址；直连部署可改为 https://服务器IP:38443（免 443
              代理，安全组放行后可用），本机使用填 http://127.0.0.1:8000。
            </Text>
          </div>
        </div>
        <Button
          type="primary"
          icon={<ApiOutlined />}
          loading={downloading}
          style={orangeBtn}
          onClick={() => void download()}
        >
          下载 custom_trader_mcp.py
        </Button>
      </div>

      {/* 第二步 */}
      <div
        style={{
          padding: '12px 16px',
          borderRadius: 8,
          background: 'var(--chart-bg)',
          border: '1px solid var(--border-color)',
          marginBottom: 12,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 8,
          }}
        >
          <Text strong>
            <DesktopOutlined style={{ color: '#fa6725', marginRight: 6 }} />
            第二步：复制配置并粘贴至客户端 &quot;mcpServers&quot; 节点下：
          </Text>
          <Space>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={refreshing}
              onClick={refresh}
            >
              刷新 Token
            </Button>
            <Button
              size="small"
              type="primary"
              icon={<CopyOutlined />}
              style={orangeBtn}
              onClick={() => void copy()}
            >
              复制配置
            </Button>
          </Space>
        </div>
        <pre
          className="mono"
          style={{
            margin: 0,
            padding: 12,
            borderRadius: 6,
            background: 'var(--panel-bg)',
            border: '1px solid var(--border-color)',
            fontSize: 13,
            lineHeight: 1.6,
            overflowX: 'auto',
          }}
        >
          {CONFIG_JSON}
        </pre>
      </div>

      {/* 说明 */}
      <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
        💡 免环境配置：脚本已内嵌上述服务地址与访问凭据，默认假定下载至
        ~/Downloads/custom_trader_mcp.py。若存于其他路径，请将 args
        调整为实际绝对路径；Windows 环境请使用 python 命令。脚本亦可通过环境变量
        QT_HOST / QT_TOKEN 临时覆盖地址与凭据（支持任意端口，含 8000 本机直连）。
      </Paragraph>
      <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        <SafetyOutlined style={{ marginRight: 4 }} />
        凭据安全：导出的脚本包含当前账户的高权限 API
        Token，请妥善保管勿公开提交；若发生泄露可点击「刷新 Token」一键作废。
      </Paragraph>

      <Collapse
        size="small"
        items={[
          {
            key: 'local',
            label: '服务器本机使用（高级）：免认证直连模式',
            children: (
              <>
                <Paragraph type="secondary" style={{ fontSize: 12 }}>
                  若 AI 助手运行在量化系统所在服务器上（127.0.0.1
                  免认证），可改用项目内置的完整 MCP 服务器，无需 Token：
                </Paragraph>
                <pre
                  className="mono"
                  style={{
                    margin: 0,
                    padding: 12,
                    borderRadius: 6,
                    background: 'var(--panel-bg)',
                    border: '1px solid var(--border-color)',
                    fontSize: 12,
                    overflowX: 'auto',
                  }}
                >
                  {LOCAL_MODE_JSON}
                </pre>
              </>
            ),
          },
        ]}
      />

      <Alert
        style={{ marginTop: 12 }}
        type="info"
        showIcon
        message={
          <Text style={{ fontSize: 12 }}>
            AI 助手的下单权限与「AI 助手」页的开关保持一致；行情/持仓/策略管理/回测等工具无需额外配置。
          </Text>
        }
      />
    </Modal>
  )
}
