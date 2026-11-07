/** 提示音播放工具（使用 Web Audio API 生成简单蜂鸣声，无需音频文件）。 */

let _audioCtx: AudioContext | null = null

function getAudioCtx(): AudioContext {
  if (!_audioCtx) {
    _audioCtx = new AudioContext()
  }
  return _audioCtx
}

/** 播放一次提示音。type: 'success' 为高音短促，'error' 为低音稍长。*/
export function playBeep(type: 'success' | 'error' | 'info' = 'info') {
  try {
    const ctx = getAudioCtx()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain)
    gain.connect(ctx.destination)

    if (type === 'success') {
      // 高音短促双响
      osc.frequency.value = 1200
      osc.type = 'sine'
      gain.gain.setValueAtTime(0.3, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.08)
      osc.start(ctx.currentTime)
      osc.stop(ctx.currentTime + 0.08)
      // 第二个短音
      const osc2 = ctx.createOscillator()
      const gain2 = ctx.createGain()
      osc2.connect(gain2)
      gain2.connect(ctx.destination)
      osc2.frequency.value = 1500
      osc2.type = 'sine'
      gain2.gain.setValueAtTime(0.25, ctx.currentTime + 0.1)
      gain2.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.18)
      osc2.start(ctx.currentTime + 0.1)
      osc2.stop(ctx.currentTime + 0.18)
    } else if (type === 'error') {
      // 低音稍长
      osc.frequency.value = 300
      osc.type = 'sawtooth'
      gain.gain.setValueAtTime(0.25, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3)
      osc.start(ctx.currentTime)
      osc.stop(ctx.currentTime + 0.3)
    } else {
      // 中音短促（默认）
      osc.frequency.value = 800
      osc.type = 'sine'
      gain.gain.setValueAtTime(0.3, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.15)
      osc.start(ctx.currentTime)
      osc.stop(ctx.currentTime + 0.15)
    }
  } catch {
    // 浏览器限制或音频不可用，静默忽略
  }
}