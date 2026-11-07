/** 参数优化面板：稳健筛选（K 段验证）+ Walk-Forward 滚动样本外。
 *
 * 复用父页面回测表单的合约/区间/成本参数（通过传入的 FormInstance 读取），
 * 自身维护：策略单选、优化目标、参数网格、模式专属参数与结果展示。
 */

import { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { FormInstance } from 'antd'
import {
  previewOptimizeGrid,
  runOptimize,
  getBacktestTask,
} from '../api/http'
import type {
  BacktestTask,
  RobustResult,
  StrategyClassInfo,
  WalkForwardResult,
} from '../types'

const { Text } = Typography

const METRIC_OPTIONS = [
  { label: '夏普比率', value: 'sharpe_ratio' },
  { label: '年化收益', value: 'annual_return' },
  { label: '总收益率', value: 'total_return' },
  { label: '收益回撤比', value: 'return_drawdown_ratio' },
  { label: '总净利', value: 'total_net_pnl' },
  { label: '最大回撤(越接近0越好)', value: 'max_ddpercent' },
]

/** 解析网格文本："10,20,30" → 枚举；"5-60:5" → 等差区间 */
export function parseGridSpec(
  text: string,
): number[] | { start: number; end: number; step: number } | null {
  const t = text.trim()
  if (!t) return null
  const rangeMatch = t.match(/^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?):(-?\d+(?:\.\d+)?)$/)
  if (rangeMatch) {
    const start = Number(rangeMatch[1])
    const end = Number(rangeMatch[2])
    const step = Number(rangeMatch[3])
    if (Number.isFinite(start) && Number.isFinite(end) && Number.isFinite(step) && step > 0) {
      return { start, end, step }
    }
    return null
  }
  const parts = t
    .split(/[,，、\s]+/)
    .map(Number)
    .filter(v => Number.isFinite(v))
  return parts.length ? Array.from(new Set(parts)) : null
}

interface OptimizeRow {
  rank: number
  params_text: string
  median: number
  worst: number
  robust_score: number
  segment_scores: number[]
  valid_segments: number
  errors: string[]
}

interface FoldRow {
  fold: number
  key: number
  train: string
  test: string
  status: string
  best_params_text?: string
  is_score?: number
  oos_score?: number
  oos_total_return?: number
  oos_trades?: number
  errors?: string[]
}

