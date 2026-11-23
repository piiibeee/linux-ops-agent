<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">流程编排</h2>
        <p class="page-desc">
          这张图不是画的示意图，是后端从编译后的 LangGraph 里读出来的真实拓扑——
          改了 <code>graph.py</code>，这里会跟着变。
        </p>
      </div>
      <div class="head-right">
        <el-tag :type="source === 'runtime' ? 'success' : 'warning'" size="small" effect="light">
          {{ source === 'runtime' ? '实时读取' : '静态兜底' }}
        </el-tag>
        <el-button :loading="loading" @click="load">重新加载</el-button>
      </div>
    </div>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="mb12"
    />

    <div class="board-wrap">
      <div class="board" v-loading="loading">
        <VueFlow
          :nodes="nodes"
          :edges="edges"
          :default-viewport="{ zoom: 0.85 }"
          :min-zoom="0.4"
          :max-zoom="1.6"
          fit-view-on-init
          @node-click="onNodeClick"
        >
          <Background pattern-color="#dbe3ec" :gap="18" />
          <Controls />
        </VueFlow>
      </div>

      <!-- 节点说明面板：点节点才出现，避免画布还没渲染就空着 -->
      <aside class="inspector">
        <template v-if="picked">
          <p class="inspector-title">{{ picked.label }}</p>
          <p class="inspector-id">id: {{ picked.id }}</p>
          <p class="inspector-body">{{ nodeDesc(picked.id) }}</p>

          <p class="inspector-sub">出边</p>
          <ul class="edge-list">
            <li v-if="!outEdges.length" class="muted">（无，终止节点）</li>
            <li v-for="e in outEdges" :key="e.id">
              <span class="edge-target">→ {{ labelOf(e.target) }}</span>
              <span v-if="e.label" class="edge-cond">{{ e.label }}</span>
            </li>
          </ul>

          <p class="inspector-sub">入边</p>
          <ul class="edge-list">
            <li v-if="!inEdges.length" class="muted">（无，起始节点）</li>
            <li v-for="e in inEdges" :key="e.id">
              <span class="edge-target">← {{ labelOf(e.source) }}</span>
            </li>
          </ul>
        </template>
        <template v-else>
          <p class="inspector-title">点一个节点</p>
          <p class="inspector-body">
            画布上每个节点对应 <code>graph.py</code> 里的一个函数。点击可看它做什么、连到哪。
          </p>
          <p class="inspector-sub">图例</p>
          <ul class="edge-list">
            <li><span class="legend-line solid"></span> 固定边（一定会走）</li>
            <li><span class="legend-line dashed"></span> 条件边（由 should_continue 决定）</li>
          </ul>
        </template>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { VueFlow, MarkerType, Position } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { fetchGraph } from '../api'

const loading = ref(false)
const error = ref('')
const source = ref('runtime')
const rawNodes = ref([])
const rawEdges = ref([])
const picked = ref(null)

/**
 * 手工布局：按"层"排。
 * 为什么不自动布局：这个图只有 5 个节点，自动布局算法（dagre）会引入额外依赖，
 * 而且排出来的位置未必比手排好看。节点变多（>15）再换 dagre 不迟。
 */
const LAYOUT = {
  __start__: { x: 300, y: 10 },
  agent: { x: 300, y: 150 },
  tools: { x: 80, y: 310 },
  finalize: { x: 520, y: 310 },
  __end__: { x: 300, y: 460 },
}

// 节点说明：跟 graph.py 里的实现一一对应，改代码时这里也要跟着改
const NODE_DESC = {
  __start__: '图的入口。一轮对话从用户输入开始。',
  agent: 'call_model：把系统提示 + 历史消息发给 LLM，得到一条 AIMessage。它可能带 tool_calls，也可能直接是答案。',
  tools: 'run_tools：执行 agent 点名的工具。这里有去重、单轮调用数上限、异常兜底三道保护。',
  finalize: 'finalize：工具轮数预算用尽时进入，换一个不绑工具的模型强制收尾，避免把英文报错抛给值班的人。',
  __end__: '结束。把最终回答和待确认操作推给前端。',
}

