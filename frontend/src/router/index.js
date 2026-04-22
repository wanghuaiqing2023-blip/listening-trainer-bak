import { createRouter, createWebHistory } from 'vue-router'
import Onboarding from '@/views/Onboarding.vue'
import Upload from '@/views/Upload.vue'
import Library from '@/views/Library.vue'
import Training from '@/views/Training.vue'
import Vocabulary from '@/views/Vocabulary.vue'

const routes = [
  { path: '/', redirect: '/library' },
  { path: '/onboarding', component: Onboarding },
  { path: '/upload', component: Upload },
  { path: '/library', component: Library },
  { path: '/training/:id', component: Training },
  { path: '/vocabulary', component: Vocabulary },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
