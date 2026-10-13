from pathlib import Path
from model_spike import podcast
root = Path(__file__).resolve().parent
font = (root.parents[1] / 'app/src/moviestar/fonts/Inter-Regular.ttf').as_uri()
bold = (root.parents[1] / 'app/src/moviestar/fonts/Inter-Bold.ttf').as_uri()
def video(name, node):
    return f'<div class="media" data-node="{node}" data-label="VIDEO: {name}"><video muted playsinline preload="auto" src="{(root/(name.lower()+".mp4")).as_uri()}"></video></div>'
logo = f'<div class="logo" data-node="logo" data-label="IMAGE: logo"><img src="{(root/"logo.png").as_uri()}"></div>'

scenes = {
'podcast': podcast('alice', 'bob'),
'podcast_alt': podcast('bob', 'alice', 'Another scene, same ListenerPair'),
'civic': f'''<div class="root civic" data-node="scene"><div class="civic-tag" data-node="tag">CITY RECAP</div><div class="civic-title" data-node="title">What changed at city hall this week?</div><div class="civic-video" data-node="footage">{video('CIVIC','civic-video')}</div><div class="civic-caption" data-node="caption">The council approved a new plan<br>for our neighborhoods.</div>{logo}</div>''',
'explainer': f'''<div class="root explainer" data-node="scene"><div class="text-panel" data-node="text-panel">{logo}<div class="eyebrow" data-node="eyebrow">THE EXPLAINER</div><div class="headline" data-node="headline">A longer headline that wraps naturally beside the speaker</div><div class="subhead" data-node="subhead">Layout should respond to copy, source footage and the final canvas without a new preset.</div></div><div class="speaker" data-node="speaker">{video('HOST','speaker-video')}</div></div>''',
'static': f'''<div class="root static" data-node="scene"><div class="static-card" data-node="card">{logo}<div class="headline" data-node="headline">A scene with text and images only</div><div class="subhead" data-node="subhead">No video source and no implicit media clock.</div></div></div>''',
}

style = f'''@font-face{{font-family:Inter;src:url("{font}")}}@font-face{{font-family:Inter;src:url("{bold}");font-weight:700}}
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#101b2b;color:#fff;font-family:Inter,sans-serif}}.root{{position:absolute;inset:0;overflow:hidden;background:#101b2b}}.media{{position:relative;width:100%;height:100%;overflow:hidden;background:#345}}.media video{{display:block;width:100%;height:100%;object-fit:cover}}.logo{{width:96px;height:96px;position:absolute;right:32px;top:28px}}.logo img{{width:100%;height:100%;object-fit:contain}}.layout .media video,.layout .logo img{{visibility:hidden}}.layout [data-label]::after{{content:attr(data-label);position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;background:rgba(54,110,148,.78);border:2px dashed #ffe07b;font-size:20px;font-weight:700}}.layout .logo::after{{font-size:11px}}
.podcast{{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);grid-template-rows:auto 1fr auto;gap:22px;padding:34px}}.pod-title{{grid-column:1/-1;font-size:42px;font-weight:700}}.pod-main,.pair{{min-height:0;overflow:hidden;border-radius:16px}}.pair{{display:grid;grid-template-rows:1fr 1fr;gap:16px;transform:translateY(var(--pair-y,0px))}}.pair .listener{{min-height:0;overflow:hidden;border-radius:14px}}.pod-caption{{grid-column:1/-1;min-height:92px;display:flex;align-items:center;justify-content:center;font-size:28px;text-align:center;background:#253a53;border-radius:12px;padding:14px}}
.civic{{display:grid;grid-template-columns:1fr;grid-template-rows:auto auto 1fr auto;gap:18px;padding:36px}}.civic-tag{{width:max-content;padding:10px 16px;background:#f0bb39;color:#17233b;font-size:21px;font-weight:700;transform:translateX(var(--tag-x,0px))}}.civic-title{{font-size:44px;font-weight:700;max-width:850px;line-height:1.1}}.civic-video{{min-height:0;overflow:hidden;border-radius:18px}}.civic-caption{{height:100px;display:flex;align-items:center;justify-content:center;text-align:center;font-size:30px;line-height:1.2;background:#192b40;border-radius:10px}}
.explainer{{display:grid;grid-template-columns:55% 45%;gap:0}}.text-panel{{position:relative;background:#182b47;padding:140px 48px 50px;overflow:hidden}}.text-panel .logo{{left:48px;top:32px;right:auto}}.eyebrow{{font-size:18px;color:#ffcf56;font-weight:700;letter-spacing:2px}}.headline{{font-size:52px;font-weight:700;line-height:1.08;margin-top:26px;max-width:640px}}.subhead{{font-size:25px;line-height:1.35;margin-top:24px;color:#d2dce8}}.speaker{{min-height:0;overflow:hidden}}.static{{display:grid;place-items:center;padding:70px}}.static-card{{position:relative;width:min(900px,100%);background:#23405e;padding:145px 54px 70px;border-radius:28px}}.static-card .logo{{left:50px;top:28px;right:auto}}
@media (orientation:portrait){{.podcast{{grid-template-columns:1fr;grid-template-rows:auto 50% 1fr auto;gap:16px;padding:28px}}.pod-title,.pod-caption{{grid-column:1}}.pod-title{{font-size:49px}}.pair{{grid-template-columns:1fr 1fr;grid-template-rows:1fr}}.pod-caption{{font-size:34px;min-height:132px}}.civic{{grid-template-rows:auto auto 1fr auto;padding:32px}}.civic-title{{font-size:60px;max-width:90%}}.civic-caption{{font-size:36px;height:145px}}.explainer{{grid-template-columns:1fr;grid-template-rows:55% 45%}}.text-panel{{padding:145px 48px 35px}}.headline{{font-size:62px}}.subhead{{font-size:28px}}}}
.hybrid .root,.hybrid .media{{background:transparent}}.hybrid .media video{{visibility:hidden}}
.without-tag .civic-tag{{visibility:hidden}}.tag-only .root > *:not(.civic-tag){{visibility:hidden}}
'''
script = '''<script>
function measuredLines(e) {
  const grouped=new Map(); const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT); let n;
  while(n=walker.nextNode()) for(const m of n.textContent.matchAll(/\\S+/g)) {
    const r=document.createRange();r.setStart(n,m.index);r.setEnd(n,m.index+m[0].length);
    const y=Math.round(r.getBoundingClientRect().y);
    if(!grouped.has(y))grouped.set(y,[]);grouped.get(y).push(m[0]);
  }
  return [...grouped].sort((a,b)=>a[0]-b[0]).map(([y,words])=>({y,text:words.join(' ')}));
}
window.setMoment = async function(t, view) {
  document.body.classList.toggle('layout', view === 'layout');
  const transparent = ['hybrid','hybrid-static','tag-only'].includes(view);
  document.body.classList.toggle('hybrid', transparent);
  document.body.classList.toggle('without-tag', view === 'hybrid-static');
  document.body.classList.toggle('tag-only', view === 'tag-only');
  document.documentElement.style.background = transparent ? 'transparent' : '#101b2b';
  document.body.style.background = transparent ? 'transparent' : '#101b2b';
  document.documentElement.style.setProperty('--pair-y', (Math.sin(t/6*Math.PI)*18).toFixed(2)+'px');
  document.documentElement.style.setProperty('--tag-x', (t*12).toFixed(2)+'px');
  await Promise.all([...document.querySelectorAll('video')].map(v => new Promise(resolve => { const done=()=>resolve(); v.addEventListener('seeked',done,{once:true}); v.currentTime=Math.min(t,5.9); setTimeout(done,1000); })));
  await document.fonts.ready;
  return [...document.querySelectorAll('[data-node]')].map(e => {const r=e.getBoundingClientRect(); return {id:e.dataset.node, x:r.x,y:r.y,width:r.width,height:r.height, text:e.innerText||'',lines:measuredLines(e)};});
};
</script>'''
for name, body in scenes.items():
    (root/f'{name}.html').write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{style}</style></head><body>{body}{script}</body></html>')

