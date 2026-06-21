import { execSync } from 'node:child_process'
import { watch as fsWatch } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const __dirname = dirname(fileURLToPath(import.meta.url))

// 侧边栏版号在 dev 模式也显示 `dev · v0.4.9 · ce617d0+`。生产 build 走
// Dockerfile 的 VITE_APP_VERSION env，那个会赢。
//
// 实现：vite 虚拟模块 + .git/logs/HEAD 文件监听。
// - load() 现跑 git 命令拿 HEAD
// - 监听 .git/logs/HEAD（每次 commit / reset / rebase 都会被改写），变化
//   时让虚拟模块失效 + 通知浏览器 full-reload。整套效果：commit 完不用
//   重启 vite，硬刷或等 HMR 立即看到新 SHA
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
  const gitLogsHead = resolve(__dirname, '..', '.git', 'logs', 'HEAD')
  return {
    name: 'infill-app-version',
    resolveId(id) {
      if (id === virtualId) return resolvedId
    },
    load(id) {
      if (id === resolvedId) {
        return `export default ${JSON.stringify(resolveAppVersion())};`
      }
    },
    configureServer(server) {
      // vite/chokidar 默认忽略 .git/，server.watcher.add() 不一定生效。
      // 直接用 node:fs.watch 监听，不走 vite 的过滤
      try {
        fsWatch(gitLogsHead, { persistent: false }, () => {
          // eslint-disable-next-line no-console
          console.log('[app-version] .git/logs/HEAD changed, invalidating')
          const mod = server.moduleGraph.getModuleById(resolvedId)
          if (mod) server.moduleGraph.invalidateModule(mod)
          server.ws.send({ type: 'full-reload' })
        })
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[app-version] could not watch .git/logs/HEAD:', e)
      }
    },
    // 源码 HMR 时顺手失效一下（兜底）
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
