import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // Bind to all interfaces, not just localhost, so another device on the
    // same LAN (e.g. a phone, for testing the camera pipeline against
    // VITAL's own screen) can reach this dev server.
    host: true,
  },
})
