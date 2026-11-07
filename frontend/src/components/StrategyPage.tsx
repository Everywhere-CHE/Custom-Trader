/** 策略管理页面：策略表格 + 生命周期操作 + 创建弹窗 +
 * 实时策略日志。 */

import { useEffect, useState } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Popover,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createStrategy,
  deleteStrategy,
  editStrategy,
  getStrategies,
  getStrategyClasses,
  initStrategy,
  reconcilePositions,
  setAutoStart,
  startStrategy,
  stopStrategy,
  syncStrategyPos,
  type ReconcileRow,
} from '../api/http'
import { useMarketStore } from '../stores/market'
import { useStrategyStore } from '../stores/strategy'
import type { StrategyClassInfo, StrategyData } from '../types'
import StrategyDocs from './StrategyDocs'
import StrategyEditor from './StrategyEditor'
import StrategyWizard from './StrategyWizard'
import GlassPage from './ui/GlassPage'
import Pill from './ui/Pill'

const { Text } = Typography

/** 参数单元格：固定单行高度 + 截断 + "更多"弹出查看。 */
function ParamCell({ r, classes }: { r: StrategyData; classes: StrategyClassInfo[] }) {
  const ci = classes.find(c => c.class_name === r.class_name)
  const pairs = Object.entries(r.parameters).map(([k, v]) => ({
    label: ci?.param_descriptions?.[k] ?? k,
    value: String(v),
  }))
  const fullText = pairs.map(p => `${p.label}=${p.value}`).join(' ')
  const maxLen = 40
  const truncated =
    fullText.length > maxLen ? fullText.slice(0, maxLen) + '…' : fullText
  const detail = (
    <div style={{ maxWidth: 360 }}>
      {pairs.map(p => (
        <div key={p.label} style={{ fontSize: 12, lineHeight: '20px' }}>
          <Text type="secondary">{p.label}</Text> ={' '}
          <Text>{p.value}</Text>
        </div>
      ))}
    </div>
  )
  return (
    <div
      style={{
        fontSize: 12,
        height: 22,
        lineHeight: '22px',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}
    >
      <Text type="secondary">{truncated}</Text>
      {fullText.length > maxLen && (
        <Popover content={detail} title="参数详情" trigger="click" placement="bottomLeft">
          <a style={{ marginLeft: 4 }}>更多</a>
        </Popover>
      )}
    </div>
  )
}

export default function StrategyPage() {
  const strategies = useStrategyStore(s => s.strategies)
  const setAll = useStrategyStore(s => s.setAll)
  const removeLocal = useStrategyStore(s => s.remove)
  const logs = useStrategyStore(s => s.logs)
  const contracts = useMarketStore(s => s.contracts)

  const [classes, setClasses] = useState<StrategyClassInfo[]>([])
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<StrategyData | null>(null)
  const [reconcileOpen, setReconcileOpen] = useState(false)
  const [reconcileRows, setReconcileRows] = useState<ReconcileRow[]>([])
  const [reconcileLoading, setReconcileLoading] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const selectedClass = Form.useWatch('class_name', createForm) as
    | string
    | undefined

  const refresh = async () => {
    try {
      setAll(await getStrategies())
    } catch {
      // 后端尚未就绪
    }
  }

  const refreshClasses = () =>
    getStrategyClasses().then(setClasses).catch(() => undefined)

  useEffect(() => {
    void refresh()
    void refreshClasses()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const doAction = async (
    action: (name: string) => Promise<unknown>,
    name: string,
    label: string,
  ) => {
    try {
      await action(name)
      messageApi.success(`${label}已提交`)
      setTimeout(() => void refresh(), 500)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`${label}失败: ${detail}`)
    }
  }

  const classInfo = classes.find(c => c.class_name === selectedClass)

  const doReconcile = async () => {
    setReconcileLoading(true)
    try {
      const rows = await reconcilePositions()
      setReconcileRows(rows)
      setReconcileOpen(true)
      const mismatched = rows.filter(r => !r.matched).length
      if (mismatched === 0) {
        messageApi.success('对账完成：全部一致')
      } else {
        messageApi.warning(`对账完成：${mismatched} 个策略持仓不一致`)
      }
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`对账失败: ${detail}`)
    } finally {
      setReconcileLoading(false)
    }
  }

  const doSyncPos = async (name: string) => {
    try {
      const result = await syncStrategyPos(name)
      messageApi.success(
        `已同步 ${name}: pos ${result.old_pos} → ${result.new_pos}`,
      )
      setReconcileRows(await reconcilePositions())
      await refresh()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      // 被多个策略共用时，弹窗让用户手动输入
      Modal.confirm({
        title: '持仓同步失败',
        content: (
          <div>
            <p style={{ marginBottom: 8 }}>{detail}</p>
            <p style={{ fontSize: 12, color: '#888' }}>请输入此策略应持有的手数（正数=多，负数=空，0=清仓）</p>
            <InputNumber
              id="manual-pos-input"
              style={{ width: '100%' }}
              min={-9999}
              max={9999}
              step={1}
              defaultValue={0}
            />
          </div>
        ),
        onOk: async () => {
          const input = document.getElementById('manual-pos-input') as HTMLInputElement
          const manualPos = Number(input?.value ?? 0)
          if (isNaN(manualPos)) {
            messageApi.error('请输入有效数字')
            return
          }
          try {
            const result = await syncStrategyPos(name, manualPos)
            messageApi.success(
              `已手动同步 ${name}: pos → ${result.new_pos}`,
            )
            setReconcileRows(await reconcilePositions())
            await refresh()
          } catch (e2) {
            messageApi.error(`同步失败: ${(e2 as Error).message}`)
          }
        },
      })
    }
  }

  const reconcileColumns: ColumnsType<ReconcileRow> = [
    { title: '策略', dataIndex: 'strategy_name', width: 130 },
    { title: '合约', dataIndex: 'vt_symbol', width: 120 },
    {
      title: '策略持仓',
      dataIndex: 'strategy_pos',
      width: 90,
      render: (v: number) => <span className="mono">{v}</span>,
    },
    {
      title: '网关净持仓',
      dataIndex: 'gateway_net_pos',
      width: 100,
      render: (v: number, r) => (
        <span className="mono">
          {r.gateway_has_position ? v : '—'}
        </span>
      ),
    },
    {
      title: '差异',
      dataIndex: 'diff',
      width: 80,
      render: (v: number, r) =>
        r.matched ? (
          <Pill tone="emerald">一致</Pill>
        ) : (
          <span className="mono price-down">{v > 0 ? `+${v}` : v}</span>
        ),
    },
    {
      title: '说明',
      render: (_, r) => {
        if (r.matched) return null
        if (r.shared) {
          return (
            <Text type="warning" style={{ fontSize: 12 }}>
              多策略共用该合约（按合计对比），需手动处理
            </Text>
          )
        }
        if (!r.gateway_has_position && r.strategy_pos !== 0) {
          return (
            <Text type="secondary" style={{ fontSize: 12 }}>
              网关无该合约持仓（可能已被手动平仓，或 CTP 未连接/未查到持仓）
            </Text>
          )
        }
        return (
          <Text type="secondary" style={{ fontSize: 12 }}>
            持仓可能在策略停止期间被手动改变
          </Text>
        )
      },
    },
    {
      title: '操作',
      width: 130,
      render: (_, r) =>
        r.matched ? null : (
          <Popconfirm
            title={`将 ${r.strategy_name} 的持仓改为网关净持仓 ${r.gateway_net_pos}？`}
            disabled={r.trading || r.shared}
            onConfirm={() => void doSyncPos(r.strategy_name)}
          >
            <Button size="small" danger disabled={r.trading || r.shared}>
              {r.trading ? '需先停止' : r.shared ? '不可同步' : '同步为网关值'}
            </Button>
          </Popconfirm>
        ),
    },
  ]

  const columns: ColumnsType<StrategyData> = [
    {
      title: '名称',
      dataIndex: 'strategy_name',
      width: 150,
      render: (v: string) => <span style={{ whiteSpace: 'nowrap' }}>{v}</span>,
    },
    {
      title: '策略类',
      dataIndex: 'class_name',
      width: 150,
      render: (className: string) => (
        <span style={{ whiteSpace: 'nowrap' }}>
          {classes.find(c => c.class_name === className)?.display_name || className}
        </span>
      ),
    },
    { title: '合约', dataIndex: 'vt_symbol', width: 130 },
    {
      title: '状态',
      width: 140,
      render: (_, r) => (
        <Space size={4}>
          <Pill tone={r.variables.inited ? 'indigo' : 'slate'}>
            {r.variables.inited ? '已初始化' : '未初始化'}
          </Pill>
          <Pill tone={r.variables.trading ? 'emerald' : 'slate'}>
            {r.variables.trading ? '运行中' : '已停止'}
          </Pill>
        </Space>
      ),
    },
    {
      title: '持仓',
      width: 70,
      render: (_, r) => {
        const pos = (r.variables.pos as number) ?? 0
        return (
          <span
            className={`mono ${pos > 0 ? 'price-up' : pos < 0 ? 'price-down' : ''}`}
          >
            {pos}
          </span>
        )
      },
    },
    {
      title: '参数',
      width: 300,
      render: (_, r) => <ParamCell r={r} classes={classes} />,
    },
    {
      title: '自动启动',
      width: 90,
      render: (_, r) => (
        <Switch
          size="small"
          checked={Boolean((r as StrategyData).auto_start)}
          onChange={async checked => {
            try {
              await setAutoStart(r.strategy_name, checked)
              // 更新本地状态
              const updater = useStrategyStore.getState().upsert
              updater({ ...r, auto_start: checked })
              messageApi.success(checked ? '已开启自动启动' : '已关闭自动启动')
            } catch {
              messageApi.error('设置失败')
            }
          }}
        />
      ),
    },
    {
      title: '操作',
      width: 300,
      render: (_, r) => (
        <Space size={4}>
          <Button
            size="small"
            disabled={Boolean(r.variables.trading)}
            onClick={() => void doAction(initStrategy, r.strategy_name, '初始化')}
          >
            初始化
          </Button>
          <Button
            size="small"
            type="primary"
            disabled={!r.variables.inited || Boolean(r.variables.trading)}
            onClick={() => void doAction(startStrategy, r.strategy_name, '启动')}
          >
            启动
          </Button>
          <Button
            size="small"
            disabled={!r.variables.trading}
            onClick={() => void doAction(stopStrategy, r.strategy_name, '停止')}
          >
            停止
          </Button>
          <Button
            size="small"
            onClick={() => {
              setEditTarget(r)
              editForm.setFieldsValue(r.parameters)
            }}
          >
            参数
          </Button>
          <Popconfirm
            title="确认删除该策略实例？"
            disabled={Boolean(r.variables.trading)}
            onConfirm={async () => {
              try {
                await deleteStrategy(r.strategy_name)
                removeLocal(r.strategy_name)
                messageApi.success('已删除')
              } catch (error) {
                const detail =
                  (error as { response?: { data?: { detail?: string } } })
                    .response?.data?.detail ?? String(error)
                messageApi.error(`删除失败: ${detail}`)
              }
            }}
          >
            <Button size="small" danger disabled={Boolean(r.variables.trading)}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <GlassPage>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {contextHolder}
        <Space>
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            创建策略
          </Button>
        <StrategyWizard
          onSaved={() => {
            void refreshClasses()
            void refresh()
          }}
        />
        <StrategyEditor
          onSaved={() => {
            void refreshClasses()
            void refresh()
          }}
        />
        <Button onClick={() => void refresh()}>刷新</Button>
        <Button loading={reconcileLoading} onClick={() => void doReconcile()}>
          持仓对账
        </Button>
      </Space>

      <Table
        size="small"
        rowKey="strategy_name"
        columns={columns}
        dataSource={Object.values(strategies)}
        pagination={false}
        tableLayout="fixed"
        scroll={{ x: 1150 }}
      />

      <div
        style={{
          background: 'var(--panel-bg)',
          border: '1px solid var(--border-color)',
          borderRadius: 4,
          padding: 8,
          height: 200,
          overflow: 'auto',
        }}
      >
        <Text strong style={{ fontSize: 13 }}>
          策略日志
        </Text>
        {logs.map((log, index) => (
          <div key={index} className="log-line">
            <Text type="secondary">{log.time?.slice(11, 19)}</Text>{' '}
            <Text>[{log.strategy_name}]</Text> {log.msg}
          </div>
        ))}
      </div>

      {/* ----- 策略文档 ----- */}
      <StrategyDocs classes={classes} />

      {/* ----- 创建弹窗 ----- */}
      <Modal
        title="创建策略"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={async values => {
            const { class_name, name, vt_symbol, vt_symbols, symbol_mode, ...setting } = values as {
              class_name: string
              name: string
              vt_symbol: string
              vt_symbols: string[]
              [key: string]: unknown
            }
            const symbols = vt_symbols?.length ? vt_symbols : (vt_symbol ? [vt_symbol] : [])
            if (!symbols.length) {
              messageApi.error('请选择至少一个合约')
              return
            }
            try {
              await createStrategy({ class_name, name, vt_symbols: symbols, setting })
              messageApi.success(`策略已创建（${symbols.length} 个合约）`)
              setCreateOpen(false)
              createForm.resetFields()
              void refresh()
            } catch (error) {
              const detail =
                (error as { response?: { data?: { detail?: string } } })
                  .response?.data?.detail ?? String(error)
              messageApi.error(`创建失败: ${detail}`)
            }
          }}
        >
          <Form.Item
            name="class_name"
            label="策略类"
            rules={[{ required: true }]}
          >
            <Select
              options={classes.map(c => ({
                label: `${c.display_name || c.class_name} (${c.author})`,
                value: c.class_name,
              }))}
            />
          </Form.Item>
          {classInfo?.description && (
            <Text
              type="secondary"
              style={{ fontSize: 12, display: 'block', marginBottom: 12 }}
            >
              {classInfo.description}
            </Text>
          )}
          <Form.Item name="name" label="实例名称" rules={[{ required: true }]}>
            <Input placeholder="如 ma_if2509" />
          </Form.Item>
          <Form.Item name="symbol_mode" label="选择方式">
            <Radio.Group>
              <Radio value="single">单合约</Radio>
              <Radio value="multi">多合约</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, curr) => prev.symbol_mode !== curr.symbol_mode}
          >
            {({ getFieldValue }) =>
              getFieldValue('symbol_mode') === 'multi' ? (
                <Form.Item
                  name="vt_symbols"
                  label="交易合约（多选）"
                  rules={[{ required: true, message: '请选择至少一个合约' }]}
                >
                  <Select
                    mode="multiple"
                    showSearch
                    placeholder="搜索并选择合约..."
                    options={contracts.map(c => ({
                      label: `${c.vt_symbol} ${c.name}`,
                      value: c.vt_symbol,
                    }))}
                    filterOption={(input, option) =>
                      (option?.label as string)
                        ?.toLowerCase()
                        .includes(input.toLowerCase()) ?? false
                    }
                  />
                </Form.Item>
              ) : (
                <Form.Item
                  name="vt_symbol"
                  label="交易合约"
                  rules={[{ required: true }]}
                >
                  <Select
                    showSearch
                    placeholder="搜索并选择合约..."
                    options={contracts.map(c => ({
                      label: `${c.vt_symbol} ${c.name}`,
                      value: c.vt_symbol,
                    }))}
                    filterOption={(input, option) =>
                      (option?.label as string)
                        ?.toLowerCase()
                        .includes(input.toLowerCase()) ?? false
                    }
                  />
                </Form.Item>
              )
            }
          </Form.Item>
          {classInfo &&
            Object.entries(classInfo.parameters).map(([key, defaultValue]) => (
              <Form.Item
                key={key}
                name={key}
                label={classInfo.param_descriptions?.[key] ?? key}
                tooltip={classInfo.param_descriptions?.[key]}
                initialValue={defaultValue}
              >
                {typeof defaultValue === 'number' ? (
                  <InputNumber style={{ width: '100%' }} />
                ) : (
                  <Input />
                )}
              </Form.Item>
            ))}
        </Form>
      </Modal>

      {/* ----- 编辑参数弹窗 ----- */}
      <Modal
        title={`编辑参数: ${editTarget?.strategy_name ?? ''}`}
        open={editTarget !== null}
        onCancel={() => setEditTarget(null)}
        onOk={() => editForm.submit()}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={async values => {
            if (!editTarget) return
            try {
              await editStrategy(
                editTarget.strategy_name,
                values as Record<string, unknown>,
              )
              messageApi.success('参数已更新')
              setEditTarget(null)
              void refresh()
            } catch {
              messageApi.error('更新失败')
            }
          }}
        >
          {editTarget &&
            Object.entries(editTarget.parameters).map(([key, value]) => {
              const ci = classes.find(c => c.class_name === editTarget.class_name)
              return (
                <Form.Item
                  key={key}
                  name={key}
                  label={ci?.param_descriptions?.[key] ?? key}
                  initialValue={value}
                >
                  {typeof value === 'number' ? (
                    <InputNumber style={{ width: '100%' }} />
                  ) : (
                    <Input />
                  )}
                </Form.Item>
              )
            })}
        </Form>
      </Modal>

      {/* 持仓对账弹窗 */}
      <Modal
        title="持仓对账（策略 pos vs 网关净持仓）"
        open={reconcileOpen}
        onCancel={() => setReconcileOpen(false)}
        footer={
          <Space>
            <Button loading={reconcileLoading} onClick={() => void doReconcile()}>
              重新对账
            </Button>
            <Button type="primary" onClick={() => setReconcileOpen(false)}>
              关闭
            </Button>
          </Space>
        }
        width={860}
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          网关净持仓 = 多头持仓 − 空头持仓（来自 CTP/网关实时持仓查询）。
          差异通常来自策略停止期间的手动交易。同步前请先停止策略；
          期货合约需 CTP 已连接且持仓查询已返回，否则网关侧显示为 —。
        </Text>
        <Table
          size="small"
          rowKey="strategy_name"
          columns={reconcileColumns}
          dataSource={reconcileRows}
          pagination={false}
          style={{ marginTop: 12 }}
        />
      </Modal>
    </div>
    </GlassPage>
  )
}
