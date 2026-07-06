import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: vite on :5173 proxies /api and /media to Django on :8000.
// Build: `npm run build` -> dist/, served by Django's catch-all view.
export default defineConfig({
  plugins: [react()],
  // assets are served by Django under /static/ in production builds
  base: '/static/',
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/media': 'http://localhost:8000',
    },
  },
})
