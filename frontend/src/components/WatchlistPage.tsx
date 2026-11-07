/** 关注页：跨 A股/期货/期权标的/期权合约的统一关注视图。
 *
 * 左侧：分组列表（全部/A股/期货/期权），每行实时行情 + 异动标记
 *       + 期权 IV；15s 轮询（股票/期货走 ws tick，期权走日K收盘）。
 * 右侧详情：行情头 + K线（复用 KlineChart，期权合约用日K）+
 *       关键价位表 + 异动/告警卡 + 期权 IV 摘要卡 + 一键 AI 分析卡。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AutoComplete,
  Button,
  Card,
  Empty,
  Modal,
  Row,
  Col,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { CloseOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import * as echarts from 'echarts'
import {
  addWatchSymbol,
  aiAnalyze,
  getAiAnalyzeHistory,
  getContracts,
  getOptionContractsList,
  getOptionUnderlyings,
  getWatchlist,
  getWatchlistDetail,
  removeWatchSymbol,
} from '../api/http'
import type { OptionUnderlying, WatchSummaryItem, WatchKind, WatchDetail, AiAnalysisRecord } from '../types'
import { useMarketStore } from '../stores/market'
import { useThemeStore } from '../stores/theme'
import KlineChart from './KlineChart'
import GlassPage from './ui/GlassPage'

const { Text } = Typography

const KIND_LABEL: Record<WatchKind, string> = {
  stock: 'A股',
  future: '期货',
  option_underlying: '期权标的',
  option_contract: '期权合约',
}

const KIND_COLOR: Record<WatchKind, string> = {
  stock: 'blue',
  future: 'orange',
  option_underlying: 'purple',
  option_contract: 'magenta',
}

const errDetail = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
  String(e)

function fmtPct(v: number | null | undefined, digits = 2): string {
  const n = Number(v)
  return Number.isFinite(n) ? `${n >= 0 ? '+' : ''}${(n * 100).toFixed(digits)}%` : '-'
}

export default function WatchlistPage() {
  const mode = useThemeStore(s => s.mode)
  const select = useMarketStore(s => s.select)
  const [messageApi, contextHolder] = message.useMessage()

  const [items, setItems] = useState<WatchSummaryItem[]>([])
  const [tab, setTab] = useState<'all' | 'option' | WatchKind>('all')
  const [selId, setSelId] = useState<string>('')
  const [detail, setDetail] = useState<WatchDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // 添加弹窗
  const [addOpen, setAddOpen] = useState(false)
  const [searchText, setSearchText] = useState('')
  const [searchOptions, setSearchOptions] = useState<{ value: string; label: string }[]>([])
  const [optUnderlyings, setOptUnderlyings] = useState<OptionUnderlying[]>([])
  const [optSearchText, setOptSearchText] = useState('')
  const [optSearchResults, setOptSearchResults] = useState<{ value: string; label: string }[]>([])

  // AI 分析
  const [analyzing, setAnalyzing] = useState(false)
  const [analysis, setAnalysis] = useState<AiAnalysisRecord | null>(null)

  const ivTermChartRef = useRef<HTMLDivElement>(null)

  // ------------------------------------------------------------------
  // 数据加载
  // ------------------------------------------------------------------

  const load = useCallback(async () => {
    try {
      const d = await getWatchlist()
      setItems(d.items ?? [])
    } catch {
      // 轮询静默
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), 15000)
    return () => clearInterval(timer)
  }, [load])

  // 详情：选中变化时加载
  useEffect(() => {
    if (!selId) return
    const entry = items.find(i => i.id === selId)
    setDetailLoading(true)
    getWatchlistDetail(selId, entry?.kind)
      .then(d => {
        setDetail(d)
        // 加载该标的的历史分析
        getAiAnalyzeHistory(selId)
          .then(h => setAnalysis(h[0] ?? null))
          .catch(() => undefined)
      })
      .catch(e => messageApi.error(`详情加载失败: ${errDetail(e)}`))
      .finally(() => setDetailLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selId])

  // 期权标的 ATM 期限结构迷你图
  useEffect(() => {
    const container = ivTermChartRef.current
    if (!container || detail?.kind !== 'option_underlying' || !detail.iv_term?.length) return
    const chart = echarts.init(container, mode === 'dark' ? 'dark' : undefined)
    const dark = mode === 'dark'
    const axisText = dark ? '#94a3b8' : '#64748b'
    const gridColor = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.05)'
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      legend: { data: ['认购', '认沽'], textStyle: { color: axisText, fontSize: 11 } },
      grid: { left: 55, right: 16, top: 30, bottom: 28 },
      xAxis: {
        type: 'category',
        data: detail.iv_term.map(x => x.expiry.slice(5)),
        axisLabel: { color: axisText, fontSize: 10 },
        axisLine: { lineStyle: { color: gridColor } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: axisText, fontSize: 10, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        splitLine: { lineStyle: { color: gridColor } },
      },
      series: [
        { name: '认购', type: 'bar', barWidth: '30%', data: detail.iv_term.map(x => x.call_iv), color: '#6366f1' },
        { name: '认沽', type: 'bar', barWidth: '30%', data: detail.iv_term.map(x => x.put_iv), color: '#f43f5e' },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [detail, mode])

  // ------------------------------------------------------------------
  // 行为
  // ------------------------------------------------------------------

  const selectEntry = (id: string) => {
    setSelId(id)
    setAnalysis(null)
    // 同步到 marketStore：KlineChart 联动。期权合约 vt_symbol =
    // thscode.交易所（bars 表 symbol 存完整 thscode，末段为交易所）
    const entry = items.find(i => i.id === id)
    if (!entry) return
    if (entry.kind === 'option_contract') {
      const exchange = id.endsWith('.CFE') ? 'CFFEX' : id.endsWith('.SZ') ? 'SZSE' : 'SSE'
      select(`${id}.${exchange}`)
    } else if (entry.kind !== 'option_underlying') {
      select(id)
    }
  }

  const doAdd = async (id: string, kind?: string, name?: string) => {
    try {
      await addWatchSymbol(id, kind, name)
      messageApi.success(`已加入关注: ${name || id}`)
      await load()
    } catch (e) {
      messageApi.error(`加入关注失败: ${errDetail(e)}`)
    }
  }

  const doRemove = async (id: string) => {
    try {
      await removeWatchSymbol(id)
      messageApi.success(`已移除关注 ${id}`)
      if (selId === id) {
        setSelId('')
        setDetail(null)
      }
      await load()
    } catch {
      messageApi.error('移除失败')
    }
  }

  const runAnalyze = async () => {
    if (!selId) return
    setAnalyzing(true)
    try {
      const r = await aiAnalyze(selId, detail?.kind)
      setAnalysis({ id: r.id, kind: r.kind, time: new Date().toISOString(), reply: r.analysis, tool_calls: r.tool_calls.length })
      messageApi.success('AI 分析完成')
    } catch (e) {
      messageApi.error(`AI 分析失败: ${errDetail(e)}`)
    } finally {
      setAnalyzing(false)
    }
  }

  // ------------------------------------------------------------------
  // 添加弹窗：股票/期货合约搜索 + 期权搜索
  // ------------------------------------------------------------------

  useEffect(() => {
    if (!addOpen) return
    let cancelled = false
    const run = async () => {
      try {
        const results = await getContracts(searchText, 20)
        if (cancelled) return
        setSearchOptions(
          results.map(c => ({
            value: c.vt_symbol,
            label: `${c.vt_symbol}  ${c.name}（${c.gateway_name}）`,
          })),
        )
      } catch {
        setSearchOptions([])
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [addOpen, searchText])

  // 期权搜索：先载入标的；输入时搜该标的下的合约
  useEffect(() => {
    if (!addOpen) return
    getOptionUnderlyings()
      .then(r => setOptUnderlyings(r.items))
      .catch(() => undefined)
  }, [addOpen])

  useEffect(() => {
    if (!addOpen || !optSearchText.trim()) {
      setOptSearchResults([])
      return
    }
    const timer = setTimeout(() => {
      // 输入 6 位代码或品种 → 该标的的合约；否则按标的过滤
      const kw = optSearchText.trim()
      const targets = optUnderlyings.filter(
        u => u.underlying.includes(kw) || u.name.includes(kw) || u.variety_code.includes(kw.toUpperCase()),
      )
      setOptSearchResults(
        targets.map(u => ({
          value: `underlying:${u.underlying}`,
          label: `[期权标的] ${u.underlying} ${u.name}（${u.contract_count} 合约）`,
        })),
      )
    }, 300)
    return () => clearTimeout(timer)
  }, [addOpen, optSearchText, optUnderlyings])

  // 期权合约级搜索：选定标的后拉合约
  const [optContractTarget, setOptContractTarget] = useState<string>('')
  const [optContractResults, setOptContractResults] = useState<{ value: string; label: string }[]>([])
  useEffect(() => {
    if (!optContractTarget) {
      setOptContractResults([])
      return
    }
    getOptionContractsList(optContractTarget)
      .then(r => {
        setOptContractResults(
          (r.items ?? []).map((c: { thscode: string; name: string; option_type: string | null; strike_price: number | null }) => ({
            value: c.thscode,
            label: `[期权合约] ${c.name}（${c.option_type === 'call' ? '购' : '沽'}${c.strike_price}）`,
          })),
        )
      })
      .catch(() => setOptContractResults([]))
  }, [optContractTarget])

  // ------------------------------------------------------------------
  // 列表过滤
  // ------------------------------------------------------------------

  const filtered = useMemo(() => {
    if (tab === 'all') return items
    if (tab === 'option') return items.filter(i => i.kind.startsWith('option'))
    return items.filter(i => i.kind === tab)
  }, [items, tab])


  // ------------------------------------------------------------------
  // 关键价位表
  // ------------------------------------------------------------------

  const klColumns: ColumnsType<NonNullable<WatchDetail['key_levels']>['levels'][number]> = [
    { title: '类型', dataIndex: 'type', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    { title: '说明', dataIndex: 'label' },
    { title: '方向', dataIndex: 'side', width: 80, render: (v: string) => (v === 'support' ? <span className="price-up">支撑</span> : v === 'resistance' ? <span className="price-down">压力</span> : <Tag>枢轴</Tag>) },
    { title: '价位', dataIndex: 'value', width: 90, align: 'right', className: 'mono' },
    { title: '距离', dataIndex: 'distance_pct', width: 80, align: 'right', className: 'mono', render: (v: number) => fmtPct(v / 100) },
    { title: '强度', dataIndex: 'strength', width: 70, align: 'right', className: 'mono' },
  ]

  return (
    <GlassPage>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {contextHolder}
        <Row gutter={8}>
          {/* 左侧：关注列表 */}
          <Col span={8}>
            <Card
              size="small"
              title="关注列表"
              extra={
                <Button size="small" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>
                  添加
                </Button>
              }
            >
              <Tabs
                size="small"
                activeKey={tab}
                onChange={k => setTab(k as 'all' | 'option' | WatchKind)}
                items={[
                  { key: 'all', label: `全部 (${items.length})` },
                  { key: 'stock', label: `A股 (${items.filter(i => i.kind === 'stock').length})` },
                  { key: 'future', label: `期货 (${items.filter(i => i.kind === 'future').length})` },
                  { key: 'option', label: `期权 (${items.filter(i => i.kind.startsWith('option')).length})` },
                ]}
              />
              <div style={{ maxHeight: 560, overflowY: 'auto' }}>
                {filtered.length === 0 && (
                  <Empty description="暂无关注标的，点右上角添加" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
                {filtered.map(it => {
                  const pct = it.quote.change_pct
                  const cls = (pct ?? 0) > 0 ? 'price-up' : (pct ?? 0) < 0 ? 'price-down' : ''
                  return (
                    <div
                      key={it.id}
                      onClick={() => selectEntry(it.id)}
                      className="group/row mx-1 flex cursor-pointer items-center justify-between rounded-xl px-2.5 py-1.5 transition-colors"
                      style={{
                        background: selId === it.id ? 'rgba(22, 119, 255, 0.09)' : undefined,
                        boxShadow: selId === it.id ? 'inset 2px 0 0 0 #1677ff' : 'inset 2px 0 0 0 transparent',
                      }}
                    >
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="flex items-center gap-1.5 text-[13px] font-medium">
                          <span className="truncate">{it.name}</span>
                          <Tag color={KIND_COLOR[it.kind]} style={{ marginRight: 0, fontSize: 10, lineHeight: '16px' }}>
                            {KIND_LABEL[it.kind]}
                          </Tag>
                          {it.abnormal && (
                            <Tooltip title={`异动 ${it.abnormal.level}（接近度 ${it.abnormal.closeness}）`}>
                              <Tag color="red" style={{ marginRight: 0, fontSize: 10, lineHeight: '16px' }}>
                                异动
                              </Tag>
                            </Tooltip>
                          )}
                        </div>
                        <div className="mono text-[11px] text-slate-400">{it.id}</div>
                      </div>
                      <div className="text-right">
                        <div className={`mono text-[13px] ${cls}`}>
                          {it.quote.last != null ? it.quote.last : '--'}
                        </div>
                        <div className={`mono text-[11px] ${cls}`}>
                          {pct != null ? fmtPct(pct) : (it.kind.startsWith('option') ? '日K' : '--')}
                        </div>
                      </div>
                      <Button
                        type="text"
                        size="small"
                        icon={<CloseOutlined style={{ fontSize: 10 }} />}
                        onClick={e => {
                          e.stopPropagation()
                          void doRemove(it.id)
                        }}
                        style={{ marginLeft: 4, opacity: 0.4 }}
                      />
                    </div>
                  )
                })}
              </div>
            </Card>
          </Col>

          {/* 右侧：详情 */}
          <Col span={16}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {!selId && (
                <Card size="small">
                  <Empty description="左侧选择关注标的查看详情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                </Card>
              )}
              {selId && detailLoading && (
                <Card size="small">
                  <div style={{ textAlign: 'center', padding: 24 }}>
                    <Spin />
                  </div>
                </Card>
              )}
              {selId && detail && !detailLoading && (
                <>
                  {/* 行情头 */}
                  <Card size="small">
                    <Row gutter={12} align="middle">
                      <Col flex="auto">
                        <Space size={8} wrap>
                          <span style={{ fontSize: 16, fontWeight: 600 }}>{detail.name}</span>
                          <Text code>{detail.id}</Text>
                          <Tag color={KIND_COLOR[detail.kind]}>{KIND_LABEL[detail.kind]}</Tag>
                          {detail.quote.source === 'daily' && (
                            <Tooltip title="期权无端外实时快照，显示日K收盘价">
                              <Tag>日K收盘</Tag>
                            </Tooltip>
                          )}
                        </Space>
                      </Col>
                      <Col>
                        <Statistic
                          value={detail.quote.last ?? '-'}
                          precision={detail.quote.last != null && detail.quote.last < 10 ? 4 : 2}
                          valueStyle={{
                            fontSize: 22,
                            color: (detail.quote.change_pct ?? 0) > 0 ? 'var(--up-color)' : (detail.quote.change_pct ?? 0) < 0 ? 'var(--down-color)' : undefined,
                          }}
                        />
                      </Col>
                      <Col>
                        <Statistic title="涨跌" value={fmtPct(detail.quote.change_pct)} valueStyle={{ fontSize: 15 }} />
                      </Col>
                      <Col>
                        <Button
                          type="primary"
                          icon={<RobotOutlined />}
                          loading={analyzing}
                          onClick={() => void runAnalyze()}
                        >
                          AI 分析
                        </Button>
                      </Col>
                    </Row>
                  </Card>

                  {/* K线：股票/期货/期权合约均复用 KlineChart（期权选日线） */}
                  {detail.kind !== 'option_underlying' && (
                    <Card
                      size="small"
                      title={detail.kind === 'option_contract' ? 'K线（期权合约请选日线）' : 'K线'}
                    >
                      <KlineChart />
                    </Card>
                  )}

                  {/* 期权标的：ATM 期限结构 */}
                  {detail.kind === 'option_underlying' && (
                    <Card
                      size="small"
                      title="ATM 隐波期限结构"
                      extra={<Button size="small" onClick={() => (window.location.hash = '/options')}>去期权页看链</Button>}
                    >
                      {detail.iv_term?.length ? (
                        <div ref={ivTermChartRef} style={{ height: 220 }} />
                      ) : (
                        <Text type="secondary">暂无本地期权行情（先在期权页打开该标的的链）</Text>
                      )}
                    </Card>
                  )}

                  {/* 期权合约：合约要素 + IV 快照 */}
                  {detail.kind === 'option_contract' && detail.contract && (
                    <Card size="small" title="期权要素">
                      <Row gutter={12}>
                        <Col span={4}><Statistic title="类型" value={detail.contract.option_type === 'call' ? '认购' : '认沽'} valueStyle={{ fontSize: 15 }} /></Col>
                        <Col span={4}><Statistic title="行权价" value={detail.contract.strike_price ?? '-'} valueStyle={{ fontSize: 15 }} /></Col>
                        <Col span={4}><Statistic title="到期日" value={detail.contract.last_trade_date ?? '-'} valueStyle={{ fontSize: 15 }} /></Col>
                        <Col span={4}><Statistic title="合约单位" value={detail.contract.trade_amount ?? '-'} valueStyle={{ fontSize: 15 }} /></Col>
                        <Col span={4}><Statistic title="IV" value={detail.iv_snapshot?.iv != null ? `${(detail.iv_snapshot.iv * 100).toFixed(1)}%` : '-'} valueStyle={{ fontSize: 15 }} /></Col>
                        <Col span={4}><Statistic title="Delta" value={detail.iv_snapshot?.delta ?? '-'} valueStyle={{ fontSize: 15 }} /></Col>
                      </Row>
                    </Card>
                  )}

                  {/* 关键价位 */}
                  {detail.key_levels?.levels?.length ? (
                    <Card size="small" title={`关键价位 (${detail.key_levels.levels.length})`}>
                      <Table
                        size="small"
                        rowKey={(r, i) => `${r.type}-${r.value}-${i}`}
                        columns={klColumns}
                        dataSource={detail.key_levels.levels}
                        pagination={false}
                        className="compact-table"
                      />
                    </Card>
                  ) : null}

                  {/* 异动 / 告警 */}
                  {(detail.abnormal || detail.alerts.length > 0) && (
                    <Card size="small" title="异动与告警">
                      {detail.abnormal && (
                        <Tag color="red" style={{ marginBottom: 8 }}>
                          全市场异动 {String(detail.abnormal.level)} · 接近度 {String(detail.abnormal.closeness)} · 3日 {String(detail.abnormal.ret3_pct)}%
                        </Tag>
                      )}
                      {detail.alerts.map((a, i) => (
                        <div key={i} style={{ fontSize: 12 }}>
                          <Text type="secondary">[{a.time?.slice(5, 16).replace('T', ' ')}]</Text> {a.message}
                        </div>
                      ))}
                    </Card>
                  )}

                  {/* AI 分析 */}
                  <Card
                    size="small"
                    title="AI 分析"
                    extra={
                      <Button size="small" icon={<RobotOutlined />} loading={analyzing} onClick={() => void runAnalyze()}>
                        {analyzing ? '分析中…' : analysis ? '重新分析' : 'AI 分析'}
                      </Button>
                    }
                  >
                    {analysis ? (
                      <div className="ai-analysis-markdown" style={{ fontSize: 13, maxHeight: 420, overflowY: 'auto' }}>
                        <ReactMarkdown>{analysis.reply}</ReactMarkdown>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          生成于 {analysis.time?.slice(0, 19).replace('T', ' ')} · 不构成投资建议
                        </Text>
                      </div>
                    ) : (
                      <Text type="secondary">
                        点「AI 分析」生成该标的的分析报告（关键价位/异动/期权视角喂给模型）
                      </Text>
                    )}
                  </Card>
                </>
              )}
            </div>
          </Col>
        </Row>

        {/* 添加关注弹窗 */}
        <Modal title="添加关注" open={addOpen} onCancel={() => setAddOpen(false)} footer={null} width={560} destroyOnHidden>
          <Space direction="vertical" style={{ width: '100%' }} size={10}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              股票/期货：搜索合约或直接输代码（如 601899.SSE、rb2510.SHFE）；期权：输入标的代码（510050）或品种（IO）
            </Text>
            <AutoComplete
              style={{ width: '100%' }}
              value={searchText}
              options={searchOptions}
              onChange={v => setSearchText(v)}
              onSelect={v => {
                void doAdd(v)
                setAddOpen(false)
                setSearchText('')
              }}
              placeholder="股票/期货合约搜索"
              onKeyDown={e => {
                if (e.key === 'Enter' && searchText.trim()) void doAdd(searchText.trim())
              }}
            />
            <AutoComplete
              style={{ width: '100%' }}
              value={optSearchText}
              options={optSearchResults}
              onChange={v => {
                setOptSearchText(v)
                if (v.startsWith('underlying:')) setOptContractTarget(v.replace('underlying:', ''))
              }}
              onSelect={v => {
                if (v.startsWith('underlying:')) {
                  setOptContractTarget(v.replace('underlying:', ''))
                } else {
                  void doAdd(v)
                  setAddOpen(false)
                }
              }}
              placeholder="期权：标的（510050 / IO）→ 选合约"
            />
            {optContractResults.length > 0 && (
              <Select
                size="small"
                style={{ width: '100%' }}
                placeholder={`选择 ${optContractTarget} 的期权合约加入关注`}
                showSearch
                optionFilterProp="label"
                onChange={(v: string) => {
                  void doAdd(v)
                  setAddOpen(false)
                }}
                options={optContractResults}
              />
            )}
            <Button type="primary" block disabled={!searchText.trim()} onClick={() => void doAdd(searchText.trim())}>
              直接添加 {searchText.trim() || '...'}
            </Button>
          </Space>
        </Modal>
      </div>
    </GlassPage>
  )
}
