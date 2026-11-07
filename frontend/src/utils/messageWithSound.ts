/** 带提示音的消息弹窗 Hook。 */

import { message } from 'antd'
import type { MessageInstance } from 'antd/es/message/interface'
import { playBeep } from '../utils/sound'
import { useSoundStore } from '../stores/sound'

type MessageFn = MessageInstance['success']

function withSound(fn: MessageFn): MessageFn {
  return ((content: Parameters<MessageFn>[0], duration?: Parameters<MessageFn>[1], onClose?: Parameters<MessageFn>[2]) => {
    if (useSoundStore.getState().enabled) {
      playBeep()
    }
    return fn(content, duration, onClose)
  }) as MessageFn
}

export function useMessageWithSound() {
  const [messageApi, contextHolder] = message.useMessage()
  const soundApi = {
    success: withSound(messageApi.success.bind(messageApi)),
    error: withSound(messageApi.error.bind(messageApi)),
    info: withSound(messageApi.info.bind(messageApi)),
    warning: withSound(messageApi.warning.bind(messageApi)),
    open: messageApi.open.bind(messageApi),
    destroy: messageApi.destroy.bind(messageApi),
  }
  return { messageApi: soundApi, contextHolder }
}