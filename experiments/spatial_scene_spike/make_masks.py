from pathlib import Path
import json
root=Path(__file__).resolve().parent
(root/'masks').mkdir(exist_ok=True)
records=json.loads((root/'browser_results.json').read_text())['scenes']
media={'podcast':[('host-video',16),('alice-video',14),('bob-video',14)],'civic':[('civic-video',18)],'explainer':[],'static':[]}
manifest=[];seen=set()
for key,rec in records.items():
    if '-' not in key:continue
    scene=key.split('-',1)[0];boxes={b['id']:b for b in rec['boxes']}
    for node,radius in media[scene]:
        b=boxes[node];w=round(b['width']);h=round(b['height']);name=f'mask_{w}x{h}_r{radius}'
        if name in seen:continue
        seen.add(name);manifest.append({'name':name,'width':w,'height':h,'radius':radius})
        (root/f'{name}.html').write_text(f'<!doctype html><html><head><style>html,body{{margin:0;width:100%;height:100%;background:#000}}.box{{width:{w}px;height:{h}px;background:#fff;border-radius:{radius}px}}</style></head><body><div class="box"></div></body></html>')
(root/'mask_manifest.json').write_text(json.dumps(manifest,indent=2))
print('masks',len(manifest))
