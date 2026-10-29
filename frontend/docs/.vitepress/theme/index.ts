import type { Theme } from 'vitepress'
import DefaultTheme from 'vitepress/theme'
import Layout from './Layout.vue'
import { setupPageEnhance } from './setup'
import './styles/index.css'
import './styles/brand-tokens.generated.css'

export default {
  extends: DefaultTheme,
  Layout,
  setup: () => setupPageEnhance()
} satisfies Theme