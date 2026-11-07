/** 回测页面：参数表单、数据下载、运行回测、统计指标卡片、
 * ECharts 资金曲线、成交列表。 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  InputNumber,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import * as echarts from 'echarts'
import dayjs, { type Dayjs } from 'dayjs'
import {
  downloadBacktestData,
  getBacktestTask,
  getPerformance,
  getStrategyClasses,
  runBacktest,
} from '../api/http'
import GlassPage from './ui/GlassPage'
import GuardianCard from './GuardianCard'
import OptimizerPanel from './OptimizerPanel'
import { useMarketStore } from '../stores/market'
import { useThemeStore } from '../stores/theme'
import type { BacktestResult, BacktestStatistics, PerformanceResult, StrategyClassInfo, Trade } from '../types'

const { Text } = Typography

interface FormValues {
  class_names: string[]
  vt_symbols: string[]
  interval: string
  range: [Dayjs, Dayjs]
  capital: number
  rate: number
  slippage: number
  size: number
  pricetick: number
  [key: string]: unknown
}

export default function BacktestPage() {
  const contracts = useMarketStore(s => s.contracts)
  const mode = useThemeStore(s => s.mode)
  const [classes, setClasses] = useState<StrategyClassInfo[]>([])
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [progress, setProgress] = useState('')
  const [running, setRunning] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()
  const [form] = Form.useForm<FormValues>()
  const chartRef = useRef<HTMLDivElement>(null)
  const selectedClasses = Form.useWatch('class_names', form) as string[] | undefined
  const [perf, setPerf] = useState<PerformanceResult | null>(null)
  const [perfHours, setPerfHours] = useState(24)
  const [productFilter, setProductFilter] = useState<string>('')

  useEffect(() => {
    getStrategyClasses().then(setClasses).catch(() => undefined)
  }, [])

  const classInfo = useMemo(
    () => selectedClasses?.length ? classes.find(c => c.class_name === selectedClasses[0]) : undefined,
    [classes, selectedClasses],
  )

  // 回测结果或主题变化时重新渲染资金曲线
  useEffect(() => {
    const container = chartRef.current
    if (!container || !result || !stats) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)

    const dates = result.daily_results.map(d => d.date)
    let balance = stats.capital
    const balances = result.daily_results.map(d => {
      balance += d.net_pnl
      return Number(balance.toFixed(2))
    })
    let peak = stats.capital
    const drawdowns = balances.map(b => {
      peak = Math.max(peak, b)
      return Number((((b - peak) / peak) * 100).toFixed(3))
    })

    const dark = mode === 'dark'
    const axisText = dark ? '#94a3b8' : '#64748b'
    const gridColor = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'
    chart.setOption({
      backgroundColor: 'transparent',
      textStyle: { fontFamily: 'Inter, PingFang SC, Microsoft YaHei, sans-serif' },
      tooltip: {
        trigger: 'axis',
        backgroundColor: dark ? 'rgba(15, 23, 42, 0.92)' : 'rgba(255,255,255,0.92)',
        borderColor: dark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.06)',
        textStyle: { color: dark ? '#e2e8f0' : '#334155', fontSize: 12 },
      },
      legend: {
        data: ['资金曲线', '回撤%'],
        textStyle: { color: axisText, fontSize: 11 },
      },
      grid: [
        { left: 70, right: 30, top: 40, height: '48%' },
        { left: 70, right: 30, top: '68%', height: '22%' },
      ],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: axisText, fontSize: 10 }, axisLine: { lineStyle: { color: gridColor } } },
        { type: 'category', data: dates, gridIndex: 1, axisLabel: { color: axisText, fontSize: 10 }, axisLine: { lineStyle: { color: gridColor } } },
      ],
      yAxis: [
        { type: 'value', scale: true, gridIndex: 0, name: '资金', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10 }, splitLine: { lineStyle: { color: gridColor } } },
        { type: 'value', gridIndex: 1, name: '回撤%', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10 }, splitLine: { lineStyle: { color: gridColor } } },
      ],
      series: [
        {
          name: '资金曲线',
          type: 'line',
          data: balances,
          showSymbol: false,
          xAxisIndex: 0,
          yAxisIndex: 0,
          lineStyle: {
            width: 2.2,
            color: '#6366f1',
            shadowBlur: 8,
            shadowColor: 'rgba(99,102,241,0.45)',
          },
          areaStyle: {
            color: {
              type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(99,102,241,0.22)' },
                { offset: 1, color: 'rgba(99,102,241,0)' },
              ],
            },
          },
          color: '#6366f1',
        },
        {
          name: '回撤%',
          type: 'line',
          data: drawdowns,
          showSymbol: false,
          xAxisIndex: 1,
          yAxisIndex: 1,
          areaStyle: { opacity: 0.25 },
          lineStyle: { width: 1.2 },
          color: '#f43f5e',
        },
      ],
    })

    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [result, mode])

  const buildPayloadBase = () => {
    const values = form.getFieldsValue()
    return {
      interval: values.interval,
      start: values.range[0].format('YYYY-MM-DDTHH:mm:ss'),
      end: values.range[1].format('YYYY-MM-DDTHH:mm:ss'),
    }
  }

  const download = async () => {
    try {
      await form.validateFields(['vt_symbols', 'interval', 'range'])
    } catch {
      return
    }
    setDownloading(true)
    try {
      const values = form.getFieldsValue()
      const sym = (values.vt_symbols as string[])[0] || ''
      // 自动识别 Gateway：期货交易所走 CTP，股票走 STOCK
      const exchange = sym.split('.').pop() || ''
      const futuresExchanges = ['CFFEX', 'SHFE', 'CZCE', 'DCE', 'INE', 'GFEX']
      const gw = futuresExchanges.includes(exchange) ? 'CTP' : 'STOCK'
      const info = await downloadBacktestData({
        ...buildPayloadBase(),
        vt_symbol: sym,
        gateway_name: gw,
      })
      messageApi.success(
        `数据已入库: ${(info as { saved?: number }).saved ?? '?'} 根`,
      )
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`下载失败: ${detail}`)
    } finally {
      setDownloading(false)
    }
  }

  const run = async () => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    setRunning(true)
    setResult(null)
    setProgress('')
    try {
      const {
        class_names,
        vt_symbols,
        interval,
        range,
        capital,
        rate,
        slippage,
        size,
        pricetick,
        ...setting
      } = values
      const task = await runBacktest({
        class_names,
        vt_symbols,
        interval,
        start: range[0].format('YYYY-MM-DDTHH:mm:ss'),
        end: range[1].format('YYYY-MM-DDTHH:mm:ss'),
        capital,
        rate,
        slippage,
        size,
        pricetick,
        setting,
      })
      // 轮询直到完成（长回测不再受 HTTP 超时限制）
      let last = task
      while (last.status === 'pending' || last.status === 'running') {
        setProgress(last.message)
        await new Promise(r => setTimeout(r, 1000))
        last = await getBacktestTask(task.task_id)
      }
      if (last.status === 'done' && last.result) {
        setResult(last.result as BacktestResult)
        messageApi.success('回测完成')
      } else {
        messageApi.error(`回测失败: ${last.error || '未知错误'}`)
      }
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`回测失败: ${detail}`)
    } finally {
      setRunning(false)
      setProgress('')
    }
  }

  const rawStats = result?.statistics
  const stats: BacktestStatistics | undefined = Array.isArray(rawStats) ? (rawStats as BacktestStatistics[])[0] : rawStats as BacktestStatistics | undefined

  const tradeColumns: ColumnsType<Trade> = [
    {
      title: '时间',
      dataIndex: 'datetime',
      width: 150,
      render: (v: string | null) => v?.replace('T', ' ').slice(0, 19),
    },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 60,
      render: (v: string | null) => (
        <span className={v === 'LONG' ? 'price-up' : 'price-down'}>
          {v === 'LONG' ? '多' : '空'}
        </span>
      ),
    },
    { title: '开平', dataIndex: 'offset', width: 100, render: (v: string) => ({ OPEN: '开仓', CLOSE: '平仓', CLOSETODAY: '平今', CLOSEYESTERDAY: '平昨' })[v] ?? v },
    { title: '价格', dataIndex: 'price', width: 90, className: 'mono' },
    { title: '数量', dataIndex: 'volume', width: 70, className: 'mono' },
  ]

  const loadPerf = async (hours: number) => {
    setPerfHours(hours)
    try {
      const r = await getPerformance(hours)
      setPerf(r)
    } catch {
      messageApi.error('加载交易统计失败')
    }
  }

  return (
    <GlassPage>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {contextHolder}

      {/* 近期交易统计卡片 */}
      <Card size="small" title="近期交易统计" extra={
        <Space>
          {[1, 12, 24].map(h => (
            <Button key={h} size="small" type={perfHours === h ? 'primary' : 'default'} onClick={() => void loadPerf(h)}>
              近{h}小时
            </Button>
          ))}
        </Space>
      }>
        {perf ? (
          <Row gutter={16}>
            <Col span={2}><Statistic title="交易笔数" value={perf.total_trades} /></Col>
            <Col span={2}><Statistic title="盈利" value={perf.win_count} valueStyle={{ color: 'var(--up-color)' }} /></Col>
            <Col span={2}><Statistic title="亏损" value={perf.loss_count} valueStyle={{ color: 'var(--down-color)' }} /></Col>
            <Col span={2}><Statistic title="胜率" value={perf.win_rate} suffix="%" precision={1} /></Col>
            <Col span={2}><Statistic title="收益率" value={perf.total_return} suffix="%" precision={2} valueStyle={{ color: perf.total_return >= 0 ? 'var(--up-color)' : 'var(--down-color)' }} /></Col>
            <Col span={3}><Statistic title="已实现盈亏" value={perf.realized_pnl} precision={2} valueStyle={{ color: perf.realized_pnl >= 0 ? 'var(--up-color)' : 'var(--down-color)' }} /></Col>
            <Col span={3}><Statistic title="未实现盈亏(持仓)" value={perf.unrealized_pnl} precision={2} valueStyle={{ color: perf.unrealized_pnl >= 0 ? 'var(--up-color)' : 'var(--down-color)' }} /></Col>
            <Col span={3}><Statistic title="总盈亏" value={perf.total_pnl} precision={2} valueStyle={{ color: perf.total_pnl >= 0 ? 'var(--up-color)' : 'var(--down-color)' }} /></Col>
            <Col span={2}><Statistic title="手续费" value={perf.total_fee} precision={2} /></Col>
            <Col span={2}><Statistic title="合约" value={perf.symbols.length} /></Col>
          </Row>
        ) : (
          <Text type="secondary">点击上方按钮查看近期交易统计</Text>
        )}
      </Card>

      <Card size="small">
        <Form
          form={form}
          layout="inline"
          initialValues={{
            interval: '1m',
            range: [dayjs().subtract(10, 'day'), dayjs()],
            capital: 1_000_000,
            rate: 0.0001,
            slippage: 0,
            size: 10,
            pricetick: 1,
          }}
        >
          <Form.Item
            name="class_names"
            label="策略（多选）"
            rules={[{ required: true, message: '请选择至少一个策略' }]}
          >
            <Select
              mode="multiple"
              style={{ width: 240 }}
              options={classes.map(c => ({
                label: c.display_name || c.class_name,
                value: c.class_name,
              }))}
              placeholder="选择策略..."
            />
          </Form.Item>
          {/* 合约类型筛选 */}
          <Col span={3}>
            <Select
              size="small"
              allowClear
              placeholder="合约类型"
              value={productFilter || undefined}
              onChange={v => setProductFilter(v || '')}
              style={{ width: '100%' }}
              options={[
                { label: '全部', value: '' },
                ...Array.from(new Set(contracts.map(c => c.product))).map(p => ({
                  label: p,
                  value: p,
                })),
              ]}
            />
          </Col>
          <Form.Item
            name="vt_symbols"
            label="合约（多选）"
            rules={[{ required: true, message: '请选择至少一个合约' }]}
          >
            <Select
              mode="multiple"
              showSearch
              style={{ width: 240 }}
              options={contracts
                .filter(c => !productFilter || c.product === productFilter)
                .map(c => ({
                  label: `${c.vt_symbol} ${c.name}`,
                  value: c.vt_symbol,
                }))}
              placeholder="搜索并选择合约..."
              filterOption={(input, option) =>
                (option?.label as string)
                  ?.toLowerCase()
                  .includes(input.toLowerCase()) ?? false
              }
            />
          </Form.Item>
          <Form.Item name="interval" label="周期">
            <Select
              style={{ width: 90 }}
              options={[
                { label: '1分钟', value: '1m' },
                { label: '1小时', value: '1h' },
                { label: '日线', value: 'd' },
              ]}
            />
          </Form.Item>
          <Form.Item name="range" label="区间" rules={[{ required: true }]}>
            <DatePicker.RangePicker />
          </Form.Item>
          <Form.Item
            name="capital"
            label="资金"
            tooltip="回测起始资金（元）"
          >
            <InputNumber style={{ width: 120 }} step={100000} />
          </Form.Item>
          <Form.Item
            name="rate"
            label="费率"
            tooltip="手续费率（按成交额），如 0.0001 = 万分之一"
          >
            <InputNumber style={{ width: 100 }} step={0.0001} />
          </Form.Item>
          <Form.Item
            name="slippage"
            label="滑点"
            tooltip="每笔成交按最小变动价位计的滑点成本"
          >
            <InputNumber style={{ width: 80 }} step={1} />
          </Form.Item>
          <Form.Item
            name="size"
            label="乘数"
            tooltip="合约乘数：期货每点价值（如螺纹钢=10、IF=300），股票=1"
          >
            <InputNumber style={{ width: 80 }} step={1} />
          </Form.Item>
          <Form.Item
            name="pricetick"
            label="最小变动"
            tooltip="最小变动价位（如螺纹钢=1、IF=0.2、股票=0.01）"
          >
            <InputNumber style={{ width: 90 }} step={0.01} />
          </Form.Item>
          {classInfo &&
            Object.entries(classInfo.parameters).map(([key, defaultValue]) => (
              <Form.Item
                key={key}
                name={key}
                label={key}
                tooltip={classInfo.param_descriptions?.[key]}
                initialValue={defaultValue}
              >
                <InputNumber style={{ width: 90 }} />
              </Form.Item>
            ))}
          <Form.Item>
            <Space>
              <Button loading={downloading} onClick={download}>
                下载数据
              </Button>
              <Button type="primary" loading={running} onClick={run}>
                {running && progress ? `回测中 ${progress}...` : '开始回测'}
              </Button>
            </Space>
          </Form.Item>
        </Form>
        {classInfo?.description && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {classInfo.class_name}：{classInfo.description}
          </Text>
        )}
      </Card>

      <OptimizerPanel classes={classes} form={form} />

      <GuardianCard form={form} />

      {stats && (
        <>
          <Row gutter={8}>
            {(
              [
                ['总收益率', `${stats.total_return.toFixed(2)}%`,
                  '整个回测期的累计收益率 =（期末权益/初始资金 - 1）×100%'],
                ['年化收益', `${stats.annual_return.toFixed(2)}%`,
                  '按 240 个交易日折算的年化收益率'],
                ['最大回撤', `${stats.max_ddpercent.toFixed(2)}%`,
                  '资金曲线从峰值回落的最大百分比，衡量最坏亏损幅度'],
                ['夏普比率', stats.sharpe_ratio.toFixed(2),
                  '风险调整后收益：日均收益/波动率×√240，越高越好，>1 较好'],
                ['胜率', `${stats.win_rate.toFixed(1)}%`,
                  '盈利平仓次数占总平仓次数的比例（开平配对统计）'],
                ['盈亏比', stats.profit_factor.toFixed(2),
                  '总盈利金额 / 总亏损金额，>1 表示总体赚钱'],
                ['成交笔数', String(stats.total_trade_count),
                  '回测期间的总成交次数'],
                ['期末权益',
                  stats.end_balance.toLocaleString(undefined, {
                    maximumFractionDigits: 0,
                  }),
                  '回测结束时的账户总资金（含手续费与滑点扣除）'],
              ] as [string, string, string][]
            ).map(([title, value, tip]) => (
              <Col span={3} key={title}>
                <Card size="small">
                  <Statistic
                    title={<Tooltip title={tip}>{title} ⓘ</Tooltip>}
                    value={value}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Card>
              </Col>
            ))}
          </Row>
          <Text type="secondary" style={{ fontSize: 12 }}>
            指标说明：总收益率=期末/期初-1；年化=按240交易日折算；最大回撤=资金曲线峰值回落幅度；
            夏普=风险调整后收益（越高越好）；胜率=盈利平仓占比；盈亏比=总盈利/总亏损（鼠标悬停指标卡可查看详情）
          </Text>

          <Card size="small" title="资金曲线 / 回撤">
            <div ref={chartRef} style={{ height: 360 }} />
          </Card>

          <Card size="small" title={`成交记录 (${result?.trades.length ?? 0})`}>
            <Table
              size="small"
              rowKey="vt_tradeid"
              columns={tradeColumns}
              dataSource={result?.trades ?? []}
              pagination={{ pageSize: 10, showSizeChanger: false }}
              className="compact-table"
            />
          </Card>
        </>
      )}
      {!stats && (
        <Card size="small">
          <Text type="secondary">
            提示：先「下载数据」（从 STOCK/CTP 网关获取真实历史 K
            线入库），再「开始回测」。也可以直接回测已有数据的合约区间。
          </Text>
        </Card>
      )}
      </div>
    </GlassPage>
  )
}