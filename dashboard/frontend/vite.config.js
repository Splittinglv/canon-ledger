import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))

function dashboardSourceFiles() {
  const files = ['index.html', 'package.json', 'package-lock.json', 'vite.config.js']
    .map(relative => path.join(frontendRoot, relative))
  const visit = directory => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name)
      if (entry.isDirectory()) visit(absolute)
      else if (entry.isFile()) files.push(absolute)
    }
  }
  visit(path.join(frontendRoot, 'src'))
  return files.sort((left, right) => {
    const leftRelative = path.relative(frontendRoot, left).split(path.sep).join('/')
    const rightRelative = path.relative(frontendRoot, right).split(path.sep).join('/')
    return leftRelative < rightRelative ? -1 : leftRelative > rightRelative ? 1 : 0
  })
}

function dashboardSourceDigest() {
  const digest = crypto.createHash('sha256')
  for (const absolute of dashboardSourceFiles()) {
    const relative = path.relative(frontendRoot, absolute).split(path.sep).join('/')
    digest.update(relative, 'utf8')
    digest.update(Buffer.from([0]))
    digest.update(fs.readFileSync(absolute))
    digest.update(Buffer.from([0]))
  }
  return digest.digest('hex')
}

function canonLedgerBuildMetadata() {
  return {
    name: 'canon-ledger-build-metadata',
    apply: 'build',
    generateBundle() {
      const packageJson = JSON.parse(
        fs.readFileSync(path.join(frontendRoot, 'package.json'), 'utf8'),
      )
      this.emitFile({
        type: 'asset',
        fileName: 'build-metadata.json',
        source: `${JSON.stringify({
          schema_version: 'canon-ledger-dashboard-build/v1',
          dashboard_version: packageJson.version,
          source_digest: dashboardSourceDigest(),
        }, null, 2)}\n`,
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), canonLedgerBuildMetadata()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'echarts-vendor': ['echarts', 'echarts-for-react'],
        },
      },
    },
  },
})