export default function OptimizerPanel({
  classes,
  form,
}: {
  classes: StrategyClassInfo[]
  form: FormInstance
}) {
  const [messageApi, contextHolder] = message.useMessage()
  const [class_name, setClassName] = useState<string>('')
  const [mode, setMode] = useState<'robust' | 'walkforward'>('robust')
  const [metric, setMetric] = useState('sharpe_ratio')
  const [kSegments, setKSegments] = useState(3)
  const [trainDays, setTrainDays] = useState(252)
  const [testDays, setTestDays] = useState(63)
  const [stepDays, setStepDays] = useState(63)
  const [gridTexts, setGridTexts] = useState<Record<string, string>>({})
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState('')
  const [robust, setRobust] = useState<RobustResult | null>(null)
  const [wf, setWf] = useState<WalkForwardResult | null>(null)

  const classInfo = useMemo(
    () => classes.find(c => c.class_name === class_name),
    [classes, class_name],
  )
  const paramNames = useMemo(
    () => (classInfo ? Object.keys(classInfo.parameters) : []),
    [classInfo],
  )

  const buildGrid = (): Record<string, number[] | { start: number; end: number; step: number }> | null => {
    const grid: Record<string, number[] | { start: number; end: number; step: number }> = {}
    for (const name of paramNames) {
      const parsed = parseGridSpec(gridTexts[name] || '')
      if (parsed) grid[name] = parsed
    }
    if (!Object.keys(grid).length) {
      messageApi.warning('请至少为一个策略参数填写网格（如 10,20,30 或 5-60:5）')
      return null
    }
    return grid
  }

  const preview = async () => {
    const grid = buildGrid()
    if (!grid) return
    try {
      const r = await previewOptimizeGrid({ grid })
      messageApi.success(`共 ${r.count} 个参数组合（上限 ${r.max_combos}）`)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`网格无效: ${detail}`)
    }
  }

  const run = async () => {
    if (!class_name) {
      messageApi.warning('请选择要优化的策略类')
      return
    }
    const grid = buildGrid()
    if (!grid) return
    // 合约/区间/成本复用主回测表单
    const values = form.getFieldsValue() as {
      vt_symbols?: string[]
      interval?: string
      range?: [{ format: (f: string) => string }, { format: (f: string) => string }]
      capital?: number
      rate?: number
      slippage?: number
      size?: number
      pricetick?: number
    }
    const vt_symbol = values.vt_symbols?.[0]
    if (!vt_symbol) {
      messageApi.warning('请先在上方表单选择合约（取第一个）')
      return
    }
    if (!values.range?.[0]) {
      messageApi.warning('请先在上方表单选择回测区间')
      return
    }
    setRunning(true)
    setProgress('')
    setRobust(null)
    setWf(null)
    try {
      const task = await runOptimize({
        mode,
        class_name,
        vt_symbol,
        interval: values.interval || 'd',
        start: values.range[0].format('YYYY-MM-DDTHH:mm:ss'),
        end: values.range[1].format('YYYY-MM-DDTHH:mm:ss'),
        metric,
        grid,
        setting: {},
        capital: values.capital,
        rate: values.rate,
        slippage: values.slippage,
        size: values.size,
        pricetick: values.pricetick,
        k_segments: kSegments,
        train_days: trainDays,
        test_days: testDays,
        step_days: stepDays,
      })
      let last: BacktestTask = task
      while (last.status === 'pending' || last.status === 'running') {
        setProgress(last.message)
        await new Promise(r => setTimeout(r, 1200))
        last = await getBacktestTask(task.task_id)
      }
      if (last.status === 'done' && last.result) {
        const res = last.result as Record<string, unknown>
        if (res.mode === 'robust') setRobust(res as unknown as RobustResult)
        else setWf(res as unknown as WalkForwardResult)
        messageApi.success('优化完成')
      } else {
        messageApi.error(`优化失败: ${last.error || '未知错误'}`)
      }
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`优化失败: ${detail}`)
    } finally {
      setRunning(false)
      setProgress('')
    }
  }

  // ---- 结果表 ----
  const robustColumns: ColumnsType<OptimizeRow> = [
    { title: '#', dataIndex: 'rank', width: 44 },
    { title: '参数组合', dataIndex: 'params_text', width: 190, className: 'mono' },
    {
      title: '稳健分',
      dataIndex: 'robust_score',
      width: 90,
      sorter: (a, b) => a.robust_score - b.robust_score,
      render: (v: number) => (
        <b style={{ color: v > 0 ? 'var(--up-color)' : 'var(--down-color)' }}>
          {v.toFixed(3)}
        </b>
      ),
    },
    { title: '中位', dataIndex: 'median', width: 80, render: (v: number) => v.toFixed(3) },
    {
      title: '最差段',
      dataIndex: 'worst',
      width: 84,
      render: (v: number) => (
        <span style={{ color: v < 0 ? 'var(--down-color)' : undefined }}>{v.toFixed(3)}</span>
      ),
    },
    ...Array.from({ length: robust?.k_segments ?? 0 }, (_, i) => ({
      title: `段${i + 1}`,
      key: `seg${i}`,
      width: 76,
      render: (_: unknown, row: OptimizeRow) => {
        const v = row.segment_scores[i]
        if (v == null || !Number.isFinite(v)) return <Tag>无效</Tag>
        return (
          <span style={{ color: v > 0 ? 'var(--up-color)' : 'var(--down-color)' }}>
            {v.toFixed(2)}
          </span>
        )
      },
    })),
    { title: '有效段', dataIndex: 'valid_segments', width: 70 },
  ]

  const foldColumns: ColumnsType<FoldRow> = [
    { title: '折', dataIndex: 'fold', width: 44 },
    { title: '训练段(IS)', dataIndex: 'train', width: 175, className: 'mono' },
    { title: '测试段(OOS)', dataIndex: 'test', width: 175, className: 'mono' },
    { title: '冠军参数', dataIndex: 'best_params_text', width: 170, className: 'mono' },
    { title: 'IS分', dataIndex: 'is_score', width: 80, render: (v?: number) => v?.toFixed(3) ?? '-' },
    {
      title: 'OOS分',
      dataIndex: 'oos_score',
      width: 90,
      render: (v?: number) =>
        v == null ? '-' : (
          <b style={{ color: v > 0 ? 'var(--up-color)' : 'var(--down-color)' }}>{v.toFixed(3)}</b>
        ),
    },
    { title: 'OOS收益%', dataIndex: 'oos_total_return', width: 90, render: (v?: number) => v?.toFixed(2) ?? '-' },
    { title: 'OOS笔数', dataIndex: 'oos_trades', width: 76 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) =>
        v === 'ok' ? <Tag color="green">完成</Tag> : <Tag color="red">{v}</Tag>,
    },
  ]

  const fmtSeg = (s: { start: string; end: string }) =>
    `${s.start.slice(0, 10)} → ${s.end.slice(0, 10)}`

  return (
    <Card
      size="small"
      title="参数优化（防过拟合）"
      extra={
        <Space>
          <Button onClick={preview}>预览网格</Button>
          <Button type="primary" loading={running} onClick={run}>
            {running && progress ? `${progress}...` : '开始优化'}
          </Button>
        </Space>
      }
    >
      {contextHolder}
      <Space wrap size={8} style={{ marginBottom: 10 }}>
        <Select
          style={{ width: 230 }}
          placeholder="策略类（单选）"
          value={class_name || undefined}
          onChange={v => {
            setClassName(v)
            setGridTexts({})
          }}
          options={classes.map(c => ({
            label: c.display_name || c.class_name,
            value: c.class_name,
          }))}
        />
        <Select style={{ width: 150 }} value={mode} onChange={setMode}
          options={[
            { label: '稳健筛选（K段）', value: 'robust' },
            { label: 'Walk-Forward', value: 'walkforward' },
          ]}
        />
        <Select style={{ width: 170 }} value={metric} onChange={setMetric}
          options={METRIC_OPTIONS}
        />
        {mode === 'robust' ? (
          <>
            <span className="text-xs text-slate-500">K 段</span>
            <InputNumber min={2} max={6} value={kSegments} onChange={v => setKSegments(v ?? 3)} style={{ width: 64 }} />
          </>
        ) : (
          <>
            <span className="text-xs text-slate-500">训练/测试/步长(天)</span>
            <InputNumber min={30} max={1500} value={trainDays} onChange={v => setTrainDays(v ?? 252)} style={{ width: 78 }} />
            <InputNumber min={14} max={500} value={testDays} onChange={v => setTestDays(v ?? 63)} style={{ width: 70 }} />
            <InputNumber min={14} max={500} value={stepDays} onChange={v => setStepDays(v ?? 63)} style={{ width: 70 }} />
          </>
        )}
      </Space>

      {paramNames.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            参数网格（枚举用逗号分隔如 <code>10,20,30</code>；等差用 <code>5-60:5</code>；
            留空 = 不参与优化；合约/区间/成本使用上方回测表单）
          </Text>
          <Row gutter={[8, 4]} style={{ marginTop: 4 }}>
            {paramNames.map(name => (
              <Col key={name} span={4} style={{ minWidth: 210 }}>
                <Tooltip title={classInfo?.param_descriptions?.[name]}>
                  <Input
                    size="small"
                    addonBefore={name}
                    placeholder="10,20,30 或 5-60:5"
                    value={gridTexts[name] || ''}
                    onChange={e => setGridTexts(prev => ({ ...prev, [name]: e.target.value }))}
                  />
                </Tooltip>
              </Col>
            ))}
          </Row>
        </div>
      )}

      {/* ---- 稳健筛选结果 ---- */}
      {robust && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {robust.total_combos} 组合 × {robust.k_segments} 段 = {robust.total_runs} 次回测；
            有效 {robust.valid_count} / 失败 {robust.error_count}；{robust.robust_formula}
          </Text>
          {robust.champion ? (
            <Card size="small" style={{ background: 'var(--subcard-bg)' }}>
              <Row gutter={12}>
                <Col span={4}>
                  <Statistic title="冠军参数" value={robust.champion.params_text} valueStyle={{ fontSize: 13 }} />
                </Col>
                <Col span={3}>
                  <Statistic title="稳健分" value={robust.champion.robust_score.toFixed(3)} valueStyle={{ fontSize: 16 }} />
                </Col>
                <Col span={3}>
                  <Statistic
                    title="收敛度"
                    value={{ unanimous: '全体一致', majority: '多数收敛', scattered: '分散' }[robust.champion.convergence]}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={3}>
                  <Statistic
                    title="参数形态"
                    value={{ plateau: '高原✓', peak: '孤峰⚠', ridge: '山脊', unknown: '未知' }[robust.champion.plateau]}
                    valueStyle={{
                      fontSize: 16,
                      color: robust.champion.plateau === 'plateau' ? 'var(--up-color)' : 'var(--down-color)',
                    }}
                  />
                </Col>
                <Col span={4}>
                  <Statistic
                    title="全窗-IS偏差"
                    value={robust.champion.in_sample_bias ?? '-'}
                    precision={robust.champion.in_sample_bias != null ? 3 : undefined}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={4}>
                  <Statistic title="最差段" value={robust.champion.worst.toFixed(3)} valueStyle={{ fontSize: 16 }} />
                </Col>
              </Row>
              {robust.champion.notes.length > 0 && (
                <Alert
                  style={{ marginTop: 8 }}
                  type={robust.champion.plateau === 'peak' || robust.champion.convergence === 'scattered' ? 'warning' : 'info'}
                  showIcon
                  message="判读"
                  description={
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {robust.champion.notes.map((n, i) => (
                        <li key={i}>{n}</li>
                      ))}
                    </ul>
                  }
                />
              )}
            </Card>
          ) : (
            <Alert type="error" showIcon message="无有效组合：所有参数在至少一段上回测失败（数据不足或报错）" />
          )}
          <Table<OptimizeRow>
            size="small"
            rowKey="rank"
            columns={robustColumns}
            dataSource={robust.results.map(r => ({ ...r, key: r.rank }))}
            pagination={false}
            className="compact-table"
          />
        </div>
      )}

      {/* ---- Walk-Forward 结果 ---- */}
      {wf && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Row gutter={12}>
            <Col span={3}>
              <Statistic title="有效折" value={`${wf.valid_folds}/${wf.folds.length}`} valueStyle={{ fontSize: 16 }} />
            </Col>
            <Col span={3}>
              <Statistic title="IS 均值" value={wf.is_mean?.toFixed(3) ?? '-'} valueStyle={{ fontSize: 16 }} />
            </Col>
            <Col span={3}>
              <Statistic title="OOS 均值" value={wf.oos_mean?.toFixed(3) ?? '-'} valueStyle={{ fontSize: 16 }} />
            </Col>
            <Col span={3}>
              <Statistic
                title="退化度(IS-OOS)"
                value={wf.degradation?.toFixed(3) ?? '-'}
                valueStyle={{
                  fontSize: 16,
                  color: (wf.degradation ?? 0) > 0 ? 'var(--down-color)' : 'var(--up-color)',
                }}
              />
            </Col>
            <Col span={3}>
              <Statistic
                title="OOS 为正占比"
                value={wf.consistency != null ? `${Math.round(wf.consistency * 100)}%` : '-'}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
            <Col span={3}>
              <Statistic title="OOS 平均收益%" value={wf.oos_avg_return?.toFixed(2) ?? '-'} valueStyle={{ fontSize: 16 }} />
            </Col>
            <Col span={3}>
              <Statistic title="OOS 总笔数" value={wf.oos_total_trades} valueStyle={{ fontSize: 16 }} />
            </Col>
          </Row>
          {wf.notes.length > 0 && (
            <Alert
              type={wf.degradation != null && wf.degradation > 0 ? 'warning' : 'info'}
              showIcon
              message="判读"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {wf.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              }
            />
          )}
          <Table<FoldRow>
            size="small"
            rowKey="key"
            columns={foldColumns}
            dataSource={wf.folds.map(f => ({
              ...f,
              key: f.fold,
              train: fmtSeg(f.train),
              test: fmtSeg(f.test),
            }))}
            pagination={false}
            className="compact-table"
          />
        </div>
      )}
    </Card>
  )
}
