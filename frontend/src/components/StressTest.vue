<template>
  <div class="stress-test">
    <div v-if="step === 'generate'">
      <p class="desc">系统将随机选择一种干扰（背景噪音 或 1.5x 语速），测试你的自动化程度。</p>
      <button class="btn-primary" :disabled="loading" @click="generate">
        {{ loading ? '准备中...' : '开始压力测试' }}
      </button>
    </div>

    <div v-if="step === 'test'">
      <div class="stress-badge">
        {{ stressType === 'noise' ? '🔊 已添加背景噪音' : '⚡ 速度 1.5x' }}
      </div>
      <AudioPlayer :src="audioUrl" />
      <p class="desc" style="margin-top:12px">听写你听到的内容：</p>
      <textarea v-model="userText" class="dictation-input" rows="3"
        placeholder="在此输入..."></textarea>
      <button class="btn-primary" :disabled="!userText.trim() || submitting" @click="submit">
        {{ submitting ? '评估中...' : '提交' }}
      </button>
    </div>

    <div v-if="step === 'result'" class="result-panel" :class="result.passed ? 'pass' : 'fail'">
      <div class="accuracy">准确率 {{ (result.accuracy * 100).toFixed(0) }}%</div>
      <div class="result-msg">{{ result.passed ? '✅ 通过！自动化验证成功' : '❌ 未通过，继续练习' }}</div>
      <div class="btn-row">
        <button v-if="!result.passed" class="btn-secondary" @click="retry">重试</button>
        <button v-if="result.passed" class="btn-primary" @click="emit('pass')">完成 →</button>
      </div>
    </div>

    <div v-if="error" class="error-msg">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import api from '@/api'
import AudioPlayer from './AudioPlayer.vue'

const props = defineProps({
  segmentId: { type: Number, required: true },
  segmentText: { type: String, default: '' },
})
const emit = defineEmits(['pass'])

const step = ref('generate')
const loading = ref(false)
const submitting = ref(false)
const audioUrl = ref('')
const stressType = ref('')
const userText = ref('')
const result = ref(null)
const error = ref('')

async function generate() {
  loading.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('segment_id', props.segmentId)
    const res = await api.post('/mastery/stress/generate', form)
    audioUrl.value = res.audio_url
    stressType.value = res.stress_type
    step.value = 'test'
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
    const res = await api.post('/mastery/stress/submit', form)
    result.value = res
    step.value = 'result'
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

function retry() {
  userText.value = ''
  step.value = 'generate'
}
</script>

<style scoped>
.stress-test { display: flex; flex-direction: column; gap: 14px; }
.desc { font-size: 13px; color: var(--text-muted); }
.stress-badge {
  display: inline-block;
  padding: 4px 12px;
  background: rgba(243,156,18,0.15);
  border: 1px solid rgba(243,156,18,0.4);
  border-radius: 8px;
  font-size: 13px;
  color: #fcd34d;
  margin-bottom: 8px;
}
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
.accuracy { font-size: 18px; font-weight: 700; margin-bottom: 8px; }
.result-msg { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
.btn-row { display: flex; gap: 8px; }
.error-msg { color: var(--error); font-size: 13px; }
</style>
