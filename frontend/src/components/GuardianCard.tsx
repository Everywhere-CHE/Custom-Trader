/** 策略守护卡片：建档（以主回测表单当前配置为基线）、复验、退役、删除。
 *
 * 三态：healthy（健康）⇄ watch（观察）→ retired（退役，建议停用）。
 */

import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { FormInstance } from 'antd'
import {
  adoptGuardian,
  dropGuardian,
  getBacktestTask,
  getGuardians,
  recheckAllGuardians,
  recheckGuardian,
} from '../api/http'
import type { GuardianRecord } from '../types'

const { Text } = Typography

const STATUS_META: Record<string, { color: string; label: string }> = {
  healthy: { color: 'green', label: '健康' },
  watch: { color: 'orange', label: '观察' },
  retired: { color: 'red', label: '退役' },
}

interface Row extends GuardianRecord {
  key: string
}

export default function GuardianCard({ form }: { form: FormInstance }) {
  const [messageApi, contextHolder] = message.useMessage()
  const [records, setRecords] = useState<GuardianRecord[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')

  const load = async () => {
    try {
      const r = await getGuardians()
      setRecords(r.guardians)
    } catch {
      // 静默失败（首次为空档案）
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const adopt = async () => {
    // 以主回测表单当前配置为基线建档
    const values = form.getFieldsValue() as {
      class_names?: string[]
      vt_symbols?: string[]
      interval?: string
      range?: [{ format: (f: string) => string }, { format: (f: string) => string }]
      capital?: number
      rate?: number
      slippage?: number
      size?: number
      pricetick?: number
      [key: string]: unknown
    }
    const class_name = values.class_names?.[0]
    const vt_symbol = values.vt_symbols?.[0]
    if (!class_name || !vt_symbol || !values.range?.[0]) {
      messageApi.warning('请先在上方回测表单选择策略与合约（各取第一个）并设置区间')
      return
    }
    const { class_names: _c, vt_symbols: _v, interval: _i, range: _r, ...setting } = values
    setBusy(true)
    try {
      await adoptGuardian({
        class_name,
        vt_symbol,
        interval: values.interval || 'd',
        setting,
        baseline_start: values.range[0].format('YYYY-MM-DDTHH:mm:ss'),
        baseline_end: values.range[1].format('YYYY-MM-DDTHH:mm:ss'),
        capital: values.capital,
        rate: values.rate,
        slippage: values.slippage,
        size: values.size,
        pricetick: values.pricetick,
      })
      messageApi.success(`已建档：${class_name} @ ${vt_symbol}（以上方区间回测为基线）`)
      await load()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`建档失败: ${detail}`)
    } finally {
      setBusy(false)
    }
  }

  const pollTask = async (taskId: string) => {
    let last = await getBacktestTask(taskId)
    while (last.status === 'pending' || last.status === 'running') {
      setProgress(last.message)
      await new Promise(r => setTimeout(r, 1200))
      last = await getBacktestTask(taskId)
    }
    setProgress('')
    if (last.status === 'failed') {
      messageApi.error(`复验失败: ${last.error || '未知错误'}`)
    }
    await load()
  }

  const recheckOne = async (gid: string) => {
    setBusy(true)
    try {
      const task = await recheckGuardian(gid)
      await pollTask(task.task_id)
      messageApi.success('复验完成')
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`复验失败: ${detail}`)
    } finally {
      setBusy(false)
      setProgress('')
    }
  }

  const recheckAll = async () => {
    setBusy(true)
    try {
      const task = await recheckAllGuardians()
      await pollTask(task.task_id)
      messageApi.success('全量复验完成')
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`复验失败: ${detail}`)
    } finally {
      setBusy(false)
      setProgress('')
    }
  }

  const drop = async (gid: string) => {
    try {
      await dropGuardian(gid)
      messageApi.success('已删除档案')
      await load()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`删除失败: ${detail}`)
    }
  }

  const columns: ColumnsType<Row> = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 76,
      filters: [
        { text: '健康', value: 'healthy' },
        { text: '观察', value: 'watch' },
        { text: '退役', value: 'retired' },
      ],
      onFilter: (v, r) => r.status === v,
      render: (s: string) => {
        const meta = STATUS_META[s] ?? { color: 'default', label: s }
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    { title: '策略', dataIndex: 'class_name', width: 150 },
    { title: '合约', dataIndex: 'vt_symbol', width: 130, className: 'mono' },
    {
      title: '参数',
      dataIndex: 'setting',
      width: 190,
      className: 'mono',
      ellipsis: true,
      render: (s: Record<string, unknown>) =>
        Object.entries(s || {})
          .map(([k, v]) => `${k}=${v}`)
          .join(', '),
    },
    {
      title: '基线',
      key: 'baseline',
      width: 120,
      render: (_: unknown, r: GuardianRecord) => {
        const b = r.baseline
        if (!b) return '-'
        return (
          <Tooltip title={`基线区间 ${b.start?.slice(0, 10)} → ${b.end?.slice(0, 10)}`}>
            <span className="mono">
              {b.score?.toFixed(3)} <Text type="secondary" style={{ fontSize: 11 }}>{r.metric}</Text>
            </span>
          </Tooltip>
        )
      },
    },
    {
      title: '最近复验',
      key: 'last_check',
      width: 260,
      render: (_: unknown, r: GuardianRecord) => {
        const last = r.checks?.[r.checks.length - 1]
        if (!last) return <Text type="secondary">未复验</Text>
        const pct = last.pct_degradation
        return (
          <Tooltip
            title={
              <div>
                <div>复验时间: {last.checked_at?.slice(0, 19)}</div>
                <div>新数据: {last.new_bars} 根 / {last.k_segments} 段</div>
                <div>稳健分: {last.robust_score?.toFixed(3) ?? '-'}</div>
                {last.errors && <div>错误: {last.errors.join('; ')}</div>}
                <div>原因: {(last.reasons ?? []).join('；')}</div>
              </div>
            }
          >
            <span>
              {last.checked_at?.slice(5, 10)}{' '}
              {last.robust_score != null && (
                <span className="mono" style={{ color: last.robust_score > 0 ? 'var(--up-color)' : 'var(--down-color)' }}>
                  {last.robust_score.toFixed(3)}
                </span>
              )}
              {pct != null && (
                <Text type="secondary" style={{ fontSize: 11 }}> 退化 {(pct * 100).toFixed(0)}%</Text>
              )}
              {last.error && <Tag color="red">异常</Tag>}
              {r.consecutive_watch >= 2 && <Tag color="orange">连续{r.consecutive_watch}次</Tag>}
            </span>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_: unknown, r: GuardianRecord) => (
        <Space size={4}>
          <Button
            size="small"
            disabled={r.status === 'retired'}
            loading={busy}
            onClick={() => void recheckOne(r.guardian_id)}
          >
            复验
          </Button>
          <Popconfirm title="删除守护档案？" onConfirm={() => void drop(r.guardian_id)}>
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card
      size="small"
      title="策略守护"
      extra={
        <Space>
          <Button size="small" loading={busy} onClick={adopt}>
            建档（用上方表单配置）
          </Button>
          <Button size="small" loading={busy} onClick={recheckAll} disabled={!records.length}>
            全量复验
          </Button>
        </Space>
      }
    >
      {contextHolder}
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        建档后定期「复验」：只用基线之后的新数据回测，相对退化 &gt;20% 转观察、&gt;50% 退役，
        交易数枯竭直接退役。数据残缺最多降观察，不会误杀。
        {progress && <b> {progress}</b>}
      </Text>
      <Table<Row>
        size="small"
        rowKey={r => r.key}
        columns={columns}
        dataSource={records.map(r => ({ ...r, key: r.guardian_id }))}
        pagination={false}
        locale={{ emptyText: '暂无守护档案——在上方回测表单选好策略/合约/区间后点「建档」' }}
        className="compact-table"
      />
    </Card>
  )
}
