/**
 * 与后端通信。
 *
 * 为什么不用 EventSource：EventSource 只支持 GET，而对话需要 POST 传消息体。
 * 所以用 fetch + ReadableStream 手工解析 SSE 帧。
 *
 * 管理端接口（审计 / 审批 / 拓扑）是普通 JSON，走统一的 request 封装：
 * 错误在这里收敛一次，页面里就不用每个都写 try/catch + 判断 res.ok。
 */

function parseFrame(raw) {
  let event = 'message'
  const dataLines = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return null
  }
}

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(path, options)
  } catch {
    throw new Error('连不上后端：请确认 uvicorn 已启动（端口 8000）')
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    // 后端用 detail 传人话错误，优先展示它
    throw new Error(body.detail || `请求失败：${res.status}`)
  }
  return body
}

/** 把查询对象拼成 query string，自动丢掉空值（否则会把 tool=undefined 发过去） */
function qs(params = {}) {
  const usable = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== ''
  )
  return usable.length ? '?' + new URLSearchParams(usable).toString() : ''
}

export async function streamChat({ sessionId, message, onEvent, signal }) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal,
  })

  if (!res.ok || !res.body) {
    throw new Error(`请求失败：${res.status} ${res.statusText}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const frame = parseFrame(raw)
      if (frame) onEvent(frame.event, frame.data)
    }
  }
}

export async function confirmAction(actionId) {
  const res = await fetch(`/api/actions/${actionId}/confirm`, { method: 'POST' })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || '执行失败')
  return body
}

export async function fetchHealth() {
  const res = await fetch('/api/health')
  return res.json()
}

// ── 管理端：审计日志 ────────────────────────────────────
export function fetchAudit(params) {
  return request('/api/audit' + qs(params))
}

export function fetchAuditStats() {
  return request('/api/audit/stats')
}

// ── 管理端：变更审批 ────────────────────────────────────
export function fetchActions(status) {
  return request('/api/actions' + qs({ status }))
}

export function approveAction(actionId, approver) {
  return request(`/api/actions/${actionId}/approve` + qs({ approver }), { method: 'POST' })
}

// ── 管理端：流程拓扑 ────────────────────────────────────
export function fetchGraph() {
  return request('/api/graph')
}
