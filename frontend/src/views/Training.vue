<template>
  <!-- Dictionary popup (fixed, outside training-view) -->
  <Teleport to="body">
    <div v-if="dictPopup"
      class="dict-popup"
      :style="{ left: dictPopup.x + 'px', top: (dictPopup.y - 8) + 'px' }"
      @click.stop
    >
        <div class="dict-header">
          <span class="dict-word">{{ dictPopup.word }}</span>
          <span v-if="dictPopup.phonetic" class="dict-phonetic">{{ dictPopup.phonetic }}</span>
          <button class="dict-speak" title="朗读" @click="speakWord(dictPopup.word)">🔊</button>
          <button class="dict-close" @click="closeDictPopup">✕</button>
        </div>
        <div v-if="dictPopup.loading" class="dict-loading">查询中…</div>
        <div v-else-if="dictPopup.entries?.length" class="dict-entries">
          <div v-for="(entry, i) in dictPopup.entries" :key="i" class="dict-entry">
            <span v-if="entry.pos" class="dict-pos">{{ entry.pos }}</span>
            <span class="dict-meanings">{{ entry.meanings.join('；') }}</span>
          </div>
        </div>
        <div v-else class="dict-loading">未找到释义</div>
      </div>
  </Teleport>

  <div v-if="card" class="training-view">
    <!-- Header -->
    <div class="training-header">
      <button class="btn-secondary" @click="router.push('/library')">← 返回</button>
      <div class="header-center">
        <DifficultyBadge :score="card.difficulty.total" />
        <span class="card-state-label">{{ stateLabel(card.card.state) }}</span>
      </div>
      <div class="gate-indicators">
        <span :class="['gate', card.card.shadow_streak >= 3 ? 'done' : '']" title="Gate 1: 跟读">①</span>
        <span :class="['gate', card.card.gen_passed ? 'done' : '']" title="Gate 2: 泛化">②</span>
        <span :class="['gate', card.card.stress_passed ? 'done' : '']" title="Gate 3: 压力">③</span>
        <span :class="['gate', card.card.state === 'mastered' ? 'done' : '']" title="Gate 4: SRS">④</span>
      </div>
    </div>

    <!-- Layer progress -->
    <div class="layer-tabs">
      <button
        v-for="(l, i) in layers"
        :key="i"
        :class="['layer-tab', currentLayer === i ? 'active' : '', i > maxLayer ? 'locked' : '']"
        :disabled="i > maxLayer"
        @click="currentLayer = i"
      >
        {{ l.label }}
      </button>
    </div>

    <!-- Audio player -->
    <AudioPlayer
      :src="audioSrc"
      :speed="layerSpeed"
      :add-noise="layerNoise"
      @ended="onAudioEnded"
      @timeupdate="t => currentPlayTime = t"
    />

    <!-- Hidden audio element for chunk playback (seek + play a sub-range) -->
    <audio ref="chunkAudioEl" :src="audioSrc" preload="auto" style="display:none" />

    <!-- Layer content -->
    <div class="layer-content card">
      <!-- Layer 0: Listen only -->
      <div v-if="currentLayer === 0" class="listen-only">
        <div class="listen-hint">🎧 只听，不看文字。</div>
        <button class="btn-primary" @click="unlockLayer(1)">我听了，继续</button>
      </div>

      <!-- Layer 1: Show text + phonetic annotations -->
      <div v-if="currentLayer === 1">
        <div class="text-toolbar">
          <button :class="['btn-hide-toggle', wordsHidden ? 'active' : '']" @click="wordsHidden = !wordsHidden">
            {{ wordsHidden ? '👁 显示文字' : '🙈 隐藏文字' }}
          </button>
        </div>
        <div class="annotated-text">
          <span
            v-for="(tok, i) in annotatedTokens"
            :key="i"
            :class="[tok.phenoClass, tok.wordIdx >= 0 && tok.wordIdx === activeWordIdx ? 'word-active' : '', tok.wordIdx >= 0 && tok.wordIdx < activeWordIdx ? 'word-past' : '', tok.wordIdx >= 0 ? 'word-clickable' : '', tok.wordIdx >= 0 && wordsHidden ? 'word-hidden' : '']"
            :title="tok.phenoTip"
            @click="onWordClick($event, tok)"
          >{{ tok.text }}</span>
        </div>
        <div v-if="card.phonetic_annotations?.length" class="pheno-legend">
          <div v-for="p in uniquePhenomena" :key="p.type" class="pheno-item">
            <span :class="`badge badge-${phenoColor(p.type)}`">{{ p.label }}</span>
            <span class="pheno-info">{{ p.info }}</span>
          </div>
        </div>
        <div v-if="card.explanation" class="explanation-box">
          <div class="explanation-label">📚 语言讲解</div>
          <div class="explanation-text">{{ card.explanation }}</div>
        </div>
        <ChunkBar :chunks="card.chunks" @play="playChunk" :playing="playingChunk" />
        <button class="btn-primary" @click="unlockLayer(2)">继续 →</button>
      </div>

      <!-- Layer 2: Slow playback -->
      <div v-if="currentLayer === 2">
        <div class="text-toolbar">
          <span class="listen-hint" style="margin:0">🐢 慢速播放 (0.75x)，感受发音节奏</span>
          <button :class="['btn-hide-toggle', wordsHidden ? 'active' : '']" @click="wordsHidden = !wordsHidden">
            {{ wordsHidden ? '👁 显示文字' : '🙈 隐藏文字' }}
          </button>
        </div>
        <div class="annotated-text">
          <span
            v-for="(tok, i) in annotatedTokens"
            :key="i"
            :class="[tok.phenoClass, tok.wordIdx >= 0 && tok.wordIdx === activeWordIdx ? 'word-active' : '', tok.wordIdx >= 0 && tok.wordIdx < activeWordIdx ? 'word-past' : '', tok.wordIdx >= 0 ? 'word-clickable' : '', tok.wordIdx >= 0 && wordsHidden ? 'word-hidden' : '']"
            :title="tok.phenoTip"
            @click="onWordClick($event, tok)"
          >{{ tok.text }}</span>
        </div>
        <ChunkBar :chunks="card.chunks" @play="playChunk" :playing="playingChunk" />
        <button class="btn-primary" @click="unlockLayer(3)">继续 →</button>
      </div>

      <!-- Layer 3: Full speed + highlights, then enter gates -->
      <div v-if="currentLayer === 3">
        <div class="text-toolbar">
          <button :class="['btn-hide-toggle', wordsHidden ? 'active' : '']" @click="wordsHidden = !wordsHidden">
            {{ wordsHidden ? '👁 显示文字' : '🙈 隐藏文字' }}
          </button>
        </div>
        <div class="annotated-text">
          <span
            v-for="(tok, i) in annotatedTokens"
            :key="i"
            :class="[tok.phenoClass, tok.wordIdx >= 0 && tok.wordIdx === activeWordIdx ? 'word-active' : '', tok.wordIdx >= 0 && tok.wordIdx < activeWordIdx ? 'word-past' : '', tok.wordIdx >= 0 ? 'word-clickable' : '', tok.wordIdx >= 0 && wordsHidden ? 'word-hidden' : '']"
            :title="tok.phenoTip"
            @click="onWordClick($event, tok)"
          >{{ tok.text }}</span>
        </div>
        <ChunkBar :chunks="card.chunks" @play="playChunk" :playing="playingChunk" />
        <button class="btn-primary" style="margin-top:16px" @click="currentLayer = 4">进入掌握验证 →</button>
      </div>

      <!-- Gate 1: Shadowing -->
      <div v-if="currentLayer === 4">
        <h3 class="gate-title">Gate 1 — 跟读验证</h3>
        <p class="gate-desc">按住麦克风，模仿音频中的发音（包括连读、弱读）</p>
        <ShadowingRecorder
          :segment-id="card.id"
          :streak="card.card.shadow_streak"
          @pass="onShadowPass"
        />
      </div>

      <!-- Gate 2: Generalization -->
      <div v-if="currentLayer === 5">
        <h3 class="gate-title">Gate 2 — 泛化测试</h3>
        <p class="gate-desc">听一个包含相同语音规律的全新句子，然后听写</p>
        <GeneralizationTest
          :segment-id="card.id"
          @pass="onGenPass"
        />
      </div>

      <!-- Gate 3: Stress test -->
      <div v-if="currentLayer === 6">
        <h3 class="gate-title">Gate 3 — 压力测试</h3>
        <p class="gate-desc">在噪音或加速条件下验证是否真正自动化</p>
        <StressTest
          :segment-id="card.id"
          :segment-text="card.text"
          @pass="onStressPass"
        />
      </div>

      <!-- All gates passed -->
      <div v-if="currentLayer === 7" class="complete">
        <div class="complete-icon">✅</div>
        <h3>三关全部通过！</h3>
        <p>卡片已进入 SRS 复习队列，将在 {{ card.card.interval_days }} 天后复习</p>
        <button class="btn-primary" @click="router.push('/library')">继续下一张</button>
      </div>
    </div>
  </div>

  <div v-else-if="loading" class="loading-center">
    <div class="loading-spinner"></div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api'
