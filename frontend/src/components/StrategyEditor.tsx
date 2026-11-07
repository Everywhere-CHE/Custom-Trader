/** 浏览器内策略代码编辑器：可基于模板创建自己的策略、
 * 编辑已有文件、保存（服务端做 AST 校验），
 * 保存后策略类注册表会自动重新加载。 */

import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Input,
  List,
  Modal,
  Space,
  Typography,
  message,
} from 'antd'
import {
  CodeOutlined,
  FileAddOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import CodeMirror from '@uiw/react-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import {
  getStrategyFiles,
  readStrategyFile,
  writeStrategyFile,
} from '../api/http'
import { useThemeStore } from '../stores/theme'
import type { StrategyFileInfo } from '../types'

const { Text } = Typography

const TEMPLATE = `"""我的自定义策略。

继承 StrategyTemplate，实现 on_bar 交易逻辑即可。
可用指标（ArrayManager）：sma / ema / std / rsi / macd / boll / atr / donchian
交易动作：self.buy / sell / short / cover(price, volume, stop=False)
当前净持仓：self.pos（多为正、空为负）
"""

from app.core.object import BarData, TickData
from app.core.strategy.array_manager import ArrayManager
from app.core.strategy.bar_generator import BarGenerator
from app.core.strategy.template import StrategyTemplate


class MyStrategy(StrategyTemplate):
    """请修改类名（须唯一），并填写策略说明。"""

    author = "me"
    description = "在这里写策略介绍：交易逻辑、适用行情、风险提示。"

    # ----- 参数（可在界面/回测中配置） -----
    rsi_window: int = 14
    rsi_buy: float = 30.0
    rsi_sell: float = 70.0
    fixed_size: int = 1

    # ----- 运行状态变量（界面实时展示） -----
    rsi_value: float = 0.0

    parameters = ["rsi_window", "rsi_buy", "rsi_sell", "fixed_size"]
    variables = ["rsi_value"]
    param_descriptions = {
        "rsi_window": "RSI 指标计算周期",
        "rsi_buy": "RSI 低于该值时买入（超卖）",
        "rsi_sell": "RSI 高于该值时卖出（超买）",
        "fixed_size": "每次开仓手数",
    }
    variable_descriptions = {"rsi_value": "当前 RSI 值"}

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting):
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(size=100)
        self.bg = BarGenerator(self.on_bar)  # 实盘: tick 合成 1 分钟K线

    def on_init(self):
        self.write_log("策略初始化")
        self.load_bar(10)  # 预热 10 天历史K线

    def on_start(self):
        self.write_log("策略启动")

    def on_stop(self):
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData):
        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        self.rsi_value = am.rsi(self.rsi_window)

        if self.rsi_value < self.rsi_buy and self.pos <= 0:
            if self.pos < 0:
                self.cover(bar.close_price + 5, abs(self.pos))
            self.buy(bar.close_price + 5, self.fixed_size)
        elif self.rsi_value > self.rsi_sell and self.pos >= 0:
            if self.pos > 0:
                self.sell(bar.close_price - 5, self.pos)
            self.short(bar.close_price - 5, self.fixed_size)

        self.put_event()
`

interface Props {
  onSaved?: () => void
}

