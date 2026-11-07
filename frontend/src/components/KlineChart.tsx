/** 专业 K 线图（ECharts）：蜡烛 + 成交量副图 + 发光 MA 均线 + 十字光标。
 *  数据管线与原 lightweight-charts 版本一致：tick 分桶聚合、成交量增量、
 *  MA 只更新最后一个点、1 分钟周期显示分时折线。仅视觉层重写。 */

import { useCallback, useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { getIntradayBars, getKeyLevels, type KeyLevel } from '../api/http'
import { useMarketStore } from '../stores/market'
import {
  addWatchSymbol,
  getWatchlist,
  removeWatchSymbol,
} from '../api/http'
import { useThemeStore } from '../stores/theme'

// ---- 常量 ----

const INTERVALS = [
  { label: '1分', value: '1m' },
  { label: '5分', value: '5m' },
  { label: '15分', value: '15m' },
  { label: '30分', value: '30m' },
  { label: '1时', value: '1h' },
  { label: '日线', value: 'd' },
  { label: '周线', value: 'w' },
]

const MA_PERIODS = [5, 10, 20, 60]
// 发光荧光色均线：亮蓝 / 亮紫 / 品红 / 琥珀
const MA_COLORS_LIGHT = ['#3b82f6', '#8b5cf6', '#d946ef', '#f59e0b']
const MA_COLORS_DARK = ['#60a5fa', '#a78bfa', '#e879f9', '#fbbf24']

interface Palette {
  up: string
  down: string
  upBorder: string
  downBorder: string
  volUp: string
  volDown: string
  text: string
  subText: string
  grid: string
  axis: string
  ma: string[]
  tooltipBg: string
  tooltipBorder: string
}

function palette(mode: string): Palette {
  if (mode === 'dark') {
    return {
      up: '#fb7185',
      down: '#34d399',
      upBorder: '#fb7185',
      downBorder: '#34d399',
      volUp: 'rgba(251, 113, 133, 0.45)',
      volDown: 'rgba(52, 211, 153, 0.45)',
      text: '#94a3b8',
      subText: '#64748b',
      grid: 'rgba(255,255,255,0.06)',
      axis: 'rgba(255,255,255,0.12)',
      ma: MA_COLORS_DARK,
      tooltipBg: 'rgba(15, 23, 42, 0.92)',
      tooltipBorder: 'rgba(255,255,255,0.1)',
    }
  }
  return {
    up: '#f43f5e',
    down: '#10b981',
    upBorder: '#f43f5e',
    downBorder: '#10b981',
    volUp: 'rgba(244, 63, 94, 0.4)',
    volDown: 'rgba(16, 185, 129, 0.4)',
    text: '#475569',
    subText: '#94a3b8',
    grid: 'rgba(0,0,0,0.05)',
    axis: 'rgba(0,0,0,0.1)',
    ma: MA_COLORS_LIGHT,
    tooltipBg: 'rgba(255,255,255,0.92)',
    tooltipBorder: 'rgba(0,0,0,0.06)',
  }
}

// ---- 工具函数（与原实现一致） ----

function bucketStartMs(dt: Date, interval: string): number {
  const d = new Date(dt.getTime())
  d.setSeconds(0, 0)
  if (interval === '5m' || interval === '15m' || interval === '30m') {
    const min = d.getMinutes()
    if (interval === '5m') d.setMinutes(Math.floor(min / 5) * 5, 0)
    else if (interval === '15m') d.setMinutes(Math.floor(min / 15) * 15, 0)
    else if (interval === '30m') d.setMinutes(Math.floor(min / 30) * 30, 0)
  } else if (interval === '1h') {
    d.setMinutes(0)
  } else if (interval === 'd') {
    d.setHours(0, 0, 0, 0)
  } else if (interval === 'w') {
    d.setHours(0, 0, 0, 0)
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7))
  }
  return d.getTime()
}

function formatBucketTime(ms: number, interval: string): string {
  const d = new Date(ms)
  if (interval === 'd' || interval === 'w') {
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function sma(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
    } else {
      let sum = 0
      for (let j = i - period + 1; j <= i; j++) sum += data[j]
      result.push(sum / period)
    }
  }
  return result
}

/** 最近 `period` 个值的均值；数据不足时返回 undefined。 */
function smaLast(data: number[], period: number): number | undefined {
  if (data.length < period) return undefined
  let sum = 0
  for (let j = data.length - period; j < data.length; j++) sum += data[j]
  return sum / period
}

