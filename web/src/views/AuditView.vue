<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">审计日志</h2>
        <p class="page-desc">
          Agent 每一次工具调用都在这里留痕：谁发起的、查了什么、结果如何、花了多久。
        </p>
      </div>
      <el-button :loading="loading" @click="reload">刷新</el-button>
    </div>

    <!-- 概览：四个指标 + 平均耗时 -->
    <div class="stat-row">
      <div v-for="card in statCards" :key="card.key" class="stat-card">
        <p class="stat-label">{{ card.label }}</p>
        <p class="stat-value" :class="card.tone">{{ card.value }}</p>
      </div>
    </div>

    <!-- 筛选：每个控件都对应后端一个真实参数 -->
    <div class="filter-bar">
      <el-select
        v-model="filters.tool"
        placeholder="全部工具"
        clearable
        size="default"
        class="w180"
      >
        <el-option v-for="t in toolOptions" :key="t" :label="t" :value="t" />
      </el-select>

      <el-select
        v-model="filters.status"
        placeholder="全部状态"
        clearable
        size="default"
        class="w140"
      >
        <el-option
          v-for="s in statusOptions"
          :key="s.value"
          :label="s.label"
          :value="s.value"
        />
      </el-select>

      <el-input
        v-model="filters.keyword"
        placeholder="搜参数或输出内容，如 rm -rf"
        clearable
        class="w260"
        @keyup.enter="search(1)"
      />

      <el-button type="primary" @click="search(1)">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
      <span class="spacer"></span>
      <el-button :disabled="!rows.length" @click="exportCsv">导出当前页 CSV</el-button>
    </div>

    <!-- 表格 -->
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
      empty-text="暂无审计记录，先去对话页让 Agent 查一次服务器"
      @row-click="openDetail"
    >
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
      </el-table-column>

      <el-table-column prop="tool" label="工具" width="150" show-overflow-tooltip />

      <el-table-column label="状态" width="92">
        <template #default="{ row }">
          <el-tag :type="statusMeta(row.status).type" size="small" effect="light">
            {{ statusMeta(row.status).label }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="耗时" width="92" align="right">
        <template #default="{ row }">
          <span :class="{ slow: row.elapsed_ms > 3000 }">{{ fmtMs(row.elapsed_ms) }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="session_id" label="会话" width="130" show-overflow-tooltip />

      <el-table-column label="参数" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <code v-if="auth.canSeeSensitive" class="mono">{{ brief(row.args) }}</code>
          <span v-else class="masked">···（值班员不可见）</span>
        </template>
      </el-table-column>

      <el-table-column label="" width="76" align="center">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click.stop="openDetail(row)">
            详情
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <span class="pager-tip">共 {{ total }} 条</span>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="sizes, prev, pager, next, jumper"
        background
        @current-change="load"
        @size-change="search(1)"
      />
    </div>

    <!-- 详情抽屉：审计的价值在于"能追到原话"，所以参数和输出都完整展示 -->
    <el-drawer v-model="drawer" title="调用详情" size="520px">
      <template v-if="current">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="记录 ID">{{ current.id }}</el-descriptions-item>
          <el-descriptions-item label="时间">{{ fmtTime(current.ts) }}</el-descriptions-item>
          <el-descriptions-item label="工具">{{ current.tool }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusMeta(current.status).type" size="small">
              {{ statusMeta(current.status).label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="耗时">{{ fmtMs(current.elapsed_ms) }}</el-descriptions-item>
          <el-descriptions-item label="会话">{{ current.session_id }}</el-descriptions-item>
          <el-descriptions-item label="发起方">{{ current.actor || 'agent' }}</el-descriptions-item>
        </el-descriptions>

        <p class="block-title">调用参数</p>
        <pre class="code-block">{{ pretty(current.args) }}</pre>

        <p class="block-title">返回内容</p>
        <pre class="code-block">{{ current.output || '（无输出）' }}</pre>

        <template v-if="current.error">
          <p class="block-title err">错误</p>
          <pre class="code-block">{{ current.error }}</pre>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchAudit, fetchAuditStats } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref('')
const stats = ref({ total: 0, today: 0, failed: 0, blocked: 0, avg_elapsed_ms: 0 })
const filters = reactive({ tool: '', status: '', keyword: '' })
const serverFilters = ref({ tools: [], statuses: [] })

const drawer = ref(false)
const current = ref(null)

// 状态枚举集中定义：表格、筛选、标签颜色都从这里取，避免三处各写一遍
const STATUS_META = {
  ok: { label: '成功', type: 'success' },
  error: { label: '失败', type: 'danger' },
  blocked: { label: '被拦截', type: 'warning' },
  running: { label: '执行中', type: 'info' },
  action: { label: '变更事件', type: 'primary' },
  approved: { label: '已批准', type: 'success' },
  executed: { label: '已执行', type: 'success' },
  failed: { label: '执行失败', type: 'danger' },
  pending: { label: '待确认', type: 'warning' },
  unknown: { label: '未知', type: 'info' },
}

function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.unknown
}

const statusOptions = computed(() => [
  { value: 'ok', label: '成功' },
  { value: 'error', label: '失败' },
  { value: 'blocked', label: '被拦截' },
  { value: 'running', label: '执行中' },
])

const toolOptions = computed(() => serverFilters.value.tools || [])

const statCards = computed(() => [
  { key: 'total', label: '累计调用', value: stats.value.total, tone: '' },
  { key: 'today', label: '今日调用', value: stats.value.today, tone: '' },
  { key: 'failed', label: '失败', value: stats.value.failed, tone: 'bad' },
  { key: 'blocked', label: '被拦截', value: stats.value.blocked, tone: 'warn' },
  {
    key: 'avg',
    label: '平均耗时',
    value: fmtMs(stats.value.avg_elapsed_ms),
    tone: '',
  },
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetchAudit({
      page: page.value,
      page_size: pageSize.value,
      ...filters,
    })
    rows.value = res.items || []
    total.value = res.total || 0
    if (res.filters) serverFilters.value = res.filters
  } catch (e) {
    error.value = e.message
    rows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    stats.value = await fetchAuditStats()
  } catch {
    // 概览失败不影响主表格，静默即可
  }
}

function search(nextPage = 1) {
  page.value = nextPage
  load()
  loadStats()
}

function reload() {
  load()
  loadStats()
}

function resetFilters() {
  filters.tool = ''
  filters.status = ''
  filters.keyword = ''
  search(1)
}

function openDetail(row) {
  current.value = row
  drawer.value = true
}

// ── 展示层格式化 ──────────────────────────────────────
function fmtTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function fmtMs(ms) {
  if (ms === undefined || ms === null) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function brief(args) {
  if (!args || !Object.keys(args).length) return '—'
  return JSON.stringify(args)
}

function pretty(args) {
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

/**
 * 导出当前页。为什么只导当前页而不是全部：
 * 全量导出需要后端分页循环拉取，容易一次拉爆浏览器内存；
 * 真实场景应该由后端出导出接口（流式写 CSV）。这里保留最小实现并在文档里标明。
 */
function exportCsv() {
  if (!rows.value.length) {
    ElMessage.info('当前页没有数据')
    return
  }
  const header = ['时间', '工具', '状态', '耗时(ms)', '会话', '参数', '输出']
  const lines = [header.join(',')]
  for (const r of rows.value) {
    const cells = [
      fmtTime(r.ts),
      r.tool,
      r.status,
      r.elapsed_ms ?? '',
      r.session_id,
      brief(r.args),
      (r.output || '').replace(/\n/g, ' '),
    ].map((c) => `"${String(c).replace(/"/g, '""')}"`)
    lines.push(cells.join(','))
  }
  // 加 BOM，否则 Excel 打开中文是乱码
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  load()
  loadStats()
})
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

.stat-row {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
}

.stat-card {
  flex: 1;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}

.stat-label {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.stat-value {
  margin: 6px 0 0;
  font-size: 22px;
  font-weight: 600;
  line-height: 1.1;
}

.stat-value.bad {
  color: #dc2626;
}

.stat-value.warn {
  color: #d97706;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.spacer {
  flex: 1;
}

.w140 {
  width: 140px;
}

.w180 {
  width: 180px;
}

.w260 {
  width: 260px;
}

.mb12 {
  margin-bottom: 12px;
}

.mono {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
  color: #334155;
}

.masked {
  color: #94a3b8;
  font-size: 12px;
}

.slow {
  color: #d97706;
  font-weight: 500;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

.pager-tip {
  font-size: 12px;
  color: var(--muted);
}

.block-title {
  margin: 16px 0 6px;
  font-size: 13px;
  font-weight: 600;
}

.block-title.err {
  color: #dc2626;
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
