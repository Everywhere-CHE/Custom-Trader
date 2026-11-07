/** 期权模板参数优化面板：K 段稳健筛选 / Walk-Forward。
 *
 * 复用父页面回测表单的标的/区间/资金/成本参数，仅追加优化目标、
 * 网格与折/段配置；任务走 /options/optimize → /backtest/{id} 轮询。
 */

import { useMemo, useState } from 'react'
import type dayjs from 'dayjs'
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Statistic,
  Table,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  getBacktestTask,
  runOptionOptimize,
  type OptionOptimizeReq,
} from '../api/http'
import { parseGridSpec } from './OptimizerPanel'
import type { OptionTemplateInfo } from '../types'

const { Text } = Typography

const METRIC_OPTIONS = [
  { label: '夏普比率', value: 'sharpe_ratio' },
  { label: '总收益率', value: 'total_return' },
  { label: '年化收益', value: 'annual_return' },
  { label: '收益回撤比', value: 'return_drawdown_ratio' },
  { label: '总净利', value: 'total_net_pnl' },
]

interface RobustRow {
  rank: number
  params_text: string
  segment_scores: number[]
  valid_segments: number
  robust_score: number
  median: number
  worst: number
  mean: number
}

interface FoldRow {
  fold: number
  status: string
  best_params_text?: string
  is_score?: number
  oos_score?: number
  oos_total_return?: number
  oos_sharpe?: number
  oos_trades?: number
  errors?: string[]
}

