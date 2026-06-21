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
  build: {
    target: 'es2022',
    // 用 terser 而不是默认 esbuild 做最小化。esbuild 在 derived class
    // super() 内部的 async start 回调里会错误地 inline 临时变量，把
    // `const x = await foo(); this.y = x` 优化成 `this.y = await foo()` —
    // 触发 V8 的 "Must call super constructor before accessing 'this'"。
    // @yume-chan/stream-extra 的 WrapReadableStream 正中此雷。terser 不做这
    // 个 inline 所以正确。代价：build 慢 ~30%，bundle 大同小异。
    minify: 'terser',
  },
  server: {
    // dev 端口约定：前端固定 5173，后端固定 8765（避开 8000 常见冲突）。
    // 生产 docker 容器照旧 8000 — 只有本机 dev 用 8765。
    // strictPort 让 vite 在 5173 被占时报错退出，而不是静默跳 5174（用户
    // 访问 5173 看到空白页半天找不到原因）。
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:8765',
      '/static': 'http://localhost:8765',
    },
  },
})
