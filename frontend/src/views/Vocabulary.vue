<template>
  <div>
    <h2 class="page-title">词汇仪表板</h2>

    <!-- Stats overview -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-num">{{ stats.total }}</div>
        <div class="stat-label">总词汇</div>
      </div>
      <div class="stat-card blue">
        <div class="stat-num">{{ stats.unknown }}</div>
        <div class="stat-label">未知 (蓝)</div>
        <div class="stat-pct">{{ stats.unknown_pct }}%</div>
      </div>
      <div class="stat-card yellow">
        <div class="stat-num">{{ stats.learning }}</div>
        <div class="stat-label">学习中 (黄)</div>
        <div class="stat-pct">{{ stats.learning_pct }}%</div>
      </div>
      <div class="stat-card white">
        <div class="stat-num">{{ stats.mastered }}</div>
        <div class="stat-label">已掌握 (白)</div>
        <div class="stat-pct">{{ stats.mastered_pct }}%</div>
      </div>
    </div>

    <!-- Progress bar -->
    <div class="progress-bar-wrapper card">
      <div class="progress-label">掌握进度</div>
      <div class="progress-bar">
        <div class="progress-seg blue-seg" :style="{ width: stats.unknown_pct + '%' }"></div>
        <div class="progress-seg yellow-seg" :style="{ width: stats.learning_pct + '%' }"></div>
        <div class="progress-seg white-seg" :style="{ width: stats.mastered_pct + '%' }"></div>
      </div>
      <div class="progress-legend">
        <span class="leg-item"><span class="leg-dot blue-dot"></span>未知</span>
        <span class="leg-item"><span class="leg-dot yellow-dot"></span>学习中</span>
        <span class="leg-item"><span class="leg-dot white-dot"></span>已掌握</span>
      </div>
    </div>

    <!-- Filter tabs -->
    <div class="filter-tabs">
      <button v-for="f in filters" :key="f.value"
        :class="['tab', filter === f.value ? 'active' : '']"
        @click="setFilter(f.value)">
        {{ f.label }}
      </button>
    </div>

    <!-- Word list -->
    <div v-if="loading" class="loading-center">
      <div class="loading-spinner"></div>
    </div>
    <div v-else class="word-grid">
      <div
        v-for="w in words"
        :key="w.word"
        class="word-chip"
        :class="`vocab-${w.color}`"
        :title="`掌握概率: ${(w.mastery_prob * 100).toFixed(0)}% · 遇到${w.encounters}次 · 答对${w.correct_count}次`"
      >
        {{ w.word }}
        <span class="word-prob">{{ (w.mastery_prob * 100).toFixed(0) }}%</span>
      </div>
    </div>

    <div v-if="hasMore" class="load-more">
      <button class="btn-secondary" @click="loadMore">加载更多</button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useVocabStore } from '@/stores/vocabulary'

const vocabStore = useVocabStore()
const stats = ref({ total: 0, unknown: 0, learning: 0, mastered: 0, unknown_pct: 0, learning_pct: 0, mastered_pct: 0 })
const words = ref([])
const loading = ref(false)
const filter = ref(null)
const offset = ref(0)
const limit = 200
const hasMore = ref(false)

const filters = [
  { value: null, label: '全部' },
  { value: 'blue', label: '未知' },
  { value: 'yellow', label: '学习中' },
  { value: 'white', label: '已掌握' },
]

async function fetchWords(reset = true) {
  loading.value = true
  if (reset) { offset.value = 0; words.value = [] }
  try {
    const res = await vocabStore.fetchWords(filter.value, limit, offset.value)
    words.value.push(...res.words)
    hasMore.value = words.value.length < res.total
    offset.value += res.words.length
  } finally {
    loading.value = false
  }
}

function setFilter(f) {
  filter.value = f
  fetchWords(true)
}

function loadMore() {
  fetchWords(false)
}

onMounted(async () => {
  stats.value = await vocabStore.fetchStats().then ? await vocabStore.fetchStats() : vocabStore.stats
  // fetchStats updates the store; read directly
  const s = await fetch('/api/vocabulary/stats').then(r => r.json())
  stats.value = s
  fetchWords()
})
</script>

<style scoped>
.page-title { font-size: 22px; font-weight: 700; margin-bottom: 20px; }

.stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  text-align: center;
}
.stat-card.blue { border-color: rgba(59,130,246,0.4); }
.stat-card.yellow { border-color: rgba(245,158,11,0.4); }
.stat-card.white { border-color: rgba(255,255,255,0.2); }
.stat-num { font-size: 28px; font-weight: 700; }
.stat-label { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.stat-pct { font-size: 11px; color: var(--text-muted); }

.progress-bar-wrapper { margin-bottom: 16px; }
.progress-label { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; }
.progress-bar { height: 10px; border-radius: 5px; overflow: hidden; display: flex; background: var(--border); }
.progress-seg { height: 100%; transition: width 0.5s; }
.blue-seg { background: var(--vocab-blue); }
.yellow-seg { background: var(--vocab-yellow); }
.white-seg { background: rgba(255,255,255,0.5); }
.progress-legend { display: flex; gap: 14px; margin-top: 8px; }
.leg-item { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-muted); }
.leg-dot { width: 8px; height: 8px; border-radius: 50%; }
.blue-dot { background: var(--vocab-blue); }
.yellow-dot { background: var(--vocab-yellow); }
.white-dot { background: rgba(255,255,255,0.5); }

.filter-tabs { display: flex; gap: 4px; margin-bottom: 16px; }
.tab {
  padding: 5px 14px; border-radius: 8px; font-size: 13px;
  background: var(--bg-card); border: 1px solid var(--border);
  color: var(--text-muted);
}
.tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }

.loading-center { display: flex; justify-content: center; padding: 40px; }

.word-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.word-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 14px;
  cursor: default;
}
.word-prob { font-size: 10px; opacity: 0.7; }

.load-more { text-align: center; margin-top: 20px; }

@media (max-width: 600px) {
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
