/** 策略文档面板：展示每个可用策略类的说明
 * 及其全部参数/运行状态变量。 */

import { Card, Collapse, Descriptions, Tag, Typography } from 'antd'
import type { StrategyClassInfo } from '../types'

const { Paragraph, Text } = Typography

export default function StrategyDocs({
  classes,
}: {
  classes: StrategyClassInfo[]
}) {
  if (!classes.length) return null
  return (
    <Card size="small" title="策略说明（所有可用策略与参数介绍）">
      <Collapse
        size="small"
        items={classes.map(cls => ({
          key: cls.class_name,
          label: (
            <span>
              <Text strong>{cls.display_name || cls.class_name}</Text>{' '}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {cls.author && `by ${cls.author}`}
              </Text>
            </span>
          ),
          children: (
            <div>
              <Paragraph style={{ fontSize: 13 }}>
                {cls.description || '（该策略未提供说明，可在类中添加 description 属性）'}
              </Paragraph>

              <Text strong style={{ fontSize: 13 }}>
                参数
              </Text>
              <Descriptions
                size="small"
                column={1}
                bordered
                style={{ margin: '8px 0' }}
                items={Object.entries(cls.parameters).map(([name, value]) => ({
                  key: name,
                  label: (
                    <span>
                      <Text>{cls.param_descriptions?.[name] ?? name}</Text>{' '}
                      <Tag style={{ marginLeft: 4 }}>默认 {String(value)}</Tag>
                    </span>
                  ),
                  children:
                    cls.param_descriptions?.[name]
                      ? cls.variable_descriptions?.[name] && (
                          <Text type="secondary">{cls.variable_descriptions[name]}</Text>
                        )
                      : <Text type="secondary">（未提供说明）</Text>,
                }))}
              />

              {cls.variables?.length > 0 && (
                <>
                  <Text strong style={{ fontSize: 13 }}>
                    运行状态变量
                  </Text>
                  <Descriptions
                    size="small"
                    column={1}
                    bordered
                    style={{ marginTop: 8 }}
                    items={cls.variables.map(name => ({
                      key: name,
                      label: <Text code>{name}</Text>,
                      children:
                        cls.variable_descriptions?.[name] ?? (
                          <Text type="secondary">（未提供说明）</Text>
                        ),
                    }))}
                  />
                </>
              )}
            </div>
          ),
        }))}
      />
    </Card>
  )
}
