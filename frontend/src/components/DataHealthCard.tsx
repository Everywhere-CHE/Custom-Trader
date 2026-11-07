/** 数据健康卡片：自选池日 K 完整性检查（OHLC 逻辑/缺口/停更/样本不足）
 *  + 一键修复（重新下载日 K，FUYAO 优先）。 */

import { useState } from 'react'
import {
  Button,
  Card,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { getDataHealth, repairDataHealth } from '../api/http'
import type { DataHealthProblem, DataHealthResult } from '../api/http'

const { Text } = Typography

const ISSUE_LABELS: Record<string, { label: string; color: string }> = {
  invalid: { label: 'OHLC 逻辑错', color: 'red' },
  missing: { label: '缺口', color: 'orange' },
  stale: { label: '停更', color: 'volcano' },
  sparse: { label: '样本不足', color: 'gold' },
}

export default function DataHealthCard() {
  const [messageApi, contextHolder] = message.useMessage()
  const [result, setResult] = useState<DataHealthResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [repairing, setRepairing] = useState(false)

  const check = async () => {
    setChecking(true)
    try {
      setResult(await getDataHealth())
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`检查失败: ${detail}`)
    } finally {
      setChecking(false)
    }
  }

  const repair = async (symbols: string[]) => {
    if (!symbols.length) return
    setRepairing(true)
    try {
      const results = await repairDataHealth(symbols)
      const ok = results.filter(r => r.ok).length
      messageApi.success(`修复完成：${ok}/${results.length} 成功`)
      await check()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`修复失败: ${detail}`)
    } finally {
      setRepairing(false)
    }
  }

  const columns: ColumnsType<DataHealthProblem> = [
    { title: '标的', dataIndex: 'vt_symbol', width: 140, className: 'mono' },
    { title: 'K线数', dataIndex: 'bars', width: 80, className: 'mono' },
    {
      title: '最新数据',
      dataIndex: 'last',
      width: 110,
      render: (v: string | null) => (v ? <span className="mono">{v.slice(0, 10)}</span> : '-'),
    },
    {
      title: '问题',
      key: 'issues',
      render: (_: unknown, r) => (
        <span>
          {r.issues.map((issue, i) => (
            <Tag key={i} color={ISSUE_LABELS[issue.type]?.color ?? 'default'}>
              {ISSUE_LABELS[issue.type]?.label ?? issue.type}
              {issue.count > 1 ? `×${issue.count}` : ''}
            </Tag>
          ))}
          <Text type="secondary" style={{ fontSize: 11 }}>
            {r.issues.map(i => i.detail).join('；')}
          </Text>
        </span>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_: unknown, r) => (
        <Button size="small" loading={repairing} onClick={() => repair([r.vt_symbol])}>
          修复
        </Button>
      ),
    },
  ]

  return (
    <Card
      size="small"
      title="数据健康（自选池日 K）"
      extra={
        <Button size="small" type="primary" loading={checking} onClick={check}>
          {result ? '重新检查' : '开始检查'}
        </Button>
      }
    >
      {contextHolder}
      {result ? (
        <>
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
            检查 {result.checked} 个标的：健康 {result.healthy}，存在问题 {result.problematic}
            （缺口按工作日近似，长假可能误报；修复 = 重新下载近 400 天日 K，FUYAO 优先、网关回退）
          </Text>
          {result.problematic > 0 && (
            <Button
              size="small"
              loading={repairing}
              onClick={() => repair(result.problems.map(p => p.vt_symbol))}
              style={{ marginBottom: 8 }}
            >
              一键修复全部问题标的
            </Button>
          )}
          <Table<DataHealthProblem>
            size="small"
            rowKey="vt_symbol"
            columns={columns}
            dataSource={result.problems}
            pagination={false}
            locale={{ emptyText: '全部健康 ✓' }}
            className="compact-table"
          />
        </>
      ) : (
        <Text type="secondary">
          点击「开始检查」扫描自选池的日 K 完整性：OHLC 逻辑错误、交易日缺口、数据停更、样本不足。
          建议在回测/实盘前先检查，避免坏数据污染指标与信号。
        </Text>
      )}
    </Card>
  )
}
