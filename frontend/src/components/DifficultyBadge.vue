<template>
  <span class="diff-badge" :style="{ background: bgColor, color: textColor }">
    {{ score.toFixed(1) }}
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ score: { type: Number, default: 5 } })

const bgColor = computed(() => {
  if (props.score <= 3) return 'rgba(39,174,96,0.2)'
  if (props.score <= 6) return 'rgba(243,156,18,0.2)'
  return 'rgba(231,76,60,0.2)'
})

const textColor = computed(() => {
  if (props.score <= 3) return '#6ee7b7'
  if (props.score <= 6) return '#fcd34d'
  return '#fca5a5'
})
</script>

<style scoped>
.diff-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
}
</style>
