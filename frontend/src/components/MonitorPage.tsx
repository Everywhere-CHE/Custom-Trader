/** 监控中心页：市场环境 Regime 卡 + 监控规则 CRUD + 告警记录。
 *  规则每 5 秒由后端定时评估（tick 快照），命中后落盘告警并推送通知，
 *  可选自动向模拟盘跟单。 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import * as echarts from 'echarts'
import {
  clearMonitorAlerts,
  createMonitorRule,
  deleteMonitorRule,
  getAbnormalMoves,
  getMarketPhase,
  getMonitorAlerts,
  getMonitorRules,
  getRegime,
  getScreenerStrategies,
  runScreener,
  toggleMonitorRule,
  updateMonitorRule,
} from '../api/http'
import type {
  AbnormalItem,
  AbnormalResult,
  MarketPhaseResult,
  MonitorAlert,
  MonitorRule,
  RegimeResult,
  ScreenerResult,
  ScreenerStrategy,
} from '../api/http'
import { Tabs } from 'antd'
import GlassPage from './ui/GlassPage'
import { useMarketStore } from '../stores/market'
import { useThemeStore } from '../stores/theme'

const { Text } = Typography

/** 异动表数值防御：后端出现字符串/null 时降级显示，不让整页白屏。 */
function fmtNum(v: unknown, digits = 2): string {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(digits) : '-'
}

const FIELD_OPTIONS = [
  { label: '最新价 last_price', value: 'last_price' },
  { label: '涨跌幅% pct_chg', value: 'pct_chg' },
  { label: '累计成交量 volume', value: 'volume' },
  { label: '最高价 high', value: 'high' },
  { label: '最低价 low', value: 'low' },
  { label: '开盘价 open', value: 'open' },
  { label: '买一价 bid1', value: 'bid1' },
  { label: '卖一价 ask1', value: 'ask1' },
]
const OP_OPTIONS = ['>', '>=', '<', '<=', '==', '!='].map(op => ({ label: op, value: op }))

const LABEL_COLORS: Record<string, string> = {
  强势: 'red',
  偏强: 'volcano',
  震荡: 'gold',
  偏弱: 'green',
  弱势: 'cyan',
}

interface ConditionRow {
  field: string
  op: string
  value: number
}

interface RuleFormValues {
  name: string
  scope_type: 'symbols' | 'watchlist'
  scope_symbols?: string[]
  conditions: ConditionRow[]
  logic: 'and' | 'or'
  cooldown_seconds: number
  paper_enabled?: boolean
  paper_direction?: 'buy' | 'sell'
  paper_volume?: number
}

