<template>
  <div class="chat-panel">
    <!-- 顶部状态条 -->
    <div class="status-bar">
      <span class="dot" :class="health.allowed ? 'ok' : 'warn'"></span>
      <span class="label">{{ statusText }}</span>
      <span class="spacer"></span>
      <el-button link size="small" @click="resetSession">清空会话</el-button>
    </div>

    <!-- 消息区 -->
    <div ref="scrollRef" class="message-list">
      <div v-if="!messages.length" class="empty">
        <p class="empty-title">服务器状态随时可查</p>
        <p class="empty-sub">直接描述现象，我会先查手册、再采数据、最后给结论。</p>
        <div class="quick">
          <el-button
            v-for="q in quickQuestions"
            :key="q"
            size="small"
            plain
            @click="send(q)"
          >
            {{ q }}
          </el-button>
        </div>
      </div>

      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="message"
        :class="msg.role"
      >
        <div class="avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
        <div class="bubble-wrap">
          <!-- 工具调用轨迹：让用户看到 Agent 在查什么 -->
          <div v-if="msg.tools && msg.tools.length" class="tool-trace">
            <el-tag
              v-for="(t, ti) in msg.tools"
              :key="ti"
              size="small"
              effect="plain"
              type="info"
            >
              {{ t.name }}
            </el-tag>
          </div>

          <div class="bubble" :class="{ streaming: msg.streaming }">
            <span v-if="msg.text">{{ msg.text }}</span>
            <span v-else-if="msg.streaming" class="typing">正在采集与分析</span>
          </div>

          <!-- 待确认的变更操作 -->
          <div v-for="act in msg.pending || []" :key="act.action_id" class="action-card">
            <div class="action-head">
              <el-tag size="small" type="warning" effect="dark">待确认</el-tag>
              <span class="action-desc">{{ act.description }}</span>
            </div>
            <code class="action-cmd">$ {{ act.command }}</code>
            <p v-if="act.reason" class="action-reason">{{ act.reason }}</p>
            <div class="action-btns">
              <el-button
                size="small"
                type="primary"
                :loading="acting === act.action_id"
                @click="doConfirm(act)"
              >
                确认执行
              </el-button>
              <span class="action-hint">未经确认不会对服务器做任何改动</span>
            </div>
            <p v-if="act.result" class="action-result" :class="act.ok ? 'ok' : 'err'">
              {{ act.result }}
            </p>
          </div>
        </div>
      </div>

      <div v-if="error" class="error-tip">{{ error }}</div>
    </div>

    <!-- 输入区 -->
    <div class="composer">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        resize="none"
        placeholder="例如：根分区满了，应用报 No space left on device"
        :disabled="loading"
        @keydown.enter.exact.prevent="send()"
      />
      <el-button
        type="primary"
        :loading="loading"
        class="send-btn"
        @click="send()"
      >
        {{ loading ? '分析中' : '发送' }}
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { confirmAction, fetchHealth, streamChat } from '../api'

const messages = ref([])
const input = ref('')
const loading = ref(false)
const acting = ref('')
const error = ref('')
const scrollRef = ref(null)
const health = ref({ allowed: false, backend: '-', model: '-' })

const sessionId = `web-${Math.random().toString(36).slice(2, 10)}`

const quickQuestions = [
  '根分区快满了，怎么排查',
  'nginx 突然访问不了了',
  '机器很卡，负载很高',
  '帮我看看日志里有哪些报错',
]

const statusText = computed(() => {
  if (!health.value.allowed) return '正在连接后端…'
  const mode = health.value.allowExecute ? '可执行' : '只读演示'
  return `${health.value.model} · 会话存储 ${health.value.backend} · ${mode}模式`
})

onMounted(async () => {
  try {
    const h = await fetchHealth()
    health.value = {
      allowed: true,
      backend: h.memory_backend,
      model: h.llm_model,
      allowExecute: h.allow_execute,
    }
  } catch {
    error.value = '后端未启动：请先运行 uvicorn app.main:app --port 8000'
  }
})

