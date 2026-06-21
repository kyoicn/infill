import { execSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev 模式下显示 git tag + 短 SHA + 脏标记，让侧边栏的"部署版本"在本地
// 也有用。生产构建走 Dockerfile 的 VITE_APP_VERSION env，那个会赢。
//
// vite 8/rolldown 对 `define: 'import.meta.env.VITE_*'` 不再做替换；
// 而 import.meta.env 的字段又只认 .env 文件里的 VITE_*。于是我们生成一份
// .env.local — vite 自己读，优先级最高且不进 git。
function resolveAppVersion(): string {
  if (process.env.VITE_APP_VERSION) return process.env.VITE_APP_VERSION
  try {
    const tag = execSync('git describe --tags --abbrev=0', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
    const sha = execSync('git rev-parse --short HEAD', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
    const dirty = execSync('git status --porcelain', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim() ? '+' : ''
    return `dev · ${tag} · ${sha}${dirty}`
  } catch {
    return 'dev'
  }
}
const APP_VERSION = resolveAppVersion()
// 把版本写进 .env.local，让 vite 的 loadEnv 接到。生产里 .env.local 不存在，
// 生产 build 时 Dockerfile 设的 VITE_APP_VERSION 环境变量本身会被 vite 看到。
const __dirname = dirname(fileURLToPath(import.meta.url))
try {
  writeFileSync(resolve(__dirname, '.env.local'), `VITE_APP_VERSION=${APP_VERSION}\n`, 'utf8')
} catch {
  // 写不进去就放弃 — 不影响主流程
}

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
