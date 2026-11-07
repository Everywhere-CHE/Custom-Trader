/** 向导式策略生成器：选模板 → 基本信息 → 参数配置 → 代码预览，
 * 生成 StrategyTemplate 子类并保存到 backend/strategies/（服务端校验+热加载）。 */

import { useMemo, useState } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Steps,
  Typography,
  message,
} from 'antd'
import { RocketOutlined, SaveOutlined } from '@ant-design/icons'
import CodeMirror from '@uiw/react-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { writeStrategyFile } from '../api/http'
import { useThemeStore } from '../stores/theme'

const { Text } = Typography

// ---------------------------------------------------------------- 模板定义

interface TemplateParam {
  key: string
  label: string
  type: 'int' | 'float'
  default: number
  desc: string
}

interface TemplateVar {
  key: string
  type: 'int' | 'float'
  default: number
  desc: string
}

interface TemplateDef {
  id: string
  name: string
  tagline: string
  desc: string
  suggestedFile: string
  params: TemplateParam[]
  vars: TemplateVar[]
  amSize: (p: Record<string, number>) => number
  initExtra?: string
  onBarBody: string
}

/** 数值按 Python 字面量渲染（float 整数值补 .0）。 */
const pyNum = (v: number, type: 'int' | 'float') =>
  type === 'float' && Number.isInteger(v) ? `${v}.0` : `${v}`

