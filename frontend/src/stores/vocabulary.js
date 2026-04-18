import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api'

export const useVocabStore = defineStore('vocabulary', () => {
  const stats = ref({ total: 0, unknown: 0, learning: 0, mastered: 0 })

  async function fetchStats() {
    stats.value = await api.get('/vocabulary/stats')
  }

  async function fetchWords(state = null, limit = 200, offset = 0) {
    const params = { limit, offset }
    if (state) params.state = state
    return api.get('/vocabulary/', { params })
  }

  return { stats, fetchStats, fetchWords }
})
