/** 期权页：数据同步、期权链 T 型报价、多腿组合回测。
 *
 *  - 同步：品种资料 + 合约目录后台任务（轮询 /options/sync/status）；
 *  - T 型报价：按行权价配对的认购/认沽截面（收盘/涨跌/IV/Delta），
 *    缺行情时后端自动补拉（首次较慢）；
 *  - 组合回测：内置模板 + 参数动态渲染，任务复用 /backtest/{id}
 *    轮询通道；结果含净值/回撤、保证金占用、到期损益图与成交台账。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Collapse,
  DatePicker,
  Form,
  InputNumber,
  Modal,
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
import { StarFilled, StarOutlined, SyncOutlined } from '@ant-design/icons'
import * as echarts from 'echarts'
import dayjs, { type Dayjs } from 'dayjs'
import {
  addWatchSymbol,
  getBacktestTask,
  getOptionBars,
  getOptionChain,
  getOptionExpiries,
  getOptionIvTerm,
  getOptionSyncStatus,
  getOptionTemplates,
  getOptionUnderlyings,
  getWatchlist,
  removeWatchSymbol,
  runOptionBacktest,
  startOptionSync,
} from '../api/http'
import OptionOptimizerPanel from './OptionOptimizerPanel'
import type {
  OptionBacktestResult,
  OptionBacktestStatistics,
  OptionChainRow,
  OptionExpiry,
  OptionSyncStatus,
  OptionTemplateInfo,
  OptionUnderlying,
} from '../types'
import GlassPage from './ui/GlassPage'
import { useThemeStore } from '../stores/theme'

const { Text } = Typography

interface BacktestFormValues {
  class_name: string
  underlying: string
  range: [Dayjs, Dayjs]
  capital: number
  commission: number
  slippage: number
  iv_fallback: number
  margin_limit_pct: number
  [key: string]: unknown
}

const errDetail = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
  String(e)

export default function OptionsPage() {
  const mode = useThemeStore(s => s.mode)
  const [messageApi, contextHolder] = message.useMessage()

  // 同步状态
  const [sync, setSync] = useState<OptionSyncStatus | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [watchedIds, setWatchedIds] = useState<Set<string>>(new Set())

  useEffect(() => {
    getWatchlist()
      .then(d => setWatchedIds(new Set((d.items ?? []).map(i => i.id))))
      .catch(() => undefined)
  }, [])

  const toggleWatch = async (id: string) => {
    try {
      if (watchedIds.has(id)) {
        await removeWatchSymbol(id)
        setWatchedIds(prev => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      } else {
        await addWatchSymbol(id)
        setWatchedIds(prev => new Set(prev).add(id))
      }
      messageApi.success(watchedIds.has(id) ? `已移出关注 ${id}` : `已加入关注 ${id}`)
    } catch (e) {
      messageApi.error(`关注操作失败: ${errDetail(e)}`)
    }
  }

  // 标的 / 到期 / 链
  const [underlyings, setUnderlyings] = useState<OptionUnderlying[]>([])
  const [underlying, setUnderlying] = useState<string>('')
  const [expiries, setExpiries] = useState<OptionExpiry[]>([])
  const [expiry, setExpiry] = useState<string>('')
  const [chain, setChain] = useState<OptionChainRow[] | null>(null)
  const [chainMeta, setChainMeta] = useState<{ close: number; name: string; fetched: number } | null>(null)
  const [chainLoading, setChainLoading] = useState(false)

  // 组合回测
  const [templates, setTemplates] = useState<OptionTemplateInfo[]>([])
  const [result, setResult] = useState<OptionBacktestResult | null>(null)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState('')
  const [form] = Form.useForm<BacktestFormValues>()
  const selTemplate = Form.useWatch('class_name', form) as string | undefined
  const templateInfo = useMemo(
    () => templates.find(t => t.class_name === selTemplate),
    [templates, selTemplate],
  )

  const equityChartRef = useRef<HTMLDivElement>(null)
  const marginChartRef = useRef<HTMLDivElement>(null)
  const payoffChartRef = useRef<HTMLDivElement>(null)
  const smileChartRef = useRef<HTMLDivElement>(null)
  const termChartRef = useRef<HTMLDivElement>(null)
  const barsChartRef = useRef<HTMLDivElement>(null)
  const [ivTerm, setIvTerm] = useState<{ expiry: string; call_iv: number | null; put_iv: number | null }[]>([])
  const [barsModal, setBarsModal] = useState<{ thscode: string; name: string } | null>(null)
  const [barsData, setBarsData] = useState<{ date: string; open: number; high: number; low: number; close: number; volume: number }[] | null>(null)

  const loadUnderlyings = () => {
    getOptionUnderlyings()
      .then(r => {
        setUnderlyings(r.items)
        setUnderlying(prev => prev || r.items[0]?.underlying || '')
      })
      .catch(() => undefined)
  }

  useEffect(() => {
    loadUnderlyings()
    getOptionTemplates().then(r => setTemplates(r.items)).catch(() => undefined)
    getOptionSyncStatus().then(setSync).catch(() => undefined)
  }, [])

  // 同步任务运行中轮询状态；结束后刷新标的清单
  useEffect(() => {
    if (sync?.status !== 'running') return
    const timer = setInterval(() => {
      getOptionSyncStatus()
        .then(s => {
          setSync(s)
          if (s.status !== 'running') {
            setSyncing(false)
            loadUnderlyings()
            if (s.status === 'done') messageApi.success(`同步完成：${s.progress}`)
            else if (s.status === 'failed') messageApi.error(`同步失败: ${s.error}`)
          }
        })
        .catch(() => undefined)
    }, 1500)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sync?.status])

  // 标的变化 → 到期月清单
  useEffect(() => {
    if (!underlying) return
    setExpiries([])
    setExpiry('')
    setChain(null)
    setChainMeta(null)
    getOptionExpiries(underlying)
      .then(r => {
        setExpiries(r.items)
        setExpiry(r.items[0]?.expiry || '')
      })
      .catch(e => messageApi.error(`到期月加载失败: ${errDetail(e)}`))
    getOptionIvTerm(underlying)
      .then(r => setIvTerm(r.items ?? []))
      .catch(() => setIvTerm([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying])

  // 到期变化 → T 型报价
  useEffect(() => {
    if (!underlying || !expiry) return
    setChainLoading(true)
    setChain(null)
    getOptionChain(underlying, expiry, true)
      .then(r => {
        setChain(r.items)
        setChainMeta({
          close: r.underlying_close,
          name: r.name,
          fetched: r.fetched_contracts,
        })
        if (r.fetched_contracts > 0) {
          messageApi.info(`已补拉 ${r.fetched_contracts} 个合约的日K行情`)
          getOptionIvTerm(underlying)
            .then(x => setIvTerm(x.items ?? []))
            .catch(() => undefined)
        }
      })
      .catch(e => messageApi.error(`期权链加载失败: ${errDetail(e)}`))
      .finally(() => setChainLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying, expiry])

  const syncNow = async (scope: string) => {
    setSyncing(true)
    try {
      await startOptionSync({ scope })
      const s = await getOptionSyncStatus()
      setSync(s)
    } catch (e) {
      messageApi.error(`同步启动失败: ${errDetail(e)}`)
      setSyncing(false)
    }
  }

  const runBacktest = async () => {
    let values: BacktestFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    setRunning(true)
    setResult(null)
    setProgress('')
    try {
      const { class_name, underlying: ul, range, capital, commission, slippage, iv_fallback, margin_limit_pct, ...setting } = values
      const task = await runOptionBacktest({
        class_name,
        underlying: ul,
        start: range[0].format('YYYY-MM-DD'),
        end: range[1].format('YYYY-MM-DD'),
        capital,
        commission,
        slippage,
        iv_fallback,
        margin_limit_pct,
        setting,
      })
      let last = task
      while (last.status === 'pending' || last.status === 'running') {
        setProgress(last.message)
        await new Promise(r => setTimeout(r, 1000))
        last = await getBacktestTask(task.task_id)
      }
      if (last.status === 'done' && last.result) {
        setResult(last.result as unknown as OptionBacktestResult)
        messageApi.success('回测完成')
      } else {
        messageApi.error(`回测失败: ${last.error || '未知错误'}`)
      }
    } catch (e) {
      messageApi.error(`回测失败: ${errDetail(e)}`)
    } finally {
      setRunning(false)
      setProgress('')
    }
  }

  // ------------------------------------------------------------------
  // 图表
  // ------------------------------------------------------------------

  const stats: OptionBacktestStatistics | undefined = result?.statistics

  useEffect(() => {
    const container = equityChartRef.current
    if (!container || !stats || !result) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    const dates = result.daily_results.map(d => d.date)
    const balances = result.daily_results.map(d => d.equity)
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
      legend: { data: ['组合权益', '回撤%'], textStyle: { color: axisText, fontSize: 11 } },
      grid: [
        { left: 80, right: 30, top: 40, height: '48%' },
        { left: 80, right: 30, top: '68%', height: '22%' },
      ],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: axisText, fontSize: 10 }, axisLine: { lineStyle: { color: gridColor } } },
        { type: 'category', data: dates, gridIndex: 1, axisLabel: { color: axisText, fontSize: 10 }, axisLine: { lineStyle: { color: gridColor } } },
      ],
      yAxis: [
        { type: 'value', scale: true, gridIndex: 0, name: '权益', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10, formatter: (v: number) => v >= 10000 ? `${(v / 10000).toFixed(0)}万` : v }, splitLine: { lineStyle: { color: gridColor } } },
        { type: 'value', gridIndex: 1, name: '回撤%', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10 }, splitLine: { lineStyle: { color: gridColor } } },
      ],
      series: [
        {
          name: '组合权益',
          type: 'line',
          data: balances,
          showSymbol: false,
          xAxisIndex: 0,
          yAxisIndex: 0,
          lineStyle: { width: 2.2, color: '#6366f1', shadowBlur: 8, shadowColor: 'rgba(99,102,241,0.45)' },
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
  }, [result, stats, mode])

  // 保证金占用 + Delta 敞口
  useEffect(() => {
    const container = marginChartRef.current
    if (!container || !result) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    const dates = result.daily_results.map(d => d.date)
    const dark = mode === 'dark'
    const axisText = dark ? '#94a3b8' : '#64748b'
    const gridColor = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      legend: { data: ['保证金占用', '组合Delta'], textStyle: { color: axisText, fontSize: 11 } },
      grid: { left: 80, right: 70, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: axisText, fontSize: 10 }, axisLine: { lineStyle: { color: gridColor } } },
      yAxis: [
        { type: 'value', name: '保证金', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10, formatter: (v: number) => v >= 10000 ? `${(v / 10000).toFixed(0)}万` : v }, splitLine: { lineStyle: { color: gridColor } } },
        { type: 'value', name: 'Delta', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        {
          name: '保证金占用',
          type: 'line',
          data: result.daily_results.map(d => d.margin_used),
          showSymbol: false,
          areaStyle: { opacity: 0.15 },
          lineStyle: { width: 1.8 },
          color: '#f59e0b',
        },
        {
          name: '组合Delta',
          type: 'line',
          data: result.daily_results.map(d => d.delta),
          showSymbol: false,
          yAxisIndex: 1,
          lineStyle: { width: 1.4, type: 'dashed' },
          color: '#10b981',
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

  // 到期损益图
  useEffect(() => {
    const container = payoffChartRef.current
    if (!container || !result?.payoff?.points?.length) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    const pts = result.payoff.points
    const dark = mode === 'dark'
    const axisText = dark ? '#94a3b8' : '#64748b'
    const gridColor = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })} 元`,
      },
      grid: { left: 90, right: 30, top: 36, bottom: 40 },
      xAxis: {
        type: 'value',
        name: '标的到期价',
        nameTextStyle: { color: axisText },
        scale: true,
        axisLabel: { color: axisText, fontSize: 10 },
        splitLine: { lineStyle: { color: gridColor } },
      },
      yAxis: {
        type: 'value',
        name: '到期损益(元)',
        nameTextStyle: { color: axisText },
        axisLabel: { color: axisText, fontSize: 10, formatter: (v: number) => v >= 10000 || v <= -10000 ? `${(v / 10000).toFixed(0)}万` : v },
        splitLine: { lineStyle: { color: gridColor } },
      },
      series: [
        {
          type: 'line',
          data: pts.map(p => [p.price, p.pnl]),
          showSymbol: false,
          lineStyle: { width: 2.2, color: '#6366f1' },
          areaStyle: {
            color: '#6366f1',
            opacity: 0.08,
          },
          markLine: {
            symbol: 'none',
            silent: true,
            lineStyle: { type: 'dashed' },
            data: [
              { yAxis: 0, lineStyle: { color: axisText } },
              {
                xAxis: result.payoff.underlying_close,
                lineStyle: { color: '#f59e0b' },
                label: { formatter: '现价', color: axisText },
              },
            ],
          },
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

  // IV 微笑曲线（当前到期月）
  useEffect(() => {
    const container = smileChartRef.current
    if (!container || !chain?.length) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    const dark = mode === 'dark'
    const axisText = dark ? '#94a3b8' : '#64748b'
    const gridColor = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'
    const strikes = chain.map(r => r.strike)
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (v == null ? '-' : `${(Number(v) * 100).toFixed(1)}%`) },
      legend: { data: ['认购 IV', '认沽 IV'], textStyle: { color: axisText, fontSize: 11 } },
      grid: { left: 60, right: 20, top: 36, bottom: 34 },
      xAxis: { type: 'category', data: strikes, name: '行权价', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10, formatter: (v: number) => fmtPx(v) } },
      yAxis: { type: 'value', scale: true, name: 'IV%', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10, formatter: (v: number) => (v * 100).toFixed(0) }, splitLine: { lineStyle: { color: gridColor } } },
      series: [
        {
          name: '认购 IV', type: 'line', smooth: true, showSymbol: true, symbolSize: 5,
          data: chain.map(r => r.call?.iv ?? null),
          connectNulls: true, lineStyle: { width: 2 }, color: '#6366f1',
        },
        {
          name: '认沽 IV', type: 'line', smooth: true, showSymbol: true, symbolSize: 5,
          data: chain.map(r => r.put?.iv ?? null),
          connectNulls: true, lineStyle: { width: 2 }, color: '#f43f5e',
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.dispose() }
  }, [chain, mode])

  // ATM IV 期限结构
  useEffect(() => {
    const container = termChartRef.current
    if (!container || !ivTerm.length) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    const dark = mode === 'dark'
    const axisText = dark ? '#94a3b8' : '#64748b'
    const gridColor = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'
    const expiries = ivTerm.map(x => x.expiry.slice(5))
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (v == null ? '-' : `${(Number(v) * 100).toFixed(1)}%`) },
      legend: { data: ['认购 ATM IV', '认沽 ATM IV'], textStyle: { color: axisText, fontSize: 11 } },
      grid: { left: 60, right: 20, top: 36, bottom: 34 },
      xAxis: { type: 'category', data: expiries, name: '到期月', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10 } },
      yAxis: { type: 'value', scale: true, name: 'IV%', nameTextStyle: { color: axisText }, axisLabel: { color: axisText, fontSize: 10, formatter: (v: number) => (v * 100).toFixed(0) }, splitLine: { lineStyle: { color: gridColor } } },
      series: [
        { name: '认购 ATM IV', type: 'bar', barWidth: '30%', data: ivTerm.map(x => x.call_iv), color: '#6366f1' },
        { name: '认沽 ATM IV', type: 'bar', barWidth: '30%', data: ivTerm.map(x => x.put_iv), color: '#f43f5e' },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.dispose() }
  }, [ivTerm, mode])

  // 单合约日K（弹窗，蜡烛图）
  useEffect(() => {
    const container = barsChartRef.current
    if (!container || !barsModal || !barsData?.length) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    const dark = mode === 'dark'
    const axisText = dark ? '#94a3b8' : '#64748b'
    const gridColor = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'
    const dates = barsData.map(b => b.date)
    const kdata = barsData.map(b => [b.open, b.close, b.low, b.high])
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      grid: [
        { left: 70, right: 20, top: 20, height: '58%' },
        { left: 70, right: 20, top: '72%', height: '20%' },
      ],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: axisText, fontSize: 10 }, axisLine: { lineStyle: { color: gridColor } } },
        { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: gridColor } } },
      ],
      yAxis: [
        { type: 'value', scale: true, gridIndex: 0, axisLabel: { color: axisText, fontSize: 10 }, splitLine: { lineStyle: { color: gridColor } } },
        { type: 'value', gridIndex: 1, axisLabel: { color: axisText, fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        { type: 'candlestick', data: kdata, itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' } },
        { type: 'bar', data: barsData.map(b => b.volume), xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: 'rgba(99,102,241,0.5)' } },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.dispose() }
  }, [barsModal, barsData, mode])

  // 打开单合约日K弹窗
  const openBarsModal = (thscode: string | undefined, name: string | undefined) => {
    if (!thscode) return
    setBarsModal({ thscode, name: name || thscode })
    setBarsData(null)
    getOptionBars(thscode, 60)
      .then(r => setBarsData(r.items))
      .catch(e => messageApi.error(`日K加载失败: ${errDetail(e)}`))
  }

  // ------------------------------------------------------------------
  // T 型报价列
  // ------------------------------------------------------------------

  const chainColumns: ColumnsType<OptionChainRow> = [
    {
      title: '★',
      dataIndex: ['call', 'thscode'],
      width: 36,
      align: 'center',
      render: (v: string | undefined) =>
        v ? (
          <a
            onClick={e => {
              e.stopPropagation()
              void toggleWatch(v)
            }}
            style={{ color: watchedIds.has(v) ? '#f59e0b' : 'rgba(148,163,184,0.5)' }}
            title={watchedIds.has(v) ? '移出关注' : '加入关注'}
          >
            {watchedIds.has(v) ? <StarFilled style={{ fontSize: 12 }} /> : <StarOutlined style={{ fontSize: 12 }} />}
          </a>
        ) : null,
    },
    {
      title: '认购 价',
      dataIndex: ['call', 'price'],
      width: 90,
      align: 'right',
      className: 'mono',
      render: (v: number | null, row) =>
        v == null ? (
          '-'
        ) : (
          <a onClick={() => openBarsModal(row.call?.thscode, row.call?.name)} style={{ cursor: 'pointer' }}>
            <span className={quoteClass(v, row.call?.prev_close)}>{fmtPx(v)}</span>
          </a>
        ),
    },
    {
      title: '涨跌%',
      dataIndex: ['call', 'change_pct'],
      width: 80,
      align: 'right',
      className: 'mono',
      render: (v: number | null) => {
        const n = Number(v)
        return v == null || !Number.isFinite(n)
          ? '-'
          : <span className={n >= 0 ? 'price-up' : 'price-down'}>{(n * 100).toFixed(1)}%</span>
      },
    },
    {
      title: 'IV%',
      dataIndex: ['call', 'iv'],
      width: 70,
      align: 'right',
      className: 'mono',
      render: (v: number | null) => (v == null || !Number.isFinite(Number(v)) ? '-' : (Number(v) * 100).toFixed(1)),
    },
    {
      title: 'Delta',
      dataIndex: ['call', 'delta'],
      width: 70,
      align: 'right',
      className: 'mono',
      render: (v: number | null) => (v == null || !Number.isFinite(Number(v)) ? '-' : Number(v).toFixed(3)),
    },
    {
      title: '量',
      dataIndex: ['call', 'volume'],
      width: 80,
      align: 'right',
      className: 'mono',
      render: (v: number | null) => (v == null ? '-' : Number(v).toLocaleString()),
    },
    {
      title: '行权价',
      dataIndex: 'strike',
      width: 90,
      align: 'center',
      className: 'mono',
      render: (v: number) => {
        const u = chainMeta?.close ?? 0
        const isATM = u > 0 && Math.abs(v / u - 1) <= 0.02
        return (
          <span style={{ fontWeight: isATM ? 600 : 400, color: isATM ? 'var(--accent)' : undefined }}>
            {fmtPx(v)}
          </span>
        )
      },
    },
    {
      title: '量',
      dataIndex: ['put', 'volume'],
      width: 80,
      align: 'left',
      className: 'mono',
      render: (v: number | null) => (v == null ? '-' : Number(v).toLocaleString()),
    },
    {
      title: 'Delta',
      dataIndex: ['put', 'delta'],
      width: 70,
      align: 'left',
      className: 'mono',
      render: (v: number | null) => (v == null || !Number.isFinite(Number(v)) ? '-' : Number(v).toFixed(3)),
    },
    {
      title: 'IV%',
      dataIndex: ['put', 'iv'],
      width: 70,
      align: 'left',
      className: 'mono',
      render: (v: number | null) => (v == null || !Number.isFinite(Number(v)) ? '-' : (Number(v) * 100).toFixed(1)),
    },
    {
      title: '涨跌%',
      dataIndex: ['put', 'change_pct'],
      width: 80,
      align: 'left',
      className: 'mono',
      render: (v: number | null) => {
        const n = Number(v)
        return v == null || !Number.isFinite(n)
          ? '-'
          : <span className={n >= 0 ? 'price-up' : 'price-down'}>{(n * 100).toFixed(1)}%</span>
      },
    },
    {
      title: '认沽 价',
      dataIndex: ['put', 'price'],
      width: 90,
      align: 'left',
      className: 'mono',
      render: (v: number | null, row) =>
        v == null ? (
          '-'
        ) : (
          <a onClick={() => openBarsModal(row.put?.thscode, row.put?.name)} style={{ cursor: 'pointer' }}>
            <span className={quoteClass(v, row.put?.prev_close)}>{fmtPx(v)}</span>
          </a>
        ),
    },
    {
      title: '★',
      dataIndex: ['put', 'thscode'],
      width: 36,
      align: 'center',
      render: (v: string | undefined) =>
        v ? (
          <a
            onClick={e => {
              e.stopPropagation()
              void toggleWatch(v)
            }}
            style={{ color: watchedIds.has(v) ? '#f59e0b' : 'rgba(148,163,184,0.5)' }}
            title={watchedIds.has(v) ? '移出关注' : '加入关注'}
          >
            {watchedIds.has(v) ? <StarFilled style={{ fontSize: 12 }} /> : <StarOutlined style={{ fontSize: 12 }} />}
          </a>
        ) : null,
    },
  ]

  const tradeColumns: ColumnsType<NonNullable<OptionBacktestResult['trades']>[number]> = [
    { title: '日期', dataIndex: 'date', width: 100, className: 'mono' },
    {
      title: '操作',
      dataIndex: 'action',
      width: 80,
      render: (v: string) => (
        <Tag color={{ open: 'blue', close: 'default', settle: 'purple', force_close: 'red' }[v] ?? 'default'}>
          {{ open: '开仓', close: '平仓', settle: '到期结算', force_close: '强平' }[v] ?? v}
        </Tag>
      ),
    },
    { title: '合约', dataIndex: 'name', width: 180, ellipsis: true },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 60,
      render: (v: number) => (
        <span className={v > 0 ? 'price-up' : 'price-down'}>{v > 0 ? '买' : '卖'}</span>
      ),
    },
    { title: '行权价', dataIndex: 'strike', width: 80, className: 'mono', render: (v: number) => (v ? fmtPx(v) : '-') },
    { title: '到期', dataIndex: 'expiry', width: 100, className: 'mono', render: (v: string) => v || '-' },
    { title: '张数', dataIndex: 'volume', width: 70, className: 'mono' },
    { title: '价格', dataIndex: 'price', width: 90, className: 'mono', render: (v: number) => fmtPx(v) },
    {
      title: '权利金',
      dataIndex: 'premium',
      width: 100,
      align: 'right',
      className: 'mono',
      render: (v: number) => {
        const n = Number(v)
        return (
          <span className={n >= 0 ? 'price-up' : 'price-down'}>
            {Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '-'}
          </span>
        )
      },
    },
    { title: '费用', dataIndex: 'fee', width: 70, align: 'right', className: 'mono' },
    {
      title: '盈亏',
      dataIndex: 'pnl',
      width: 100,
      align: 'right',
      className: 'mono',
      render: (v: number) => {
        const n = Number(v)
        return n === 0 || !Number.isFinite(n)
          ? '-'
          : <span className={n > 0 ? 'price-up' : 'price-down'}>{n.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
      },
    },
  ]

  const netPremium = stats ? stats.premium_received - stats.premium_paid : 0

  return (
    <GlassPage>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {contextHolder}

        {/* 数据同步 */}
        <Card
          size="small"
          title="期权数据同步"
          extra={
            <Space>
              <Button
                size="small"
                loading={syncing}
                onClick={() => void syncNow('details')}
                disabled={sync?.status === 'running'}
              >
                补全主要标的详情
              </Button>
              <Button
                size="small"
                icon={<SyncOutlined spin={syncing} />}
                loading={syncing}
                disabled={sync?.status === 'running'}
                onClick={() => void syncNow('all')}
              >
                同步品种 + 合约目录
              </Button>
            </Space>
          }
        >
          <Space size="large" wrap>
            <Text type="secondary">
              合约目录：{sync?.contract_count ?? (underlyings.reduce((a, u) => a + u.contract_count, 0) || 0)} 条 ·
              可交易标的：{underlyings.length} 个
            </Text>
            {sync?.last_sync && <Text type="secondary">上次同步：{sync.last_sync.slice(0, 19).replace('T', ' ')}</Text>}
            {sync?.status === 'running' && (
              <Tag color="processing">
                {sync.progress || '同步中…'}
                {sync.detail_total > 0 && `（详情 ${sync.detail_done}/${sync.detail_total}）`}
              </Tag>
            )}
            {sync?.status === 'failed' && <Tag color="error">{sync.error}</Tag>}
            <Text type="secondary" style={{ fontSize: 12 }}>
              目录仅含在市合约（约 2.8 万条）；回测窗口 = 在市合约生命周期（最长约 7 个月）。链页首次打开会自动补拉行情。
            </Text>
          </Space>
        </Card>

        {/* T 型报价 */}
        <Card
          size="small"
          title={`期权链 T 型报价${chainMeta?.name ? ` · ${chainMeta.name}` : ''}`}
          extra={
            <Space>
              <Select
                showSearch
                size="small"
                style={{ width: 220 }}
                placeholder="选择标的"
                value={underlying || undefined}
                onChange={v => setUnderlying(v)}
                options={underlyings.map(u => ({
                  label: `${u.underlying} ${u.name} (${u.contract_count})`,
                  value: u.underlying,
                }))}
                optionFilterProp="label"
              />
              <Tooltip title={underlying && watchedIds.has(underlying) ? '移出标的关注' : '标的加入关注'}>
                <Button
                  size="small"
                  type="text"
                  onClick={() => underlying && void toggleWatch(underlying)}
                  disabled={!underlying}
                  style={{ color: underlying && watchedIds.has(underlying) ? '#f59e0b' : undefined }}
                  icon={underlying && watchedIds.has(underlying) ? <StarFilled /> : <StarOutlined />}
                />
              </Tooltip>
              <Select
                size="small"
                style={{ width: 150 }}
                placeholder="到期月"
                value={expiry || undefined}
                onChange={v => setExpiry(v)}
                options={expiries.map(e => ({
                  label: `${e.expiry} (C${e.calls}/P${e.puts})`,
                  value: e.expiry,
                }))}
              />
            </Space>
          }
        >
          {chainMeta && (
            <Space size="large" style={{ marginBottom: 8 }}>
              <Text type="secondary">
                标的收盘：
                <span className={quoteClass(chainMeta.close, undefined)}>{fmtPx(chainMeta.close)}</span>
              </Text>
              <Text type="secondary">到期日：{expiry}</Text>
            </Space>
          )}
          <Table
            size="small"
            rowKey="strike"
            columns={chainColumns}
            dataSource={chain ?? []}
            loading={chainLoading}
            pagination={{ pageSize: 15, showSizeChanger: false }}
            className="compact-table"
            locale={{ emptyText: chainLoading ? '加载中（首次需补拉行情）…' : '选择标的与到期月查看 T 型报价' }}
          />
        </Card>

        {/* 波动率结构：通栏布局，X 轴宽度随实际页面大小伸缩 */}
        {chain?.length ? (
          <Card size="small" title={`IV 微笑曲线 · ${expiry || ''}`}>
            <div ref={smileChartRef} style={{ height: 240, width: '100%' }} />
          </Card>
        ) : null}
        {ivTerm.length ? (
          <Card size="small" title="ATM 隐波期限结构">
            <div ref={termChartRef} style={{ height: 220, width: '100%' }} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              各到期月最接近现价的合约 IV（仅本地已有行情的月份）
            </Text>
          </Card>
        ) : null}

        {/* 组合回测表单 */}
        <Card size="small" title="期权组合回测">
          <Form
            form={form}
            layout="inline"
            initialValues={{
              class_name: templates[0]?.class_name,
              underlying: undefined,
              range: [dayjs().subtract(3, 'month'), dayjs()] as [Dayjs, Dayjs],
              capital: 100_000,
              commission: 0,
              slippage: 0,
              iv_fallback: 0.25,
              margin_limit_pct: 0.8,
            }}
          >
            <Form.Item name="class_name" label="组合模板" rules={[{ required: true }]}>
              <Select
                style={{ width: 180 }}
                placeholder="选择组合模板"
                options={templates.map(t => ({ label: t.display_name, value: t.class_name }))}
              />
            </Form.Item>
            <Form.Item name="underlying" label="标的" rules={[{ required: true, message: '请选择标的' }]}>
              <Select
                showSearch
                style={{ width: 180 }}
                placeholder="选择标的"
                options={underlyings.map(u => ({
                  label: `${u.underlying} ${u.name}`,
                  value: u.underlying,
                }))}
                optionFilterProp="label"
              />
            </Form.Item>
            <Form.Item name="range" label="区间" rules={[{ required: true }]}>
              <DatePicker.RangePicker />
            </Form.Item>
            <Form.Item name="capital" label="资金" tooltip="初始资金（元）。ETF 期权一手约需数千至数万保证金">
              <InputNumber style={{ width: 110 }} step={10000} />
            </Form.Item>
            <Form.Item name="commission" label="手续费/张" tooltip="每张合约手续费（元），0 = 用品种默认（ETF 1.3、股指 15）">
              <InputNumber style={{ width: 90 }} step={0.1} />
            </Form.Item>
            <Form.Item name="slippage" label="滑点/张" tooltip="每张滑点（权利金口径，元）">
              <InputNumber style={{ width: 80 }} step={0.0001 } />
            </Form.Item>
            <Form.Item name="iv_fallback" label="IV回退" tooltip="缺行情时的 BSM 回退隐含波动率">
              <InputNumber style={{ width: 80 }} step={0.05} min={0.01} max={3} />
            </Form.Item>
            <Form.Item name="margin_limit_pct" label="保证金上限%" tooltip="保证金占用/权益 超过该比例强平义务仓（0=不启用）">
              <InputNumber style={{ width: 90 }} step={0.1} min={0} max={10} />
            </Form.Item>
            {templateInfo &&
              Object.entries(templateInfo.parameters).map(([key, defaultValue]) => (
                <Form.Item
                  key={key}
                  name={key}
                  label={key}
                  tooltip={templateInfo.param_descriptions?.[key]}
                  initialValue={defaultValue as number}
                >
                  <InputNumber style={{ width: 90 }} />
                </Form.Item>
              ))}
            <Form.Item>
              <Button type="primary" loading={running} onClick={() => void runBacktest()}>
                {running && progress ? `回测中 ${progress}…` : '开始回测'}
              </Button>
            </Form.Item>
          </Form>
          {templateInfo?.description && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {templateInfo.display_name}：{templateInfo.description}
            </Text>
          )}
        </Card>

        <OptionOptimizerPanel
          form={{ getFieldsValue: () => form.getFieldsValue(), validateFields: () => form.validateFields() }}
          templateInfo={templateInfo}
        />

        {/* 结果 */}
        {stats && result && (
          <>
            <Row gutter={8}>
              {(
                [
                  ['总收益率', fmtNum(stats.total_return, 4, true), '整个回测期的组合权益累计收益率', Number(stats.total_return) >= 0],
                  ['年化收益', fmtNum(stats.annual_return, 4, true), '按 244 交易日折算', Number(stats.annual_return) >= 0],
                  ['最大回撤', fmtNum(stats.max_ddpercent, 4, true), '组合权益从峰值回落的最大幅度', false],
                  ['夏普比率', fmtNum(stats.sharpe_ratio, 2), '日频年化风险调整收益', Number(stats.sharpe_ratio) >= 0],
                  ['胜率(回合)', fmtNum(stats.win_rate, 4, true), '盈利平仓/结算次数占比', Number(stats.win_rate) >= 0.5],
                  ['盈亏比', fmtNum(stats.profit_factor, 2), '总盈利/总亏损（按回合）', Number(stats.profit_factor) >= 1],
                  ['最大保证金', stats.max_margin_used.toLocaleString(undefined, { maximumFractionDigits: 0 }), '回测期内义务仓保证金峰值', true],
                  ['权利金净额', netPremium.toLocaleString(undefined, { maximumFractionDigits: 0 }), '收取权利金 - 支付权利金（不含盈亏）', netPremium >= 0],
                  ['期末权益', stats.end_balance.toLocaleString(undefined, { maximumFractionDigits: 0 }), '段末全平后的权益', stats.end_balance >= stats.capital],
                ] as [string, string, string, boolean][]
              ).map(([title, value, tip, good]) => (
                <Col span={3} key={title}>
                  <Card size="small">
                    <Statistic
                      title={<Tooltip title={tip}>{title} ⓘ</Tooltip>}
                      value={value}
                      valueStyle={{ fontSize: 15, color: title === '总收益率' || title === '期末权益' ? (good ? 'var(--up-color)' : 'var(--down-color)') : undefined }}
                    />
                  </Card>
                </Col>
              ))}
            </Row>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {stats.class_name} · {stats.underlying} · {stats.start_date} ~ {stats.end_date}（{stats.total_days} 交易日）
              · 到期按内在价值现金结算（简化） · {stats.busted ? <Tag color="red">曾爆仓终止</Tag> : null}
            </Text>

            <Card size="small" title="组合权益 / 回撤">
              <div ref={equityChartRef} style={{ height: 320 }} />
            </Card>
            <Card size="small" title="保证金占用 / Delta 敞口">
              <div ref={marginChartRef} style={{ height: 260 }} />
            </Card>
            <Card size="small" title="到期损益图（段末持仓，含已付费用）">
              {result.payoff?.points?.length ? (
                <div ref={payoffChartRef} style={{ height: 260 }} />
              ) : (
                <Text type="secondary">回测结束时无持仓，无到期损益结构</Text>
              )}
            </Card>

            <Card size="small" title={`成交台账 (${result.trades.length})`}>
              <Table
                size="small"
                rowKey={(r, i) => `${r.date}-${r.thscode}-${r.action}-${i}`}
                columns={tradeColumns}
                dataSource={result.trades}
                pagination={{ pageSize: 10, showSizeChanger: false }}
                className="compact-table"
              />
            </Card>

            <Collapse
              size="small"
              items={[
                {
                  key: 'logs',
                  label: '回测日志',
                  children: (
                    <pre className="mono" style={{ fontSize: 12, maxHeight: 260, overflow: 'auto', margin: 0 }}>
                      {result.logs.join('\n')}
                    </pre>
                  ),
                },
              ]}
            />
          </>
        )}
        {!stats && (
          <Card size="small">
            <Text type="secondary">
              提示：先「同步品种 + 合约目录」，再选标的查看 T 型报价；回测窗口受在市合约生命周期限制
              （近月合约约 1-2 个月、季月合约最长约 7 个月），选区间后直接「开始回测」。
            </Text>
          </Card>
        )}

        <Modal
          open={!!barsModal}
          title={barsModal ? `${barsModal.name} 日K（近60日）` : ''}
          onCancel={() => setBarsModal(null)}
          footer={null}
          width={760}
          destroyOnHidden
        >
          {barsData?.length ? (
            <div ref={barsChartRef} style={{ height: 380 }} />
          ) : (
            <Text type="secondary">{barsModal ? '加载中（首次需补拉行情）…' : ''}</Text>
          )}
        </Modal>
      </div>
    </GlassPage>
  )
}

// --------------------------------------------------------------------

/** 数值防御：非有限数值返回 '-'；pct=true 时按百分数格式（×100）。 */
function fmtNum(v: unknown, digits = 2, pct = false): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return pct ? `${(n * 100).toFixed(digits)}%` : n.toFixed(digits)
}

function fmtPx(v: number | null | undefined): string {
  const n = Number(v)
  if (v == null || !Number.isFinite(n)) return '-'
  return n >= 100 ? n.toFixed(1) : n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
}

function quoteClass(price: number | null | undefined, prev: number | null | undefined): string {
  if (price == null) return ''
  if (prev == null || prev === 0) return ''
  return price >= prev ? 'price-up' : 'price-down'
}
