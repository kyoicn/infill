import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // @yume-chan/stream-extra 等包里有 class XYZ extends ReadableStream 这种
  // 继承浏览器原生 exotic 类的写法，esbuild 默认会把它降级 ES2017，结果
  // 跑起来报 "Must call super constructor in derived class before accessing
  // 'this' or returning from derived constructor"。把 target 拔到 ES2022
  // 让 esbuild 跳过降级。
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
  build: { target: 'es2022' },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
    },
  },
})
