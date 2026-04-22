<template>
  <div class="dictation-check">
    <p class="desc">
      先不看原句，直接听写。系统会按这句话的词数给你词槽，按空格跳到下一个槽；整句粘贴也会自动拆词。
    </p>

    <div class="slot-meta">
      <span>词槽数 {{ expectedSlotCount }}</span>
      <span>已填写 {{ filledSlotCount }}</span>
      <span>漏听可以留空，多听出的词会自动追加到后面</span>
    </div>

    <div class="slot-grid">
      <div
        v-for="(slot, index) in slots"
        :key="index"
        :class="['slot-cell', index >= expectedSlotCount ? 'overflow' : '']"
      >
        <span class="slot-index">{{ index + 1 }}</span>
        <input
          :ref="el => setInputRef(el, index)"
          v-model="slots[index]"
          :class="['slot-input', index >= expectedSlotCount ? 'soft-slot' : '']"
          type="text"
          autocomplete="off"
          autocapitalize="off"
          spellcheck="false"
          @input="onInput(index)"
          @keydown="onKeydown($event, index)"
          @paste="onPaste($event, index)"
        />
      </div>
    </div>

    <div v-if="slots.length > expectedSlotCount" class="overflow-tip">
      已自动追加 {{ slots.length - expectedSlotCount }} 个溢出词槽，用来承接多听出的词。
    </div>

    <div class="action-row">
      <button class="btn-secondary" :disabled="submitting || !joinedUserText" @click="clearAnswer">
        清空
      </button>
      <button class="btn-primary" :disabled="submitting || !joinedUserText" @click="submit">
        {{ submitting ? '分析中...' : '检查听写' }}
      </button>
    </div>

    <div v-if="error" class="error-msg">{{ error }}</div>

    <div v-if="result" class="result-panel">
      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-value">{{ percent(result.accuracy) }}</div>
          <div class="summary-label">准确率</div>
        </div>
        <div class="summary-card">
          <div class="summary-value">{{ result.correct_word_count }}/{{ result.reference_word_count }}</div>
          <div class="summary-label">正确词数</div>
        </div>
        <div class="summary-card">
          <div class="summary-value">{{ result.error_count }}</div>
          <div class="summary-label">错误片段</div>
        </div>
      </div>

      <div v-if="result.errors.length" class="error-list">
        <div v-for="(item, index) in result.errors" :key="index" class="error-item">
          <div class="error-head">
            <span :class="['error-badge', item.type]">{{ errorLabel(item.type) }}</span>
            <span class="error-index">错误 {{ index + 1 }}</span>
          </div>
          <div class="error-body">
            <div v-if="item.reference_text" class="error-line">
              <span class="line-label">参考</span>
              <span class="line-text">{{ item.reference_text }}</span>
            </div>
            <div v-if="item.user_text" class="error-line">
              <span class="line-label">你的</span>
              <span class="line-text">{{ item.user_text }}</span>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="all-correct">这次听写没有发现词级错误。</div>

      <div class="token-section">
        <div class="token-title">参考句定位</div>
        <div class="token-flow">
          <span
            v-for="token in result.reference_tokens"
            :key="`ref-${token.index}`"
            :class="['token-chip', token.status]"
            :title="token.partner_text ? `对应: ${token.partner_text}` : ''"
          >
            {{ token.text }}
          </span>
        </div>
      </div>

      <div class="token-section">
        <div class="token-title">你的答案定位</div>
        <div class="token-flow">
          <span
            v-for="token in result.hypothesis_tokens"
            :key="`hyp-${token.index}`"
            :class="['token-chip', token.status]"
            :title="token.partner_text ? `对应: ${token.partner_text}` : ''"
          >
            {{ token.text }}
          </span>
        </div>
      </div>

      <div class="legend">
        <span class="legend-item"><span class="legend-dot correct"></span>正确</span>
        <span class="legend-item"><span class="legend-dot replace"></span>听错</span>
        <span class="legend-item"><span class="legend-dot missing"></span>漏听</span>
        <span class="legend-item"><span class="legend-dot extra"></span>多写</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import api from '@/api'

const props = defineProps({
  segmentId: { type: Number, required: true },
  referenceText: { type: String, default: '' },
})

const emit = defineEmits(['checked'])

const slots = ref([])
const inputRefs = ref([])
const submitting = ref(false)
const result = ref(null)
const error = ref('')