/** 注入 Python 字符串字面量前的转义（防引号破坏生成代码）。 */
const pyStr = (s: string) => s.replace(/\\/g, '/').replace(/"/g, "'").replace(/\r?\n/g, ' ')

interface BuildCtx {
  className: string
  displayName: string
  description: string
  p: Record<string, number>
}

/** 组装完整的用户策略文件（与「编写策略代码」模板同一套约定）。 */
function buildStrategyCode(
  t: TemplateDef,
  ctx: Omit<BuildCtx, 'p'> & { p: Record<string, number> },
): string {
  const { className, displayName, description, p } = ctx
  const safeName = pyStr(displayName)
  const safeDesc = pyStr(description)
  const paramDecls = t.params
    .map(x => `    ${x.key}: ${x.type} = ${pyNum(p[x.key] ?? x.default, x.type)}`)
    .join('\n')
  const varDecls = t.vars
    .map(x => `    ${x.key}: ${x.type} = ${pyNum(x.default, x.type)}`)
    .join('\n')
  const paramDesc = t.params
    .map(x => `        "${x.key}": "${x.desc}",`)
    .join('\n')
  const varDesc = t.vars
    .map(x => `        "${x.key}": "${x.desc}",`)
    .join('\n')
  const paramKeys = t.params.map(x => `"${x.key}"`).join(', ')
  const varKeys = t.vars.map(x => `"${x.key}"`).join(', ')
  const initExtra = t.initExtra ? `\n${t.initExtra}` : ''

  return `"""${safeName}（向导生成）。

${t.desc}
"""

from app.core.object import BarData, TickData
from app.core.strategy.array_manager import ArrayManager
from app.core.strategy.bar_generator import BarGenerator
from app.core.strategy.template import StrategyTemplate


class ${className}(StrategyTemplate):
    """${safeName}：${pyStr(t.tagline)}"""

    author = "wizard"
    display_name = "${safeName}"
    description = "${safeDesc}"

    # ----- 参数（可在界面/回测中配置） -----
${paramDecls}

    # ----- 运行状态变量（界面实时展示） -----
${varDecls}

    parameters = [${paramKeys}]
    variables = [${varKeys}]
    param_descriptions = {
${paramDesc}
    }
    variable_descriptions = {
${varDesc}
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting):
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(size=${t.amSize(p)})
        self.bg = BarGenerator(self.on_bar)${initExtra}

    def on_init(self):
        self.write_log("策略初始化")
        self.load_bar(10)

    def on_start(self):
        self.write_log("策略启动")

    def on_stop(self):
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData):
${t.onBarBody}
`
}

const TEMPLATES: TemplateDef[] = [
  {
    id: 'ma_cross',
    name: '双均线交叉',
    tagline: '快慢均线金叉做多、死叉做空，经典趋势跟随',
    desc: (
      '双均线交叉策略：快线上穿慢线（金叉）买入，下穿（死叉）卖出；' +
      '适合有明显趋势的行情，震荡市中容易反复止损，建议配合冷却或过滤使用。'
    ),
    suggestedFile: 'my_ma_cross.py',
    params: [
      { key: 'fast_window', label: '快速均线周期', type: 'int', default: 10, desc: '快速均线周期（K线根数）' },
      { key: 'slow_window', label: '慢速均线周期', type: 'int', default: 20, desc: '慢速均线周期，须大于快线' },
      { key: 'fixed_size', label: '开仓手数', type: 'int', default: 1, desc: '每次开仓手数' },
    ],
    vars: [
      { key: 'fast_ma', type: 'float', default: 0.0, desc: '当前快速均线值' },
      { key: 'slow_ma', type: 'float', default: 0.0, desc: '当前慢速均线值' },
    ],
    amSize: p => Math.max((p.slow_window ?? 20) + 5, 25),
    initExtra: '        self._last_fast = None\n        self._last_slow = None',
    onBarBody: `        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        fast = am.sma(self.fast_window)
        slow = am.sma(self.slow_window)
        self.fast_ma, self.slow_ma = fast, slow

        if self._last_fast is None:
            self._last_fast, self._last_slow = fast, slow
            self.put_event()
            return

        golden = self._last_fast <= self._last_slow and fast > slow
        death = self._last_fast >= self._last_slow and fast < slow
        self._last_fast, self._last_slow = fast, slow

        if golden and self.pos <= 0:
            if self.pos < 0:
                self.cover(bar.close_price + 5, abs(self.pos))
            self.buy(bar.close_price + 5, self.fixed_size)
        elif death and self.pos >= 0:
            if self.pos > 0:
                self.sell(bar.close_price - 5, self.pos)
            self.short(bar.close_price - 5, self.fixed_size)

        self.put_event()`,
  },
  {
    id: 'rsi_reversal',
    name: 'RSI 超买超卖',
    tagline: 'RSI 超卖买入、超买卖出，均值回归思路',
    desc: (
      'RSI 超买超卖策略：RSI 低于超卖阈值时买入，高于超买阈值时卖出；' +
      '适合震荡行情，单边趋势中逆势信号风险较高。'
    ),
    suggestedFile: 'my_rsi_strategy.py',
    params: [
      { key: 'rsi_window', label: 'RSI 周期', type: 'int', default: 14, desc: 'RSI 指标计算周期' },
      { key: 'rsi_buy', label: '超卖阈值', type: 'float', default: 30, desc: 'RSI 低于该值时买入' },
      { key: 'rsi_sell', label: '超买阈值', type: 'float', default: 70, desc: 'RSI 高于该值时卖出' },
      { key: 'fixed_size', label: '开仓手数', type: 'int', default: 1, desc: '每次开仓手数' },
    ],
    vars: [{ key: 'rsi_value', type: 'float', default: 0.0, desc: '当前 RSI 值' }],
    amSize: () => 100,
    onBarBody: `        am = self.am
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

        self.put_event()`,
  },
  {
    id: 'donchian',
    name: '唐奇安通道突破',
    tagline: '突破近 N 根最高价做多、跌破最低价做空',
    desc: (
      '唐奇安通道突破策略：收盘价突破近 N 根K线最高价时做多，跌破最低价时做空（反手式）；' +
      '经典海龟规则骨架，适合趋势行情。'
    ),
    suggestedFile: 'my_donchian.py',
    params: [
      { key: 'entry_window', label: '通道周期', type: 'int', default: 20, desc: '唐奇安通道回看K线根数' },
      { key: 'fixed_size', label: '开仓手数', type: 'int', default: 1, desc: '每次开仓手数' },
    ],
    vars: [
      { key: 'upper_band', type: 'float', default: 0.0, desc: '通道上轨（近N根最高）' },
      { key: 'lower_band', type: 'float', default: 0.0, desc: '通道下轨（近N根最低）' },
    ],
    amSize: p => Math.max((p.entry_window ?? 20) + 5, 25),
    onBarBody: `        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        upper, lower = am.donchian(self.entry_window)
        self.upper_band, self.lower_band = upper, lower

        if bar.close_price > upper and self.pos <= 0:
            if self.pos < 0:
                self.cover(bar.close_price + 5, abs(self.pos))
            self.buy(bar.close_price + 5, self.fixed_size)
        elif bar.close_price < lower and self.pos >= 0:
            if self.pos > 0:
                self.sell(bar.close_price - 5, self.pos)
            self.short(bar.close_price - 5, self.fixed_size)

        self.put_event()`,
  },
  {
    id: 'boll_reversion',
    name: '布林带均值回归',
    tagline: '跌破下轨买入、突破上轨卖出，博取回归',
    desc: (
      '布林带均值回归策略：收盘价跌破下轨时买入、突破上轨时卖出（双向）；' +
      '适合震荡行情，趋势破位时需注意止损。'
    ),
    suggestedFile: 'my_boll_strategy.py',
    params: [
      { key: 'boll_window', label: '布林带周期', type: 'int', default: 20, desc: '布林带中轨计算周期' },
      { key: 'boll_dev', label: '标准差倍数', type: 'float', default: 2.0, desc: '上下轨偏离中轨的标准差倍数' },
      { key: 'fixed_size', label: '开仓手数', type: 'int', default: 1, desc: '每次开仓手数' },
    ],
    vars: [
      { key: 'upper_band', type: 'float', default: 0.0, desc: '布林上轨' },
      { key: 'lower_band', type: 'float', default: 0.0, desc: '布林下轨' },
    ],
    amSize: p => Math.max((p.boll_window ?? 20) + 5, 25),
    onBarBody: `        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        up, down = am.boll(self.boll_window, self.boll_dev)
        self.upper_band, self.lower_band = up, down

        if bar.close_price < down and self.pos <= 0:
            if self.pos < 0:
                self.cover(bar.close_price + 5, abs(self.pos))
            self.buy(bar.close_price + 5, self.fixed_size)
        elif bar.close_price > up and self.pos >= 0:
            if self.pos > 0:
                self.sell(bar.close_price - 5, self.pos)
            self.short(bar.close_price - 5, self.fixed_size)

        self.put_event()`,
  },
  {
    id: 'grid',
    name: '网格交易（多头）',
    tagline: '按百分比间距逢低分批买入、逐格止盈',
    desc: (
      '网格交易策略：以首根K线收盘价为基准，向下按固定百分比划分格位，' +
      '每跌破一格买入一份，反弹至买入价上方一格宽度时卖出该格止盈；仅做多，适合震荡偏多的品种。'
    ),
    suggestedFile: 'my_grid_strategy.py',
    params: [
      { key: 'grid_step_pct', label: '网格间距（%）', type: 'float', default: 1.0, desc: '相邻格位的百分比间距' },
      { key: 'max_grids', label: '最大格数', type: 'int', default: 5, desc: '最多同时持有的格数（最大补仓次数）' },
      { key: 'fixed_size', label: '每格手数', type: 'int', default: 1, desc: '每格买入手数' },
    ],
    vars: [
      { key: 'base_price', type: 'float', default: 0.0, desc: '网格基准价（首根K线收盘）' },
      { key: 'filled_grids', type: 'int', default: 0, desc: '当前已成交格数' },
    ],
    amSize: () => 25,
    initExtra:
      '        self._levels: list[float] = []\n' +
      '        self._level_holding: list[int] = []',
    onBarBody: `        price = bar.close_price
        if not self._levels:
            step = self.grid_step_pct / 100
            self.base_price = price
            self._levels = [price * (1 - k * step) for k in range(1, self.max_grids + 1)]
            self._level_holding = [0] * self.max_grids
            self.write_log(f"网格建立：基准 {price:.2f}，间距 {self.grid_step_pct}%")
            self.put_event()
            return

        # 跌破未持仓的格位 -> 买入一格
        for i, level in enumerate(self._levels):
            if price <= level and self._level_holding[i] == 0:
                if self.pos + self.fixed_size <= self.max_grids * self.fixed_size:
                    self.buy(price + 5, self.fixed_size)
                    self._level_holding[i] = self.fixed_size
                    self.filled_grids = sum(1 for v in self._level_holding if v > 0)
                    self.write_log(f"网格买入 第{i + 1}格 @ {price:.2f}")
                break

        # 反弹至买入价上方一格宽度 -> 卖出该格止盈
        for i, level in enumerate(self._levels):
            target = level * (1 + self.grid_step_pct / 100)
            if self._level_holding[i] > 0 and price >= target:
                self.sell(price - 5, self._level_holding[i])
                self._level_holding[i] = 0
                self.filled_grids = sum(1 for v in self._level_holding if v > 0)
                self.write_log(f"网格止盈 第{i + 1}格 @ {price:.2f}")

        self.put_event()`,
  },
]

// ---------------------------------------------------------------- 工具函数

const FILENAME_RE = /^[A-Za-z][A-Za-z0-9_]{0,63}\.py$/
const CLASSNAME_RE = /^[A-Za-z][A-Za-z0-9_]*$/

/** 文件名转 PascalCase 类名：my_ma_cross.py -> MyMaCross。 */
function deriveClassName(filename: string): string {
  return filename
    .replace(/\.py$/, '')
    .split('_')
    .filter(Boolean)
    .map(s => s.charAt(0).toUpperCase() + s.slice(1))
    .join('')
}

// ---------------------------------------------------------------- 组件

interface Props {
  onSaved?: () => void
}

export default function StrategyWizard({ onSaved }: Props) {
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)
  const [templateId, setTemplateId] = useState<string | null>(null)
  const [filename, setFilename] = useState('')
  const [className, setClassName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [description, setDescription] = useState('')
  const [paramValues, setParamValues] = useState<Record<string, number>>({})
  const [code, setCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()
  const mode = useThemeStore(s => s.mode)

  const template = TEMPLATES.find(t => t.id === templateId) ?? null

  const reset = () => {
    setStep(0)
    setTemplateId(null)
    setFilename('')
    setClassName('')
    setDisplayName('')
    setDescription('')
    setParamValues({})
    setCode('')
  }

  const pickTemplate = (t: TemplateDef) => {
    setTemplateId(t.id)
    setFilename(t.suggestedFile)
    setClassName(deriveClassName(t.suggestedFile))
    setDisplayName(t.name)
    setDescription(t.tagline)
    setParamValues(Object.fromEntries(t.params.map(x => [x.key, x.default])))
  }

  const generated = useMemo(() => {
    if (!template || !className || !displayName) return ''
    const p = Object.fromEntries(
      template.params.map(x => [x.key, paramValues[x.key] ?? x.default]),
    )
    return buildStrategyCode(template, {
      className,
      displayName,
      description: description || template.tagline,
      p,
    })
  }, [template, className, displayName, description, paramValues])

  const filenameValid = FILENAME_RE.test(filename.trim())
  const classNameValid = CLASSNAME_RE.test(className.trim())

  const next = () => {
    if (step === 0) {
      if (!template) {
        messageApi.warning('请先选择一个策略模板')
        return
      }
    } else if (step === 1) {
      if (!filenameValid) {
        messageApi.warning(
          '文件名不合法：仅允许字母开头的字母/数字/下划线组合并以 .py 结尾',
        )
        return
      }
      if (!classNameValid) {
        messageApi.warning('类名不合法：仅允许字母开头的字母/数字/下划线组合')
        return
      }
      if (!displayName.trim()) {
        messageApi.warning('请填写显示名称')
        return
      }
    }
    if (step === 2) setCode(generated)
    setStep(step + 1)
  }

  const save = async () => {
    const name = filename.trim()
    if (!name.endsWith('.py')) {
      messageApi.warning('文件名必须以 .py 结尾')
      return
    }
    setSaving(true)
    try {
      const result = await writeStrategyFile(name, code, false)
      messageApi.success(
        `已保存并加载策略类: ${result.classes_found.join(', ')}`,
      )
      onSaved?.()
      setOpen(false)
      reset()
    } catch (error) {
      const status = (error as { response?: { status?: number } }).response?.status
      const detail =
        (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? String(error)
      if (status === 409) {
        messageApi.warning('文件已存在，请在「编写策略代码」中打开该文件修改，或换一个文件名')
      } else {
        messageApi.error(`保存失败: ${detail}`)
      }
    } finally {
      setSaving(false)
    }
  }

  const stepsItems = [
    { title: '选择模板' },
    { title: '基本信息' },
    { title: '参数配置' },
    { title: '代码预览' },
  ]

  return (
    <>
      {contextHolder}
      <Button icon={<RocketOutlined />} onClick={() => setOpen(true)}>
        向导创建策略
      </Button>

      <Modal
        title="向导式创建策略（生成代码 → 服务端校验 → 热加载）"
        open={open}
        onCancel={() => {
          setOpen(false)
          reset()
        }}
        width={920}
        style={{ top: 24 }}
        footer={
          <Space>
            {step > 0 && (
              <Button onClick={() => setStep(step - 1)}>上一步</Button>
            )}
            {step < 3 && <Button type="primary" onClick={next}>下一步</Button>}
            {step === 3 && (
              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={saving}
                onClick={() => void save()}
              >
                保存并加载
              </Button>
            )}
          </Space>
        }
      >
        <Steps
          size="small"
          current={step}
          items={stepsItems}
          style={{ marginBottom: 16 }}
        />

        {/* 步骤 0：选择模板 */}
        {step === 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {TEMPLATES.map(t => (
              <div
                key={t.id}
                onClick={() => pickTemplate(t)}
                style={{
                  cursor: 'pointer',
                  padding: '10px 12px',
                  borderRadius: 6,
                  border: `1px solid ${templateId === t.id ? '#1677ff' : 'var(--border-color)'}`,
                  background:
                    templateId === t.id ? 'rgba(22,119,255,0.08)' : 'transparent',
                }}
              >
                <Text strong>{t.name}</Text>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t.tagline}
                  </Text>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 步骤 1：基本信息 */}
        {step === 1 && template && (
          <Form layout="vertical" style={{ maxWidth: 520 }}>
            <Form.Item
              label="文件名"
              required
              validateStatus={filename && !filenameValid ? 'error' : undefined}
              help={filename && !filenameValid ? '字母开头，仅字母/数字/下划线，.py 结尾' : '保存至 backend/strategies/ 目录'}
            >
              <Input
                value={filename}
                onChange={e => {
                  setFilename(e.target.value)
                  setClassName(deriveClassName(e.target.value))
                }}
                placeholder="my_strategy.py"
              />
            </Form.Item>
            <Form.Item
              label="策略类名"
              required
              validateStatus={className && !classNameValid ? 'error' : undefined}
              help="Python 类名，须与已有策略类不重名"
            >
              <Input value={className} onChange={e => setClassName(e.target.value)} />
            </Form.Item>
            <Form.Item label="显示名称" required help="在策略列表与创建策略下拉中显示的中文名">
              <Input
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                placeholder="我的双均线"
              />
            </Form.Item>
            <Form.Item label="策略简介" help="显示在策略文档与创建弹窗中">
              <Input
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder={template.tagline}
              />
            </Form.Item>
          </Form>
        )}

        {/* 步骤 2：参数配置 */}
        {step === 2 && template && (
          <Form layout="vertical" style={{ maxWidth: 520 }}>
            {template.params.map(x => (
              <Form.Item
                key={x.key}
                label={`${x.label}（${x.key}）`}
                required
                help={x.desc}
              >
                <InputNumber
                  style={{ width: 200 }}
                  value={paramValues[x.key] ?? x.default}
                  min={x.key === 'fixed_size' ? 1 : undefined}
                  precision={x.type === 'int' ? 0 : 2}
                  onChange={v =>
                    setParamValues(prev => ({ ...prev, [x.key]: (v ?? x.default) as number }))
                  }
                />
              </Form.Item>
            ))}
          </Form>
        )}

        {/* 步骤 3：代码预览与保存 */}
        {step === 3 && (
          <div style={{ border: '1px solid var(--border-color)', borderRadius: 6, overflow: 'hidden' }}>
            <CodeMirror
              value={code}
              height="420px"
              extensions={[python()]}
              theme={mode === 'dark' ? oneDark : 'light'}
              basicSetup={{ lineNumbers: true, foldGutter: false }}
              onChange={setCode}
            />
          </div>
        )}
      </Modal>
    </>
  )
}
