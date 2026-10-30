# Daybook frontend (PWA)

Vite + React + TypeScript, `vite-plugin-pwa` in `injectManifest` mode with our own service worker,
React Router, `lucide-react`, plain CSS with design tokens. Versions are pinned exactly (`.npmrc` has
`save-exact=true`). The HTTP contract is `../docs/API.md`.

## Run

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api → http://localhost:8300 (start the backend first)
npm run typecheck  # tsc --noEmit
npm run build      # typecheck + production build into dist/ (app shell, sw.js, manifest)
npm run preview    # serve dist/ locally (the service worker only runs in the built app)
npm run icons      # regenerate the PNG icons in public/ (Node built-ins only)
```

Time travel (dev/demo only): `localStorage.setItem("daybook.debugNow", "2026-10-27T19:05:00+05:30")`
makes every request send `X-Debug-Now`.

## Structure

```
index.html              iOS meta tags, apple-touch-icon
vite.config.ts          dev port 5173, /api proxy, PWA manifest + injectManifest config
public/                 generated icons (192, 512, 512 maskable, apple-touch, favicon)
scripts/gen-icons.mjs   icon generator
src/
  main.tsx              mounts the app, registers the service worker
  sw.ts                 Workbox precache + SPA fallback, push → showNotification, notificationclick
  App.tsx               routes and role guards
  me.tsx                GET /api/me context (role, settings, demo, features)
  api.ts                typed fetch wrapper for every endpoint, ApiError, sendBeacon end
  types.ts              types from docs/API.md
  audio.ts              one reused <audio>: unlock on first tap, playTurn, "Tap to hear" fallback
  styles.css            design tokens (light + dark), components, reduced motion
  lib/                  storage (try/catch localStorage), format, platform, trace helpers
  components/           Page + TabBar + DemoBanner, Ring, PendingCard, SafetyCard, ItemCard, SummaryBody
  screens/              Landing, Today, Summary, Timeline, ItemDetail, Ask, Remind, Settings
  screens/reflect/      Reflect (live session), Composer (auto-send), BoardPanel, Transcript, Replay, HowItDecided
  screens/admin/        AdminOverview (KPIs, sessions, evals), AdminTrace (per-turn trace view)
```

## Notes

- **Auto-send:** one input event adding ≥ 20 chars counts as dictation; if the text then stays unchanged
  for 1.5 s it sends (ring visible). Any smaller change cancels. Typed text sends on Enter.
  `dictated_chars` counts the burst characters.
- **Offline:** each turn has a `client_turn_id` and is kept in `localStorage` until acknowledged, then
  retried with the same id when the network is back. The unsent draft is saved per session.
- **Audio on iOS:** the tap that starts a reflection unlocks the audio element. If a reflection is opened
  without a tap (notification, reload), the Reflect screen shows "Start reflection" first.
- **Push:** Settings → Enable reminders (owner only). On iPhone this works only in the installed app.