const referenceWords = computed(() => (props.referenceText.match(/[A-Za-z0-9']+/g) || []))
const expectedSlotCount = computed(() => Math.max(referenceWords.value.length, 1))
const filledSlotCount = computed(() => slots.value.filter(slot => normalizeSlotValue(slot)).length)
const joinedUserText = computed(() => slots.value.map(normalizeSlotValue).filter(Boolean).join(' '))

watch(expectedSlotCount, () => {
  resetSlots()
}, { immediate: true })

function normalizeSlotValue(value) {
  return String(value || '').trim()
}

function splitWords(text) {
  return String(text || '')
    .split(/\s+/)
    .map(word => word.trim())
    .filter(Boolean)
}

function percent(value) {
  return `${Math.round((value || 0) * 100)}%`
}

function errorLabel(type) {
  return {
    replace: '听错',
    missing: '漏听',
    extra: '多写',
  }[type] || type
}

function ensureSlotCount(requiredCount) {
  while (slots.value.length < requiredCount) {
    slots.value.push('')
  }
}

function trimTrailingOverflowSlots() {
  while (
    slots.value.length > expectedSlotCount.value &&
    !normalizeSlotValue(slots.value[slots.value.length - 1])
  ) {
    slots.value.pop()
    inputRefs.value.pop()
  }
}

function resetSlots() {
  slots.value = Array.from({ length: expectedSlotCount.value }, () => '')
  inputRefs.value = []
  result.value = null
  error.value = ''
}

function clearAnswer() {
  resetSlots()
  focusSlot(0)
}

function setInputRef(el, index) {
  if (el) {
    inputRefs.value[index] = el
  }
}

function focusSlot(index) {
  nextTick(() => {
    const input = inputRefs.value[index]
    if (!input) return
    input.focus()
    input.select()
  })
}

function touchInput() {
  result.value = null
  error.value = ''
}

function distributeWords(startIndex, words) {
  if (!words.length) return
  ensureSlotCount(startIndex + words.length)
  words.forEach((word, offset) => {
    slots.value[startIndex + offset] = word
  })
  trimTrailingOverflowSlots()
  const nextIndex = Math.min(startIndex + words.length, slots.value.length - 1)
  focusSlot(nextIndex)
}

function onInput(index) {
  touchInput()
  const words = splitWords(slots.value[index])
  if (words.length > 1) {
    distributeWords(index, words)
    return
  }
  slots.value[index] = words[0] || ''
  trimTrailingOverflowSlots()
}

function onPaste(event, index) {
  const text = event.clipboardData?.getData('text') || ''
  const words = splitWords(text)
  if (!words.length) return
  event.preventDefault()
  touchInput()
  distributeWords(index, words)
}

function onKeydown(event, index) {
  if (event.key === ' ') {
    event.preventDefault()
    if (index === slots.value.length - 1 && normalizeSlotValue(slots.value[index])) {
      ensureSlotCount(slots.value.length + 1)
    }
    if (index < slots.value.length - 1) {
      focusSlot(index + 1)
    }
    return
  }

  if (event.key === 'Enter') {
    event.preventDefault()
    if (joinedUserText.value) {
      submit()
    }
    return
  }

  if (event.key === 'Backspace' && !normalizeSlotValue(slots.value[index]) && index > 0) {
    event.preventDefault()
    trimTrailingOverflowSlots()
    focusSlot(index - 1)
  }
}

async function submit() {
  if (!joinedUserText.value) return
  submitting.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('segment_id', props.segmentId)
    form.append('user_text', joinedUserText.value)
    result.value = await api.post('/mastery/dictation/check', form)
    emit('checked', result.value)
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.dictation-check {
  width: 100%;
  max-width: 680px;
  margin: 0 auto;
  text-align: left;
}

.desc {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-muted);
  margin-bottom: 10px;
}

.slot-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
  font-size: 12px;
  color: var(--text-muted);
}

.slot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
  gap: 12px;
}

.slot-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.slot-cell.overflow .slot-index {
  color: #8bc8ff;
}

.slot-index {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
}

.slot-input {
  width: 100%;
  min-height: 46px;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--text);
  font-size: 15px;
  line-height: 1.3;
  text-align: center;
}

.slot-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(74, 144, 226, 0.12);
}

.slot-input.soft-slot {
  border-style: dashed;
  border-color: rgba(139, 200, 255, 0.4);
}

.overflow-tip {
  margin-top: 12px;
  font-size: 12px;
  color: #8bc8ff;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}

.error-msg {
  margin-top: 12px;
  font-size: 13px;
  color: var(--error);
}

.result-panel {
  margin-top: 18px;
  padding: 18px;
  border-radius: 14px;
  border: 1px solid var(--border);
  background: var(--bg-card);
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.summary-card {
  padding: 12px;
  border-radius: 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  text-align: center;
}

.summary-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text);
}

.summary-label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-muted);
}

.error-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}

.error-item {
  padding: 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg);
}

.error-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.error-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.error-badge.replace {
  background: rgba(231, 76, 60, 0.14);
  color: #ff8e86;
}

.error-badge.missing {
  background: rgba(241, 196, 15, 0.14);
  color: #f6d365;
}

.error-badge.extra {
  background: rgba(52, 152, 219, 0.14);
  color: #8bc8ff;
}

.error-index {
  font-size: 12px;
  color: var(--text-muted);
}

.error-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.error-line {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.line-label {
  min-width: 30px;
  font-size: 12px;
  color: var(--text-muted);
}

.line-text {
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
}

.all-correct {
  margin-bottom: 16px;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgba(39, 174, 96, 0.1);
  color: #9fe3b1;
  font-size: 14px;
  font-weight: 600;
}

.token-section + .token-section {
  margin-top: 14px;
}

.token-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-muted);
}

.token-flow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.token-chip {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg);
  font-size: 14px;
  color: var(--text);
}

.token-chip.correct {
  border-color: rgba(39, 174, 96, 0.38);
  background: rgba(39, 174, 96, 0.12);
}

.token-chip.replace {
  border-color: rgba(231, 76, 60, 0.4);
  background: rgba(231, 76, 60, 0.12);
}

.token-chip.missing {
  border-color: rgba(241, 196, 15, 0.4);
  background: rgba(241, 196, 15, 0.12);
}

.token-chip.extra {
  border-color: rgba(52, 152, 219, 0.4);
  background: rgba(52, 152, 219, 0.12);
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 16px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.legend-dot.correct {
  background: #27ae60;
}

.legend-dot.replace {
  background: #e74c3c;
}

.legend-dot.missing {
  background: #f1c40f;
}

.legend-dot.extra {
  background: #3498db;
}

@media (max-width: 640px) {
  .slot-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .action-row {
    justify-content: stretch;
  }

  .action-row button {
    flex: 1;
  }
}
</style>
