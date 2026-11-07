/** 全市场数据同步卡片：A股全市场日K底座（fuyao 批量拉取 + 盘后增量自动）。 */

import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Popconfirm,
  Progress,
  Space,
  Statistic,
  Typography,
  message,
} from 'antd'
import {
  cancelMarketSync,
  getMarketSyncStatus,
  startMarketSync,
} from '../api/http'
import type { MarketSyncStatus } from '../api/http'

const { Text } = Typography

const STATUS_META: Record<string, { color: string; label: string }> = {
  idle: { color: 'default', label: '空闲' },
  pending: { color: 'processing', label: '排队中' },
  running: { color: 'processing', label: '同步中' },
  done: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
  cancelled: { color: 'warning', label: '已取消' },
}

export default function MarketSyncCard() {
  const [messageApi, contextHolder] = message.useMessage()
  const [status, setStatus] = useState<MarketSyncStatus | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const load = () =>
      getMarketSyncStatus()
        .then(setStatus)
        .catch(() => undefined)
    void load()
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [])

  const start = async (mode: 'full' | 'incremental') => {
    setBusy(true)
    try {
      const r = await startMarketSync(mode, mode === 'full' ? 800 : 12)
      if (r.ok) messageApi.success(r.message)
      else messageApi.warning(r.message)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`启动失败: ${detail}`)
    } finally {
      setBusy(false)
    }
  }

  const running = status?.status === 'running' || status?.status === 'pending'
  const pct =
    status && status.total_symbols > 0
      ? Math.round((status.done_symbols / status.total_symbols) * 100)
      : 0

  return (
    <Card
      size="small"
      title="全市场数据底座（A股日K）"
      extra={
        <Space>
          <Button
            size="small"
            disabled={running}
            loading={busy}
            onClick={() => void start('incremental')}
          >
            增量同步（近12天）
          </Button>
          <Button
            size="small"
            type="primary"
            disabled={running}
            onClick={() => void start('full')}
          >
            全量同步（800天）
          </Button>
          {running && (
            <Popconfirm title="取消当前同步任务？" onConfirm={() => void cancelMarketSync()}>
              <Button size="small" danger>
                取消
              </Button>
            </Popconfirm>
          )}
        </Space>
      }
    >
      {contextHolder}
      <Space size={24} wrap>
        <Statistic
          title="状态"
          value={STATUS_META[status?.status ?? 'idle']?.label ?? '-'}
          valueStyle={{ fontSize: 16 }}
        />
        <Statistic
          title="标的清单"
          value={status?.symbol_count ?? '-'}
          suffix={status?.symbol_count ? '只' : ''}
          valueStyle={{ fontSize: 16 }}
        />
        <Statistic
          title="上次全量"
          value={status?.last_full_sync?.slice(5, 16).replace('T', ' ') ?? '从未'}
          valueStyle={{ fontSize: 16 }}
        />
        <Statistic
          title="上次增量"
          value={status?.last_incremental?.slice(5, 16).replace('T', ' ') ?? '从未'}
          valueStyle={{ fontSize: 16 }}
        />
        {status?.saved_bars ? (
          <Statistic
            title="本次入库K线"
            value={status.saved_bars.toLocaleString()}
            valueStyle={{ fontSize: 16 }}
          />
        ) : null}
        {status?.errors ? (
          <Statistic title="错误" value={status.errors} valueStyle={{ fontSize: 16, color: 'var(--down-color)' }} />
        ) : null}
      </Space>

      {running && (
        <div style={{ marginTop: 10 }}>
          <Progress percent={pct} size="small" />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {status?.progress} · 限流自适应（约 5 请求/秒，全量约 20 分钟）
          </Text>
        </div>
      )}
      {status?.status === 'failed' && (
        <Text type="danger" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          失败：{status.error}
        </Text>
      )}
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
        A股全市场（沪深北 ~5400 只）日线前复权入库，来源 FUYAO 同花顺。
        每个交易日下午 15:10 后自动增量同步（幂等可重跑，节假日自动跳过周末）；
        全量首次约 270 万根K线，为因子/全市场扫描等研究功能提供数据底座。
      </Text>
    </Card>
  )
}
