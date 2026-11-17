// Minimal service worker for PWA installability
// DO NOT cache API/conversation responses or static assets
// to avoid breaking deploys with stale caches

const CACHE_NAME = 'metapelet-v1';

// Only cache the absolute minimum for offline page
const OFFLINE_URLS = [
  '/talk'
];

self.addEventListener('install', (event) => {
  console.log('Service worker installing...');
  // Skip waiting to activate immediately
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  console.log('Service worker activating...');
  // Clean up old caches
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  // Take control immediately
  return self.clients.claim();
});

// Network-first strategy: always try network, never cache API responses
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  
  // Never intercept API calls - always go to network
  if (url.pathname.startsWith('/chat') || 
      url.pathname.startsWith('/tts') || 
      url.pathname.startsWith('/pilot/') ||
      url.pathname.startsWith('/budget/')) {
    return;
  }
  
  // For everything else, try network first
  event.respondWith(
    fetch(event.request).catch(() => {
      // If network fails and it's a navigation request, return offline page
      if (event.request.mode === 'navigate') {
        return caches.match('/talk');
      }
      return new Response('Offline', { status: 503 });
    })
  );
});
