/** 设置页面：Gateway 连接管理 + 风控参数。 */

import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ApiOutlined, SettingOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  connectGateway,
  disconnectGateway,
  getFuyaoConfig,
  getGateways,
  getPositions,
  getRisk,
  getStockConfig,
  removeTrailingStop,
  setTrailingStop,
  testFuyao,
  updateFuyaoConfig,
  updateRisk,
  updateStockConfig,
} from '../api/http'
import type { FuyaoTestResult } from '../api/http'
import GlassPage from './ui/GlassPage'
import type { GatewayInfo } from '../types'
import CtpSettingsModal from './CtpSettingsModal'
import { useAuthStore } from '../stores/auth'
import { useSoundStore } from '../stores/sound'

const { Text } = Typography

const RISK_LABELS: Record<string, string> = {
  active: '风控开关',
  order_flow_limit: '每秒委托笔数上限',
  order_flow_clear: '流控窗口(秒)',
  order_size_limit: '单笔委托量上限',
  order_count_limit: '当日总委托数上限',
  active_order_limit: '活动委托数上限',
  trade_count_limit: '当日成交数上限',
  position_limit: '单品种持仓上限',
  daily_loss_limit_pct: '当日亏损熔断(%)',
  enable_order_size: '单笔量限制',
  enable_order_flow: '流控限制',
  enable_order_count: '当日委托笔数限制',
  enable_active_order: '活动委托数限制',
  enable_trade_count: '当日成交笔数限制',
  enable_position_limit: '持仓上限限制',
  enable_daily_loss_breaker: '亏损熔断',
  enable_trailing_stop: '合约移动止损',
  trailing_stops: '各合约移动止损配置',
}

const STATUS_LABELS: Record<string, string> = {
  order_flow_count: '当前流控计数',
  order_count: '当日委托笔数',
  trade_count: '当日成交笔数',
  breaker_tripped: '熔断状态',
  day_start_balance: '账户日初权益',
  current_date: '交易日',
}

