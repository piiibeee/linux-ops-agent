import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

/**
 * 角色状态。
 *
 * 为什么用 Pinia 而不是组件内部 ref：
 * 角色要跨三个页面共享（审批页看按钮、对话页看能否确认、审计页看是否显示敏感参数），
 * 放组件里就得靠 props 一层层传或者事件总线，很别扭。
 *
 * 为什么存 localStorage：
 * 刷新页面角色不该被重置，否则每次都要重新选，这是 B 端的基本预期。
 *
 * 注意：这只是"演示用的角色开关"，不是真实鉴权。
 * 真正的 RBAC 必须由后端校验 —— 代码里 approve 接口显式接收 approver 参数，
 * 将来接真实登录时，把参数改成从 token 解析即可，前端这段逻辑不用动。
 */
export const useAuthStore = defineStore('auth', () => {
  const role = ref(localStorage.getItem('ops-agent-role') || 'admin')

  // 角色枚举集中在这里，页面里不要散写字符串
  const ROLES = {
    admin: { label: '管理员', desc: '可审批变更、可查看完整命令参数' },
    oncall: { label: '值班员', desc: '只能发起诊断，不能审批变更' },
  }

  const roleLabel = computed(() => ROLES[role.value]?.label || role.value)
  /** 唯一的权限判断入口：以后加角色只改这一行 */
  const canApprove = computed(() => role.value === 'admin')
  const canSeeSensitive = computed(() => role.value === 'admin')

  watch(role, (v) => localStorage.setItem('ops-agent-role', v))

  function setRole(next) {
    if (ROLES[next]) role.value = next
  }

  return { role, roleLabel, canApprove, canSeeSensitive, ROLES, setRole }
})