function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function send(preset) {
  const text = (preset ?? input.value).trim()
  if (!text || loading.value) return

  input.value = ''
  error.value = ''
  loading.value = true

  messages.value.push({ role: 'user', text })
  // 必须从数组里取回响应式代理再改，直接改原始对象不会触发视图更新
  messages.value.push({ role: 'assistant', text: '', tools: [], pending: [], streaming: true })
  const reply = messages.value[messages.value.length - 1]
  scrollToBottom()

  try {
    await streamChat({
      sessionId,
      message: text,
      onEvent(event, data) {
        if (event === 'token') {
          reply.text += data.text
          scrollToBottom()
        } else if (event === 'tool_start') {
          reply.tools.push({ name: data.name, input: data.input })
          scrollToBottom()
        } else if (event === 'done') {
          reply.streaming = false
          reply.pending = (data.pending_actions || []).map((a) => ({ ...a }))
          if (!reply.text) reply.text = data.answer || '（本轮无文本输出）'
          scrollToBottom()
        } else if (event === 'error') {
          error.value = data.message
          reply.streaming = false
        }
      },
    })
  } catch (e) {
    error.value = e.message || '请求失败'
    reply.streaming = false
  } finally {
    loading.value = false
    reply.streaming = false
    scrollToBottom()
  }
}

async function doConfirm(act) {
  acting.value = act.action_id
  try {
    const res = await confirmAction(act.action_id)
    act.result = res.output
    act.ok = true
  } catch (e) {
    act.result = e.message
    act.ok = false
  } finally {
    acting.value = ''
  }
}

async function resetSession() {
  await fetch('/api/session/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  }).catch(() => {})
  messages.value = []
  error.value = ''
}
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 900px;
  margin: 0 auto;
  background: var(--surface);
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
}

.status-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  color: var(--muted);
}

.status-bar .spacer {
  flex: 1;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #d1d5db;
}

.dot.ok {
  background: #16a34a;
}

.dot.warn {
  background: #d97706;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px 18px;
  background: #fbfcfe;
}

.empty {
  margin: 60px auto 0;
  max-width: 520px;
  text-align: center;
}

.empty-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 6px;
}

.empty-sub {
  color: var(--muted);
  font-size: 13px;
  margin: 0 0 20px;
}

.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.message {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.message.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  background: #e8eef6;
  color: var(--primary);
}

.message.user .avatar {
  background: var(--primary);
  color: #fff;
}

.bubble-wrap {
  max-width: 78%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.bubble {
  padding: 10px 14px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid var(--border);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.message.user .bubble {
  background: #eef4fb;
  border-color: #d5e2f2;
}

.bubble.streaming {
  border-color: #b9cfe8;
}

.typing {
  color: var(--muted);
  font-size: 13px;
}

.typing::after {
  content: '';
  animation: blink 1.2s steps(3, start) infinite;
}

@keyframes blink {
  0% { content: ''; }
  33% { content: '.'; }
  66% { content: '..'; }
  100% { content: '...'; }
}

.tool-trace {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.action-card {
  border: 1px solid #f0c987;
  background: #fffdf5;
  border-radius: 8px;
  padding: 12px 14px;
}

.action-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.action-desc {
  font-size: 13px;
  font-weight: 600;
}

.action-cmd {
  display: block;
  background: #1f2937;
  color: #e5e7eb;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-family: Consolas, Monaco, monospace;
}

.action-reason {
  font-size: 12px;
  color: var(--muted);
  margin: 8px 0 0;
}

.action-btns {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}

.action-hint {
  font-size: 11px;
  color: var(--muted);
}

.action-result {
  margin: 10px 0 0;
  font-size: 12px;
  white-space: pre-wrap;
  padding: 8px 10px;
  border-radius: 6px;
  background: #f3f4f6;
}

.action-result.ok {
  color: #166534;
  background: #f0fdf4;
}

.action-result.err {
  color: #991b1b;
  background: #fef2f2;
}

.error-tip {
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 13px;
}

.composer {
  display: flex;
  gap: 10px;
  padding: 14px 18px;
  border-top: 1px solid var(--border);
  background: #fff;
  align-items: flex-end;
}

.send-btn {
  height: 54px;
  width: 92px;
}
</style>
