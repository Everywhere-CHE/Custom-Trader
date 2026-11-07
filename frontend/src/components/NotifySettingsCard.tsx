/** 通知渠道设置卡片：飞书 / 企业微信 / 邮件 / 系统通知 + 事件开关。 */

import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { getNotifyConfig, testNotify, updateNotifyConfig } from '../api/http'
import type { NotifyConfig } from '../api/http'

const { Text } = Typography

const EVENT_SWITCHES: { key: keyof NotifyConfig; label: string }[] = [
  { key: 'notify_trades', label: '成交回报' },
  { key: 'notify_risk', label: '风控告警（强平/熔断）' },
  { key: 'notify_strategy', label: '策略事件' },
  { key: 'notify_guardian', label: '策略守护告警' },
]

export default function NotifySettingsCard() {
  const [messageApi, contextHolder] = message.useMessage()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResults, setTestResults] = useState<Record<string, string> | null>(null)
  const [switches, setSwitches] = useState<Record<string, boolean>>({})

  useEffect(() => {
    getNotifyConfig()
      .then(cfg => {
        const { notify_trades, notify_risk, notify_strategy, notify_guardian, ...fields } = cfg
        form.setFieldsValue({
          ...fields,
          email_to: (cfg.email_to || []).join(', '),
        })
        setSwitches({ notify_trades, notify_risk, notify_strategy, notify_guardian })
      })
      .catch(() => undefined)
  }, [form])

  const save = async () => {
    const values = await form.validateFields()
    setLoading(true)
    try {
      await updateNotifyConfig({
        ...values,
        email_to: String(values.email_to || '')
          .split(/[,，;；\s]+/)
          .map(s => s.trim())
          .filter(Boolean),
        ...switches,
      })
      messageApi.success('通知配置已保存（立即生效）')
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`保存失败: ${detail}`)
    } finally {
      setLoading(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setTestResults(null)
    try {
      const r = await testNotify()
      setTestResults(r.results)
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      messageApi.error(`测试失败: ${detail}`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <Card
      size="small"
      title="通知推送"
      extra={
        <Space>
          <Button size="small" loading={testing} onClick={test}>
            测试
          </Button>
          <Button size="small" type="primary" loading={loading} onClick={save}>
            保存
          </Button>
        </Space>
      }
    >
      {contextHolder}
      <Form form={form} layout="vertical" size="middle" style={{ maxWidth: 760 }}>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item
              name="feishu_webhook"
              label="飞书群机器人 Webhook"
              tooltip="飞书群 → 设置 → 群机器人 → 自定义机器人，复制 Webhook 地址"
            >
              <Input placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxx" allowClear />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="feishu_secret"
              label="飞书签名密钥（可选）"
              tooltip="机器人开启了签名校验时填写；留空保留原值"
            >
              <Input.Password placeholder="留空保留原值；传 - 清除" autoComplete="new-password" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="wecom_webhook"
              label="企业微信群机器人 Webhook"
              tooltip="企微群 → 添加群机器人，复制 Webhook 地址"
            >
              <Input placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" allowClear />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item name="email_smtp_host" label="SMTP 服务器">
              <Input placeholder="smtp.qq.com" allowClear />
            </Form.Item>
          </Col>
          <Col span={3}>
            <Form.Item name="email_smtp_port" label="端口">
              <InputNumber min={1} max={65535} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={3}>
            <Form.Item name="email_smtp_ssl" label="SSL" valuePropName="checked">
              <Checkbox>SSL(465)</Checkbox>
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item name="email_username" label="邮箱账号">
              <Input placeholder="user@qq.com" allowClear />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item
              name="email_password"
              label="邮箱授权码"
              tooltip="SMTP 授权码（非登录密码）；留空保留原值"
            >
              <Input.Password placeholder="留空保留原值；传 - 清除" autoComplete="new-password" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="email_to" label="收件人（逗号分隔）">
              <Input placeholder="a@x.com, b@y.com" allowClear />
            </Form.Item>
          </Col>
        </Row>
      </Form>

      <div style={{ marginBottom: 8 }}>
        <Text type="secondary" style={{ fontSize: 12, marginRight: 12 }}>
          推送事件：
        </Text>
        {EVENT_SWITCHES.map(({ key, label }) => (
          <Checkbox
            key={key}
            checked={Boolean(switches[key])}
            onChange={e => setSwitches(prev => ({ ...prev, [key]: e.target.checked }))}
            style={{ marginRight: 12 }}
          >
            {label}
          </Checkbox>
        ))}
      </div>
      <Text type="secondary" style={{ fontSize: 12 }}>
        成交回报在盘中可能频繁触发，建议只在对账需要时开启；风控告警与守护告警默认开启。
        配置保存在 data/notify_config.json，保存立即生效无需重启。
      </Text>

      {testResults && (
        <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {Object.entries(testResults).map(([channel, result]) => (
            <span key={channel}>
              <Tag color={result === 'ok' ? 'success' : result === '未配置' || result === '未启用' ? 'default' : 'error'}>
                {channel}
              </Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>{result}</Text>
            </span>
          ))}
        </div>
      )}
    </Card>
  )
}
