import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 端口与代理**冻结**（AGENTS.md「端口约定」）：
//   前端 dev server 5173，/api 代理到后端 127.0.0.1:8000
// 生产构建产物必须能离线直接打开（无 CDN、无在线字体，NFR-9）。
//
// 环境变量只用于**验收脚本**把 /api 指向本机的模拟后端（见 scripts/verify-layout-css-vars.mjs），
// 不传任何变量时行为与冻结约定完全一致。
const apiTarget = process.env.MATHGEN_API_TARGET ?? 'http://127.0.0.1:8000';
const devPort = Number(process.env.MATHGEN_DEV_PORT ?? 5173);
const previewPort = Number(process.env.MATHGEN_PREVIEW_PORT ?? 4173);

export default defineConfig({
  plugins: [react()],
  server: {
    port: devPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: previewPort,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // 不生成 sourcemap：产物要能直接拷到内网机器上跑，体积越小越好
    sourcemap: false,
  },
});
