import { execSync } from 'node:child_process'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// 侧边栏版号在 dev 模式也显示 `dev · v0.4.9 · ce617d0+`。生产 build 走
// Dockerfile 的 VITE_APP_VERSION env，那个会赢。
//
// 之前用 .env.local 一次性生成，每次 commit 都得重启 vite 才会更新 — 烦。
// 改成 vite 虚拟模块：浏览器每次请求 Layout（硬刷或新 tab）vite 会 re-run
// 这个 load()，git 命令现跑现取，永远是最新提交。
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

function appVersionPlugin(): Plugin {
  const virtualId = 'virtual:app-version'
  const resolvedId = '\0' + virtualId
  return {
    name: 'infill-app-version',
    resolveId(id) {
      if (id === virtualId) return resolvedId
    },
    load(id) {
      if (id === resolvedId) {
        // 每次 load 都现取 — 浏览器硬刷就能看到新 commit
        return `export default ${JSON.stringify(resolveAppVersion())};`
      }
    },
    // 文件改动时让虚拟模块失效，触发 HMR 重新 load
    handleHotUpdate({ server }) {
      const mod = server.moduleGraph.getModuleById(resolvedId)
      if (mod) server.moduleGraph.invalidateModule(mod)
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), appVersionPlugin()],
  // @yume-chan/stream-extra 等包里有 class XYZ extends ReadableStream 这种
  // 继承浏览器原生 exotic 类的写法，esbuild 默认会把它降级 ES2017，结果
  // 跑起来报 "Must call super constructor in derived class before accessing
  // 'this' or returning from derived constructor"。把 target 拔到 ES2022
  // 让 esbuild 跳过降级。
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
  // react-draggable（react-resizable 的依赖）里有 `if (process.env.NODE_ENV)` 这种
  // node-only 写法，浏览器跑会抛 ReferenceError: process is not defined。
  // vite 默认不替换这类引用，显式 define 兜住。
  define: {
    'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV ?? 'production'),
  },
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