const nodes = computed(() =>
  rawNodes.value.map((n) => ({
    id: n.id,
    label: n.label,
    position: LAYOUT[n.id] || { x: 300, y: 600 },
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    class: 'flow-node-' + (n.id.includes('__') ? 'terminal' : n.id),
    data: { desc: nodeDesc(n.id) },
  }))
)

const edges = computed(() =>
  rawEdges.value.map((e, i) => ({
    id: `e-${i}-${e.source}-${e.target}`,
    source: e.source,
    target: e.target,
    label: e.label || '',
    type: 'default',
    animated: !!e.conditional,
    labelBgPadding: [6, 3],
    labelBgBorderRadius: 4,
    style: e.conditional
      ? { stroke: '#94a3b8', strokeDasharray: '4 4' }
      : { stroke: '#64748b' },
    markerEnd: MarkerType.ArrowClosed,
  }))
)

const outEdges = computed(() =>
  rawEdges.value.filter((e) => e.source === picked.value?.id)
)
const inEdges = computed(() => rawEdges.value.filter((e) => e.target === picked.value?.id))

function nodeDesc(id) {
  return NODE_DESC[id] || '（暂无说明，可能是新加的节点）'
}

function labelOf(id) {
  return rawNodes.value.find((n) => n.id === id)?.label || id
}

function onNodeClick({ node }) {
  picked.value = { id: node.id, label: node.label }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchGraph()
    rawNodes.value = data.nodes || []
    rawEdges.value = data.edges || []
    source.value = data.source || 'static'
    picked.value = null
  } catch (e) {
    error.value = e.message
    rawNodes.value = []
    rawEdges.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page {
  padding: 18px 20px 28px;
  height: 100%;
  display: flex;
  flex-direction: column;
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
  max-width: 620px;
  line-height: 1.6;
}

.page-desc code {
  background: #f1f5f9;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mb12 {
  margin-bottom: 12px;
}

.board-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
}

.board {
  flex: 1;
  min-width: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  /* VueFlow 需要一个有确定高度的容器，否则画布高度会塌成 0 */
  height: calc(100vh - 190px);
  overflow: hidden;
}

.inspector {
  width: 270px;
  flex: 0 0 270px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  height: calc(100vh - 190px);
  overflow: auto;
}

.inspector-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.inspector-id {
  margin: 4px 0 10px;
  font-size: 11px;
  color: #94a3b8;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}

.inspector-body {
  margin: 0;
  font-size: 12px;
  line-height: 1.75;
  color: #475569;
}

.inspector-body code {
  background: #f1f5f9;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.inspector-sub {
  margin: 16px 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
}

.edge-list {
  margin: 0;
  padding-left: 0;
  list-style: none;
  font-size: 12px;
  line-height: 1.9;
}

.edge-target {
  color: #334155;
}

.edge-cond {
  margin-left: 6px;
  padding: 1px 6px;
  background: #f1f5f9;
  border-radius: 10px;
  font-size: 11px;
  color: #64748b;
}

.muted {
  color: #94a3b8;
}

.legend-line {
  display: inline-block;
  width: 18px;
  height: 0;
  border-top: 2px solid #64748b;
  margin-right: 6px;
  vertical-align: middle;
}

.legend-line.dashed {
  border-top-style: dashed;
  border-top-color: #94a3b8;
}

/* 画布节点样式：VueFlow 渲染的节点在组件作用域外，需要 :deep */
:deep(.vue-flow__node-flow-node-agent) {
  background: #eff6ff;
  border: 1px solid #2563eb;
  color: #1e40af;
}

:deep(.vue-flow__node-flow-node-tools) {
  background: #fffbeb;
  border: 1px solid #d97706;
  color: #92400e;
}

:deep(.vue-flow__node-flow-node-finalize) {
  background: #faf5ff;
  border: 1px solid #9333ea;
  color: #6b21a8;
}

:deep(.vue-flow__node-flow-node-terminal) {
  background: #f1f5f9;
  border: 1px dashed #94a3b8;
  color: #475569;
}

:deep(.vue-flow__node) {
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
  min-width: 120px;
  text-align: center;
}
</style>
