/** 模型设置弹窗：主流模型预设 + 自定义服务商配置。 */

import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  getAiPresets,
  updateAiConfig,
  type AiPreset,
  type AiStatus,
} from '../api/http'

const { Text } = Typography

interface Props {
  open: boolean
  status: AiStatus | null
  onClose: () => void
  onSaved: (status: AiStatus) => void
}

export default function ModelSettingsModal({
  open,
  status,
  onClose,
  onSaved,
}: Props) {
  const [presets, setPresets] = useState<AiPreset[]>([])
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()
  const [messageApi, contextHolder] = message.useMessage()

  useEffect(() => {
    getAiPresets().then(setPresets).catch(() => undefined)
  }, [])

  // 每次打开弹窗时用当前状态同步表单
  useEffect(() => {
    if (open && status) {
      form.setFieldsValue({
        provider: status.provider,
        model: status.model,
        base_url: status.base_url,
        api_key: '',
        allow_trading: status.allow_trading,
        auto_optimize: status.auto_optimize,
      })
    }
  }, [open, status, form])

  const presetOptions = useMemo(() => {
    const groups = new Map<string, AiPreset[]>()
    for (const preset of presets) {
      const arr = groups.get(preset.group) ?? []
      arr.push(preset)
      groups.set(preset.group, arr)
    }
    return [...groups.entries()].map(([group, items]) => ({
      label: group,
      options: items.map(p => ({
        label: `${p.label}（${p.model}）`,
        value: p.id,
      })),
    }))
  }, [presets])

  const applyPreset = (presetId: string) => {
    const preset = presets.find(p => p.id === presetId)
    if (!preset) return
    form.setFieldsValue({
      provider: preset.provider,
      model: preset.model,
      base_url: preset.base_url,
    })
  }

  const save = async () => {
    const values = form.getFieldsValue() as {
      provider: string
      model: string
      base_url: string
      api_key: string
      allow_trading: boolean
      auto_optimize: boolean
    }
    if (!values.model) {
      messageApi.warning('请填写模型名')
      return
    }
    setSaving(true)
    try {
      const newStatus = await updateAiConfig({
        provider: values.provider,
        model: values.model,
        base_url: values.base_url ?? '',
        api_key: values.api_key || undefined,
        allow_trading: values.allow_trading,
        auto_optimize: values.auto_optimize,
      })
      messageApi.success('模型配置已保存')
      onSaved(newStatus)
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
      title="模型设置"
      open={open}
      onCancel={onClose}
      footer={[
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button key="save" type="primary" loading={saving} onClick={save}>
          保存并生效
        </Button>,
      ]}
      width={560}
    >
      {contextHolder}
      <Form form={form} layout="vertical" size="middle">
        <Form.Item label="快捷选择（主流模型预设）">
          <Select
            placeholder="选择后自动填充下方配置，可再手动修改"
            options={presetOptions}
            onChange={value => applyPreset(value as string)}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>

        <Form.Item
          name="provider"
          label="接口协议"
          tooltip="anthropic = Anthropic Messages (/v1/messages)；chat = Chat Completions (/chat/completions，DeepSeek/Kimi/通义/GLM/豆包/OpenRouter 及绝大多数中转站)；responses = OpenAI Responses (/responses)"
        >
          <Select
            options={[
              { label: 'Anthropic Messages（/v1/messages）', value: 'anthropic' },
              { label: 'Chat Completions（/chat/completions）', value: 'chat' },
              { label: 'OpenAI Responses（/responses）', value: 'responses' },
            ]}
          />
        </Form.Item>

        <Form.Item name="model" label="模型名" rules={[{ required: true }]}>
          <Input placeholder="如 claude-sonnet-4-5 / gpt-5 / deepseek-chat" />
        </Form.Item>

        <Form.Item
          name="base_url"
          label="API 地址（Base URL）"
          tooltip="留空 = 官方地址；Anthropic Messages 填到根路径（SDK 自动拼 /v1/messages），Chat/Responses 填含 /v1 的地址（SDK 自动拼 /chat/completions 或 /responses）"
        >
          <Input placeholder="留空使用官方；中转站如 https://your-relay.com/v1" />
        </Form.Item>

        <Form.Item
          name="api_key"
          label={
            <span>
              API Key{' '}
              {status?.has_api_key && (
                <Tag color="success" style={{ marginLeft: 4 }}>
                  已配置
                </Tag>
              )}
            </span>
          }
          tooltip="留空表示沿用当前已保存的 Key"
        >
          <Input.Password
            placeholder={
              status?.has_api_key ? '留空保持现有 Key 不变' : '粘贴你的 API Key'
            }
          />
        </Form.Item>

        <Form.Item
          name="allow_trading"
          label="允许下单"
          valuePropName="checked"
          tooltip="开启后 AI 可直接下单交易（仍受风控限制）"
        >
          <Switch />
        </Form.Item>

        <Form.Item
          name="auto_optimize"
          label="自动策略优化"
          valuePropName="checked"
          tooltip="休盘期间 AI 自动分析策略表现并调整参数"
        >
          <Switch />
        </Form.Item>
      </Form>

      <Alert
        type="info"
        showIcon
        message={
          <Text style={{ fontSize: 12 }}>
            配置保存在 data/ai_config.json（已加入
            .gitignore），重启后仍然生效；也可通过 backend/.env
            设置初始默认值。中转站（one-api/new-api 等）一般选 OpenAI
            协议并填其地址与 Key 即可。
          </Text>
        }
      />
    </Modal>
  )
}
