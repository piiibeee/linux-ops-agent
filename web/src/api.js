/**
 * 与后端通信。
 *
 * 为什么不用 EventSource：EventSource 只支持 GET，而对话需要 POST 传消息体。
 * 所以用 fetch + ReadableStream 手工解析 SSE 帧。
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
