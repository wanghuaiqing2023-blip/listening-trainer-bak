<template>
  <div v-if="chunks?.length" class="chunk-bar">
    <div class="chunk-bar-label">语块</div>
    <div class="chunk-pills">
      <button
        v-for="c in chunks"
        :key="c.text"
        :class="['chunk-pill', playing === c.text ? 'playing' : '']"
        :title="c.meaning_zh || ''"
        @click="$emit('play', c)"
      >
        <span class="chunk-play-icon">{{ playing === c.text ? '▶' : '▷' }}</span>
        {{ c.text }}
        <span v-if="c.meaning_zh" class="chunk-meaning">{{ c.meaning_zh }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  chunks: { type: Array, default: () => [] },
  playing: { type: String, default: '' },
})
defineEmits(['play'])
</script>

<style scoped>
.chunk-bar {
  margin: 14px 0 16px;
  padding: 12px 14px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
}

.chunk-bar-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  text-transform: uppercase;
  margin-bottom: 10px;
}

.chunk-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chunk-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border-radius: 20px;
  font-size: 14px;
  font-family: inherit;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.chunk-pill:hover {
  background: rgba(79, 110, 247, 0.1);
  border-color: var(--accent);
}

.chunk-pill.playing {
  background: rgba(79, 110, 247, 0.18);
  border-color: var(--accent);
  color: var(--accent);
}

.chunk-play-icon {
  font-size: 10px;
  opacity: 0.6;
}

.chunk-meaning {
  font-size: 11px;
  color: var(--text-muted);
  margin-left: 2px;
}
</style>
