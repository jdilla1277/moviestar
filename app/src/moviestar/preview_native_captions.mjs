// Caption-only revisions may reuse decoders only when every playback input,
// source identity, layout sample, range and audio route is exactly unchanged.
export function playbackKey(packet){
  const {state_id,request_id,requested_at,caption_plane,audio_plane,prepared_audio,...playback}=packet;
  if(playback.segments)playback.segments=playback.segments.map(s=>({...s,packet:playbackKey(s.packet)}));
  return JSON.stringify(playback);
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
