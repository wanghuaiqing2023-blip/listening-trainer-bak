<template>
  <div class="shadowing">
    <div class="streak-bar">
      <span
        v-for="i in 3"
        :key="i"
        :class="['streak-dot', i <= props.streak ? 'filled' : '']"
      ></span>
      <span class="streak-text">{{ props.streak }}/3 次通过</span>
    </div>

    <div class="record-area">
      <button
        class="record-btn"
        :class="{ recording }"
        @mousedown="startRecording"
        @mouseup="stopRecording"
        @touchstart.prevent="startRecording"
        @touchend.prevent="stopRecording"
      >
        {{ recording ? '🔴 录音中...' : '🎤 按住跟读' }}
      </button>
    </div>

    <div v-if="result" class="result-panel" :class="result.passed ? 'pass' : 'fail'">
      <div class="result-score">
        发音评分：<strong>{{ result.assessment.pronunciation_score?.toFixed(0) }}</strong>/100
      </div>
      <div class="result-detail">
        准确度 {{ result.assessment.accuracy_score?.toFixed(0) }}
        · 流利度 {{ result.assessment.fluency_score?.toFixed(0) }}
        · 完整度 {{ result.assessment.completeness_score?.toFixed(0) }}
      </div>
      <div v-if="!result.passed" class="fail-words">
        <span v-for="w in failedWords" :key="w.word" class="fail-word">
          {{ w.word }} ({{ w.accuracy.toFixed(0) }})
        </span>
      </div>
      <div class="result-msg">{{ result.passed ? '✅ 通过！' : '❌ 需要再练习，注意模仿连读弱读' }}</div>
    </div>

    <div v-if="submitting" class="loading-center">
      <div class="loading-spinner"></div>
      <span>评估中...</span>
    </div>

    <div v-if="error" class="error-msg">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import api from '@/api'

const props = defineProps({
  segmentId: { type: Number, required: true },
  streak: { type: Number, default: 0 },
})
const emit = defineEmits(['pass'])

const recording = ref(false)
const submitting = ref(false)
const result = ref(null)
const error = ref('')
let mediaRecorder = null
let recordedBlobs = []

const failedWords = computed(() =>
  (result.value?.assessment?.words || []).filter(w => w.accuracy < 70)
)

async function startRecording() {
  error.value = ''
  result.value = null
  recordedBlobs = []
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    mediaRecorder = new MediaRecorder(stream)
    mediaRecorder.ondataavailable = e => recordedBlobs.push(e.data)
    mediaRecorder.start()
    recording.value = true
  } catch (e) {
    error.value = '无法访问麦克风：' + e.message
  }
}

async function stopRecording() {
  if (!mediaRecorder || !recording.value) return
  recording.value = false
  mediaRecorder.stop()
  mediaRecorder.onstop = async () => {
    const blob = new Blob(recordedBlobs, { type: 'audio/webm' })
    await submitRecording(blob)
    mediaRecorder.stream.getTracks().forEach(t => t.stop())
  }
}

async function submitRecording(blob) {
  submitting.value = true
  try {
    const form = new FormData()
    form.append('segment_id', props.segmentId)
    form.append('audio', blob, 'shadow.webm')
    const res = await api.post('/mastery/shadow', form)
    result.value = res
    if (res.gate1_complete) emit('pass')
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.shadowing { display: flex; flex-direction: column; gap: 16px; }

.streak-bar { display: flex; align-items: center; gap: 8px; }
.streak-dot {
  width: 14px; height: 14px;
  border-radius: 50%;
  border: 2px solid var(--border);
  background: transparent;
  transition: all 0.2s;
}
.streak-dot.filled { background: var(--success); border-color: var(--success); }
.streak-text { font-size: 13px; color: var(--text-muted); }

.record-area { display: flex; justify-content: center; }
.record-btn {
  padding: 16px 36px;
  border-radius: 50px;
  font-size: 16px;
  background: var(--bg);
  border: 2px solid var(--accent);
  color: var(--text);
  transition: all 0.15s;
  user-select: none;
}
.record-btn.recording {
  background: rgba(231,76,60,0.15);
  border-color: var(--error);
  animation: pulse 1s infinite;
}
@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.03); }
}

.result-panel {
  padding: 14px 16px;
  border-radius: 10px;
  border: 1px solid var(--border);
}
.result-panel.pass { border-color: var(--success); background: rgba(39,174,96,0.08); }
.result-panel.fail { border-color: var(--error); background: rgba(231,76,60,0.08); }
.result-score { font-size: 16px; margin-bottom: 4px; }
.result-detail { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; }
.fail-words { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.fail-word {
  padding: 2px 8px;
  background: rgba(231,76,60,0.15);
  border-radius: 6px;
  font-size: 12px;
  color: #fca5a5;
}
.result-msg { font-size: 14px; font-weight: 600; }

.loading-center { display: flex; gap: 8px; align-items: center; color: var(--text-muted); font-size: 13px; }
.error-msg { color: var(--error); font-size: 13px; }
</style>
