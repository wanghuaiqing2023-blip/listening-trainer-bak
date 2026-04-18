<template>
  <div class="onboarding">
    <div v-if="step === 'intro'" class="intro">
      <div class="intro-icon">🎯</div>
      <h2>听力水平测试</h2>
      <p>系统将播放 5 段由易到难的音频，每段听完后请听写你所听到的内容。</p>
      <p style="color: var(--text-muted); font-size: 13px; margin-top: 8px">
        不需要完美，系统会根据你的答案自动评估你的初始水平。
      </p>
      <button class="btn-primary" style="margin-top:24px" :disabled="loading" @click="loadSentences">
        {{ loading ? '准备中...' : '开始测试' }}
      </button>
      <div v-if="error" class="error-msg">{{ error }}</div>
    </div>

    <div v-if="step === 'test'" class="test-panel">
      <div class="test-progress">
        第 {{ currentIndex + 1 }} / {{ sentences.length }} 题
        <span class="level-hint">难度 {{ sentences[currentIndex]?.level }}</span>
      </div>

      <AudioPlayer :src="sentences[currentIndex]?.audio_url" />

      <p class="test-hint">听写你听到的内容（可多次播放）：</p>
      <textarea
        v-model="answers[currentIndex]"
        class="dictation-input"
        rows="3"
        placeholder="在此输入..."
      ></textarea>

      <div class="test-nav">
        <button v-if="currentIndex > 0" class="btn-secondary" @click="currentIndex--">← 上一题</button>
        <button v-if="currentIndex < sentences.length - 1" class="btn-primary" @click="currentIndex++">
          下一题 →
        </button>
        <button v-else class="btn-primary" :disabled="submitting" @click="submitTest">
          {{ submitting ? '评估中...' : '提交并完成' }}
        </button>
      </div>
    </div>

    <div v-if="step === 'result'" class="result-panel">
      <div class="result-icon">🏆</div>
      <h2>测试完成</h2>
      <div class="level-display">
        <div class="level-num">{{ testResult.initial_level }}</div>
        <div class="level-label">{{ testResult.level_label }}</div>
      </div>

      <div class="result-details">
        <div v-for="r in testResult.results" :key="r.level" class="result-row">
          <span class="result-level">难度 {{ r.level }}</span>
          <div class="result-bar-wrap">
            <div class="result-bar" :style="{ width: r.accuracy * 100 + '%', background: r.passed ? 'var(--success)' : 'var(--error)' }"></div>
          </div>
          <span :class="r.passed ? 'pass-label' : 'fail-label'">
            {{ (r.accuracy * 100).toFixed(0) }}% {{ r.passed ? '✓' : '✗' }}
          </span>
        </div>
      </div>

      <button class="btn-primary" style="margin-top:24px" @click="router.push('/library')">
        开始训练 →
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import AudioPlayer from '@/components/AudioPlayer.vue'
import api from '@/api'

const router = useRouter()
const userStore = useUserStore()

const step = ref('intro')
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const sentences = ref([])
const answers = ref([])
const currentIndex = ref(0)
const testResult = ref(null)

// Store answers keyed by server-side answer text
const serverAnswers = ref([])

async function loadSentences() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/user/test/sentences')
    sentences.value = res
    // Extract hidden answers from _answer field (will be removed by server in production)
    serverAnswers.value = res.map(s => s._answer || s.text || '')
    answers.value = new Array(res.length).fill('')
    step.value = 'test'
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function submitTest() {
  submitting.value = true
  try {
    const responses = sentences.value.map((s, i) => ({
      level: s.level,
      user_text: answers.value[i] || '',
      answer: serverAnswers.value[i] || '',
    }))
    const res = await api.post('/user/test/submit', responses)
    testResult.value = res
    userStore.levelScore = res.initial_level
    userStore.levelLabel = res.level_label
    step.value = 'result'
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.onboarding { max-width: 560px; margin: 0 auto; }
.intro { text-align: center; padding: 40px 0; }
.intro-icon { font-size: 48px; margin-bottom: 16px; }
.intro h2 { font-size: 24px; margin-bottom: 10px; }

.test-panel { display: flex; flex-direction: column; gap: 14px; }
.test-progress {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 14px; color: var(--text-muted);
}
.level-hint { font-size: 12px; }
.test-hint { font-size: 13px; color: var(--text-muted); }
.dictation-input {
  width: 100%;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 15px;
  resize: vertical;
}
.dictation-input:focus { outline: none; border-color: var(--accent); }
.test-nav { display: flex; gap: 8px; justify-content: flex-end; }

.result-panel { text-align: center; padding: 30px 0; }
.result-icon { font-size: 48px; margin-bottom: 12px; }
.result-panel h2 { font-size: 24px; margin-bottom: 20px; }
.level-display { margin-bottom: 24px; }
.level-num { font-size: 56px; font-weight: 800; color: var(--accent); }
.level-label { font-size: 16px; color: var(--text-muted); }

.result-details { text-align: left; }
.result-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 13px; }
.result-level { width: 40px; color: var(--text-muted); }
.result-bar-wrap { flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.result-bar { height: 100%; border-radius: 3px; transition: width 0.5s; }
.pass-label { color: var(--success); width: 60px; text-align: right; }
.fail-label { color: var(--error); width: 60px; text-align: right; }

.error-msg { color: var(--error); font-size: 13px; margin-top: 10px; }
</style>
