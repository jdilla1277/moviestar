// Captions and border colors may reuse decoders only when every playback input,
// source identity, geometry sample, range and audio route is unchanged.
const withoutBorderColors=nodes=>nodes.map(node=>{
  const next={...node};
  if(node.style){next.style={...node.style};delete next.style['border-color'];}
  if(node.rule_sources){next.rule_sources={...node.rule_sources};delete next.rule_sources['border-color'];}
  if(node.children)next.children=withoutBorderColors(node.children);
  return next;
});
export function playbackKey(packet){
  const {state_id,request_id,requested_at,caption_plane,audio_plane,prepared_audio,...playback}=packet;
  if(playback.initial?.nodes)playback.initial={...playback.initial,nodes:withoutBorderColors(playback.initial.nodes)};
  if(playback.segments)playback.segments=playback.segments.map(s=>({...s,packet:playbackKey(s.packet)}));
  return JSON.stringify(playback);
}

export function prepareBorderColors(entries,initial,signal){
  signal?.throwIfAborted();
  const nodes=new Map(),walk=list=>{for(const node of list){nodes.set(JSON.stringify(node.path),node);walk(node.children??[]);}};
  walk(initial.nodes);
  const updates=entries.map(entry=>{
    const node=nodes.get(JSON.stringify(entry.node.path));
    if(!node)throw Error('Native border update changed the scene structure');
    const color=node.style?.['border-color'];
    if(color!==undefined&&!CSS.supports('border-color',color))throw Error('Invalid native border color');
    return{entry,color,origin:node.rule_sources?.['border-color']};
  });
  return()=>{
    signal?.throwIfAborted();
    for(const {entry,color,origin}of updates){
      if(color!==entry.node.style?.['border-color']){
        entry.node.style??={};
        if(color===undefined){entry.element.style.removeProperty('border-color');delete entry.node.style['border-color'];}
        else{entry.element.style.setProperty('border-color',color);entry.node.style['border-color']=color;}
      }
      if(origin!==undefined){entry.node.rule_sources??={};entry.node.rule_sources['border-color']=origin;}
      else if(entry.node.rule_sources)delete entry.node.rule_sources['border-color'];
    }
  };
}

export async function prepareCaptionImages(plane,signal){
  const images=[];
  const dispose=()=>{for(const image of images){image.remove();image.removeAttribute('src');}};
  signal?.throwIfAborted();signal?.addEventListener('abort',dispose,{once:true});
  try{
    await Promise.all((plane?.states??[]).map(state=>{
      const image=new Image(),rect=state.rect;images.push(image);
      Object.assign(image.style,{display:'block',position:'absolute',left:rect.x+'px',top:rect.y+'px',width:rect.width+'px',height:rect.height+'px'});
      image.src='assets/'+state.asset;return image.decode();
    }));
    signal?.throwIfAborted();return{images,dispose};
  }catch(error){dispose();throw error;}
  finally{signal?.removeEventListener('abort',dispose);}
}
