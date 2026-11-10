import {SourceBuffer} from './preview_native_buffer.mjs';

const sourceBuffer=new SourceBuffer();
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('message',event=>{
  if(event.data==='native-source-buffer-stats')event.ports[0]?.postMessage(sourceBuffer.stats);
});
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url),scope=new URL(self.registration.scope);
  const path=url.pathname.slice(scope.pathname.length);
  if(event.request.method!=='GET'||url.origin!==scope.origin||!url.pathname.startsWith(scope.pathname)
      ||!/^assets\/[a-f0-9]{24}$/.test(path)||url.searchParams.get('native_video')!=='1')return;
  event.respondWith(sourceBuffer.respond(event.request).catch(error=>{
    console.error('Native source buffering failed',error);
    return new Response(null,{status:502,headers:{'Cache-Control':'no-store'}});
  }));
});
