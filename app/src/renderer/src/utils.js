export const STATUS_LABEL = {
  queued: '排队中',
  probing: '探测中',
  downloading: '下载中',
  checking: '校验中',
  paused: '已暂停',
  completed: '已完成',
  error: '异常中断',
  cancelled: '已取消'
}

export function fmtBytes(n) {
  if (n == null) return '—'
  if (n === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i]
}

export function fmtSpeed(bps) {
  if (!bps || bps <= 0) return '0 B/s'
  return fmtBytes(bps) + '/s'
}

export function fmtEta(sec) {
  if (sec == null || !isFinite(sec) || sec <= 0) return '—'
  if (sec < 60) return (sec | 0) + ' 秒'
  const m = sec / 60
  if (m < 60) return (m | 0) + ' 分 ' + ((sec % 60) | 0) + ' 秒'
  return (m / 60).toFixed(1) + ' 小时'
}

export function clamp(v, lo, hi, def) {
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : def
}