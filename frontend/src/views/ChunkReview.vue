<template>
  <div class="chunk-review-page">
    <h2 class="page-title">语块复习</h2>

    <div class="stats-row" v-if="stats.total > 0">
      <div class="stat-item">
        <span class="stat-num">{{ stats.total }}</span>
        <span class="stat-label">已提取语块</span>
      </div>
      <div class="stat-item">
        <span class="stat-num">{{ stats.encountered }}</span>
        <span class="stat-label">已接触</span>
      </div>
    </div>

    <div class="coming-soon card">
      <div class="coming-soon-icon">🧩</div>
      <h3>听音识义复习</h3>
      <p>功能开发中，即将上线。</p>
      <p class="sub">上线后你可以在此复习所有接触过的语块——播放发音，选择中文含义。</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '@/api'

const stats = ref({ total: 0, encountered: 0 })

onMounted(async () => {
  try {
    stats.value = await api.get('/chunks/stats')
  } catch {}
})
</script>

<style scoped>
.chunk-review-page { max-width: 600px; margin: 0 auto; }
.page-title { font-size: 22px; font-weight: 700; margin-bottom: 20px; }

.stats-row {
  display: flex;
  gap: 24px;
  margin-bottom: 24px;
}
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 28px;
}
.stat-num { font-size: 32px; font-weight: 700; }
.stat-label { font-size: 12px; color: var(--text-muted); margin-top: 4px; }

.coming-soon {
  text-align: center;
  padding: 48px 24px;
}
.coming-soon-icon { font-size: 48px; margin-bottom: 16px; }
.coming-soon h3 { font-size: 20px; font-weight: 700; margin-bottom: 10px; }
.coming-soon p { color: var(--text-muted); margin-bottom: 6px; }
.coming-soon .sub { font-size: 13px; }
</style>
