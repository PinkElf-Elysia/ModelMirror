import { defineConfig } from 'vite';

// Acceptance uses the built files through ui-host, never the development server.
export default defineConfig({
  base: '/',
  server: { host: '127.0.0.1', strictPort: true, cors: false },
  preview: { host: '127.0.0.1', strictPort: true, cors: false },
  build: { outDir: 'dist', sourcemap: false, target: 'es2022', assetsInlineLimit: 0 },
});
