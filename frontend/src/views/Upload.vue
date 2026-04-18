<template>
  <div>
    <h2 class="page-title">上传内容</h2>

    <div class="upload-grid">
      <!-- File Upload -->
      <div class="card">
        <h3>上传音频 / 视频文件</h3>
        <p class="hint">支持 MP3、MP4、WAV、M4A、MKV 等格式</p>

        <div
          class="drop-zone"
          :class="{ dragover: isDragover }"
          @dragover.prevent="isDragover = true"
          @dragleave="isDragover = false"
          @drop.prevent="onDrop"
          @click="fileInput.click()"
        >
          <div class="drop-icon">📁</div>
          <div>点击或拖拽文件到此处</div>
        </div>
        <input ref="fileInput" type="file" accept="audio/*,video/*" hidden @change="onFileSelect" />

        <div v-if="selectedFile" class="selected-file">
          <span>{{ selectedFile.name }}</span>
          <span class="file-size">{{ formatSize(selectedFile.size) }}</span>
        </div>

        <button class="btn-primary" style="width:100%;margin-top:12px"
          :disabled="!selectedFile || uploading" @click="uploadFile">
          {{ uploading ? '上传中...' : '开始上传' }}
        </button>
      </div>

      <!-- YouTube -->
      <div class="card">
        <h3>YouTube 链接</h3>
        <p class="hint">粘贴 YouTube 视频链接，系统自动下载并处理</p>

        <input
          v-model="youtubeUrl"
          type="url"
          placeholder="https://www.youtube.com/watch?v=..."
          class="text-input"
        />

        <button class="btn-primary" style="width:100%;margin-top:12px"
          :disabled="!youtubeUrl || uploading" @click="submitYoutube">
          {{ uploading ? '处理中...' : '提交' }}
        </button>
      </div>
    </div>

    <!-- Processing queue -->
    <div v-if="jobs.length" class="card" style="margin-top:24px">
      <h3>处理队列</h3>
      <div v-for="job in jobs" :key="job.id" class="job-card">

        <!-- Job header row -->
        <div class="job-header">
          <div class="job-info">
            <span class="job-title">{{ job.title }}</span>
            <span :class="statusClass(job.status)">{{ statusLabel(job.status) }}</span>
          </div>
          <div class="job-actions">
            <div v-if="job.status === 'processing'" class="loading-spinner"></div>
            <div v-if="job.status === 'ready'" class="job-count">{{ job.segment_count }} 个片段</div>
            <router-link v-if="job.status === 'ready'" to="/library"
              class="btn-secondary" style="padding:4px 12px;font-size:13px">
              去训练
            </router-link>
          </div>
        </div>

        <!-- Progress bar (shown while processing) -->
        <div v-if="job.status === 'processing' || job.progress > 0" class="progress-bar-wrap">
          <div class="progress-bar" :style="{ width: job.progress + '%' }"></div>
          <span class="progress-label">{{ job.progress }}%</span>
        </div>

        <!-- Step timeline -->
        <div v-if="job.steps && job.steps.length" class="step-timeline">
          <div
            v-for="step in job.steps"
            :key="step.name"
            class="step-row"
            :class="'step-' + step.status"
          >
            <span class="step-icon">{{ stepIcon(step.status) }}</span>
            <span class="step-label">{{ step.label }}</span>
            <span v-if="step.message" class="step-message">{{ step.message }}</span>
          </div>
        </div>

        <!-- Error banner -->
        <div v-if="job.status === 'error'" class="job-error">
          <span>{{ job.error || '处理失败，请检查服务器日志' }}</span>
        </div>

      </div>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import api from '@/api'

const fileInput = ref(null)
const selectedFile = ref(null)
const youtubeUrl = ref('')
const uploading = ref(false)
const jobs = ref([])
const error = ref('')
const isDragover = ref(false)
let pollingTimers = {}

function onFileSelect(e) {
  selectedFile.value = e.target.files[0] || null
}

function onDrop(e) {
  isDragover.value = false
  selectedFile.value = e.dataTransfer.files[0] || null
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

async function uploadFile() {
  if (!selectedFile.value) return
  uploading.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('file', selectedFile.value)
    const res = await api.post('/content/upload', form)
    addJob(res)
    selectedFile.value = null
  } catch (e) {
    error.value = e.message
  } finally {
    uploading.value = false
  }
}

async function submitYoutube() {
  if (!youtubeUrl.value) return
  uploading.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('url', youtubeUrl.value)
    const res = await api.post('/content/youtube', form)
    addJob(res)
    youtubeUrl.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    uploading.value = false
  }
}

