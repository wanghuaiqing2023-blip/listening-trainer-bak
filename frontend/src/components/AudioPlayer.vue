<template>
  <div class="audio-player card">
    <div ref="waveformEl" class="waveform"></div>
    <div class="controls">
      <button class="ctrl-btn" @click="togglePlay">
        {{ playing ? '⏸' : '▶' }}
      </button>
      <div class="time-info">
        <span>{{ formatTime(currentTime) }}</span>
        <span class="sep">/</span>
        <span>{{ formatTime(duration) }}</span>
      </div>
      <div class="speed-group">
        <button
          v-for="s in speeds"
          :key="s"
          :class="['speed-btn', currentSpeed === s ? 'active' : '']"
          @click="setSpeed(s)"
        >{{ s }}x</button>
      </div>
    </div>
    <div v-if="loading" class="wave-loading">
      <div class="loading-spinner"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import WaveSurfer from 'wavesurfer.js'

const props = defineProps({
  src: { type: String, default: '' },
  speed: { type: Number, default: 1.0 },
})
const emit = defineEmits(['ended', 'timeupdate'])

const waveformEl = ref(null)
const playing = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const loading = ref(true)
const currentSpeed = ref(props.speed)
const speeds = [0.5, 0.75, 1.0, 1.25, 1.5]

let ws = null

onMounted(() => {
  ws = WaveSurfer.create({
    container: waveformEl.value,
    waveColor: '#2a2d3a',
    progressColor: '#4f6ef7',
    cursorColor: '#4f6ef7',
    barWidth: 2,
    barGap: 1,
    height: 60,
    normalize: true,
  })

  ws.on('ready', () => {
    loading.value = false
    duration.value = ws.getDuration()
    ws.setPlaybackRate(currentSpeed.value)
  })

  ws.on('audioprocess', () => {
    currentTime.value = ws.getCurrentTime()
    emit('timeupdate', currentTime.value)
  })

  ws.on('finish', () => {
    playing.value = false
    emit('ended')
  })

  if (props.src) ws.load(props.src)
})

watch(() => props.src, (src) => {
  if (ws && src) {
    loading.value = true
    ws.load(src)
  }
})

watch(() => props.speed, (s) => {
  currentSpeed.value = s
  ws?.setPlaybackRate(s)
})

function togglePlay() {
  ws?.playPause()
  playing.value = !playing.value
}

function setSpeed(s) {
  currentSpeed.value = s
  ws?.setPlaybackRate(s)
}

function formatTime(s) {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

onUnmounted(() => ws?.destroy())
</script>

<style scoped>
.audio-player { position: relative; margin-bottom: 16px; }
.waveform { min-height: 60px; }
.wave-loading {
  position: absolute; top: 0; left: 0; right: 0; bottom: 0;
  display: flex; align-items: center; justify-content: center;
  background: rgba(26,29,38,0.8);
  border-radius: 12px;
}
.controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}
.ctrl-btn {
  width: 36px; height: 36px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.ctrl-btn:hover { background: var(--accent-hover); }
.time-info { font-size: 13px; color: var(--text-muted); }
.sep { margin: 0 3px; }
.speed-group { display: flex; gap: 4px; margin-left: auto; }
.speed-btn {
  padding: 3px 8px;
  border-radius: 5px;
  font-size: 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text-muted);
}
.speed-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
</style>
