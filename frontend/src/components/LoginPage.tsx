/** 登录页（ARL 风格）：深蓝黑背景 + 主题色光晕，居中卡片。 */

import { useState } from 'react'
import { Button, Form, Input, Typography, message } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useAuthStore } from '../stores/auth'
import LogoMark from './ui/LogoMark'

const { Title, Text } = Typography

export default function LoginPage() {
  const [loading, setLoading] = useState(false)
  const login = useAuthStore(s => s.login)

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      await login(values.username, values.password)
      message.success('登录成功')
    } catch {
      message.error('用户名或密码错误')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background:
          'radial-gradient(720px circle at 18% 22%, rgba(var(--accent-rgb), 0.18), transparent 55%),' +
          'radial-gradient(640px circle at 82% 78%, rgba(var(--accent-rgb), 0.1), transparent 55%),' +
          '#0b1120',
      }}
    >
      <div
        className="w-[360px] rounded-xl border p-8"
        style={{
          background: 'var(--card-bg)',
          borderColor: 'var(--card-border)',
          boxShadow: '0 20px 50px rgba(0, 0, 0, 0.35)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div
            className="mx-auto mb-3"
            style={{
              width: 46,
              filter: 'drop-shadow(0 6px 18px rgba(var(--accent-rgb), 0.45))',
            }}
          >
            <LogoMark size={46} />
          </div>
          <Title level={4} style={{ marginBottom: 4 }}>
            CustomTrader
          </Title>
          <Text type="secondary">期货 + 股票量化交易平台</Text>
        </div>
        <Form name="login" onFinish={onFinish} size="large">
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" autoFocus />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button
              type="primary"
              htmlType="submit"
              block
              loading={loading}
              style={{ height: 42 }}
            >
              登 录
            </Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  )
}
