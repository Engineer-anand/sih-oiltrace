import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // Relative assets work both on Netlify root and FastAPI /app/.
  base: './',
  server: { port: 5173, host: true },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
}))
