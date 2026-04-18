<template>
  <div class="gen-test">
    <!-- Step 1: Generate -->
    <div v-if="step === 'generate'">
      <p class="desc">系统将生成一个包含相同语音规律的全新句子，你需要听写它。</p>
      <button class="btn-primary" :disabled="loading" @click="generate">
        {{ loading ? '生成中...' : '生成测试句子' }}
      </button>
    </div>

    <!-- Step 2: Listen and dictate -->
    <div v-if="step === 'dictate'">
      <AudioPlayer :src="audioUrl" />
      <p class="desc" style="margin-top:12px">🎧 听上面的音频，将你听到的内容听写下来：</p>
      <textarea
        v-model="userText"
        class="dictation-input"
        placeholder="在此输入你听到的内容..."
        rows="3"
      ></textarea>
      <button class="btn-primary" :disabled="!userText.trim() || submitting" @click="submit">
        {{ submitting ? '评估中...' : '提交答案' }}
      </button>
    </div>

    <!-- Step 3: Result -->
    <div v-if="step === 'result'" class="result-panel" :class="evalResult.correct ? 'pass' : 'fail'">
      <div class="result-score">得分 {{ (evalResult.score * 100).toFixed(0) }}/100</div>
      <div class="feedback">{{ evalResult.feedback }}</div>
      <div class="reference">参考答案：<em>{{ reference }}</em></div>
      <div class="result-msg">{{ evalResult.correct ? '✅ 通过！' : '❌ 未通过，再试一次' }}</div>
      <div class="btn-row">
        <button v-if="!evalResult.correct" class="btn-secondary" @click="retry">重试</button>
        <button v-if="evalResult.correct" class="btn-primary" @click="emit('pass')">继续 →</button>
      </div>
    </div>

    <div v-if="error" class="error-msg">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import api from '@/api'
import AudioPlayer from './AudioPlayer.vue'

const props = defineProps({ segmentId: { type: Number, required: true } })
const emit = defineEmits(['pass'])

const step = ref('generate')
const loading = ref(false)
const submitting = ref(false)
const audioUrl = ref('')
const userText = ref('')
const evalResult = ref(null)
const reference = ref('')
const error = ref('')

async function generate() {
  loading.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('segment_id', props.segmentId)
    const res = await api.post('/mastery/generalize/generate', form)
    if (res.skipped) {
      emit('pass')
      return
    }
    audioUrl.value = res.audio_url
    step.value = 'dictate'
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function submit() {
  submitting.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('segment_id', props.segmentId)
    form.append('user_text', userText.value)
    const res = await api.post('/mastery/generalize/submit', form)
    evalResult.value = res
    reference.value = res.reference
    step.value = 'result'
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

function retry() {
  userText.value = ''
  step.value = 'dictate'
}
</script>

<style scoped>
.gen-test { display: flex; flex-direction: column; gap: 14px; }
.desc { font-size: 13px; color: var(--text-muted); }
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
.result-panel { padding: 14px 16px; border-radius: 10px; border: 1px solid var(--border); }
.result-panel.pass { border-color: var(--success); background: rgba(39,174,96,0.08); }
.result-panel.fail { border-color: var(--error); background: rgba(231,76,60,0.08); }
.result-score { font-size: 18px; font-weight: 700; margin-bottom: 6px; }
.feedback { font-size: 14px; margin-bottom: 8px; }
.reference { font-size: 13px; color: var(--text-muted); margin-bottom: 10px; }
.reference em { font-style: normal; color: var(--text); }
.result-msg { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
.btn-row { display: flex; gap: 8px; }
.error-msg { color: var(--error); font-size: 13px; }
</style>
