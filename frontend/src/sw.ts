/// <reference lib="webworker" />
// Daybook service worker (vite-plugin-pwa injectManifest).
// Precaches the app shell, serves index.html for navigations (SPA, offline), and shows push
// notifications whose click opens or focuses `data.url`.
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { clientsClaim } from "workbox-core";

declare let self: ServiceWorkerGlobalScope;

self.skipWaiting();
clientsClaim();

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

// SPA navigations fall back to the precached index.html. API calls are never handled here.
registerRoute(
  new NavigationRoute(createHandlerBoundToURL("index.html"), {
    denylist: [/^\/api\//],
  }),
);

interface PushPayload {
  title?: string;
  body?: string;
  url?: string;
  data?: { url?: string };
}

self.addEventListener("push", (event: PushEvent) => {
  let payload: PushPayload = {};
  try {
    payload = (event.data?.json() as PushPayload) ?? {};
  } catch {
    payload = { body: event.data?.text() };
  }
  const title = payload.title || "Daybook";
  const url = payload.data?.url || payload.url || "/reflect?mode=guided&src=push";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || "How did today go?",
      icon: "/pwa-192x192.png",
      badge: "/favicon-32x32.png",
      data: { url },
    }),
  );
});

self.addEventListener("notificationclick", (event: NotificationEvent) => {
  event.notification.close();
  const data = event.notification.data as { url?: string } | undefined;
  const target = new URL(data?.url || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (client.url === target && "focus" in client) return client.focus();
      }
      const sameOrigin = windows.find((c) => new URL(c.url).origin === self.location.origin);
      if (sameOrigin) {
        await sameOrigin.focus();
        return sameOrigin.navigate(target);
      }
      return self.clients.openWindow(target);
    })(),
  );
});
