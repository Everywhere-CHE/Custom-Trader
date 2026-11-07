/** CTP 账户/服务器设置弹窗（SimNow 或实盘期货公司柜台）。 */

import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  getCtpConfig,
  getCtpPresets,
  updateCtpConfig,
  type CtpConfig,
  type CtpServerPreset,
} from '../api/http'

const { Text } = Typography

interface Props {
  open: boolean
  onClose: () => void
  onSaved: () => void
}

export default function CtpSettingsModal({ open, onClose, onSaved }: Props) {
  const [presets, setPresets] = useState<CtpServerPreset[]>([])
  const [config, setConfig] = useState<CtpConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()
  const [messageApi, contextHolder] = message.useMessage()

  useEffect(() => {
    getCtpPresets().then(setPresets).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!open) return
    getCtpConfig()
      .then(cfg => {
        setConfig(cfg)
        form.setFieldsValue({
          userid: cfg.userid,
          password: '',
          brokerid: cfg.brokerid,
          td_address: cfg.td_address,
          md_address: cfg.md_address,
          appid: cfg.appid,
          auth_code: cfg.auth_code,
        })
      })
      .catch(() => undefined)
  }, [open, form])

  const applyPreset = (presetId: string) => {
    const preset = presets.find(p => p.id === presetId)
    if (!preset) return
    form.setFieldsValue({
      brokerid: preset.brokerid,
      td_address: preset.td_address,
      md_address: preset.md_address,
      appid: preset.appid,
      auth_code: preset.auth_code,
    })
    if (preset.note) messageApi.info(preset.note)
  }

  const save = async () => {
    const values = form.getFieldsValue() as Record<string, string>
    if (!values.userid || !values.brokerid) {
      messageApi.warning('请填写投资者代码与经纪商代码')
      return
    }
    setSaving(true)
    try {
      await updateCtpConfig(values)
      messageApi.success('CTP 连接配置已保存')
      onSaved()
      onClose()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`保存失败: ${detail}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="CTP 连接设置（SimNow / 实盘期货公司）"
      open={open}
      onCancel={onClose}
      footer={[
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button key="save" type="primary" loading={saving} onClick={save}>
          保存
        </Button>,
      ]}
      width={600}
    >
      {contextHolder}
      <Form form={form} layout="vertical" size="middle">
        <Form.Item label="服务器快捷选择">
          <Select
            placeholder="选择后自动填充服务器/经纪商信息，账号密码需自填"
            options={presets.map(p => ({ label: p.label, value: p.id }))}
            onChange={value => applyPreset(value as string)}
          />
        </Form.Item>

        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item
            name="userid"
            label="投资者代码"
            style={{ flex: 1 }}
            rules={[{ required: true }]}
          >
            <Input placeholder="期货公司/SimNow 资金账号" />
          </Form.Item>
          <Form.Item
            name="password"
            label={
              <span>
                密码{' '}
                {config?.has_password && (
                  <Tag color="success" style={{ marginLeft: 4 }}>
                    已保存
                  </Tag>
                )}
              </span>
            }
            style={{ flex: 1 }}
            tooltip="留空表示沿用已保存的密码"
          >
            <Input.Password
              placeholder={config?.has_password ? '留空保持不变' : '输入交易密码'}
            />
          </Form.Item>
          <Form.Item
            name="brokerid"
            label="经纪商代码"
            style={{ width: 120 }}
            rules={[{ required: true }]}
          >
            <Input placeholder="9999" />
          </Form.Item>
        </div>

        <Form.Item name="td_address" label="交易前置地址">
          <Input placeholder="tcp://180.168.146.187:10201" />
        </Form.Item>
        <Form.Item name="md_address" label="行情前置地址">
          <Input placeholder="tcp://180.168.146.187:10211" />
        </Form.Item>

        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="appid" label="AppID" style={{ flex: 1 }}>
            <Input placeholder="实盘由期货公司提供；SimNow 为 simnow_client_test" />
          </Form.Item>
          <Form.Item name="auth_code" label="授权码 AuthCode" style={{ flex: 1 }}>
            <Input placeholder="实盘由期货公司提供" />
          </Form.Item>
        </div>
      </Form>

      <Alert
        type="warning"
        showIcon
        message={
          <Text style={{ fontSize: 12 }}>
            配置保存在 data/ctp_config.json（已加入
            .gitignore），保存后点「连接」即生效；已连接时需先「断开」再重连以切换账号。
            接入实盘前请务必在 SimNow / 仿真环境充分验证策略与风控。
          </Text>
        }
      />
    </Modal>
  )
}
