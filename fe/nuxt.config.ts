// https://nuxt.com/docs/api/configuration/nuxt-config
import Aura from '@primeuix/themes/aura'

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },

  modules: ['@primevue/nuxt-module'],
  css: ['primeicons/primeicons.css'],

  primevue: {
    options: {
      theme: { preset: Aura },
    },
  },

  runtimeConfig: {
    public: {
      // Overridden at runtime by NUXT_PUBLIC_API_BASE.
      apiBase: 'http://localhost:8000',
    },
  },

  vite: {
    server: {
      // Bind-mount file events don't reach Docker on Windows/macOS; poll instead.
      watch: process.env.DEV_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
    },
  },
})
