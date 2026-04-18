<template>
  <div>
    <div class="header-row">
      <div>
        <h2 class="page-title">训练库</h2>
        <p class="subtitle">
          当前水平 <strong>{{ userStore.levelScore.toFixed(1) }}</strong>（{{ userStore.levelLabel }}）— 显示难度
          {{ userStore.levelScore.toFixed(1) }} ~ {{ (userStore.levelScore + 1).toFixed(1) }} 的片段
        </p>
      </div>
      <div class="header-actions">
        <div class="mode-tabs">
          <button :class="['tab', mode === 'training' ? 'active' : '']" @click="setMode('training')">新卡片</button>
          <button :class="['tab', mode === 'review' ? 'active' : '']" @click="setMode('review')">复习</button>
          <button :class="['tab', mode === 'all' ? 'active' : '']" @click="setMode('all')">全部</button>
        </div>
        <button v-if="cards.length" class="btn-delete-all" @click="deleteAll">删除全部</button>
      </div>
    </div>

    <div v-if="loading" class="loading-center">
      <div class="loading-spinner"></div>
      <span>加载中...</span>
    </div>

    <div v-else-if="!cards.length" class="empty">
      <div class="empty-icon">📭</div>
      <div v-if="mode === 'training'">当前水平下没有合适的片段，<router-link to="/upload">上传新内容</router-link> 或调整水平</div>
      <div v-else-if="mode === 'review'">今天没有需要复习的卡片</div>
      <div v-else>还没有内容，<router-link to="/upload">去上传</router-link></div>
    </div>

    <div v-else>
      <div v-for="group in groupedCards" :key="group.content_id" class="video-group">
        <div class="video-title-row">
          <span class="video-title">{{ group.title }}</span>
          <span class="video-count">{{ group.cards.length }} 个片段</span>
        </div>
        <div class="cards-grid">
          <div
            v-for="card in group.cards"
            :key="card.id"
            class="card card-item"
            @click="router.push(`/training/${card.id}`)"
          >
            <div class="card-top">
              <div style="display:flex;align-items:center;gap:6px;">
                <span class="card-number">#{{ card.card_number }}</span>
                <span :class="stateClass(card.card_state)">{{ stateLabel(card.card_state) }}</span>
              </div>
              <div style="display:flex;align-items:center;gap:8px;">
                <DifficultyBadge :score="card.difficulty.total" />
                <button class="btn-delete-card" @click.stop="deleteCard(card.id)">✕</button>
              </div>
            </div>

            <p class="card-text">{{ card.text }}</p>

            <div class="card-dims">
              <div v-for="(val, key) in dimLabels" :key="key" class="dim-item">
                <span class="dim-label">{{ val }}</span>
                <div class="dim-bar">
                  <div class="dim-fill" :style="{ width: card.difficulty[key] * 10 + '%', background: dimColor(card.difficulty[key]) }"></div>
                </div>
                <span class="dim-val">{{ card.difficulty[key] }}</span>
              </div>
            </div>

            <div v-if="card.next_review" class="next-review">
              下次复习：{{ formatDate(card.next_review) }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import DifficultyBadge from '@/components/DifficultyBadge.vue'
import api from '@/api'

const router = useRouter()
const userStore = useUserStore()
const cards = ref([])
const loading = ref(false)
const mode = ref(localStorage.getItem('library_mode') || 'training')

const dimLabels = {
  speech_rate: '语速',
  phonetics: '音变',
  vocabulary: '词汇',
  complexity: '句法',
  audio_quality: '音质',
}

const groupedCards = computed(() => {
  const map = new Map()
  for (const card of cards.value) {
    if (!map.has(card.content_id)) {
      map.set(card.content_id, { content_id: card.content_id, title: card.content_title, cards: [] })
    }
    map.get(card.content_id).cards.push(card)
  }
  return Array.from(map.values())
})

async function fetchCards() {
  loading.value = true
  try {
    cards.value = await api.get('/cards/', { params: { mode: mode.value } })
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function deleteCard(id) {
  try {
    await api.delete(`/cards/${id}`)
    cards.value = cards.value.filter(c => c.id !== id)
  } catch (e) {
    console.error(e)
  }
}

async function deleteAll() {
  if (!confirm(`确定要删除全部 ${cards.value.length} 张卡片吗？此操作不可恢复。`)) return
  try {
    await api.delete('/cards/all')
    cards.value = []
  } catch (e) {
    console.error(e)
  }
}

function setMode(m) {
  mode.value = m
  localStorage.setItem('library_mode', m)
  fetchCards()
}

function stateLabel(s) {
  return { new: '新卡片', learning: '学习中', review: '待复习', mastered: '已掌握' }[s] || s
}

function stateClass(s) {
  return {
    new: 'badge badge-blue',
    learning: 'badge badge-yellow',
    review: 'badge badge-purple',
    mastered: 'badge badge-green',
  }[s] || 'badge'
}

function dimColor(val) {
  if (val <= 3) return '#27ae60'
  if (val <= 6) return '#f39c12'
  return '#e74c3c'
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString('zh-CN')
}

onMounted(fetchCards)
</script>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}
.page-title { font-size: 22px; font-weight: 700; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }

.header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.mode-tabs { display: flex; gap: 4px; }
.tab {
  padding: 6px 14px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 13px;
}
.tab.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.loading-center { display: flex; gap: 10px; align-items: center; padding: 40px; justify-content: center; color: var(--text-muted); }

.empty { text-align: center; padding: 60px 20px; color: var(--text-muted); }
.empty-icon { font-size: 48px; margin-bottom: 12px; }

.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}

.card-item {
  cursor: pointer;
  transition: all 0.15s;
}
.card-item:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.card-text {
  font-size: 14px;
  line-height: 1.7;
  margin-bottom: 14px;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-dims { display: flex; flex-direction: column; gap: 5px; }
.dim-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.dim-label { width: 28px; color: var(--text-muted); }
.dim-bar {
  flex: 1;
  height: 4px;
  background: var(--border);
  border-radius: 2px;
  overflow: hidden;
}
.dim-fill { height: 100%; border-radius: 2px; transition: width 0.3s; }
.dim-val { width: 20px; text-align: right; color: var(--text-muted); }

.next-review { margin-top: 10px; font-size: 12px; color: var(--text-muted); }

.video-group { margin-bottom: 28px; }
.video-title-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.video-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.video-count { font-size: 12px; color: var(--text-muted); white-space: nowrap; }

.card-number {
  font-size: 12px;
  font-weight: 700;
  color: var(--accent);
  min-width: 28px;
}

.btn-delete-all {
  padding: 6px 12px;
  border-radius: 8px;
  background: transparent;
  border: 1px solid #e74c3c;
  color: #e74c3c;
  font-size: 13px;
  cursor: pointer;
}
.btn-delete-all:hover { background: #e74c3c; color: #fff; }

.btn-delete-card {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
.btn-delete-card:hover { background: #e74c3c; border-color: #e74c3c; color: #fff; }
</style>
