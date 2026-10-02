// https://nuxt.com/docs/api/configuration/nuxt-config
import Aura from '@primeuix/themes/aura'

export default defineNuxtConfig({

  modules: ['@nuxt/eslint', '@primevue/nuxt-module'],
  devtools: { enabled: true },
  css: ['primeicons/primeicons.css'],

  runtimeConfig: {
    public: {
      // Overridden at runtime by NUXT_PUBLIC_API_BASE.
      apiBase: 'http://localhost:8000',
    },
  },
  compatibilityDate: '2025-07-15',

  vite: {
    server: {
      // Bind-mount file events don't reach Docker on Windows/macOS; poll instead.
      watch: process.env.DEV_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
    },
  },

  typescript: {
    strict: true,
  },

  // Formatting rules live in ESLint (no Prettier), so `npm run lint:fix` formats too.
  eslint: {
    config: { stylistic: true },
  },

  primevue: {
    options: {
      theme: { preset: Aura },
    },
  },
})