interface LiveBar {
  bucketMs: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/** 关键价位 markLine 配色：阻力=红 / 支撑=绿 / 中枢=琥珀 */
function levelColor(side: KeyLevel['side'], mode: string): string {
  const p = palette(mode)
  if (side === 'resistance') return p.up
  if (side === 'support') return p.down
  return '#f59e0b'
}

function levelMarkLine(levels: KeyLevel[], mode: string) {
  // 降噪：只画 strength 前 10 条线；标签仅标 strength 前 6 条并上下交错，避免右缘叠压
  const selected = [...levels].sort((a, b) => b.strength - a.strength).slice(0, 10)
  const labeled = new Set(selected.slice(0, 6).map(lv => lv.value))
  const ordered = [...selected].sort((a, b) => a.value - b.value)
  return {
    silent: true,
    symbol: 'none',
    animation: false,
    z: 5,
    data: ordered.map((lv, i) => ({
      yAxis: lv.value,
      name: lv.label,
      lineStyle: {
        color: levelColor(lv.side, mode),
        type: lv.side === 'pivot' ? 'solid' : 'dashed',
        width: 1,
        opacity: Math.min(0.9, 0.3 + lv.strength / 160),
      },
      label: {
        show: labeled.has(lv.value),
        position: (i % 2 ? 'insideEndBottom' : 'insideEndTop') as
          | 'insideEndTop'
          | 'insideEndBottom',
        fontSize: 9,
        color: levelColor(lv.side, mode),
        formatter: `${lv.label} ${lv.value >= 1000 ? lv.value.toFixed(0) : lv.value.toFixed(2)}`,
      },
    })),
  }
}

export default function KlineChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)

  // 数据缓存（ECharts 数据数组，按 bar 顺序）
  const timesRef = useRef<string[]>([])
  const candleDataRef = useRef<number[][]>([]) // [open, close, low, high]
  const volDataRef = useRef<{ value: number; itemStyle: { color: string } }[]>([])
  const maDataRef = useRef<(number | null)[][]>([[], [], [], []])
  const closesRef = useRef<number[]>([])
  const liveBarRef = useRef<LiveBar | null>(null)
  const lastVolumeRef = useRef<number>(0)
  const dataLoadedRef = useRef(false)

  const selected = useMarketStore(s => s.selected)
  const tick = useMarketStore(s => (selected ? s.ticks[selected] : undefined))
  const mode = useThemeStore(s => s.mode)
  const [chartInterval, setChartInterval] = useState<string>('1m')
  const chartIntervalRef = useRef(chartInterval)
  chartIntervalRef.current = chartInterval
  const [barCount, setBarCount] = useState(0)
  const [maVisible, setMaVisible] = useState<Record<string, boolean>>({
    '5': true, '10': false, '20': false, '60': false,
  })
  const maVisibleRef = useRef(maVisible)
  maVisibleRef.current = maVisible

  // 关键价位（筹码/枢轴/前高低/布林/Keltner/ATR/缺口/Fib/整数关口）
  const [levels, setLevels] = useState<KeyLevel[]>([])
  const [levelsVisible, setLevelsVisible] = useState(false)
  // 关注星标：selected 是否已在 watchlist 中
  const [watchedIds, setWatchedIds] = useState<Set<string>>(new Set())
  useEffect(() => {
    getWatchlist()
      .then(d => setWatchedIds(new Set((d.items ?? []).map(i => i.id))))
      .catch(() => undefined)
  }, [])
  const vtId = selected ?? ''
  const watched = watchedIds.has(vtId) || watchedIds.has(vtId.split('.').slice(0, 2).join('.'))
  const toggleWatch = async () => {
    try {
      if (watched) {
        // 优先按完整 vt_symbol 移除，其次按短 id
        const target = watchedIds.has(vtId) ? vtId : vtId.split('.').slice(0, 2).join('.')
        await removeWatchSymbol(target)
        setWatchedIds(prev => {
          const next = new Set(prev)
          next.delete(target)
          return next
        })
      } else {
        await addWatchSymbol(vtId)
        setWatchedIds(prev => new Set(prev).add(vtId))
      }
    } catch {
      // 静默
    }
  }
  const levelsRef = useRef(levels)
  levelsRef.current = levels
  const levelsVisibleRef = useRef(levelsVisible)
  levelsVisibleRef.current = levelsVisible
  const modeRef = useRef(mode)
  modeRef.current = mode

  // 数据重载（60s 定时器/切周期）会用 notMerge 重建 option 并抹掉 markLine，
  // 因此重载完成后必须用 refs 重新应用一次价位线
  const applyLevelsMarkLine = useCallback(() => {
    const chart = chartRef.current
    if (!chart) return
    try {
      chart.setOption(
        {
          series: [
            {
              markLine:
                levelsVisibleRef.current && levelsRef.current.length
                  ? levelMarkLine(levelsRef.current, modeRef.current)
                  : { data: [] },
            },
          ],
        },
        { lazyUpdate: true },
      )
    } catch {
      // 忽略更新异常
    }
  }, [])

  const is1m = chartInterval === '1m'

  // ---- 从 refs 构建完整 option（初始化/主题切换时使用） ----
  const buildOption = useCallback((): echarts.EChartsCoreOption => {
    const p = palette(mode)
    const times = timesRef.current
    const candleData = candleDataRef.current
    const volData = volDataRef.current

    const mainSeries: echarts.SeriesOption =
      is1m
        ? {
            type: 'line',
            data: candleData.map(c => c[1]),
            showSymbol: false,
            lineStyle: { width: 1.8, color: p.up },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(244,63,94,0.18)' },
                { offset: 1, color: 'rgba(244,63,94,0)' },
              ]),
            },
            z: 3,
          }
        : {
            type: 'candlestick',
            data: candleData,
            // 高密度下防止实体缩到亚像素发虚；极度放大时防止实体变成色块
            barMinWidth: 1.5,
            barMaxWidth: 14,
            itemStyle: {
              color: p.up,
              color0: p.down,
              borderColor: p.upBorder,
              borderColor0: p.downBorder,
              borderWidth: 1,
            },
            z: 3,
          }

    const maSeries: echarts.SeriesOption[] = MA_PERIODS.map((period, i) => ({
      type: 'line',
      data: maVisibleRef.current[String(period)] ? maDataRef.current[i] : [],
      showSymbol: false,
      silent: true,
      lineStyle: {
        width: 1.4,
        color: p.ma[i],
        shadowBlur: 7,
        shadowColor: p.ma[i],
      },
      emphasis: { disabled: true },
      z: 4,
    }))

    const volSeries: echarts.SeriesOption = {
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: volData,
      barWidth: '62%',
      barMinWidth: 1,
      z: 2,
    }

    return {
      animation: false,
      backgroundColor: 'transparent',
      textStyle: { fontFamily: 'Inter, PingFang SC, Microsoft YaHei, sans-serif' },
      grid: [
        // 主图 + 成交量副图整体在面板内上下居中（上下留白对称）
        { left: 8, right: 62, top: '9%', height: '57%', containLabel: false },
        { left: 8, right: 62, top: '73%', height: '15%', containLabel: false },
      ],
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        lineStyle: { color: 'rgba(100,116,139,0.5)', type: 'dashed' },
        crossStyle: { color: 'rgba(100,116,139,0.5)' },
        label: { backgroundColor: '#64748b' },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: p.tooltipBg,
        borderColor: p.tooltipBorder,
        borderWidth: 1,
        padding: [6, 10],
        textStyle: { color: mode === 'dark' ? '#e2e8f0' : '#334155', fontSize: 12 },
        formatter: (params: unknown) => {
          const list = params as { axisValue?: string; data?: unknown; seriesName?: string; seriesType?: string; value?: unknown; color?: string }[]
          let html = `<div style="font-weight:600;margin-bottom:2px">${list[0]?.axisValue ?? ''}</div>`
          const candle = list.find(x => x.seriesType === 'candlestick')
          if (candle && Array.isArray(candle.data)) {
            // ECharts candlestick 的 value 为 [dataIndex, open, close, low, high]，
            // 取末 4 位跳过索引前缀，否则"开"会显示成数组下标
            const raw = candle.data as number[]
            const [o, c, l, h] = raw.length > 4 ? raw.slice(-4) : raw
            const chg = o ? ((c - o) / o) * 100 : 0
            const cls = c >= o ? p.up : p.down
            html += `<div style="font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.7">
              开 <b>${o?.toFixed(2)}</b>&nbsp; 高 <b>${h?.toFixed(2)}</b>&nbsp; 低 <b>${l?.toFixed(2)}</b>&nbsp; 收 <b style="color:${cls}">${c?.toFixed(2)}</b>
              <span style="color:${cls}">&nbsp;${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span></div>`
          }
          for (let i = 0; i < MA_PERIODS.length; i++) {
            const ma = list.find(x => x.seriesName === `MA${MA_PERIODS[i]}`)
            if (ma && ma.value != null) {
              html += `<div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:${p.ma[i]}">MA${MA_PERIODS[i]} ${Number(ma.value).toFixed(2)}</div>`
            }
          }
          const vol = list.find(x => x.seriesType === 'bar')
          if (vol && vol.value != null) {
            html += `<div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:${p.subText}">Vol ${Number(vol.value).toLocaleString()}</div>`
          }
          return html
        },
      },
      xAxis: [
        {
          type: 'category',
          gridIndex: 0,
          data: times,
          boundaryGap: true,
          axisLine: { lineStyle: { color: p.axis } },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: times,
          boundaryGap: true,
          axisLine: { lineStyle: { color: p.axis } },
          axisTick: { show: false },
          axisLabel: {
            color: p.text,
            fontSize: 10,
            interval: Math.max(0, Math.floor(times.length / 8) - 1),
          },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          position: 'right',
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: p.text, fontSize: 10 },
          splitLine: { lineStyle: { color: p.grid } },
        },
        {
          scale: true,
          gridIndex: 1,
          position: 'right',
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
          splitNumber: 2,
        },
      ],
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: [0, 1],
          start: 0,
          end: 100,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
        },
      ],
      series: [mainSeries, ...maSeries, volSeries],
    }
  }, [mode, is1m])

  // ---- MA 均线全量重算（基于缓存 closes） ----
  const recomputeMa = useCallback(() => {
    const closes = closesRef.current
    maDataRef.current = MA_PERIODS.map(period =>
      sma(closes, period),
    )
  }, [])

  // ---- 关键价位拉取（基于日K，切合约时一次） ----
  useEffect(() => {
    if (!selected) return
    let cancelled = false
    getKeyLevels(selected)
      .then(res => {
        if (!cancelled) setLevels(res.levels || [])
      })
      .catch(() => {
        if (!cancelled) setLevels([])
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  // ---- 价位线渲染（开关/数据/主题变化时重新应用） ----
  useEffect(() => {
    applyLevelsMarkLine()
  }, [levelsVisible, levels, mode, barCount, applyLevelsMarkLine])

  // ---- 创建/销毁图表（随合约与周期重建） ----
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const chart = echarts.init(container, null, { renderer: 'canvas' })
    chartRef.current = chart

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(container)
    resizeObserverRef.current = observer

    return () => {
      observer.disconnect()
      resizeObserverRef.current = null
      chart.dispose()
      chartRef.current = null
    }
  }, [selected, chartInterval])

  // ---- 应用主题与全量数据 ----
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption(buildOption(), { notMerge: true, lazyUpdate: true })
  }, [buildOption, barCount, selected, chartInterval])

  // ---- 加载数据 ----
  useEffect(() => {
    if (!selected) return
    let cancelled = false
    dataLoadedRef.current = false
    liveBarRef.current = null
    lastCandleReset()

    function lastCandleReset() {
      candleDataRef.current = []
      volDataRef.current = []
      timesRef.current = []
      maDataRef.current = [[], [], [], []]
      closesRef.current = []
    }

    const load = async () => {
      try {
        const limit = chartInterval === '1m' ? 500 : 1000
        const bars = await getIntradayBars(selected, chartInterval, limit)
        if (cancelled || !chartRef.current) return

        const p = palette(mode)
        const times: string[] = []
        const candleData: number[][] = []
        const volData: { value: number; itemStyle: { color: string } }[] = []
        const closes: number[] = []

        for (const bar of bars) {
          if (!bar.datetime) continue
          if (bar.open_price == null || bar.high_price == null ||
              bar.low_price == null || bar.close_price == null) continue
          const bucketMs = bucketStartMs(new Date(bar.datetime), chartInterval)
          times.push(formatBucketTime(bucketMs, chartInterval))
          candleData.push([bar.open_price, bar.close_price, bar.low_price, bar.high_price])
          volData.push({
            value: bar.volume,
            itemStyle: {
              color: bar.close_price >= bar.open_price ? p.volUp : p.volDown,
            },
          })
          closes.push(bar.close_price)
        }

        timesRef.current = times
        candleDataRef.current = candleData
        volDataRef.current = volData
        closesRef.current = closes
        recomputeMa()
        const opt = buildOption() as { dataZoom?: { start?: number; end?: number }[] }
        // 默认聚焦最近 ~120 根（保留滚轮缩放查看全部）
        if (opt.dataZoom?.[0] && times.length > 120) {
          opt.dataZoom[0].start = Math.max(0, (1 - 120 / times.length) * 100)
        }
        chartRef.current.setOption(opt, { notMerge: true, lazyUpdate: true })

        dataLoadedRef.current = true
        setBarCount(candleData.length)
        // notMerge 重建会抹掉 markLine，重载数据后重新应用价位线
        applyLevelsMarkLine()

        // 初始化实时 Bar
        const lastBar = bars[bars.length - 1]
        if (lastBar?.datetime && candleData.length) {
          const lc = candleData[candleData.length - 1]
          const bucketMs = bucketStartMs(new Date(lastBar.datetime), chartInterval)
          liveBarRef.current = {
            bucketMs,
            open: lc[0], high: lc[3], low: lc[2], close: lc[1],
            volume: volData[volData.length - 1]?.value ?? 0,
          }
        }
      } catch {
        // 忽略错误
      }
    }
    void load()
    const timer = chartInterval !== '1m' ? setInterval(load, 60000) : undefined
    return () => { cancelled = true; if (timer) clearInterval(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, chartInterval])

  // ---- 实时 Tick 行情更新（分桶聚合 + 增量均线，与原实现一致） ----
  useEffect(() => {
    if (!tick || !tick.datetime) return
    const chart = chartRef.current
    if (!chart || !dataLoadedRef.current) return

    const interval = chartIntervalRef.current
    const dt = new Date(tick.datetime)
    const bucketMs = bucketStartMs(dt, interval)
    const price = tick.last_price
    if (!price) return

    const volumeDelta = Math.max(0, tick.volume - (lastVolumeRef.current || tick.volume))
    lastVolumeRef.current = tick.volume

    const p = palette(mode)

    let live = liveBarRef.current
    const isNewBar = !live || live.bucketMs !== bucketMs
    if (!live || live.bucketMs !== bucketMs) {
      live = {
        bucketMs, open: price, high: price, low: price, close: price,
        volume: volumeDelta,
      }
    } else {
      live.high = Math.max(live.high, price)
      live.low = Math.min(live.low, price)
      live.close = price
      live.volume += volumeDelta
    }
    liveBarRef.current = live

    const label = formatBucketTime(bucketMs, interval)

    if (isNewBar) {
      timesRef.current.push(label)
      candleDataRef.current.push([live.open, live.close, live.low, live.high])
      volDataRef.current.push({ value: live.volume, itemStyle: { color: live.close >= live.open ? p.volUp : p.volDown } })
      closesRef.current.push(live.close)
      for (let i = 0; i < MA_PERIODS.length; i++) {
        const period = MA_PERIODS[i]
        const v = smaLast(closesRef.current, period)
        maDataRef.current[i].push(v === undefined ? null : v)
      }
    } else {
      candleDataRef.current[candleDataRef.current.length - 1] = [live.open, live.close, live.low, live.high]
      volDataRef.current[volDataRef.current.length - 1] = { value: live.volume, itemStyle: { color: live.close >= live.open ? p.volUp : p.volDown } }
      closesRef.current[closesRef.current.length - 1] = live.close
      for (let i = 0; i < MA_PERIODS.length; i++) {
        if (!maVisibleRef.current[String(MA_PERIODS[i])]) continue
        const v = smaLast(closesRef.current, MA_PERIODS[i])
        const arr = maDataRef.current[i]
        if (v !== undefined && arr.length) arr[arr.length - 1] = v
      }
    }

    // 只推送受影响的 series 数据（merge 模式，series 顺序：主图 + 4 条 MA + 成交量）
    const mainData: unknown = is1m
      ? candleDataRef.current.map(c => c[1])
      : candleDataRef.current
    try {
      chart.setOption(
        {
          series: [
            { data: mainData },
            ...MA_PERIODS.map((period, i) => ({
              data: maVisibleRef.current[String(period)] ? maDataRef.current[i] : [],
            })),
            { data: volDataRef.current },
          ],
        },
        { lazyUpdate: true },
      )
    } catch {
      // 忽略更新异常
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick])

  // ---- 最新一帧 OHLC（工具栏图例） ----
  const lastCandle = candleDataRef.current[candleDataRef.current.length - 1]
  const ohlcChg =
    lastCandle && lastCandle[0] ? ((lastCandle[1] - lastCandle[0]) / lastCandle[0]) * 100 : 0

  return (
    <div className="flex h-full flex-col">
      {/* 顶部工具栏 */}
      <div className="flex min-h-[40px] flex-wrap items-center justify-between gap-2 border-b border-[var(--card-border)] px-3 py-1.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-[13px] font-semibold text-slate-800 dark:text-slate-200">
            {selected || '--'}
          </span>
          {tick && (
            <span
              className={`mono text-base font-semibold ${
                tick.last_price >= tick.pre_close ? 'price-up' : 'price-down'
              }`}
            >
              {tick.last_price?.toFixed(2)}
            </span>
          )}
          {lastCandle && (
            <span className="mono hidden items-center gap-2 text-[11px] text-slate-500 sm:flex">
              <span>开 <b className="text-slate-700 dark:text-slate-300">{lastCandle[0]?.toFixed(2)}</b></span>
              <span>高 <b className="text-slate-700 dark:text-slate-300">{lastCandle[3]?.toFixed(2)}</b></span>
              <span>低 <b className="text-slate-700 dark:text-slate-300">{lastCandle[2]?.toFixed(2)}</b></span>
              <span>收 <b className={lastCandle[1] >= lastCandle[0] ? 'price-up' : 'price-down'}>{lastCandle[1]?.toFixed(2)}</b></span>
              <span className={ohlcChg >= 0 ? 'price-up' : 'price-down'}>
                {ohlcChg >= 0 ? '+' : ''}{ohlcChg.toFixed(2)}%
              </span>
            </span>
          )}
          <span className="h-4 w-px bg-[var(--card-border)]" />
          <span className="flex items-center gap-1.5">
            {MA_PERIODS.map((period, i) => {
              const active = maVisible[String(period)]
              const p = palette(mode)
              return (
                <button
                  key={period}
                  type="button"
                  onClick={() =>
                    setMaVisible(prev => ({ ...prev, [String(period)]: !prev[String(period)] }))
                  }
                  className="cursor-pointer appearance-none select-none rounded-md border-0 bg-transparent px-1 py-0.5 text-[11px] leading-none transition-opacity"
                  style={{
                    color: active ? p.ma[i] : undefined,
                    opacity: active ? 1 : 0.4,
                    textDecoration: active ? 'none' : 'line-through',
                  }}
                >
                  MA{period}
                </button>
              )
            })}
            <button
              type="button"
              onClick={() => setLevelsVisible(v => !v)}
              className="cursor-pointer appearance-none select-none rounded-md border-0 bg-transparent px-1 py-0.5 text-[11px] leading-none transition-opacity"
              style={{
                color: levelsVisible ? '#f59e0b' : undefined,
                opacity: levelsVisible ? 1 : 0.4,
                textDecoration: levelsVisible ? 'none' : 'line-through',
              }}
              title="关键价位：筹码密集区/枢轴点/前高前低/布林/Keltner/ATR/缺口/Fib/整数关口"
            >
              价位
            </button>
            <button
              type="button"
              onClick={() => void toggleWatch()}
              className="cursor-pointer appearance-none select-none rounded-md border-0 bg-transparent px-1 py-0.5 text-[11px] leading-none transition-opacity"
              style={{
                color: watched ? '#f59e0b' : undefined,
                opacity: watched ? 1 : 0.4,
              }}
              title={watched ? '移出关注列表' : '加入关注列表'}
            >
              {watched ? '★' : '☆'}
            </button>
          </span>
        </div>

        {/* 周期切换 */}
        <div className="flex items-center gap-0.5 rounded-xl bg-[var(--subcard-bg)] p-0.5">
          {INTERVALS.map(iv => (
            <button
              key={iv.value}
              type="button"
              onClick={() => setChartInterval(iv.value)}
              className={`cursor-pointer appearance-none rounded-lg border-0 px-2.5 py-1 text-xs leading-tight transition-all ${
                chartInterval === iv.value
                  ? 'bg-blue-500/15 font-semibold text-blue-600 shadow-sm dark:text-blue-300'
                  : 'bg-transparent text-slate-500 hover:bg-[var(--card-bg)] hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
              }`}
            >
              {iv.label}
            </button>
          ))}
        </div>
      </div>

      {/* 图表 */}
      <div ref={containerRef} className="min-h-0 flex-1" />
    </div>
  )
}
