import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// GitHub project Pages needs a subpath; Vercel and `vite preview` use `/`.
const base = process.env.GITHUB_PAGES === 'true' ? '/tube-delay-dynamics/' : '/'

export default defineConfig({
  plugins: [react()],
  base,
  server: {
    host: '127.0.0.1',
    port: 43145,
    proxy: {
      '/api/collector-status': {
        target: 'https://tube-delay-dynamics.vercel.app',
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 43145,
  },
})