export default function MonitorPage() {
  const [messageApi, contextHolder] = message.useMessage()
  const contracts = useMarketStore(s => s.contracts)
  const mode = useThemeStore(s => s.mode)

  const [regime, setRegime] = useState<RegimeResult | null>(null)
  const [phase, setPhase] = useState<MarketPhaseResult | null>(null)
  const [abnormal, setAbnormal] = useState<AbnormalResult | null>(null)
  const [scanCatalog, setScanCatalog] = useState<ScreenerStrategy[]>([])
  const [scanSelected, setScanSelected] = useState<string[]>([])
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState<ScreenerResult | null>(null)
  const [rules, setRules] = useState<MonitorRule[]>([])
  const [alerts, setAlerts] = useState<MonitorAlert[]>([])
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<MonitorRule | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<RuleFormValues>()
  const chartRef = useRef<HTMLDivElement>(null)
  const phaseChartRef = useRef<HTMLDivElement>(null)
  const regimeKeyRef = useRef('')
  const phaseKeyRef = useRef('')
  const abnormalKeyRef = useRef('')
  const regimeChartInstRef = useRef<echarts.ECharts | null>(null)
  const phaseChartInstRef = useRef<echarts.ECharts | null>(null)

  const load = useCallback(async () => {
    try {
      const [r, a, g, p, ab] = await Promise.all([
        getMonitorRules(),
        getMonitorAlerts(100),
        getRegime(30).catch(() => null),
        getMarketPhase().catch(() => null),
        getAbnormalMoves().catch(() => null),
      ])
      setRules(r)
      setAlerts(a)
      // 数据去重：未变化不 setState，避免轮询触发图表重绘闪烁
      if (g) {
        const key = `${g.as_of}|${g.score}`
        if (key !== regimeKeyRef.current) {
          regimeKeyRef.current = key
          setRegime(g)
        }
      }
      if (p) {
        const key = `${p.as_of}|${p.sentiment_now?.score}|${p.history?.length ?? 0}`
        if (key !== phaseKeyRef.current) {
          phaseKeyRef.current = key
          setPhase(p)
        }
      }
      if (ab) {
        const key = `${ab.as_of}|${ab.counts?.触发 ?? 0}|${ab.items?.[0]?.closeness ?? ''}`
        if (key !== abnormalKeyRef.current) {
          abnormalKeyRef.current = key
          setAbnormal(ab)
        }
      }
    } catch {
      // 静默（轮询场景）
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [load])

  useEffect(() => {
    getScreenerStrategies()
      .then(c => {
        setScanCatalog(c)
        setScanSelected(c.map(s => s.id))
      })
      .catch(() => undefined)
  }, [])

  const doScan = useCallback(async () => {
    setScanning(true)
    try {
      const r = await runScreener(scanSelected)
      if (r.status === 'ok') {
        setScanResult(r)
        messageApi.success(
          `扫描完成：${r.universe_size} 只标的 · ${r.strategies?.map(s => `${s.name} ${s.count}`).join(' / ')}`,
        )
      } else {
        messageApi.warning(r.hint ?? '扫描失败')
      }
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`扫描失败: ${detail}`)
    } finally {
      setScanning(false)
    }
  }, [messageApi, scanSelected])

  // regime 历史小图（实例复用，数据不变不重绘）
  useEffect(() => {
    const container = chartRef.current
    if (!container || !regime?.history?.length) return
    const chart =
      regimeChartInstRef.current ??
      echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    regimeChartInstRef.current = chart
    const dates = regime.history.map(h => h.date.slice(5, 10))
    const scores = regime.history.map(h => h.score)
    const dark = mode === 'dark'
    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 34, right: 10, top: 10, bottom: 20 },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, color: dark ? '#94a3b8' : '#64748b' } },
      yAxis: { type: 'value', min: 0, max: 100, axisLabel: { fontSize: 9, color: dark ? '#94a3b8' : '#64748b' }, splitLine: { lineStyle: { opacity: 0.15 } } },
      tooltip: { trigger: 'axis' },
      series: [
        {
          type: 'line',
          data: scores,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 2, color: '#6366f1' },
          areaStyle: { opacity: 0.15 },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [
              { yAxis: 70, lineStyle: { opacity: 0.3, type: 'dashed' } },
              { yAxis: 30, lineStyle: { opacity: 0.3, type: 'dashed' } },
            ],
            label: { show: false },
          },
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
    }
  }, [regime, mode])

  // 卸载时释放两个图表实例（页面保活，极少触发）
  useEffect(() => {
    return () => {
      regimeChartInstRef.current?.dispose()
      regimeChartInstRef.current = null
      phaseChartInstRef.current?.dispose()
      phaseChartInstRef.current = null
    }
  }, [])

  // 情绪指数折线 + 沸点→冰点色带（同花顺式，实例复用）
  useEffect(() => {
    const container = phaseChartRef.current
    if (!container || !phase?.history?.length) return
    const chart =
      phaseChartInstRef.current ??
      echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    phaseChartInstRef.current = chart
    const dark = mode === 'dark'
    const zones = phase.sentiment_zones ?? [
      { name: '沸点', low: 90, high: 100 },
      { name: '过热', low: 75, high: 90 },
      { name: '微热', low: 60, high: 75 },
      { name: '微冷', low: 40, high: 60 },
      { name: '过冷', low: 25, high: 40 },
      { name: '冰点', low: 0, high: 25 },
    ]
    const ZONE_COLORS: Record<string, string> = {
      沸点: 'rgba(225, 29, 72, 0.50)',
      过热: 'rgba(244, 63, 94, 0.34)',
      微热: 'rgba(251, 146, 60, 0.26)',
      微冷: 'rgba(45, 212, 191, 0.20)',
      过冷: 'rgba(20, 184, 166, 0.30)',
      冰点: 'rgba(13, 148, 136, 0.42)',
    }
    const markAreaData: unknown[] = zones.map(z => [
      {
        yAxis: z.high,
        itemStyle: { color: ZONE_COLORS[z.name] ?? 'rgba(99,102,241,0.15)' },
        label: {
          show: true,
          position: 'insideTopLeft' as const,
          fontSize: 9,
          color: dark ? 'rgba(226,232,240,0.75)' : 'rgba(51,65,85,0.75)',
          formatter: z.name,
          distance: 2,
        },
      },
      { yAxis: z.low },
    ])
    const scores = phase.history.map(h => h.score ?? 50)
    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 34, right: 12, top: 14, bottom: 20 },
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const list = params as { dataIndex?: number }[]
          const idx = list[0]?.dataIndex ?? 0
          const h = phase.history![idx]
          return `${h.date.slice(5, 10)}<br/>情绪 ${h.score ?? '-'}（${h.zone ?? '-'}）<br/>涨停 ${h.limit_up} · 最高 ${h.max_streak} 板`
        },
      },
      xAxis: {
        type: 'category',
        data: phase.history.map(h => h.date.slice(5, 10)),
        axisLabel: { fontSize: 9, color: dark ? '#94a3b8' : '#64748b', interval: Math.max(0, Math.floor(phase.history.length / 8) - 1) },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 100,
        axisLabel: { fontSize: 9, color: dark ? '#94a3b8' : '#64748b' },
        splitLine: { show: false },
      },
      series: [
        {
          type: 'line',
          data: scores,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.6, color: dark ? '#e2e8f0' : '#334155' },
          itemStyle: { color: '#1677ff' },
          markPoint: {
            silent: true,
            symbolSize: 34,
            data: [
              {
                coord: [phase.history.length - 1, scores[scores.length - 1]],
                value: `${scores[scores.length - 1]}`,
                itemStyle: { color: '#1677ff' },
                label: { fontSize: 10, color: '#fff' },
              },
            ],
          },
          markArea: { silent: true, data: markAreaData },
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
    }
  }, [phase, mode])

  const openEditor = (rule: MonitorRule | null) => {
    setEditingRule(rule)
    if (rule) {
      form.setFieldsValue({
        name: rule.name,
        scope_type: rule.scope.type,
        scope_symbols: rule.scope.symbols,
        conditions: rule.conditions,
        logic: rule.logic,
        cooldown_seconds: rule.cooldown_seconds,
        paper_enabled: Boolean(rule.paper_action?.direction),
        paper_direction: (rule.paper_action?.direction as 'buy' | 'sell') || 'buy',
        paper_volume: rule.paper_action?.volume || 1,
      })
    } else {
      form.resetFields()
      form.setFieldsValue({
        scope_type: 'symbols',
        conditions: [{ field: 'pct_chg', op: '>=', value: 5 }],
        logic: 'and',
        cooldown_seconds: 300,
        paper_enabled: false,
        paper_direction: 'buy',
        paper_volume: 1,
      })
    }
    setEditorOpen(true)
  }

  const saveRule = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      const body: Partial<MonitorRule> = {
        name: values.name,
        scope: {
          type: values.scope_type,
          symbols: values.scope_type === 'symbols' ? values.scope_symbols ?? [] : [],
        },
        conditions: values.conditions,
        logic: values.logic,
        cooldown_seconds: values.cooldown_seconds,
        paper_action: {
          direction: values.paper_enabled ? values.paper_direction ?? 'buy' : '',
          volume: values.paper_enabled ? values.paper_volume ?? 1 : 0,
        },
      }
      if (editingRule) {
        await updateMonitorRule(editingRule.rule_id, body)
        messageApi.success('规则已更新')
      } else {
        await createMonitorRule(body)
        messageApi.success('规则已创建')
      }
      setEditorOpen(false)
      await load()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`保存失败: ${detail}`)
    } finally {
      setSaving(false)
    }
  }

  const ruleColumns: ColumnsType<MonitorRule> = [
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 64,
      render: (v: boolean, r) => (
        <Switch
          size="small"
          checked={v}
          onChange={async checked => {
            await toggleMonitorRule(r.rule_id, checked)
            await load()
          }}
        />
      ),
    },
    { title: '名称', dataIndex: 'name', width: 150 },
    {
      title: '范围',
      key: 'scope',
      width: 170,
      ellipsis: true,
      render: (_: unknown, r) =>
        r.scope.type === 'watchlist' ? (
          <Tag>自选分组</Tag>
        ) : (
          <span className="mono" style={{ fontSize: 11 }}>
            {r.scope.symbols.join(', ')}
          </span>
        ),
    },
    {
      title: '条件',
      key: 'conditions',
      render: (_: unknown, r) => (
        <span className="mono" style={{ fontSize: 11 }}>
          {r.conditions.map(c => `${c.field} ${c.op} ${c.value}`).join(` ${r.logic === 'and' ? '且' : '或'} `)}
        </span>
      ),
    },
    { title: '冷却', dataIndex: 'cooldown_seconds', width: 70, render: (v: number) => `${v}s` },
    {
      title: '跟单',
      key: 'paper',
      width: 80,
      render: (_: unknown, r) =>
        r.paper_action?.direction ? (
          <Tag color={r.paper_action.direction === 'buy' ? 'red' : 'green'}>
            {r.paper_action.direction === 'buy' ? '买入' : '卖出'}×{r.paper_action.volume}
          </Tag>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '最近触发',
      dataIndex: 'last_triggered_at',
      width: 140,
      render: (v: string | null) =>
        v ? <span className="mono" style={{ fontSize: 11 }}>{v.slice(5, 19).replace('T', ' ')}</span> : <Text type="secondary">未触发</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: unknown, r) => (
        <Space size={4}>
          <Button size="small" onClick={() => openEditor(r)}>
            编辑
          </Button>
          <Popconfirm
            title="删除该规则？"
            onConfirm={async () => {
              await deleteMonitorRule(r.rule_id)
              messageApi.success('已删除')
              await load()
            }}
          >
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const alertColumns: ColumnsType<MonitorAlert> = useMemo(
    () => [
      { title: '时间', dataIndex: 'time', width: 150, render: (v: string) => <span className="mono" style={{ fontSize: 11 }}>{v.slice(5, 19).replace('T', ' ')}</span> },
      { title: '规则', dataIndex: 'rule_name', width: 150 },
      { title: '合约', dataIndex: 'vt_symbol', width: 130, className: 'mono' },
      { title: '内容', dataIndex: 'message' },
    ],
    [],
  )

  const paperSwitch = Form.useWatch('paper_enabled', form)

  return (
    <GlassPage>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {contextHolder}

        {/* 市场环境 */}
        <Card size="small" title="市场环境（自选池口径）">
          {regime?.status === 'ok' ? (
            <Row gutter={16}>
              <Col span={4}>
                <Statistic
                  title="综合分"
                  value={regime.score}
                  suffix={`/100`}
                  valueStyle={{ fontSize: 22, color: (regime.score ?? 0) >= 55 ? 'var(--up-color)' : (regime.score ?? 0) < 45 ? 'var(--down-color)' : undefined }}
                />
                <Tag color={LABEL_COLORS[regime.label ?? ''] ?? 'default'} style={{ marginTop: 4 }}>
                  {regime.label}
                </Tag>
              </Col>
              <Col span={6}>
                <Statistic title="趋势(站上MA20)" value={regime.dimensions?.trend} suffix="/100" valueStyle={{ fontSize: 15 }} />
                <Statistic title="广度(5日上涨占比)" value={regime.dimensions?.breadth} suffix="/100" valueStyle={{ fontSize: 15 }} />
              </Col>
              <Col span={6}>
                <Statistic title="动量(平均5日涨跌)" value={regime.dimensions?.momentum} suffix="/100" valueStyle={{ fontSize: 15 }} />
                <Statistic title="波动(高分=低波动)" value={regime.dimensions?.volatility} suffix="/100" valueStyle={{ fontSize: 15 }} />
              </Col>
              <Col span={8}>
                <div ref={chartRef} style={{ height: 92 }} />
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {regime.note} · 样本 {regime.universe_size} 个标的 · 截至 {regime.as_of}
                </Text>
              </Col>
            </Row>
          ) : (
            <Text type="secondary">{regime?.hint ?? '加载中…'}</Text>
          )}
        </Card>

        {/* 市场情绪（全市场口径） */}
        <Card size="small" title="市场情绪（全市场涨停梯队口径）">
          {phase?.status === 'ok' ? (
            <Row gutter={16}>
              <Col span={4}>
                <Statistic
                  title={`情绪指数（${phase.sentiment_now?.zone ?? '-'}）`}
                  value={phase.sentiment_now?.score ?? '-'}
                  suffix="/100"
                  valueStyle={{ fontSize: 20 }}
                />
                <Tag
                  color={{
                    冰点: 'cyan', 过冷: 'green', 微冷: 'default',
                    微热: 'orange', 过热: 'volcano', 沸点: 'magenta',
                  }[phase.sentiment_now?.zone ?? ''] ?? 'default'}
                  style={{ marginTop: 2 }}
                >
                  阶段：{phase.phase ?? '-'}
                </Tag>
                <Text type="secondary" style={{ fontSize: 11, display: 'block' }}>
                  截至 {phase.as_of} · {phase.universe_size} 只
                </Text>
              </Col>
              <Col span={7}>
                <Row gutter={[8, 4]}>
                  <Col span={8}><Statistic title="涨停" value={phase.ladder_stats?.limit_up} valueStyle={{ fontSize: 15 }} /></Col>
                  <Col span={8}><Statistic title="首板" value={phase.ladder_stats?.first_board} valueStyle={{ fontSize: 15 }} /></Col>
                  <Col span={8}><Statistic title="最高连板" value={phase.ladder_stats?.max_streak} valueStyle={{ fontSize: 15 }} /></Col>
                  <Col span={8}><Statistic title="2板+" value={phase.ladder_stats?.ge2} valueStyle={{ fontSize: 15 }} /></Col>
                  <Col span={8}><Statistic title="3板+" value={phase.ladder_stats?.ge3} valueStyle={{ fontSize: 15 }} /></Col>
                  <Col span={8}><Statistic title="5板+" value={phase.ladder_stats?.ge5} valueStyle={{ fontSize: 15 }} /></Col>
                  <Col span={8}><Statistic title="跌停" value={phase.ladder_stats?.limit_down ?? '-'} valueStyle={{ fontSize: 15, color: 'var(--down-color)' }} /></Col>
                </Row>
              </Col>
              <Col span={5}>
                <Statistic
                  title="晋级率（昨涨停今续板）"
                  value={phase.ladder_stats?.promotion_rate != null ? `${Math.round(phase.ladder_stats.promotion_rate * 100)}%` : '样本不足'}
                  valueStyle={{ fontSize: 15 }}
                />
                <Statistic
                  title="炸板率"
                  value={phase.ladder_stats?.blast_rate != null ? `${Math.round(phase.ladder_stats.blast_rate * 100)}%` : '-'}
                  valueStyle={{ fontSize: 15 }}
                />
              </Col>
              <Col span={8}>
                <div ref={phaseChartRef} style={{ height: 108 }} />
                <Text type="secondary" style={{ fontSize: 11 }}>{phase.note}</Text>
              </Col>
              {(phase.ladder_top?.length ?? 0) > 0 && (
                <Col span={24}>
                  <Space size={6} wrap style={{ marginTop: 6 }}>
                    {phase.ladder_top!.map(it => (
                      <Tag key={it.vt_symbol} color={it.streak >= 5 ? 'magenta' : it.streak >= 3 ? 'red' : it.streak >= 2 ? 'volcano' : 'default'}>
                        {it.streak}板 {it.name}
                      </Tag>
                    ))}
                  </Space>
                </Col>
              )}
            </Row>
          ) : (
            <Text type="secondary">{phase?.hint ?? '加载中…'}</Text>
          )}
        </Card>

        {/* 全市场扫描 */}
        <Card
          size="small"
          title="全市场扫描（内置信号策略）"
          extra={
            <Space>
              <Button
                size="small"
                type="primary"
                loading={scanning}
                onClick={() => void doScan()}
              >
                扫描全市场
              </Button>
            </Space>
          }
        >
          <Space wrap style={{ marginBottom: 8 }}>
            <Select
              mode="multiple"
              style={{ minWidth: 420 }}
              maxTagCount={3}
              placeholder="选择扫描策略（默认全部）"
              value={scanSelected}
              onChange={v => setScanSelected(v)}
              options={scanCatalog.map(s => ({
                label: `${s.name} · ${s.description}`,
                value: s.id,
              }))}
            />
          </Space>
          {scanResult?.status === 'ok' ? (
            <Tabs
              size="small"
              items={(scanResult.strategies ?? []).map(s => ({
                key: s.id,
                label: `${s.name} (${s.count})`,
                children: (
                  <Table
                    size="small"
                    rowKey="vt_symbol"
                    pagination={{ pageSize: 8, showSizeChanger: false }}
                    className="compact-table"
                    dataSource={s.items}
                    columns={[
                      { title: '标的', dataIndex: 'vt_symbol', width: 120, className: 'mono' },
                      { title: '名称', dataIndex: 'name', width: 110 },
                      { title: '现价', dataIndex: 'close', width: 90, className: 'mono' },
                      { title: '信号', dataIndex: 'detail' },
                    ]}
                    locale={{ emptyText: '无命中' }}
                  />
                ),
              }))}
            />
          ) : (
            <Text type="secondary">
              选择策略后点「扫描全市场」——对 ~5579 只 A股执行 6 类内置信号判定
              （突破新高/放量上涨/均线多头/金叉/涨停基因/RPS60 强度），约 5 秒出结果，缓存 30 分钟。
            </Text>
          )}
        </Card>

        {/* 异动监控（交易所口径） */}
        <Card size="small" title="异动监控（交易所异常波动口径）">
          {abnormal?.status === 'ok' ? (
            <>
              <Space size={24} wrap style={{ marginBottom: 8 }}>
                <Statistic title="触发(≥1.0)" value={abnormal.counts?.触发 ?? 0} valueStyle={{ fontSize: 16, color: 'var(--up-color)' }} />
                <Statistic title="边缘(≥0.7)" value={abnormal.counts?.边缘 ?? 0} valueStyle={{ fontSize: 16 }} />
                <Statistic title="关注(≥0.5)" value={abnormal.counts?.关注 ?? 0} valueStyle={{ fontSize: 16 }} />
                <Statistic title="全市场3日均涨幅" value={`${abnormal.baseline3_pct ?? '-'}%`} valueStyle={{ fontSize: 16 }} />
                <Statistic title="数据截至" value={abnormal.as_of ?? '-'} valueStyle={{ fontSize: 16 }} />
              </Space>
              <Table
                size="small"
                rowKey="vt_symbol"
                pagination={false}
                className="compact-table"
                dataSource={abnormal.items ?? []}
                columns={[
                  { title: '标的', dataIndex: 'vt_symbol', width: 120, className: 'mono' },
                  { title: '名称', dataIndex: 'name', width: 100 },
                  { title: '板块', dataIndex: 'board', width: 80, render: (v: string) => <Tag>{v}</Tag> },
                  {
                    title: '3日涨幅',
                    dataIndex: 'ret3_pct',
                    width: 90,
                    render: (v: number) => {
                      const n = Number(v)
                      return (
                        <span className={n >= 0 ? 'price-up' : 'price-down'}>
                          {n >= 0 ? '+' : ''}{fmtNum(v)}%
                        </span>
                      )
                    },
                  },
                  {
                    title: '偏离值',
                    dataIndex: 'dev3_pct',
                    width: 90,
                    render: (v: number) => {
                      const n = Number(v)
                      return (
                        <span className={n >= 0 ? 'price-up' : 'price-down'}>
                          {n >= 0 ? '+' : ''}{fmtNum(v)}%
                        </span>
                      )
                    },
                  },
                  { title: '阈值', dataIndex: 'threshold_pct', width: 70, render: (v: number) => `±${v}%` },
                  {
                    title: '接近度',
                    dataIndex: 'closeness',
                    width: 90,
                    sorter: (a: AbnormalItem, b: AbnormalItem) => Math.abs(a.closeness) - Math.abs(b.closeness),
                    render: (v: number) => {
                      const n = Number(v)
                      return <b>{n >= 0 ? '+' : ''}{fmtNum(v)}</b>
                    },
                  },
                  {
                    title: '级别',
                    dataIndex: 'level',
                    width: 100,
                    filters: [
                      { text: '触发', value: '触发' },
                      { text: '边缘', value: '边缘' },
                      { text: '关注', value: '关注' },
                    ],
                    onFilter: (v, r) => (r.level as string).startsWith(String(v)),
                    render: (v: string) => (
                      <Tag color={v.startsWith('触发') ? 'red' : v.startsWith('边缘') ? 'orange' : v.startsWith('关注') ? 'gold' : 'default'}>
                        {v}
                      </Tag>
                    ),
                  },
                  { title: '10日', dataIndex: 'ret10_pct', width: 80, render: (v: number | null) => (v == null || !Number.isFinite(Number(v)) ? '-' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`) },
                  { title: '30日', dataIndex: 'ret30_pct', width: 80, render: (v: number | null) => (v == null || !Number.isFinite(Number(v)) ? '-' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`) },
                ]}
                locale={{ emptyText: '暂无异动' }}
              />
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 6 }}>
                {abnormal.note}
              </Text>
            </>
          ) : (
            <Text type="secondary">{abnormal?.hint ?? '加载中…'}</Text>
          )}
        </Card>

        {/* 监控规则 */}
        <Card
          size="small"
          title="监控规则"
          extra={
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)}>
              新建规则
            </Button>
          }
        >
          <Table<MonitorRule>
            size="small"
            rowKey="rule_id"
            columns={ruleColumns}
            dataSource={rules}
            pagination={false}
            locale={{ emptyText: '暂无规则——新建一条，如「600519.SSE 涨跌幅 >= 5」' }}
            className="compact-table"
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            后端每 5 秒用最新行情快照评估；命中后写入告警记录并推送通知（事件开关见设置页），
            冷却期内同一规则+合约不重复告警；开启「自动跟单」后命中即向模拟盘下市价单。
          </Text>
        </Card>

        {/* 告警记录 */}
        <Card
          size="small"
          title={`告警记录 (${alerts.length})`}
          extra={
            <Popconfirm title="清空全部告警记录？" onConfirm={() => void clearMonitorAlerts().then(load)}>
              <Button size="small">清空</Button>
            </Popconfirm>
          }
        >
          <Table<MonitorAlert>
            size="small"
            rowKey={(a, i) => `${a.time}-${i}`}
            columns={alertColumns}
            dataSource={alerts}
            pagination={{ pageSize: 8, showSizeChanger: false }}
            locale={{ emptyText: '暂无告警' }}
            className="compact-table"
          />
        </Card>

        {/* 规则编辑器 */}
        <Modal
          title={editingRule ? '编辑规则' : '新建规则'}
          open={editorOpen}
          onCancel={() => setEditorOpen(false)}
          onOk={saveRule}
          confirmLoading={saving}
          width={640}
          okText="保存"
        >
          <Form form={form} layout="vertical" size="middle">
            <Row gutter={12}>
              <Col span={10}>
                <Form.Item name="name" label="规则名称" rules={[{ required: true, message: '请输入名称' }]}>
                  <Input placeholder="如：IF 突破早高点" />
                </Form.Item>
              </Col>
              <Col span={7}>
                <Form.Item name="scope_type" label="监控范围">
                  <Select
                    options={[
                      { label: '指定合约', value: 'symbols' },
                      { label: '自选分组（跟随自选列表）', value: 'watchlist' },
                    ]}
                    onChange={() => undefined}
                  />
                </Form.Item>
              </Col>
              <Col span={7}>
                <Form.Item name="cooldown_seconds" label="冷却（秒）" tooltip="同一规则+合约在冷却期内不重复告警">
                  <InputNumber min={0} max={86400} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item noStyle shouldUpdate={(p, c) => p.scope_type !== c.scope_type}>
              {({ getFieldValue }) =>
                getFieldValue('scope_type') === 'symbols' ? (
                  <Form.Item
                    name="scope_symbols"
                    label="合约（多选）"
                    rules={[{ required: true, message: '至少选择一个合约' }]}
                  >
                    <Select
                      mode="multiple"
                      showSearch
                      maxTagCount={4}
                      options={contracts.map(c => ({ label: `${c.vt_symbol} ${c.name}`, value: c.vt_symbol }))}
                      filterOption={(input, option) =>
                        (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
                      }
                    />
                  </Form.Item>
                ) : null
              }
            </Form.Item>
            <Form.Item label="触发条件" required style={{ marginBottom: 8 }}>
              <Form.List name="conditions">
                {(fields, { add, remove }) => (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {fields.map(field => (
                      <Space key={field.key} align="baseline" style={{ display: 'flex' }}>
                        <Form.Item name={[field.name, 'field']} noStyle>
                          <Select style={{ width: 170 }} options={FIELD_OPTIONS} />
                        </Form.Item>
                        <Form.Item name={[field.name, 'op']} noStyle>
                          <Select style={{ width: 70 }} options={OP_OPTIONS} />
                        </Form.Item>
                        <Form.Item
                          name={[field.name, 'value']}
                          noStyle
                          rules={[{ required: true, message: '必填' }]}
                        >
                          <InputNumber style={{ width: 120 }} placeholder="阈值" />
                        </Form.Item>
                        <Button
                          size="small"
                          type="text"
                          disabled={fields.length <= 1}
                          onClick={() => remove(field.name)}
                        >
                          删除
                        </Button>
                      </Space>
                    ))}
                    <div>
                      <Button size="small" icon={<PlusOutlined />} onClick={() => add({ field: 'pct_chg', op: '>=', value: 1 })} disabled={fields.length >= 8}>
                        添加条件
                      </Button>
                    </div>
                  </div>
                )}
              </Form.List>
            </Form.Item>
            <Form.Item name="logic" label="条件组合">
              <Select
                style={{ width: 140 }}
                options={[
                  { label: '全部满足（AND）', value: 'and' },
                  { label: '任一满足（OR）', value: 'or' },
                ]}
              />
            </Form.Item>
            <Card size="small" style={{ background: 'var(--subcard-bg)' }} title="自动跟单（可选）">
              <Space size={16} wrap>
                <Form.Item name="paper_enabled" valuePropName="checked" style={{ marginBottom: 0 }}>
                  <Switch checkedChildren="开" unCheckedChildren="关" />
                </Form.Item>
                <Form.Item name="paper_direction" label="方向" style={{ marginBottom: 0 }}>
                  <Select
                    style={{ width: 90 }}
                    disabled={!paperSwitch}
                    options={[
                      { label: '买入', value: 'buy' },
                      { label: '卖出', value: 'sell' },
                    ]}
                  />
                </Form.Item>
                <Form.Item name="paper_volume" label="数量" style={{ marginBottom: 0 }}>
                  <InputNumber min={1} disabled={!paperSwitch} style={{ width: 110 }} />
                </Form.Item>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  命中即向模拟盘下市价单（净持仓模型）
                </Text>
              </Space>
            </Card>
          </Form>
        </Modal>
      </div>
    </GlassPage>
  )
}
