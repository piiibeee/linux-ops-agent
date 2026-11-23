<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">变更审批</h2>
        <p class="page-desc">
          Agent 只能<b>提交</b>变更申请，不能自己执行。这里的每一次批准，都会写进审计日志。
        </p>
      </div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <!-- 状态机：把"一张单子会经历什么"画出来，比在表格里看状态标签清楚 -->
    <div class="flow">
      <template v-for="(step, i) in STATE_FLOW" :key="step.key">
        <div class="flow-node" :class="[step.key, { active: activeCounts[step.key] > 0 }]">
          <span class="flow-name">{{ step.label }}</span>
          <span class="flow-count">{{ activeCounts[step.key] || 0 }}</span>
        </div>
        <span v-if="i < STATE_FLOW.length - 1" class="flow-arrow">→</span>
      </template>
      <span class="flow-hint">pending 之后只能走一次：要么 executed，要么 failed</span>
    </div>

    <!-- 筛选 -->
    <div class="filter-bar">
      <el-radio-group v-model="statusFilter" @change="load">
        <el-radio-button value="">全部 ({{ allTotal }})</el-radio-button>
        <el-radio-button value="pending">待确认 ({{ counts.pending || 0 }})</el-radio-button>
        <el-radio-button value="executed">已执行 ({{ counts.executed || 0 }})</el-radio-button>
        <el-radio-button value="failed">失败 ({{ counts.failed || 0 }})</el-radio-button>
      </el-radio-group>
      <span class="spacer"></span>
      <el-tag v-if="!auth.canApprove" type="warning" effect="light" size="small">
        当前角色「{{ auth.roleLabel }}」无审批权限
      </el-tag>
    </div>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="mb12"
    />

    <el-table
      v-loading="loading"
      :data="rows"
      border
      stripe
      size="small"
      empty-text="还没有变更单。去对话页让它处理一次故障，比如「日志占满了，帮我清理」"
    >
      <el-table-column label="提交时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>

      <el-table-column prop="action" label="操作" width="150" />

      <el-table-column prop="target" label="目标" width="130">
        <template #default="{ row }">{{ row.target || '—' }}</template>
      </el-table-column>

      <el-table-column label="命令" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <code class="mono">{{ row.command }}</code>
        </template>
      </el-table-column>

      <el-table-column label="状态" width="98">
        <template #default="{ row }">
          <el-tag :type="statusMeta(row.status).type" size="small">
            {{ statusMeta(row.status).label }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="168" align="center">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openDetail(row)">
            详情
          </el-button>
          <el-tooltip
            :disabled="auth.canApprove || row.status !== 'pending'"
            content="值班员只能发起，不能批准变更。切换为管理员后可用。"
            placement="top"
          >
            <span>
              <el-button
                type="primary"
                size="small"
                :disabled="!auth.canApprove || row.status !== 'pending' || approving === row.action_id"
                :loading="approving === row.action_id"
                @click="doApprove(row)"
              >
                批准执行
              </el-button>
            </span>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawer" title="变更单详情" size="540px">
      <template v-if="current">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="单号">{{ current.action_id }}</el-descriptions-item>
          <el-descriptions-item label="操作">{{ current.action }}</el-descriptions-item>
          <el-descriptions-item label="目标">{{ current.target || '—' }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusMeta(current.status).type" size="small">
              {{ statusMeta(current.status).label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="提交时间">{{ fmtTime(current.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="开始执行">{{ fmtTime(current.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ fmtTime(current.finished_at) }}</el-descriptions-item>
          <el-descriptions-item label="会话">{{ current.session_id || '—' }}</el-descriptions-item>
        </el-descriptions>

        <p class="block-title">Agent 给出的理由</p>
        <p class="reason">{{ current.reason || '（未填写）' }}</p>

        <p class="block-title">将执行的命令</p>
        <pre class="code-block">{{ current.command }}</pre>

        <p class="block-title">影响说明</p>
        <p class="reason">{{ current.description || '—' }}</p>

        <template v-if="current.output">
          <p class="block-title">执行输出</p>
          <pre class="code-block">{{ current.output }}</pre>
        </template>

        <template v-if="current.error">
          <p class="block-title err">错误</p>
          <pre class="code-block">{{ current.error }}</pre>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approveAction, fetchActions } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const rows = ref([])
const counts = ref({})
const allTotal = ref(0)
const loading = ref(false)
const error = ref('')
const statusFilter = ref('')
const approving = ref('')
const drawer = ref(false)
const current = ref(null)

// 状态机定义：顺序即流转顺序，key 与后端 status 字段一致
const STATE_FLOW = [
  { key: 'pending', label: '待确认' },
  { key: 'executing', label: '执行中' },
  { key: 'executed', label: '已执行' },
  { key: 'failed', label: '失败' },
]

const STATUS_META = {
  pending: { label: '待确认', type: 'warning' },
  executing: { label: '执行中', type: 'primary' },
  executed: { label: '已执行', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  unknown: { label: '未知', type: 'info' },
}

function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.unknown
}

const activeCounts = computed(() => counts.value)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetchActions(statusFilter.value)
    rows.value = res.items || []
    counts.value = res.counts || {}
    allTotal.value = res.total || 0
  } catch (e) {
    error.value = e.message
    rows.value = []
  } finally {
    loading.value = false
  }
}

async function doApprove(row) {
  // 二次确认不是形式：这条命令会真的在服务器上执行
  try {
    await ElMessageBox.confirm(
      `即将在服务器上执行：\n\n${row.command}\n\n该操作不可撤销，确认继续？`,
      '确认执行变更',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  approving.value = row.action_id
  try {
    const result = await approveAction(row.action_id, auth.role)
    if (result.ok) ElMessage.success('执行成功')
    else ElMessage.warning(result.error || '执行失败，详情见审计日志')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    approving.value = ''
  }
}

function openDetail(row) {
  current.value = row
  drawer.value = true
}

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

onMounted(load)
</script>

<style scoped>
.page {
  padding: 18px 20px 28px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
}

.page-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.page-desc {
  margin: 5px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.flow {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.flow-node {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 6px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  font-size: 12px;
  color: #475569;
}

.flow-node.active.pending {
  background: #fffbeb;
  border-color: #fbbf24;
  color: #92400e;
}

.flow-node.active.executing {
  background: #eff6ff;
  border-color: #60a5fa;
  color: #1d4ed8;
}

.flow-node.active.executed {
  background: #f0fdf4;
  border-color: #4ade80;
  color: #15803d;
}

.flow-node.active.failed {
  background: #fef2f2;
  border-color: #f87171;
  color: #b91c1c;
}

.flow-count {
  font-weight: 600;
}

.flow-arrow {
  color: #cbd5e1;
  font-size: 12px;
}

.flow-hint {
  margin-left: auto;
  font-size: 11px;
  color: #94a3b8;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.spacer {
  flex: 1;
}

.mb12 {
  margin-bottom: 12px;
}

.mono {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
  color: #334155;
}

.block-title {
  margin: 16px 0 6px;
  font-size: 13px;
  font-weight: 600;
}

.block-title.err {
  color: #dc2626;
}

.reason {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #334155;
}

.code-block {
  margin: 0;
  padding: 10px 12px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
