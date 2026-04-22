<template>
  <div id="app-root">
    <div v-if="llmError" class="llm-alert">
      <span class="llm-alert-icon">⚠</span>
      <span class="llm-alert-msg">
        大模型不可用，语义切割和测试功能将无法运行。
        <span class="llm-alert-detail">{{ llmError }}</span>
      </span>
      <button class="llm-alert-close" @click="llmError = ''">✕</button>
    </div>
    <nav class="nav">
      <div class="nav-brand">🎧 听力训练</div>
      <div class="nav-links">
        <router-link to="/library">训练库</router-link>
        <router-link to="/upload">上传</router-link>
        <router-link to="/vocabulary">词汇</router-link>
      </div>
      <div class="nav-level" @click="router.push('/onboarding')">
        <span class="level-dot" :class="{ 'level-dot-error': llmError }"></span>
        <span>{{ userStore.levelLabel || `Level ${userStore.levelScore}` }}</span>
      </div>
    </nav>
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import api from '@/api'

const router = useRouter()
const userStore = useUserStore()
const llmError = ref('')

onMounted(async () => {
  userStore.fetchLevel()
  try {
    const res = await api.get('/health/llm')
    if (res.status === 'error') {
      llmError.value = res.detail || '连接失败'
    }
  } catch (e) {
    llmError.value = '无法连接后端服务'
  }
})
</script>

<style scoped>
#app-root {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.llm-alert {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  background: #7a3a00;
  border-bottom: 1px solid #a85000;
  color: #ffd199;
  font-size: 13px;
  position: sticky;
  top: 0;
  z-index: 101;
}
.llm-alert-icon { font-size: 16px; flex-shrink: 0; }
.llm-alert-msg { flex: 1; }
.llm-alert-detail {
  margin-left: 8px;
  opacity: 0.75;
  font-family: monospace;
  font-size: 11px;
  word-break: break-all;
}
.llm-alert-close {
  background: none;
  border: none;
  color: #ffd199;
  font-size: 14px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}
.llm-alert-close:hover { background: rgba(255,255,255,0.1); }

.nav {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 24px;
  height: 56px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-brand {
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
  white-space: nowrap;
}

.nav-links {
  display: flex;
  gap: 4px;
}

.nav-links a {
  padding: 6px 14px;
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 14px;
  transition: all 0.15s;
}

.nav-links a:hover,
.nav-links a.router-link-active {
  background: rgba(79, 110, 247, 0.15);
  color: var(--accent);
}

.nav-level {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px 10px;
  border-radius: 8px;
  transition: background 0.15s;
}
.nav-level:hover { background: var(--bg-card-hover); }

.level-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--accent);
}
.level-dot-error { background: #e74c3c; }

.main-content {
  flex: 1;
  padding: 24px;
  max-width: 960px;
  margin: 0 auto;
  width: 100%;
}
</style>
