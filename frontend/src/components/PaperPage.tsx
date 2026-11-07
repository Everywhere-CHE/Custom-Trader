/** 模拟盘页：账户总览（台账重放）、市价单下单、成交台账、参数与重置。
 *  fills.jsonl append-only 为唯一事实源，持仓/权益全部由重放推导。 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Form,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  getPaperFills,
  getPaperOverview,
  placePaperOrder,
  resetPaper,
  updatePaperSettings,
} from '../api/http'
import type { PaperFill, PaperOverview, PaperPosition } from '../api/http'
import GlassPage from './ui/GlassPage'
import { useMarketStore } from '../stores/market'

const { Text } = Typography

export default function PaperPage() {
  const [messageApi, contextHolder] = message.useMessage()
  const contracts = useMarketStore(s => s.contracts)
  const [overview, setOverview] = useState<PaperOverview | null>(null)
  const [fills, setFills] = useState<PaperFill[]>([])
  const [orderLoading, setOrderLoading] = useState(false)
  const [form] = Form.useForm()
  const settingsFilledRef = useRef(false)

  const load = useCallback(async () => {
    try {
      const [o, f] = await Promise.all([getPaperOverview(), getPaperFills(200)])
      setOverview(o)
      setFills(f)
      // 参数只在首次载入时回填，避免轮询覆盖用户正在编辑的值
      if (!settingsFilledRef.current && o.settings) {
        form.setFieldsValue({
          capital: o.settings.capital,
          rate: o.settings.rate,
          slippage: o.settings.slippage,
        })
        settingsFilledRef.current = true
      }
    } catch {
      // 静默（轮询场景）
    }
  }, [form])

  useEffect(() => {
    void load()
    const timer = setInterval(load, 4000)
    return () => clearInterval(timer)
  }, [load])

  const submitOrder = async (direction: 'buy' | 'sell') => {
    const values = await form.validateFields()
    setOrderLoading(true)
    try {
      const r = await placePaperOrder({
        vt_symbol: values.vt_symbol,
        direction,
        volume: values.volume,
      })
      messageApi.success(r.message)
      await load()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`下单失败: ${detail}`)
    } finally {
      setOrderLoading(false)
    }
  }

  const saveSettings = async () => {
    const values = await form.validateFields(['capital', 'rate', 'slippage'])
    try {
      await updatePaperSettings(values)
      messageApi.success('参数已保存')
      await load()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`保存失败: ${detail}`)
    }
  }

  const positionColumns: ColumnsType<PaperPosition> = [
    { title: '合约', dataIndex: 'vt_symbol', width: 150, className: 'mono' },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 70,
      render: (v: string) => (
        <Tag color={v === 'long' ? 'red' : 'green'}>{v === 'long' ? '多' : '空'}</Tag>
      ),
    },
    { title: '数量', dataIndex: 'volume', width: 80, className: 'mono' },
    { title: '均价', dataIndex: 'avg_price', width: 100, className: 'mono' },
    { title: '现价', dataIndex: 'mark_price', width: 100, className: 'mono' },
    {
      title: '浮动盈亏',
      dataIndex: 'unrealized_pnl',
      width: 110,
      render: (v: number) => (
        <span className={v >= 0 ? 'price-up' : 'price-down'}>{v.toFixed(2)}</span>
      ),
    },
  ]

  const fillColumns: ColumnsType<PaperFill> = [
    { title: '时间', dataIndex: 'time', width: 150, render: (v: string) => <span className="mono" style={{ fontSize: 11 }}>{v.slice(5, 19).replace('T', ' ')}</span> },
    { title: '合约', dataIndex: 'vt_symbol', width: 140, className: 'mono' },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 70,
      render: (v: string) => (
        <span className={v === 'LONG' ? 'price-up' : 'price-down'}>
          {v === 'LONG' ? '买入' : '卖出'}
        </span>
      ),
    },
    { title: '价格', dataIndex: 'price', width: 100, className: 'mono' },
    { title: '数量', dataIndex: 'volume', width: 80, className: 'mono' },
    { title: '手续费', dataIndex: 'fee', width: 90, className: 'mono' },
    {
      title: '来源',
      dataIndex: 'source',
      width: 90,
      render: (v: string) =>
        v === 'monitor' ? <Tag color="orange">监控跟单</Tag> : <Tag>手动</Tag>,
    },
    { title: '备注', dataIndex: 'reason', ellipsis: true },
  ]

  return (
    <GlassPage>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {contextHolder}

        <Card size="small" title="账户总览">
          {overview ? (
            <Row gutter={16}>
              <Col span={3}><Statistic title="权益" value={overview.equity} precision={2} /></Col>
              <Col span={3}><Statistic title="可用现金" value={overview.cash} precision={2} /></Col>
              <Col span={3}><Statistic title="总收益率" value={overview.total_return_pct} suffix="%" precision={2} valueStyle={{ color: overview.total_return_pct >= 0 ? 'var(--up-color)' : 'var(--down-color)' }} /></Col>
              <Col span={3}><Statistic title="已实现盈亏" value={overview.realized_pnl} precision={2} valueStyle={{ color: overview.realized_pnl >= 0 ? 'var(--up-color)' : 'var(--down-color)' }} /></Col>
              <Col span={3}><Statistic title="浮动盈亏" value={overview.unrealized_pnl} precision={2} valueStyle={{ color: overview.unrealized_pnl >= 0 ? 'var(--up-color)' : 'var(--down-color)' }} /></Col>
              <Col span={3}><Statistic title="累计手续费" value={overview.fees_total} precision={2} /></Col>
              <Col span={3}><Statistic title="成交笔数" value={overview.trade_count} /></Col>
            </Row>
          ) : (
            <Text type="secondary">加载中…</Text>
          )}
        </Card>

        <Card size="small" title="下单（市价，按最新快照价即时成交）">
          <Form form={form} layout="inline">
            <Form.Item name="vt_symbol" label="合约" rules={[{ required: true, message: '请选择合约' }]}>
              <Select
                showSearch
                style={{ width: 260 }}
                options={contracts.map(c => ({ label: `${c.vt_symbol} ${c.name}`, value: c.vt_symbol }))}
                filterOption={(input, option) =>
                  (option?.label as string)?.toLowerCase().includes(input.toLowerCase()) ?? false
                }
                placeholder="搜索合约（需已在行情页订阅）"
              />
            </Form.Item>
            <Form.Item name="volume" label="数量" rules={[{ required: true }]} initialValue={1}>
              <InputNumber min={1} style={{ width: 110 }} />
            </Form.Item>
            <Form.Item>
              <Space>
                <Button type="primary" loading={orderLoading} onClick={() => submitOrder('buy')} style={{ background: 'var(--up-color)' }}>
                  买入
                </Button>
                <Button type="primary" loading={orderLoading} onClick={() => submitOrder('sell')} style={{ background: 'var(--down-color)' }}>
                  卖出
                </Button>
              </Space>
            </Form.Item>
          </Form>
          <Text type="secondary" style={{ fontSize: 12, marginTop: 6, display: 'block' }}>
            净持仓模型：期货支持多空双向；股票不允许做空。无实时行情时拒单（请先在行情页订阅该合约）。
          </Text>
        </Card>

        <Card size="small" title={`持仓 (${overview?.positions.length ?? 0})`}>
          <Table<PaperPosition>
            size="small"
            rowKey="vt_symbol"
            columns={positionColumns}
            dataSource={overview?.positions ?? []}
            pagination={false}
            locale={{ emptyText: '无持仓' }}
            className="compact-table"
          />
        </Card>

        <Card
          size="small"
          title="参数与重置"
          extra={
            <Space>
              <Button size="small" onClick={saveSettings}>
                保存参数
              </Button>
              <Popconfirm
                title="重置模拟盘账户？"
                description="旧成交台账会归档为 fills.<时间戳>.jsonl，不会删除"
                onConfirm={async () => {
                  await resetPaper()
                  messageApi.success('已重置（旧台账已归档）')
                  await load()
                }}
              >
                <Button size="small" danger>
                  重置账户
                </Button>
              </Popconfirm>
            </Space>
          }
        >
          <Form form={form} layout="inline">
            <Form.Item name="capital" label="初始资金" tooltip="重置后按此资金重新起算">
              <InputNumber style={{ width: 130 }} step={100000} />
            </Form.Item>
            <Form.Item name="rate" label="手续费率" tooltip="按成交额计，如 0.0001 = 万分之一">
              <InputNumber style={{ width: 110 }} step={0.0001} />
            </Form.Item>
            <Form.Item name="slippage" label="滑点" tooltip="按价格绝对值，买入加、卖出减">
              <InputNumber style={{ width: 90 }} step={0.01} />
            </Form.Item>
          </Form>
        </Card>

        <Card size="small" title={`成交台账 (${fills.length})`}>
          <Table<PaperFill>
            size="small"
            rowKey={(f, i) => `${f.time}-${i}`}
            columns={fillColumns}
            dataSource={fills}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            className="compact-table"
          />
        </Card>
      </div>
    </GlassPage>
  )
}
