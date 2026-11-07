/** 配置备份卡片：勾选类别导出 ZIP / 上传恢复。密钥不在备份内。 */

import { useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Space,
  Typography,
  Upload,
  message,
} from 'antd'
import { DownloadOutlined, UploadOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import { exportBackup, importBackup } from '../api/http'
import type { BackupItems } from '../api/http'

const { Text } = Typography

const CATEGORIES: { key: keyof BackupItems; label: string }[] = [
  { key: 'strategies', label: '策略实例' },
  { key: 'watchlist', label: '自选列表' },
  { key: 'gateway_configs', label: '网关配置' },
  { key: 'notify_config', label: '通知配置' },
  { key: 'risk_settings', label: '风控参数' },
]

export default function BackupCard() {
  const [messageApi, contextHolder] = message.useMessage()
  const [items, setItems] = useState<BackupItems>(() => {
    const initial = {} as BackupItems
    for (const c of CATEGORIES) initial[c.key] = true
    return initial
  })
  const [exporting, setExporting] = useState(false)
  const [allowOverwrite, setAllowOverwrite] = useState(false)

  const doExport = async () => {
    setExporting(true)
    try {
      const filename = await exportBackup(items)
      messageApi.success(`已导出 ${filename}`)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`导出失败: ${detail}`)
    } finally {
      setExporting(false)
    }
  }

  const doImport = async (file: UploadFile) => {
    if (!file.originFileObj) return false
    try {
      const summary = await importBackup(file.originFileObj, allowOverwrite)
      const parts: string[] = []
      for (const [category, info] of Object.entries(summary.restored || {})) {
        const label = CATEGORIES.find(c => c.key === category)?.label ?? category
        if (category === 'strategies' && typeof info === 'object' && info) {
          const s = info as { created: number; updated: number; skipped: number }
          parts.push(`策略: 新建${s.created}/覆盖${s.updated}/跳过${s.skipped}`)
        } else {
          const files = (info as { files?: string[] })?.files ?? []
          parts.push(`${label}: ${files.length} 项`)
        }
      }
      messageApi.success(`恢复完成：${parts.join('；') || '无内容'}`)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`恢复失败: ${detail}`)
    }
    return false // 阻止 antd 自动上传
  }

  return (
    <Card size="small" title="配置备份">
      {contextHolder}
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <span style={{ fontSize: 13 }}>备份类别：</span>
          {CATEGORIES.map(({ key, label }) => (
            <Checkbox
              key={key}
              checked={Boolean(items[key])}
              onChange={e => setItems(prev => ({ ...prev, [key]: e.target.checked }))}
            >
              {label}
            </Checkbox>
          ))}
        </div>
        <Space wrap>
          <Button icon={<DownloadOutlined />} loading={exporting} onClick={doExport}>
            导出备份 ZIP
          </Button>
          <Checkbox
            checked={allowOverwrite}
            onChange={e => setAllowOverwrite(e.target.checked)}
          >
            恢复时覆盖同名策略
          </Checkbox>
          <Upload accept=".zip" showUploadList={false} beforeUpload={doImport} maxCount={1}>
            <Button icon={<UploadOutlined />}>上传备份恢复</Button>
          </Upload>
        </Space>
        <Text type="secondary" style={{ fontSize: 12 }}>
          密钥一律不导出（CTP 密码 / fuyao API Key / 通知授权码 / 登录凭据）——恢复后请到对应设置页重新填写或沿用现值。
          策略恢复后一律处于停止状态，确认参数无误后手动启动。恢复仅合并备份内的类别，不会删除现有数据。
        </Text>
      </Space>
    </Card>
  )
}
