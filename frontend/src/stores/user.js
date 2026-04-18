import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api'

export const useUserStore = defineStore('user', () => {
  const levelScore = ref(5.0)
  const levelLabel = ref('')

  async function fetchLevel() {
    const data = await api.get('/user/level')
    levelScore.value = data.level_score
    levelLabel.value = data.level_label
  }

  async function setLevel(score) {
    const data = await api.put('/user/level', null, { params: { level_score: score } })
    levelScore.value = data.level_score
    levelLabel.value = data.level_label
  }

  return { levelScore, levelLabel, fetchLevel, setLevel }
})
