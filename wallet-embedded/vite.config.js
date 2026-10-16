import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
    base: './',
    build: {
        outDir: '../generated-artifacts/wallet-sdk',
        emptyOutDir: true,
        rollupOptions: {
            input: fileURLToPath(new URL('./src/main.jsx', import.meta.url)),
            output: {
                entryFileNames: 'embedded-wallet.js',
                chunkFileNames: 'chunks/[name]-[hash].js',
                assetFileNames: 'assets/[name]-[hash][extname]',
            },
        },
    },
});