(root/'calibration.html').write_text('''<!doctype html><html><head><style>
html,body{margin:0;width:1280px;height:720px;background:#101b2b}
.box{position:absolute;left:34px;top:107px;width:793px;height:465px;
background:#f23e45;border-radius:16px}
</style></head><body><div class="box"></div></body></html>''')

transition_script = '''<script>
window.setTransition = async function(t, view) {
  document.body.classList.toggle('layout', view === 'layout');
  const p=Math.max(0,Math.min(1,(t-5.5)/0.5));
  document.querySelector('#outgoing').style.opacity=1;
  document.querySelector('#incoming').style.opacity=p;
  document.querySelector('#outgoing').style.setProperty('--pair-y',(Math.sin(Math.min(t,6)/6*Math.PI)*18).toFixed(2)+'px');
  document.querySelector('#outgoing').style.setProperty('--tag-x',(t*12).toFixed(2)+'px');
  document.querySelector('#incoming').style.setProperty('--tag-x',((t-5.5)*12).toFixed(2)+'px');
  await Promise.all([...document.querySelectorAll('video')].map(v => new Promise(resolve => {const done=()=>resolve();v.addEventListener('seeked',done,{once:true});v.currentTime=Math.max(0,Math.min(v.closest('#incoming') ? t-5.5 : t,5.9));setTimeout(done,1000)})));
  await document.fonts.ready;
  return [...document.querySelectorAll('[data-node]')].map(e => {const r=e.getBoundingClientRect();return {tree:e.closest('#incoming')?'incoming':'outgoing',id:e.dataset.node,x:r.x,y:r.y,width:r.width,height:r.height}});
};
</script>'''
for name, incoming in [('transition_structured',scenes['civic']),('transition_legacy',f'<div class="root" data-node="legacy-scene">{video("HOST","legacy-video")}</div>')]:
    body=f'<div id="outgoing" class="transition-layer">{scenes["podcast"] if name=="transition_structured" else scenes["civic"]}</div><div id="incoming" class="transition-layer">{incoming}</div>'
    (root/f'{name}.html').write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{style}.transition-layer{{position:absolute;inset:0}}</style></head><body>{body}{transition_script}</body></html>')