import DifficultyBadge from '@/components/DifficultyBadge.vue'
import AudioPlayer from '@/components/AudioPlayer.vue'
import ShadowingRecorder from '@/components/ShadowingRecorder.vue'
import GeneralizationTest from '@/components/GeneralizationTest.vue'
import StressTest from '@/components/StressTest.vue'
import ChunkBar from '@/components/ChunkBar.vue'

const route = useRoute()
const router = useRouter()
const card = ref(null)
const loading = ref(true)
const currentLayer = ref(0)
const maxLayer = ref(0)
const currentPlayTime = ref(0)
const wordsHidden = ref(false)

// Chunk playback
const chunkAudioEl = ref(null)
const playingChunk = ref('')

// Dictionary popup
const dictPopup = ref(null)   // { word, phonetic, entries, x, y } | null
const dictLoading = ref(false)
const dictCache = new Map()

async function onWordClick(event, tok) {
  event.stopPropagation()
  if (tok.wordIdx < 0) return
  const word = tok.text.replace(/[^A-Za-z0-9']/g, '').toLowerCase()
  if (!word) return

  const rect = event.target.getBoundingClientRect()
  const x = rect.left + rect.width / 2
  const y = rect.top

  // Dismiss if same word clicked again
  if (dictPopup.value?.word === word) {
    dictPopup.value = null
    return
  }

  dictPopup.value = { word, phonetic: '', entries: [], x, y, loading: true }

  // Use setTimeout(0) to ensure the current click event has fully propagated
  // before we register the document listener that closes the popup
  setTimeout(() => {
    document.addEventListener('click', closeDictPopup, { once: true })
  }, 0)

  try {
    let data = dictCache.get(word)
    if (!data) {
      const res = await fetch(`/api/dictionary/${encodeURIComponent(word)}`)
      if (res.ok) {
        data = await res.json()
        dictCache.set(word, data)
      } else {
        data = { word, phonetic: '', entries: [] }
      }
    }
    dictPopup.value = { ...data, x, y, loading: false }
  } catch (e) {
    console.error('Dictionary fetch error:', e)
    dictPopup.value = { word, phonetic: '', entries: [], x, y, loading: false }
  }
}

function closeDictPopup() {
  dictPopup.value = null
}

function speakWord(word) {
  if (!word || !window.speechSynthesis) return
  window.speechSynthesis.cancel()
  const utt = new SpeechSynthesisUtterance(word)
  utt.lang = 'en-US'
  utt.rate = 0.9
  // Voices may not be loaded yet — wait if needed
  const voices = window.speechSynthesis.getVoices()
  const pickVoice = (list) =>
    list.find(v => v.lang.startsWith('en') && v.localService) ||
    list.find(v => v.lang.startsWith('en'))
  if (voices.length) {
    const v = pickVoice(voices)
    if (v) utt.voice = v
    window.speechSynthesis.speak(utt)
  } else {
    window.speechSynthesis.addEventListener('voiceschanged', () => {
      const v = pickVoice(window.speechSynthesis.getVoices())
      if (v) utt.voice = v
      window.speechSynthesis.speak(utt)
    }, { once: true })
  }
}

const layers = [
  { label: '只听', speed: 1.0, noise: false },
  { label: '查看标注', speed: 1.0, noise: false },
  { label: '慢速', speed: 0.75, noise: false },
  { label: '正常速度', speed: 1.0, noise: false },
  { label: 'Gate 1', speed: 1.0, noise: false },
  { label: 'Gate 2', speed: 1.0, noise: false },
  { label: 'Gate 3', speed: 1.0, noise: false },
  { label: '完成', speed: 1.0, noise: false },
]

const layerSpeed = computed(() => layers[currentLayer.value]?.speed || 1.0)
const layerNoise = computed(() => layers[currentLayer.value]?.noise || false)
const audioSrc = computed(() => card.value ? `/audio/${card.value.id}` : '')

// Active word index based on playback time and word timestamps
const activeWordIdx = computed(() => {
  const wts = card.value?.word_timestamps || []
  const t = currentPlayTime.value + (card.value?.start_time || 0)
  if (!wts.length || t <= 0) return -1
  for (let i = wts.length - 1; i >= 0; i--) {
    if (wts[i].start != null && wts[i].start <= t) return i
  }
  return -1
})

const annotatedTokens = computed(() => {
  if (!card.value) return []
  const text = card.value.text
  const annotations = card.value.phonetic_annotations || []

  // Build word → phenomena map
  const wordPhenoMap = {}
  for (const ann of annotations) {
    wordPhenoMap[ann.word_index] = ann.phenomena || []
  }

  // Tokenize
  const tokens = []
  const wordPattern = /([A-Za-z0-9'']+)|([^A-Za-z0-9'']+)/g
  let match
  let wordIdx = 0
  while ((match = wordPattern.exec(text)) !== null) {
    if (match[1]) {
      const phenos = wordPhenoMap[wordIdx] || []
      const primary = phenos[0]
      tokens.push({
        text: match[1],
        phenoClass: primary ? `pheno-${primary.type}` : '',
        phenoTip: phenos.map(p => `${p.label}: ${p.info}`).join('\n'),
        wordIdx,
      })
      wordIdx++
    } else {
      tokens.push({ text: match[2], phenoClass: '', phenoTip: '', wordIdx: -1 })
    }
  }
  return tokens
})

const uniquePhenomena = computed(() => {
  if (!card.value) return []
  const seen = new Set()
  const result = []
  for (const ann of (card.value.phonetic_annotations || [])) {
    for (const p of (ann.phenomena || [])) {
      if (!seen.has(p.type + p.info)) {
        seen.add(p.type + p.info)
        result.push(p)
      }
    }
  }
  return result
})

function phenoColor(type) {
  return { linking: 'red', weakForm: 'blue', assimilation: 'red', elision: 'green', flapping: 'purple' }[type] || 'blue'
}

function unlockLayer(n) {
  // When first entering Layer 1, mark chunks as encountered
  if (n === 1 && currentLayer.value === 0 && card.value) {
    api.post('/chunks/encounter', { segment_id: card.value.id }).catch(() => {})
  }
  maxLayer.value = Math.max(maxLayer.value, n)
  currentLayer.value = n
}

function playChunk(chunk) {
  const el = chunkAudioEl.value
  if (!el) return
  // Stop any ongoing chunk playback
  playingChunk.value = ''
  el.pause()

  el.currentTime = chunk.start
  playingChunk.value = chunk.text
  el.play()

  const stop = () => {
    if (el.currentTime >= chunk.end) {
      el.pause()
      playingChunk.value = ''
      el.removeEventListener('timeupdate', stop)
    }
  }
  el.addEventListener('timeupdate', stop)
}

function onShadowPass() {
  card.value.card.shadow_streak = 3
  unlockLayer(5)
}

function onGenPass() {
  card.value.card.gen_passed = true
  unlockLayer(6)
}

function onStressPass() {
  card.value.card.stress_passed = true
  unlockLayer(7)
}

function onAudioEnded() {
  // Auto-unlock next layer after first listen
  if (currentLayer.value === 0) unlockLayer(0)
}

function stateLabel(s) {
  return { new: '新卡片', learning: '学习中', review: '待复习', mastered: '已掌握' }[s] || s
}

onMounted(async () => {
  // Pre-warm speech synthesis so first real call is instant
  if (window.speechSynthesis) {
    const warmup = new SpeechSynthesisUtterance(' ')
    warmup.volume = 0
    window.speechSynthesis.speak(warmup)
  }
  try {
    card.value = await api.get(`/cards/${route.params.id}`)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.training-view { max-width: 720px; margin: 0 auto; }

.training-header {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.header-center { display: flex; align-items: center; gap: 8px; flex: 1; }
.card-state-label { font-size: 13px; color: var(--text-muted); }

.gate-indicators { display: flex; gap: 6px; }
.gate {
  width: 26px; height: 26px;
  border-radius: 50%;
  border: 2px solid var(--border);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px;
  color: var(--text-muted);
}
.gate.done { border-color: var(--success); color: var(--success); }

.layer-tabs { display: flex; gap: 4px; margin-bottom: 14px; flex-wrap: wrap; }
.layer-tab {
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  color: var(--text-muted);
}
.layer-tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.layer-tab.locked { opacity: 0.4; cursor: not-allowed; }

.layer-content { margin-top: 14px; }

.listen-only { text-align: center; padding: 20px 0; }
.listen-hint { font-size: 16px; margin-bottom: 20px; color: var(--text-muted); }

.annotated-text {
  font-size: 18px;
  line-height: 2.4;
  letter-spacing: 0.02em;
  margin-bottom: 16px;
}

/* Hide toggle toolbar */
.text-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 10px;
}
.btn-hide-toggle {
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text-muted);
  cursor: pointer;
  white-space: nowrap;
  margin-left: auto;
}
.btn-hide-toggle.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

/* Hidden word placeholder — text stays in DOM for natural width, just made invisible */
.word-hidden {
  color: transparent !important;
  background: var(--border);
  border-radius: 3px;
  user-select: none;
}

/* Clickable words */
.word-clickable { cursor: pointer; }
.word-clickable:hover { text-decoration: underline; text-underline-offset: 3px; }

/* Dictionary popup */
.dict-popup {
  position: fixed;
  transform: translateX(-50%) translateY(-100%);
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  min-width: 220px;
  max-width: 320px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  z-index: 1001;
}
.dict-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.dict-word {
  font-weight: 700;
  font-size: 16px;
}
.dict-phonetic {
  font-size: 13px;
  color: var(--text-muted);
  flex: 1;
}
.dict-speak {
  background: none;
  border: none;
  font-size: 16px;
  cursor: pointer;
  padding: 0 2px;
  opacity: 0.7;
}
.dict-speak:hover { opacity: 1; }
.dict-close {
  background: none;
  border: none;
  font-size: 13px;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0 2px;
}
.dict-close:hover { color: var(--text); }
.dict-entries { display: flex; flex-direction: column; gap: 4px; }
.dict-entry { font-size: 13px; line-height: 1.5; }
.dict-pos {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  margin-right: 5px;
  font-style: italic;
}
.dict-meanings { color: var(--text-secondary, var(--text-muted)); }
.dict-loading { font-size: 13px; color: var(--text-muted); }

/* Karaoke highlighting */
.word-active {
  background: var(--accent);
  color: #fff;
  border-radius: 3px;
  padding: 0 3px;
  transition: background 0.08s ease;
}
.word-past {
  color: var(--accent);
  opacity: 0.6;
}

.pheno-legend { margin-bottom: 16px; }
.pheno-item { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 6px; }
.pheno-info { font-size: 13px; color: var(--text-muted); }

.explanation-box {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 16px;
}
.explanation-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 6px;
  letter-spacing: 0.04em;
}
.explanation-text {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-secondary, var(--text-muted));
}

.gate-title { font-size: 16px; font-weight: 700; margin-bottom: 6px; }
.gate-desc { font-size: 13px; color: var(--text-muted); margin-bottom: 16px; }

.complete { text-align: center; padding: 30px 0; }
.complete-icon { font-size: 48px; margin-bottom: 12px; }
.complete h3 { font-size: 20px; margin-bottom: 8px; }
.complete p { color: var(--text-muted); margin-bottom: 20px; }

.loading-center { display: flex; justify-content: center; padding: 60px; }
</style>