function addJob(res) {
  jobs.value.unshift({
    id: res.id,
    title: res.title,
    status: res.status,
    segment_count: 0,
    steps: [],
    progress: 0,
    error: '',
  })
  startPolling(res.id)
}

function startPolling(id) {
  pollingTimers[id] = setInterval(async () => {
    try {
      const data = await api.get(`/content/${id}/status`)
      const job = jobs.value.find(j => j.id === id)
      if (job) {
        job.status = data.status
        job.segment_count = data.segment_count
        job.error = data.error
        job.steps = data.steps || []
        job.progress = data.progress || 0
      }
      if (data.status === 'ready' || data.status === 'error') {
        clearInterval(pollingTimers[id])
        delete pollingTimers[id]
      }
    } catch {}
  }, 2000)
}

function stepIcon(status) {
  return { pending: '○', running: '◉', success: '✓', error: '✗' }[status] || '○'
}

function statusLabel(s) {
  return { processing: '处理中', ready: '已完成', error: '失败' }[s] || s
}

function statusClass(s) {
  return {
    processing: 'badge badge-blue',
    ready: 'badge badge-green',
    error: 'badge badge-red',
  }[s] || 'badge'
}

onUnmounted(() => {
  Object.values(pollingTimers).forEach(clearInterval)
})
</script>

<style scoped>
.page-title { font-size: 22px; font-weight: 700; margin-bottom: 20px; }
.upload-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.card h3 { font-size: 15px; margin-bottom: 6px; }
.hint { font-size: 13px; color: var(--text-muted); margin-bottom: 14px; }

.drop-zone {
  border: 2px dashed var(--border);
  border-radius: 10px;
  padding: 32px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.15s;
  color: var(--text-muted);
}
.drop-zone:hover, .drop-zone.dragover {
  border-color: var(--accent);
  background: rgba(79,110,247,0.06);
  color: var(--text);
}
.drop-icon { font-size: 32px; margin-bottom: 8px; }

.selected-file {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  font-size: 13px;
  padding: 8px 12px;
  background: var(--bg);
  border-radius: 6px;
}
.file-size { color: var(--text-muted); }

.text-input {
  width: 100%;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 14px;
}
.text-input:focus { outline: none; border-color: var(--accent); }

/* Job card */
.job-card {
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
}
.job-card:last-child { border-bottom: none; }

.job-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.job-info { display: flex; gap: 10px; align-items: center; flex: 1; min-width: 0; }
.job-title { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.job-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.job-count { font-size: 13px; color: var(--text-muted); }

/* Progress bar */
.progress-bar-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.progress-bar-wrap > .progress-bar {
  flex: 1;
  height: 4px;
  background: var(--accent);
  border-radius: 2px;
  transition: width 0.4s ease;
}
.progress-bar-wrap {
  background: var(--border);
  height: 4px;
  border-radius: 2px;
  overflow: visible;
  position: relative;
}
/* Rewrite as positioned element */
.progress-bar-wrap {
  background: rgba(255,255,255,0.08);
  height: 5px;
  border-radius: 3px;
  margin-top: 10px;
  position: relative;
  display: flex;
  align-items: center;
}
.progress-bar {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.5s ease;
  min-width: 4px;
}
.progress-label {
  font-size: 11px;
  color: var(--text-muted);
  margin-left: 6px;
  flex-shrink: 0;
}

/* Step timeline */
.step-timeline {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-left: 4px;
}

.step-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  padding: 3px 0;
}

.step-icon {
  font-size: 13px;
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}
.step-label { color: var(--text-muted); }
.step-message { font-size: 12px; color: var(--text-muted); opacity: 0.7; margin-left: 4px; }

/* States */
.step-pending .step-icon  { color: var(--text-muted); opacity: 0.4; }
.step-pending .step-label { opacity: 0.4; }

.step-running .step-icon  { color: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
.step-running .step-label { color: var(--text); }

.step-success .step-icon  { color: #2ecc71; }
.step-success .step-label { color: var(--text); }

.step-error .step-icon    { color: var(--error); }
.step-error .step-label   { color: var(--error); }
.step-error .step-message { color: var(--error); opacity: 0.8; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}

/* Error banner at bottom of job card */
.job-error {
  margin-top: 8px;
  font-size: 13px;
  color: var(--error);
  padding: 6px 10px;
  background: rgba(231,76,60,0.08);
  border-radius: 6px;
}

.error-banner {
  margin-top: 16px;
  padding: 12px 16px;
  background: rgba(231,76,60,0.12);
  border: 1px solid var(--error);
  border-radius: 8px;
  color: var(--error);
}

@media (max-width: 600px) {
  .upload-grid { grid-template-columns: 1fr; }
}
</style>
