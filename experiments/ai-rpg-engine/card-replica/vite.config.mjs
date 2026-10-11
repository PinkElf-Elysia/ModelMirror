import {defineConfig} from 'vite';
import {fileURLToPath} from 'node:url';
export default defineConfig({base:'./',build:{rollupOptions:{input:{earth:fileURLToPath(new URL('./index.html',import.meta.url)),god:fileURLToPath(new URL('./god.html',import.meta.url))}}}});