export default function StrategyEditor({ onSaved }: Props) {
  const [open, setOpen] = useState(false)
  const [files, setFiles] = useState<StrategyFileInfo[]>([])
  const [filename, setFilename] = useState('my_strategy.py')
  const [code, setCode] = useState(TEMPLATE)
  const [isExisting, setIsExisting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()
  const mode = useThemeStore(s => s.mode)

  const refreshFiles = () =>
    getStrategyFiles().then(setFiles).catch(() => undefined)

  useEffect(() => {
    if (open) void refreshFiles()
  }, [open])

  const newFile = () => {
    setFilename('my_strategy.py')
    setCode(TEMPLATE)
    setIsExisting(false)
  }

  const openFile = async (name: string) => {
    try {
      const data = await readStrategyFile(name)
      setFilename(data.filename)
      setCode(data.code)
      setIsExisting(true)
    } catch {
      messageApi.error('读取失败')
    }
  }

  const save = async () => {
    const name = filename.trim()
    if (!name.endsWith('.py')) {
      messageApi.warning('文件名必须以 .py 结尾')
      return
    }
    setSaving(true)
    try {
      const result = await writeStrategyFile(name, code, isExisting)
      messageApi.success(
        `已保存并加载策略类: ${result.classes_found.join(', ')}`,
      )
      setIsExisting(true)
      void refreshFiles()
      onSaved?.()
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      // 409 = 文件已存在且未指定覆盖：询问是否覆盖
      const status = (error as { response?: { status?: number } }).response
        ?.status
      if (status === 409) {
        Modal.confirm({
          title: '文件已存在',
          content: `${name} 已存在，是否覆盖？`,
          onOk: async () => {
            const result = await writeStrategyFile(name, code, true)
            messageApi.success(
              `已覆盖并加载: ${result.classes_found.join(', ')}`,
            )
            setIsExisting(true)
            void refreshFiles()
            onSaved?.()
          },
        })
      } else {
        messageApi.error(`保存失败: ${detail}`)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      {contextHolder}
      <Button icon={<CodeOutlined />} onClick={() => setOpen(true)}>
        编写策略代码
      </Button>

      <Modal
        title="策略代码编辑器（保存后自动校验并加载）"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={980}
        style={{ top: 24 }}
      >
        <div style={{ display: 'flex', gap: 12, minHeight: 560 }}>
          {/* 文件列表 */}
          <div
            style={{
              width: 200,
              borderRight: '1px solid var(--border-color)',
              paddingRight: 8,
            }}
          >
            <Button
              size="small"
              block
              icon={<FileAddOutlined />}
              onClick={newFile}
              style={{ marginBottom: 8 }}
            >
              新建策略
            </Button>
            <List
              size="small"
              dataSource={files}
              locale={{ emptyText: '暂无自定义策略文件' }}
              renderItem={file => (
                <List.Item
                  style={{
                    cursor: 'pointer',
                    padding: '4px 8px',
                    background:
                      file.filename === filename && isExisting
                        ? 'rgba(22,119,255,0.15)'
                        : undefined,
                    borderRadius: 4,
                  }}
                  onClick={() => void openFile(file.filename)}
                >
                  <Text style={{ fontSize: 12 }}>{file.filename}</Text>
                </List.Item>
              )}
            />
          </div>

          {/* 编辑器 */}
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <Space>
              <Input
                value={filename}
                onChange={e => {
                  setFilename(e.target.value)
                  setIsExisting(false)
                }}
                style={{ width: 240 }}
                addonBefore="文件名"
                placeholder="my_strategy.py"
              />
              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={saving}
                onClick={() => void save()}
              >
                保存并加载
              </Button>
            </Space>

            <div
              style={{
                flex: 1,
                border: '1px solid var(--border-color)',
                borderRadius: 6,
                overflow: 'hidden',
              }}
            >
              <CodeMirror
                value={code}
                height="460px"
                extensions={[python()]}
                theme={mode === 'dark' ? oneDark : 'light'}
                basicSetup={{ lineNumbers: true, foldGutter: false }}
                onChange={setCode}
              />
            </div>

            <Alert
              type="info"
              showIcon
              message={
                <Text style={{ fontSize: 12 }}>
                  保存时服务端自动校验：必须继承
                  StrategyTemplate、禁止危险导入（os/subprocess
                  等）、语法错误会提示行号。保存成功即可在「创建策略」和回测页中使用新策略类；文件保存于
                  backend/strategies/。
                </Text>
              }
            />
          </div>
        </div>
      </Modal>
    </>
  )
}
