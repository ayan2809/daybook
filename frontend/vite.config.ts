import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// vite-plugin-pwa in injectManifest mode: our own service worker (src/sw.ts) gets the precache
// manifest injected at build time and adds push + notificationclick handlers.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "autoUpdate",
      injectRegister: false, // registered in src/main.tsx via virtual:pwa-register
      includeAssets: ["apple-touch-icon.png", "favicon-32x32.png"],
      injectManifest: {
        // Icons, includeAssets and the manifest are added by the plugin itself.
        globPatterns: ["**/*.{js,css,html}"],
      },
      manifest: {
        name: "Daybook",
        short_name: "Daybook",
        description:
          "A 5-minute evening voice conversation about your workday that turns into a resume-ready record.",
        display: "standalone",
        start_url: "/",
        scope: "/",
        theme_color: "#2f5d50",
        background_color: "#f7f4ee",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "maskable-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      devOptions: {
        enabled: false,
        type: "module",
      },
    }),
  ],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://localhost:8300", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
  },
});