export default function OptionOptimizerPanel({
  form,
  templateInfo,
}: {
  form: {
    getFieldsValue: () => Record<string, unknown>
    validateFields: () => Promise<Record<string, unknown>>
  }
  templateInfo: OptionTemplateInfo | undefined
}) {
  const [messageApi, contextHolder] = message.useMessage()
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState('')
  const [robustRows, setRobustRows] = useState<RobustRow[]>([])
  const [foldRows, setFoldRows] = useState<FoldRow[]>([])
  const [wfSummary, setWfSummary] = useState<Record<string, number | string> | null>(null)
  const [optimizeMode, setOptimizeMode] = useState<'robust' | 'walkforward'>('robust')

  const paramNames = useMemo(
    () => Object.keys(templateInfo?.parameters ?? {}),
    [templateInfo],
  )

  const run = async () => {
    let main: Record<string, unknown>
    try {
      main = await form.validateFields()
    } catch {
      return
    }
    // 网格解析
    const grid: Record<string, number[] | { start: number; end: number; step: number }> = {}
    for (const name of paramNames) {
      const spec = (main[`grid_${name}`] as string) || ''
      if (!spec.trim()) continue
      const parsed = parseGridSpec(spec)
      if (!parsed) {
        messageApi.error(`网格格式不正确: ${name} = ${spec}（如 "0,0.02,0.04" 或 "0.01-0.06:0.01"）`)
        return
      }
      grid[name] = parsed as OptionOptimizeReq['grid'][string]
    }
    if (!Object.keys(grid).length) {
      messageApi.error('请至少为一个参数填写网格')
      return
    }
    const range = main.range as [dayjs.Dayjs, dayjs.Dayjs]
    setRunning(true)
    setProgress('')
    try {
      const task = await runOptionOptimize({
        mode: optimizeMode,
        class_name: main.class_name as string,
        underlying: main.underlying as string,
        start: range[0].format('YYYY-MM-DD'),
        end: range[1].format('YYYY-MM-DD'),
        metric: (main.metric as string) || 'sharpe_ratio',
        grid,
        setting: {},
        capital: main.capital as number,
        commission: main.commission as number,
        slippage: main.slippage as number,
        iv_fallback: main.iv_fallback as number,
        margin_limit_pct: main.margin_limit_pct as number,
        k_segments: (main.k_segments as number) || 3,
        train_days: (main.train_days as number) || 60,
        test_days: (main.test_days as number) || 20,
        step_days: (main.step_days as number) || 20,
      })
      let last = task
      while (last.status === 'pending' || last.status === 'running') {
        setProgress(last.message)
        await new Promise(r => setTimeout(r, 1500))
        last = await getBacktestTask(task.task_id)
      }
      if (last.status !== 'done' || !last.result) {
        messageApi.error(`优化失败: ${last.error || '未知错误'}`)
        return
      }
      const result = last.result as Record<string, unknown>
      if (optimizeMode === 'robust') {
        setRobustRows((result.results as RobustRow[]) ?? [])
        setFoldRows([])
        setWfSummary(null)
        messageApi.success(`稳健筛选完成，共 ${result.total_combos} 组合`)
      } else {
        setFoldRows((result.folds as FoldRow[]) ?? [])
        setRobustRows([])
        setWfSummary({
          折数: (result.folds as FoldRow[])?.length ?? 0,
          IS平均分: Number(result.is_mean ?? 0).toFixed(3),
          OOS平均分: Number(result.oos_mean ?? 0).toFixed(3),
          衰减: Number(result.degradation ?? 0).toFixed(3),
          一致性: String(result.consistency ?? '-'),
          OOS平均收益: `${((Number(result.oos_avg_return ?? 0)) * 100).toFixed(2)}%`,
        })
        messageApi.success('Walk-Forward 完成')
      }
    } catch (e) {
      const detail =
        (e as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
        String(e)
      messageApi.error(`优化失败: ${detail}`)
    } finally {
      setRunning(false)
      setProgress('')
    }
  }

  const robustColumns: ColumnsType<RobustRow> = [
    { title: '#', dataIndex: 'rank', width: 50 },
    { title: '参数', dataIndex: 'params_text', width: 200, className: 'mono' },
    {
      title: '各段得分',
      dataIndex: 'segment_scores',
      render: (v: number[]) =>
        v.map((x, i) => (
          <span key={i} className="mono" style={{ marginRight: 8 }}>
            {Number.isFinite(Number(x)) ? Number(x).toFixed(2) : '-'}
          </span>
        )),
    },
    { title: '稳健分', dataIndex: 'robust_score', width: 90, className: 'mono' },
    { title: '中位', dataIndex: 'median', width: 80, className: 'mono', render: (v: number) => (Number.isFinite(Number(v)) ? Number(v).toFixed(2) : '-') },
    { title: '最差', dataIndex: 'worst', width: 80, className: 'mono', render: (v: number) => (Number.isFinite(Number(v)) ? Number(v).toFixed(2) : '-') },
  ]

  const foldColumns: ColumnsType<FoldRow> = [
    { title: '折', dataIndex: 'fold', width: 50 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'ok' ? '✓ ok' : v),
    },
    { title: '冠军参数', dataIndex: 'best_params_text', className: 'mono', render: (v?: string) => v ?? '-' },
    { title: 'IS分', dataIndex: 'is_score', width: 80, className: 'mono', render: (v?: number) => (v == null || !Number.isFinite(Number(v)) ? '-' : Number(v).toFixed(3)) },
    { title: 'OOS分', dataIndex: 'oos_score', width: 80, className: 'mono', render: (v?: number) => (v == null || !Number.isFinite(Number(v)) ? '-' : Number(v).toFixed(3)) },
    {
      title: 'OOS收益',
      dataIndex: 'oos_total_return',
      width: 90,
      className: 'mono',
      render: (v?: number) => (v == null || !Number.isFinite(Number(v)) ? '-' : `${(Number(v) * 100).toFixed(2)}%`),
    },
    { title: 'OOS交易', dataIndex: 'oos_trades', width: 80, className: 'mono', render: (v?: number) => v ?? '-' },
  ]

  return (
    <Card
      size="small"
      title="参数优化（稳健筛选 / Walk-Forward）"
      extra={
        <Space>
          <Select
            size="small"
            style={{ width: 130 }}
            value={optimizeMode}
            onChange={v => setOptimizeMode(v)}
            options={[
              { label: 'K 段稳健筛选', value: 'robust' },
              { label: 'Walk-Forward', value: 'walkforward' },
            ]}
          />
          <Form.Item name="metric" noStyle initialValue="sharpe_ratio">
            <Select size="small" style={{ width: 110 }} options={METRIC_OPTIONS} />
          </Form.Item>
          <Button size="small" type="primary" loading={running} onClick={() => void run()}>
            {running && progress ? `优化中 ${progress}…` : '开始优化'}
          </Button>
        </Space>
      }
    >
      <Space wrap size={8} style={{ marginBottom: 8 }}>
        {optimizeMode === 'robust' ? (
          <Form.Item name="k_segments" label="K 段" initialValue={3} style={{ marginBottom: 0 }}>
            <InputNumber size="small" style={{ width: 70 }} min={1} precision={0} />
          </Form.Item>
        ) : (
          <>
            <Form.Item name="train_days" label="训练天" initialValue={60} style={{ marginBottom: 0 }}>
              <InputNumber size="small" style={{ width: 70 }} min={1} precision={0} />
            </Form.Item>
            <Form.Item name="test_days" label="测试天" initialValue={20} style={{ marginBottom: 0 }}>
              <InputNumber size="small" style={{ width: 70 }} min={1} precision={0} />
            </Form.Item>
            <Form.Item name="step_days" label="步长" initialValue={20} style={{ marginBottom: 0 }}>
              <InputNumber size="small" style={{ width: 70 }} min={1} precision={0} />
            </Form.Item>
          </>
        )}
        <Text type="secondary" style={{ fontSize: 12 }}>
          期权回测每段含数据加载（首次拉K后走本地缓存）；网格写法 "0,0.02,0.04" 枚举 / "0.01-0.05:0.01" 等差
        </Text>
      </Space>

      {templateInfo && (
        <div style={{ marginBottom: 8 }}>
          {paramNames.map(name => (
            <Form.Item
              key={`grid_${name}`}
              name={`grid_${name}`}
              label={`网格 ${name}`}
              tooltip={templateInfo.param_descriptions?.[name]}
              style={{ marginBottom: 4 }}
            >
              <Input size="small" style={{ width: 200 }} placeholder="如 0,0.02,0.04" allowClear />
            </Form.Item>
          ))}
        </div>
      )}

      {wfSummary && (
        <Space size="large" style={{ marginBottom: 8 }} wrap>
          {Object.entries(wfSummary).map(([k, v]) => (
            <Statistic key={k} title={k} value={v} valueStyle={{ fontSize: 15 }} />
          ))}
        </Space>
      )}

      {robustRows.length > 0 && (
        <Table
          size="small"
          rowKey="rank"
          columns={robustColumns}
          dataSource={robustRows}
          pagination={false}
          className="compact-table"
        />
      )}
      {foldRows.length > 0 && (
        <Table
          size="small"
          rowKey="fold"
          columns={foldColumns}
          dataSource={foldRows}
          pagination={false}
          className="compact-table"
        />
      )}
      {!robustRows.length && !foldRows.length && (
        <Text type="secondary">
          复用上方回测表单的标的/区间/资金/成本；为至少一个模板参数填网格后运行。
                  </Text>
      )}
      {contextHolder}
    </Card>
  )
}
