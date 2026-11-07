/** 市场自选列表：由用户维护的合约列表，实时显示最新价格。

 * - 添加：搜索已知合约（任意 Gateway），或直接输入任意 A 股代码
 *   如 601899.SSE（STOCK Gateway 会按需懒加载注册）。
 * - 移除：鼠标悬停某行，点击 × 按钮。
 * - 持久化保存在服务端（data/watchlist.json）。
 */

import { memo, useEffect, useState } from 'react'
import {
  AutoComplete,
  Button,
  Modal,
  Space,
  Typography,
  message,
} from 'antd'
import { CloseOutlined, PlusOutlined } from '@ant-design/icons'
import {
  addWatchSymbol,
  getContracts,
  getTicks,
  getWatchlist,
  removeWatchSymbol,
  subscribeSymbols,
} from '../api/http'
import { useMarketStore } from '../stores/market'
import type { Contract, Tick } from '../types'

const { Text } = Typography

/** 单条自选行。已做记忆化处理，且只订阅自己合约的 Tick 行情，
 * 因此合约 A 的 Tick 不会导致合约 B 的行重新渲染。 */
const WatchRow = memo(function WatchRow({
  vtSymbol,
  contract,
  fallbackName,
  summaryPct,
  isSelected,
  onSelect,
  onRemove,
}: {
  vtSymbol: string
  fallbackName?: string
  summaryPct?: number | null
  contract: Contract | undefined
  isSelected: boolean
  onSelect: (vtSymbol: string) => void
  onRemove: (vtSymbol: string, event: React.MouseEvent) => void
}) {
  const tick = useMarketStore(s => s.ticks[vtSymbol])
  const last = tick?.last_price ?? 0
  const preClose = tick?.pre_close ?? 0
  // openctp 模拟环境等场景 tick 无昨收：用关注摘要的日线口径涨跌兜底
  const pct = preClose
    ? ((last - preClose) / preClose) * 100
    : (summaryPct ?? 0) * 100
  const cls = pct > 0 ? 'price-up' : pct < 0 ? 'price-down' : ''
  const digits = (contract?.pricetick ?? 0.01) < 0.1 ? 2 : 1
  return (
    <div
      onClick={() => onSelect(vtSymbol)}
      className="watch-row group/row mx-1.5 flex cursor-pointer items-center justify-between rounded-xl px-2.5 py-1.5 transition-colors"
      style={{
        background: isSelected
          ? 'rgba(22, 119, 255, 0.09)'
          : undefined,
        boxShadow: isSelected
          ? 'inset 2px 0 0 0 #1677ff'
          : 'inset 2px 0 0 0 transparent',
      }}
      onMouseEnter={e => {
        if (!isSelected) e.currentTarget.style.background = 'rgba(22, 119, 255, 0.05)'
      }}
      onMouseLeave={e => {
        if (!isSelected) e.currentTarget.style.background = 'transparent'
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="text-[13px] font-medium text-slate-800 dark:text-slate-200">
          {contract?.name || tick?.name || fallbackName || vtSymbol.split('.')[0]}
        </div>
        <div className="mono text-[11px] text-slate-400 dark:text-slate-500">
          {vtSymbol}
        </div>
      </div>
      <div className="text-right">
        <div className={`mono text-[13px] ${cls}`}>
          {last ? last.toFixed(digits) : '--'}
        </div>
        <div
          className={`mono text-[11px] ${cls} ${last ? '' : 'opacity-0'}`}
        >
          <span
            className="inline-block rounded-md px-1.5 py-px"
            style={{
              background:
                pct > 0
                  ? 'rgba(244, 63, 94, 0.08)'
                  : pct < 0
                    ? 'rgba(16, 185, 129, 0.08)'
                    : undefined,
            }}
          >
            {last ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : ''}
          </span>
        </div>
      </div>
      <Button
        className="watch-remove"
        type="text"
        size="small"
        icon={<CloseOutlined style={{ fontSize: 10 }} />}
        onClick={e => onRemove(vtSymbol, e)}
        style={{ marginLeft: 4, opacity: 0.5 }}
      />
    </div>
  )
})

export default function MarketList() {
  const contracts = useMarketStore(s => s.contracts)
  const selected = useMarketStore(s => s.selected)
  const select = useMarketStore(s => s.select)
  const setContracts = useMarketStore(s => s.setContracts)
  const setTicks = useMarketStore(s => s.setTicks)

  const [watchlist, setWatchlist] = useState<string[]>([])
  const [nameMap, setNameMap] = useState<Record<string, string>>({})
  const [summaryQuote, setSummaryQuote] = useState<Record<string, { change_pct: number | null }>>({})
  const [addOpen, setAddOpen] = useState(false)
  const [searchText, setSearchText] = useState('')
  const [searchOptions, setSearchOptions] = useState<
    { value: string; label: string }[]
  >([])
  const [messageApi, contextHolder] = message.useMessage()

  // 加载合约（用于显示名称）与自选列表，并订阅自选合约行情
  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const [contractList, watchData] = await Promise.all([
          getContracts('', 0, true),
          getWatchlist(),
        ])
        if (!active) return
        setContracts(contractList)
        const items = watchData.items ?? []
        const symbols = items.map(i => i.id)
        setWatchlist(symbols)
        setNameMap(Object.fromEntries(items.map(i => [i.id, i.name] as const)))
        setSummaryQuote(
          Object.fromEntries(items.map(i => [i.id, { change_pct: i.quote?.change_pct ?? null }] as const)),
        )

        // 通过 REST API 加载当前 Tick 行情数据（以防 WebSocket
        // 还未收到任何 Tick —— 例如刚重连之后）
        getTicks().then(ticks => {
          const tickMap: Record<string, Tick> = {}
          for (const t of ticks) {
            tickMap[t.vt_symbol] = t
          }
          setTicks(tickMap)
        }).catch(() => {})

        // 将自选合约按所属 Gateway 分组后订阅
        const contractMap = new Map(
          contractList.map(c => [c.vt_symbol, c] as const),
        )
        const byGateway = new Map<string, string[]>()
        for (const vtSymbol of symbols) {
          const contract = contractMap.get(vtSymbol)
          if (!contract) continue
          const arr = byGateway.get(contract.gateway_name) ?? []
          arr.push(vtSymbol)
          byGateway.set(contract.gateway_name, arr)
        }
        for (const [gateway, list] of byGateway) {
          subscribeSymbols(gateway, list).catch(() => undefined)
        }
        if (symbols.length && !useMarketStore.getState().selected) {
          select(symbols[0])
        }
      } catch {
        // 下一个轮询周期再重试
      }
    }
    void load()
    const timer = setInterval(load, 30000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [setContracts, select])

  // 添加弹窗中的合约搜索
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

  const doAdd = async (vtSymbol: string) => {
    const target = vtSymbol.trim()
    if (!target) return
    try {
      const entry = await addWatchSymbol(target)
      setWatchlist(prev => (prev.includes(target) ? prev : [...prev, target]))
      setNameMap(prev => ({ ...prev, [target]: entry.name || target }))
      messageApi.success(`已添加 ${target}`)
      setAddOpen(false)
      setSearchText('')
      select(target)
      // 刷新合约列表，让懒加载注册的代码尽快显示名称
      setTimeout(() => {
        getContracts('', 0, true).then(setContracts).catch(() => undefined)
      }, 1500)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`添加失败: ${detail}`)
    }
  }

  const doRemove = async (vtSymbol: string, event: React.MouseEvent) => {
    event.stopPropagation()
    try {
      await removeWatchSymbol(vtSymbol)
      setWatchlist(prev => prev.filter(s => s !== vtSymbol))
      messageApi.success(`已移除 ${vtSymbol}`)
    } catch {
      messageApi.error('移除失败')
    }
  }

  const contractMap = new Map(contracts.map(c => [c.vt_symbol, c] as const))

  return (
    <div className="pb-2">
      {contextHolder}
      <div className="flex items-center justify-between border-b border-[var(--card-border)] px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          自选列表
        </span>
        <Button
          type="text"
          size="small"
          icon={<PlusOutlined />}
          onClick={() => setAddOpen(true)}
          className="!text-slate-500 hover:!bg-blue-500/10 hover:!text-blue-500"
        />
      </div>

      {watchlist.length === 0 && (
        <div className="px-4 py-6 text-center text-xs text-slate-400 dark:text-slate-500">
          点右上角 + 添加合约
        </div>
      )}

      {watchlist.map(vtSymbol => (
        <WatchRow
          key={vtSymbol}
          vtSymbol={vtSymbol}
          fallbackName={nameMap[vtSymbol]}
          summaryPct={summaryQuote[vtSymbol]?.change_pct}
          contract={contractMap.get(vtSymbol)}
          isSelected={vtSymbol === selected}
          onSelect={select}
          onRemove={(symbol, event) => void doRemove(symbol, event)}
        />
      ))}

      <Modal
        title="添加自选合约"
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        footer={null}
        width={520}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <AutoComplete
            style={{ width: '100%' }}
            value={searchText}
            options={searchOptions}
            onChange={value => setSearchText(value)}
            onSelect={value => void doAdd(value)}
            placeholder="搜索已有合约（代码/名称），或直接输入代码回车"
            autoFocus
            onKeyDown={e => {
              if (e.key === 'Enter') void doAdd(searchText)
            }}
          />
          <Button
            type="primary"
            block
            disabled={!searchText.trim()}
            onClick={() => void doAdd(searchText)}
          >
            添加 {searchText.trim() || '...'}
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            格式：代码.交易所，如 <Text code>601899.SSE</Text>（A股任意代码，
            STOCK 网关自动注册）、<Text code>rb2510.SHFE</Text>
            （期货，需已连接 CTP）。 交易所：SSE 上交所 /
            SZSE 深交所 / BSE 北交所 / SHFE 上期所 / DCE 大商所 / CZCE 郑商所 /
            CFFEX 中金所 / INE 能源中心 / GFEX 广期所
          </Text>
        </Space>
      </Modal>
    </div>
  )
}