/** 合约移动止损配置管理组件 */
function TrailingStopManager() {
  const [stops, setStops] = useState<Record<string, number>>({})
  const [key, setKey] = useState<string | undefined>(undefined)
  const [pct, setPct] = useState<number>(5)
  const [loading, setLoading] = useState(false)
  const [positions, setPositions] = useState<{ label: string; value: string }[]>([])
  const [messageApi, contextHolder] = message.useMessage()

  const loadStops = useCallback(async () => {
    try {
      const res = await getRisk()
      const stops = res.settings?.trailing_stops as unknown as Record<string, number> | undefined
      setStops(stops || {})
    } catch { /* ignore */ }
  }, [])

  const loadPositions = useCallback(async () => {
    try {
      const data = await getPositions()
      const list = data
        .filter(p => p.volume > 0)
        .map(p => ({
          label: `${p.vt_symbol}.${p.direction}（${p.volume}手）`,
          value: `${p.vt_symbol}.${p.direction}`,
        }))
      // 也加上策略的合约
      setPositions(list)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { loadStops(); loadPositions() }, [loadStops, loadPositions])

  const addStop = async () => {
    if (!key) { messageApi.warning('请选择合约'); return }
    if (!pct || pct <= 0) { messageApi.warning('请设置有效百分比'); return }
    setLoading(true)
    try {
      await setTrailingStop(key, pct)
      messageApi.success(`已设置 ${key} = ${pct}%`)
      await loadStops()
    } catch { messageApi.error('设置失败') }
    finally { setLoading(false) }
  }

  const removeStop = async (k: string) => {
    setLoading(true)
    try {
      await removeTrailingStop(k)
      messageApi.success(`已删除 ${k}`)
      await loadStops()
    } catch { messageApi.error('删除失败') }
    finally { setLoading(false) }
  }

  return (
    <div style={{ marginTop: 12, padding: '8px 0', borderTop: '1px solid var(--border-color)' }}>
      {contextHolder}
      <Text strong style={{ fontSize: 13 }}>合约移动止损配置</Text>

      {/* 已有配置列表 */}
      <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {Object.entries(stops).length === 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>暂无配置，请添加</Text>
        )}
        {Object.entries(stops).map(([k, v]) => (
          <Tag
            key={k}
            closable
            onClose={() => removeStop(k)}
            style={{ fontSize: 12 }}
          >
            {k} = {v}%
          </Tag>
        ))}
      </div>

      {/* 新增配置 */}
      <Space style={{ marginTop: 8 }} wrap>
        <Select
          size="small"
          placeholder="选择合约"
          value={key}
          onChange={v => setKey(v)}
          style={{ width: 260 }}
          showSearch
          filterOption={(input, option) =>
            (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
          }
          options={positions}
          dropdownRender={menu => (
            <>
              {menu}
              <div style={{ padding: '4px 8px', borderTop: '1px solid #eee', fontSize: 11, color: '#999' }}>
                来自当前持仓中的合约
              </div>
            </>
          )}
        />
        <InputNumber
          size="small"
          value={pct}
          onChange={v => setPct(v ?? 5)}
          min={0.1}
          max={50}
          step={0.5}
          style={{ width: 80 }}
          addonAfter="%"
        />
        <Button size="small" type="primary" loading={loading} onClick={addStop}>
          添加
        </Button>
      </Space>
    </div>
  )
}

import BackupCard from './BackupCard'
import DataHealthCard from './DataHealthCard'
import MarketSyncCard from './MarketSyncCard'
import NotifySettingsCard from './NotifySettingsCard'

export default function SettingsPage() {
  const [gateways, setGateways] = useState<GatewayInfo[]>([])
  const [ctpSettingsOpen, setCtpSettingsOpen] = useState(false)
  const [stockSettingsOpen, setStockSettingsOpen] = useState(false)
  const [savingStock, setSavingStock] = useState(false)
  const [riskSettings, setRiskSettings] = useState<Record<
    string,
    number | boolean
  > | null>(null)
  const [riskStatus, setRiskStatus] = useState<Record<string, unknown>>({})
  const [messageApi, contextHolder] = message.useMessage()
  const [riskForm] = Form.useForm()
  const [changePwdOpen, setChangePwdOpen] = useState(false)
  const [pwd, setPwd] = useState(['', '', ''])  // [old, new, confirm]
  const [stockForm] = Form.useForm()
  const [fuyaoSettingsOpen, setFuyaoSettingsOpen] = useState(false)
  const [savingFuyao, setSavingFuyao] = useState(false)
  const [testingFuyao, setTestingFuyao] = useState(false)
  const [fuyaoTestResult, setFuyaoTestResult] =
    useState<FuyaoTestResult | null>(null)
  const [fuyaoForm] = Form.useForm()

  const refreshGateways = () =>
    getGateways().then(setGateways).catch(() => undefined)

  const refreshRisk = () =>
    getRisk()
      .then(data => {
        setRiskSettings(data.settings)
        setRiskStatus(data.status as Record<string, unknown>)
        riskForm.setFieldsValue(data.settings)
      })
      .catch(() => undefined)

  useEffect(() => {
    void refreshGateways()
    void refreshRisk()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openStockSettings = async () => {
    try {
      const cfg = await getStockConfig()
      stockForm.setFieldsValue({ poll_interval: cfg.poll_interval })
      setStockSettingsOpen(true)
    } catch {
      messageApi.error('加载 STOCK 配置失败')
    }
  }

  const saveStockConfig = async () => {
    const values = stockForm.getFieldsValue() as { poll_interval: number }
    setSavingStock(true)
    try {
      await updateStockConfig(values)
      messageApi.success('STOCK 配置已保存，断开后重连生效')
      setStockSettingsOpen(false)
    } catch {
      messageApi.error('保存失败')
    } finally {
      setSavingStock(false)
    }
  }

  const openFuyaoSettings = async () => {
    try {
      const cfg = await getFuyaoConfig()
      fuyaoForm.setFieldsValue({
        // 脱敏 Key 回填为空，placeholder 提示当前 Key；输入即覆盖
        api_key: '',
        masked_key: cfg.api_key || '',
        poll_interval: cfg.poll_interval,
        enable_quotes: cfg.enable_quotes,
      })
      setFuyaoTestResult(null)
      setFuyaoSettingsOpen(true)
    } catch {
      messageApi.error('加载 FUYAO 配置失败')
    }
  }

  const saveFuyaoConfig = async () => {
    const values = fuyaoForm.getFieldsValue() as {
      api_key?: string
      poll_interval: number
      enable_quotes: boolean
    }
    setSavingFuyao(true)
    try {
      // api_key 留空 = 保留原 Key
      await updateFuyaoConfig({
        api_key: (values.api_key || '').trim() || undefined,
        poll_interval: values.poll_interval,
        enable_quotes: values.enable_quotes,
      })
      messageApi.success('FUYAO 配置已保存，断开后重连生效')
      setFuyaoSettingsOpen(false)
    } catch {
      messageApi.error('保存失败')
    } finally {
      setSavingFuyao(false)
    }
  }

  const runFuyaoTest = async () => {
    setTestingFuyao(true)
    setFuyaoTestResult(null)
    try {
      const result = await testFuyao()
      setFuyaoTestResult(result)
    } catch {
      messageApi.error('测试请求失败（后端不可达？）')
    } finally {
      setTestingFuyao(false)
    }
  }

  const gatewayColumns: ColumnsType<GatewayInfo> = [
    { title: '网关', dataIndex: 'name', width: 100 },
    {
      title: '状态',
      dataIndex: 'connected',
      width: 100,
      render: (v: boolean) =>
        v ? <Tag color="success">已连接</Tag> : <Tag>未连接</Tag>,
    },
    {
      title: '支持交易所',
      dataIndex: 'exchanges',
      render: (v: string[]) => v.join(' '),
    },
  {
    title: '操作',
    width: 320,
      render: (_, r) => (
        <Space size={4}>
          {!r.connected ? (
            <Button
              size="small"
              type="primary"
              onClick={async () => {
                try {
                  await connectGateway(r.name)
                  messageApi.success(
                    `${r.name} 连接请求已提交（结果见日志）`,
                  )
                  setTimeout(() => void refreshGateways(), 3000)
                } catch {
                  messageApi.error('连接失败')
                }
              }}
            >
              连接
            </Button>
          ) : (
            <Popconfirm
              title={`确认断开 ${r.name}？`}
              onConfirm={async () => {
                try {
                  await disconnectGateway(r.name)
                  messageApi.success(`${r.name} 已断开`)
                  setTimeout(() => void refreshGateways(), 1000)
                } catch {
                  messageApi.error('断开失败')
                }
              }}
            >
              <Button size="small" danger>
                断开
              </Button>
            </Popconfirm>
          )}
          {r.name === 'STOCK' && (
            <Button
              size="small"
              icon={<SettingOutlined />}
              onClick={openStockSettings}
            >
              行情设置
            </Button>
          )}
          {r.name === 'CTP' && (
            <Button
              size="small"
              icon={<SettingOutlined />}
              onClick={() => setCtpSettingsOpen(true)}
            >
              账号设置
            </Button>
          )}
          {r.name === 'FUYAO' && (
            <>
              <Button
                size="small"
                icon={<SettingOutlined />}
                onClick={openFuyaoSettings}
              >
                数据源设置
              </Button>
              <Button
                size="small"
                icon={<ApiOutlined />}
                loading={testingFuyao}
                onClick={async () => {
                  await openFuyaoSettings()
                  await runFuyaoTest()
                }}
              >
                测试
              </Button>
            </>
          )}
        </Space>
      ),
    },
  ]

  return (
    <GlassPage>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {contextHolder}
      <Card size="small" title="网关管理">
        <Table
          size="small"
          rowKey="name"
          columns={gatewayColumns}
          dataSource={gateways}
          pagination={false}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          STOCK 设置修改后需断开重连生效；CTP 支持 SimNow 仿真与实盘期货公司柜台：点「账号设置」配置投资者代码/密码/服务器（含
          SimNow 与 openctp 预设，实盘信息由期货公司提供）；FUYAO 为同花顺金融数据
          API（A 股日线历史 + 可选快照），点「数据源设置」填 API Key，「测试」可自动调试连通性；切换配置需先断开再连接。
        </Text>
      </Card>

      {/* STOCK 设置弹窗 */}
      <Modal
        title="STOCK 股票行情网关设置"
        open={stockSettingsOpen}
        onCancel={() => setStockSettingsOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setStockSettingsOpen(false)}>
            取消
          </Button>,
          <Button key="save" type="primary" loading={savingStock} onClick={saveStockConfig}>
            保存
          </Button>,
        ]}
        width={400}
      >
        <Form form={stockForm} layout="vertical" size="middle">
          <Form.Item
            name="poll_interval"
            label="轮询间隔（秒）"
            tooltip="拉取腾讯行情接口的频率（秒），数值越小行情更新越快但对 API 请求频率更高，默认 3.0"
            rules={[{ required: true }]}
          >
            <InputNumber
              min={0.5}
              max={30}
              step={0.5}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Form>
        <Text type="secondary" style={{ fontSize: 12 }}>
          配置保存在 data/stock_config.json，保存后需断开 STOCK 再重新连接以生效。
        </Text>
      </Modal>

      <CtpSettingsModal
        open={ctpSettingsOpen}
        onClose={() => setCtpSettingsOpen(false)}
        onSaved={() => void refreshGateways()}
      />

      {/* FUYAO 数据源设置弹窗 */}
      <Modal
        title="FUYAO 同花顺数据源设置"
        open={fuyaoSettingsOpen}
        onCancel={() => setFuyaoSettingsOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setFuyaoSettingsOpen(false)}>
            取消
          </Button>,
          <Button
            key="test"
            icon={<ApiOutlined />}
            loading={testingFuyao}
            onClick={runFuyaoTest}
          >
            测试连接
          </Button>,
          <Button key="save" type="primary" loading={savingFuyao} onClick={saveFuyaoConfig}>
            保存
          </Button>,
        ]}
        width={520}
      >
        <Form form={fuyaoForm} layout="vertical" size="middle">
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.masked_key !== cur.masked_key}
          >
            {({ getFieldValue }) => (
              <Form.Item
                name="api_key"
                label="API Key"
                tooltip={
                  getFieldValue('masked_key')
                    ? `已配置（${getFieldValue('masked_key')}），留空保留原 Key；在 fuyao.aicubes.cn 的 /admin/ 页签发`
                    : '在 fuyao.aicubes.cn 注册后在 /admin/ 页签发 API Key'
                }
              >
                <Input.Password
                  placeholder={
                    getFieldValue('masked_key')
                      ? `已配置（${getFieldValue('masked_key')}），留空保留原 Key`
                      : '粘贴 X-api-key'
                  }
                  autoComplete="new-password"
                />
              </Form.Item>
            )}
          </Form.Item>
          <Form.Item
            name="poll_interval"
            label="快照轮询间隔（秒）"
            tooltip="仅在开启实时快照时生效；同花顺 API 会动态限流，建议 ≥5 秒"
            rules={[{ required: true }]}
          >
            <InputNumber min={2} max={60} step={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="enable_quotes"
            label="实时快照行情"
            valuePropName="checked"
            tooltip="关闭时 FUYAO 仅作为日线历史数据源（回测/策略历史）；实时盘口由免费的腾讯源提供"
          >
            <Switch checkedChildren="开" unCheckedChildren="关" />
          </Form.Item>
        </Form>

        {fuyaoTestResult && (
          <div style={{ marginTop: 8 }}>
            <Text strong style={{ fontSize: 13 }}>
              自动调试结果：
              <Tag color={fuyaoTestResult.ok ? 'success' : 'error'}>
                {fuyaoTestResult.ok ? '全部通过' : '存在问题'}
              </Tag>
            </Text>
            <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {fuyaoTestResult.checks.map(c => (
                <div key={c.name} style={{ fontSize: 12, lineHeight: 1.5 }}>
                  <Tag color={c.ok ? 'success' : 'error'} style={{ marginRight: 6 }}>
                    {c.ok ? '通过' : '失败'}
                  </Tag>
                  <Text strong style={{ marginRight: 6 }}>{c.name}</Text>
                  <Text type="secondary">
                    {c.latency_ms}ms · {c.detail}
                  </Text>
                </div>
              ))}
            </div>
          </div>
        )}

        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 10 }}>
          连接后 A 股日线（前复权）自动优先走同花顺源，腾讯源自动回退；配置保存在
          data/fuyao_config.json，保存后需断开 FUYAO 再重新连接以生效。
        </Text>
      </Modal>

      <Card
        size="small"
        title="风控参数"
        extra={
          <Button size="small" onClick={() => void refreshRisk()}>
            刷新
          </Button>
        }
      >
        {riskSettings && (
          <>
            {/* 各规则独立开关 */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 12, padding: '6px 0' }}>
              {Object.entries(riskSettings)
                .filter(([key]) => key.startsWith('enable_') || key === 'active')
                .map(([key, value]) => (
                  <Checkbox
                    key={key}
                    checked={Boolean(value)}
                    onChange={async e => {
                      const newVal = e.target.checked
                      // 直接更新后端，不依赖 Form 提交
                      try {
                        await updateRisk({ [key]: newVal } as Record<string, number | boolean>)
                        // 更新本地状态，立即反映勾选变化
                        setRiskSettings(prev => prev ? { ...prev, [key]: newVal } : prev)
                      } catch {
                        messageApi.error('更新失败')
                      }
                    }}
                  >
                    {RISK_LABELS[key] ?? key}
                  </Checkbox>
                ))}
            </div>
            <Form
              form={riskForm}
              layout="inline"
              onFinish={async values => {
                try {
                  await updateRisk(values as Record<string, number | boolean>)
                  messageApi.success('风控参数已更新')
                  void refreshRisk()
                } catch {
                  messageApi.error('更新失败')
                }
              }}
            >
              {Object.entries(riskSettings)
                .filter(([key]) => !key.startsWith('enable_') && key !== 'active' && key !== 'trailing_stops')
                .map(([key, value]) => (
                  <Form.Item
                    key={key}
                    name={key}
                    label={RISK_LABELS[key] ?? key}
                    valuePropName={
                      typeof value === 'boolean' ? 'checked' : 'value'
                    }
                    style={{ marginBottom: 12 }}
                  >
                    {typeof value === 'boolean' ? (
                      <Switch />
                    ) : (
                      <InputNumber style={{ width: 110 }} />
                    )}
                  </Form.Item>
                ))}
              <Form.Item>
                <Space>
                  <Button type="primary" htmlType="submit">
                    保存
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </>
        )}
        <Descriptions
          size="small"
          column={4}
          style={{ marginTop: 12 }}
          items={Object.entries(riskStatus).map(([key, value]) => ({
            key,
            label: STATUS_LABELS[key] ?? key,
            children:
              key === 'breaker_tripped'
                ? value
                  ? '已触发（禁止开仓）'
                  : '正常'
                : typeof value === 'object' && value !== null
                  ? JSON.stringify(value)
                  : String(value),
          }))}
        />
        {riskSettings && riskSettings.enable_trailing_stop && (
          <TrailingStopManager />
        )}
      </Card>

      <NotifySettingsCard />

      <MarketSyncCard />

      <DataHealthCard />

      <BackupCard />

      <Card size="small" title="其他设置">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Switch
            checked={useSoundStore(s => s.enabled)}
            onChange={v => useSoundStore.getState().setEnabled(v)}
          />
          <span>消息提示音</span>
        </div>
      </Card>

      <Card size="small" title="账户">
        <Space size={8}>
          <Button
            onClick={() => {
              setChangePwdOpen(true)
              setPwd(['', '', ''])
            }}
          >
            修改密码
          </Button>
          <Popconfirm
            title="确认退出登录？"
            onConfirm={() => {
              useAuthStore.getState().logout()
              window.location.href = '/login'
            }}
          >
            <Button danger>退出登录</Button>
          </Popconfirm>
        </Space>
      </Card>

      <Modal
        title="修改登录密码"
        open={changePwdOpen}
        onCancel={() => setChangePwdOpen(false)}
        onOk={async () => {
          if (pwd[1] !== pwd[2]) {
            messageApi.error('两次输入的新密码不一致')
            return
          }
          try {
            await useAuthStore.getState().changePassword(pwd[1], pwd[2])
            messageApi.success('密码修改成功')
            setChangePwdOpen(false)
          } catch (e) {
            const detail =
              (e as { response?: { data?: { detail?: string } } }).response?.data
                ?.detail ?? '修改失败'
            messageApi.error(typeof detail === 'string' ? detail : '修改失败')
          }
        }}
        okText="保存"
        cancelText="取消"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 16 }}>
          <Input.Password
            placeholder="当前密码"
            value={pwd[0]}
            onChange={e => setPwd([e.target.value, pwd[1], pwd[2]])}
          />
          <Input.Password
            placeholder="新密码（至少 4 位）"
            value={pwd[1]}
            onChange={e => setPwd([pwd[0], e.target.value, pwd[2]])}
          />
          <Input.Password
            placeholder="确认新密码"
            value={pwd[2]}
            onChange={e => setPwd([pwd[0], pwd[1], e.target.value])}
          />
        </div>
      </Modal>
      </div>
    </GlassPage>
  )
}