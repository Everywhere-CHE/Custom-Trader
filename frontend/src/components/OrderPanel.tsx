/** 交易面板：五档盘口（点击价格自动填入）、买入/卖出表单，
 * 以及持仓快捷平仓。 */

import { useEffect, useState } from 'react'
import {
  Button,
  Form,
  InputNumber,
  Radio,
  Segmented,
  Space,
  message,
} from 'antd'
import { cancelOrder, placeOrder } from '../api/http'
import { useMarketStore } from '../stores/market'
import { useTradingStore } from '../stores/trading'
import { useSoundStore } from '../stores/sound'
import { playBeep } from '../utils/sound'

export default function OrderPanel() {
  const selected = useMarketStore(s => s.selected)
  const tick = useMarketStore(s => (selected ? s.ticks[selected] : undefined))
  const contract = useMarketStore(s =>
    s.contracts.find(c => c.vt_symbol === s.selected),
  )
  const positions = useTradingStore(s => s.positions)
  const [messageApi, contextHolder] = message.useMessage()

  const msg = (level: 'success' | 'error' | 'info' | 'warning', content: string) => {
    if (useSoundStore.getState().enabled) playBeep(level === 'error' ? 'error' : 'success')
    messageApi[level](content)
  }

  const [direction, setDirection] = useState<'LONG' | 'SHORT'>('LONG')
  const [offset, setOffset] = useState<'OPEN' | 'CLOSE'>('OPEN')
  const [orderType, setOrderType] = useState<'LIMIT' | 'MARKET'>('LIMIT')
  const [price, setPrice] = useState<number>(0)
  const [volume, setVolume] = useState<number>(1)

  // 切换合约时刷新默认价格，并重置方向/开平/类型，避免误下单
  useEffect(() => {
    if (tick?.last_price) setPrice(tick.last_price)
    setDirection('LONG')
    setOffset('OPEN')
    setOrderType('LIMIT')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  // 首个 Tick 行情到达时填入价格（价格仍为空时）
  useEffect(() => {
    if (price === 0 && tick?.last_price) setPrice(tick.last_price)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick?.last_price])

  const pricetick = contract?.pricetick || 0.01

  const submit = async () => {
    if (!selected) return
    try {
      const result = await placeOrder({
        vt_symbol: selected,
        direction,
        offset,
        type: orderType,
        price: orderType === 'LIMIT' ? price : 0,
        volume,
      })
      try {
        msg('success', `委托已提交: ${result.vt_orderid}`)
      } catch {
        // msg 可能尚未就绪；忽略
      }
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      try {
        msg('error', `委托失败: ${detail}`)
      } catch {
        alert(`委托失败: ${detail}`)
      }
    }
  }

  const closePosition = async (
    vtSymbol: string,
    posDirection: string,
    posVolume: number,
  ) => {
    if (!tick) return
    const closeDirection = posDirection === 'LONG' ? 'SHORT' : 'LONG'
    const closePrice =
      closeDirection === 'SHORT'
        ? (tick.bid_price_1 ?? tick.last_price)
        : (tick.ask_price_1 ?? tick.last_price)
    try {
      await placeOrder({
        vt_symbol: vtSymbol,
        direction: closeDirection,
        offset: 'CLOSE',
        type: 'LIMIT',
        price: closePrice,
        volume: posVolume,
      })
      msg('success', '平仓委托已提交')
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      msg('error', `平仓失败: ${detail}`)
    }
  }

  const depthLevels = [5, 4, 3, 2, 1]
  const myPositions = Object.values(positions).filter(
    p => p.vt_symbol === selected && p.volume !== 0,
  )

  // 五档量能占比底色的基准（买卖各自的最大量）
  const askVols = depthLevels
    .map(l => (tick?.[`ask_volume_${l}` as keyof typeof tick] as number | undefined) ?? 0)
  const bidVols = depthLevels
    .slice()
    .reverse()
    .map(l => (tick?.[`bid_volume_${l}` as keyof typeof tick] as number | undefined) ?? 0)
  const maxAsk = Math.max(...askVols, 1)
  const maxBid = Math.max(...bidVols, 1)

  return (
    <div className="flex flex-col gap-3 p-3">
      {contextHolder}
      {/* ----- 五档盘口 ----- */}
      <div className="rounded-xl bg-[var(--subcard-bg)] p-1">
        {depthLevels.map((level, idx) => {
          const p = tick?.[`ask_price_${level}` as keyof typeof tick] as number | undefined
          const v = tick?.[`ask_volume_${level}` as keyof typeof tick] as number | undefined
          const ratio = ((v ?? 0) / maxAsk) * 100
          return (
            <div
              key={`ask${level}`}
              className="depth-row relative cursor-pointer rounded-lg"
              onClick={() => p && setPrice(p)}
            >
              <div
                aria-hidden
                className="absolute inset-y-0 right-0 rounded-lg bg-rose-500/[0.08] transition-[width] duration-300"
                style={{ width: `${ratio}%` }}
              />
              <span className="relative z-10 text-[11px] text-slate-400">卖{level}</span>
              <span className="price-down mono relative z-10">{p != null ? p : '--'}</span>
              <span className="mono relative z-10 text-slate-400">
                {v != null && v > 0 ? v : ''}
              </span>
              <span className="hidden">{idx}</span>
            </div>
          )
        })}
        <div
          className="depth-row relative my-0.5 cursor-default rounded-lg border-y border-[var(--card-border)] font-semibold"
          style={{ padding: '4px 8px' }}
        >
          <span className="text-[11px] text-slate-400">最新</span>
          <span
            className={`mono ${
              (tick?.last_price ?? 0) >= (tick?.pre_close ?? 0)
                ? 'price-up'
                : 'price-down'
            }`}
          >
            {tick?.last_price ?? '--'}
          </span>
          <span />
        </div>
        {depthLevels
          .slice()
          .reverse()
          .map(level => {
            const p = tick?.[`bid_price_${level}` as keyof typeof tick] as number | undefined
            const v = tick?.[
              `bid_volume_${level}` as keyof typeof tick
            ] as number | undefined
            const ratio = ((v ?? 0) / maxBid) * 100
            return (
              <div
                key={`bid${level}`}
                className="depth-row relative cursor-pointer rounded-lg"
                onClick={() => p && setPrice(p)}
              >
                <div
                  aria-hidden
                  className="absolute inset-y-0 right-0 rounded-lg bg-emerald-500/[0.08] transition-[width] duration-300"
                  style={{ width: `${ratio}%` }}
                />
                <span className="relative z-10 text-[11px] text-slate-400">买{level}</span>
                <span className="price-up mono relative z-10">{p != null ? p : '--'}</span>
                <span className="mono relative z-10 text-slate-400">
                  {v != null && v > 0 ? v : ''}
                </span>
              </div>
            )
          })}
      </div>

      {/* ----- 下单表单 ----- */}
      <Form layout="vertical" size="small">
        <Space direction="vertical" style={{ width: '100%' }} size={6}>
          <Segmented
            block
            value={direction}
            onChange={value => setDirection(value as 'LONG' | 'SHORT')}
            options={[
              { label: '买入', value: 'LONG' },
              { label: '卖出', value: 'SHORT' },
            ]}
          />
          <Radio.Group
            size="small"
            value={offset}
            onChange={e => setOffset(e.target.value as 'OPEN' | 'CLOSE')}
            options={[
              { label: '开仓', value: 'OPEN' },
              { label: '平仓', value: 'CLOSE' },
            ]}
            optionType="button"
            buttonStyle="solid"
            className="w-full"
          />
          <Radio.Group
            size="small"
            value={orderType}
            onChange={e => setOrderType(e.target.value as 'LIMIT' | 'MARKET')}
            options={[
              { label: '限价', value: 'LIMIT' },
              { label: '市价', value: 'MARKET' },
            ]}
            optionType="button"
          />
          {orderType === 'LIMIT' && (
            <InputNumber
              style={{ width: '100%' }}
              value={price}
              step={pricetick}
              onChange={value => setPrice(value ?? 0)}
              addonBefore="价格"
            />
          )}
          <InputNumber
            style={{ width: '100%' }}
            value={volume}
            min={1}
            step={1}
            onChange={value => setVolume(value ?? 1)}
            addonBefore="数量"
          />
          <Button
            block
            size="large"
            className="!rounded-xl !border-0 font-semibold !text-white transition-all hover:!opacity-90"
            style={
              direction === 'LONG'
                ? {
                    background:
                      'linear-gradient(135deg, #fb7185 0%, #f43f5e 100%)',
                    boxShadow: '0 4px 14px rgba(244, 63, 94, 0.3)',
                  }
                : {
                    background:
                      'linear-gradient(135deg, #34d399 0%, #10b981 100%)',
                    boxShadow: '0 4px 14px rgba(16, 185, 129, 0.3)',
                  }
            }
            onClick={submit}
            disabled={!selected}
          >
            {direction === 'LONG' ? '买入' : '卖出'}
            {offset === 'OPEN' ? '开仓' : '平仓'}
          </Button>
        </Space>
      </Form>

      {/* ----- 快捷平仓 ----- */}
      {myPositions.length > 0 && (
        <div className="mt-1">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-400">
            当前持仓
          </div>
          {myPositions.map(p => (
            <div
              key={p.vt_positionid}
              className="flex items-center justify-between rounded-lg px-1 py-1 text-xs transition-colors hover:bg-blue-500/5"
            >
              <span
                className={`mono ${p.direction === 'LONG' ? 'price-up' : 'price-down'}`}
              >
                {p.direction === 'LONG' ? '多' : '空'} {p.volume}手 @{' '}
                {p.price.toFixed(1)}
              </span>
              <Button
                size="small"
                className="!rounded-lg"
                onClick={() =>
                  closePosition(p.vt_symbol, p.direction, p.volume)
                }
              >
                平仓
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export { cancelOrder }
