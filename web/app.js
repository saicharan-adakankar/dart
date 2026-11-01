'use strict';
const $ = id => document.getElementById(id);
let jobId = null, pollTimer = null, generation = 0, busy = false, selectedPlatform = 'auto';
const names = {video:'video',photo:'photo',profile:'profile picture',file:'file'};
const audioFormats = new Set(['mp3','m4a','wav']);
const platforms = {YouTube:['youtube.com','youtu.be'],Instagram:['instagram.com'],Facebook:['facebook.com','fb.watch'],X:['x.com','twitter.com']};
const formatLabels = {original:'Original · keep source',mp4:'MP4 · video',mkv:'MKV · video',webm:'WebM · video',mp3:'MP3 · audio',m4a:'M4A · audio',wav:'WAV · audio',png:'PNG',jpg:'JPEG',webp:'WebP'};
const formatNotes = {
  original:'Keep the source format. No conversion or re-encoding.',
  mp4:'H.264 video + AAC audio for broad compatibility. Re-encoding can take time and may change quality.',
  mkv:'Move the source streams into an MKV container without re-encoding.',
  webm:'VP9 video + Opus audio. Re-encoding can take time and may change quality.',
  mp3:'Audio only, encoded as high-quality variable-bitrate MP3. The video is omitted.',
  m4a:'Audio only, encoded as 256 kbps AAC. The video is omitted.',
  wav:'Audio only, decoded to uncompressed PCM. This can make a much larger file; it cannot restore lost quality.',
  png:'Convert PNG, JPEG or WebP input to a PNG still image. The first frame is used.',
  jpg:'Convert PNG, JPEG or WebP input to a JPEG still image. Transparency and animation are not preserved.',
  webp:'Convert PNG, JPEG or WebP input to a lossless WebP still image. The first frame is used.'
};
function kind() { return document.querySelector('input[name="kind"]:checked').value; }
function detect(value) {
  try {
    const url = new URL(value);
    if (!['http:','https:'].includes(url.protocol)) return null;
    const host = url.hostname.toLowerCase().replace(/^www\./,'');
    for(const [name,domains] of Object.entries(platforms)) {
      if(domains.some(d=>host===d || host.endsWith('.'+d))) return {name,host};
    }
    return {name:'Web',host};
  } catch { return null; }
}
function syncSource() {
  const detected = detect($('url').value.trim());
  $('source').textContent = detected ? detected.name==='Web' ? detected.host : detected.name : '';
  $('clear').hidden = !$('url').value;
  document.querySelectorAll('.platform-tile').forEach(tile=>{
    const selected = tile.dataset.platform===selectedPlatform;
    tile.classList.toggle('selected',selected);
    tile.classList.toggle('detected',selectedPlatform==='auto' && detected?.name===tile.dataset.platform);
    tile.setAttribute('aria-pressed',String(selected));
  });
  $('platform-status').textContent = selectedPlatform==='auto' ? detected ? `Detected ${detected.name==='Web' ? 'website' : detected.name}` : 'Auto-detect' : `${selectedPlatform==='Web' ? 'Website' : selectedPlatform} selected`;
}
function syncHints() {
  const name = selectedPlatform==='auto' ? '' : selectedPlatform==='Web' ? 'website' : selectedPlatform;
  const noun = names[kind()];
  $('url').placeholder = name ? `Paste a ${name} ${noun} link` : kind()==='file' ? 'Paste a direct file URL' : `Paste a ${noun} link`;
  $('panel-title').textContent = name ? `${name} downloader` : 'Your next download';
  $('inspect').firstElementChild.textContent = `Find my ${noun}`;
  $('hint').textContent = kind()==='profile' ? 'Publicly visible avatars, including on private accounts.' : kind()==='file' ? 'Use a direct file URL, rather than a webpage.' : 'Public links only. We’ll detect the source.';
}
async function api(path,body) {
  const response = await fetch(path,body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}
function setBusy(value) {
  busy=value;
  for(const id of ['inspect','download','url','types','clear','quality','format']) $(id).disabled=value;
  document.querySelectorAll('.platform-tile').forEach(tile=>{tile.disabled=value;});
  if(!value) syncFormat();
}
function syncFormat() {
  const format=$('format').value || 'original';
  const audio=kind()==='video' && audioFormats.has(format);
  $('quality-row').hidden=kind()!=='video' || audio;
  $('quality').disabled=busy || audio;
  $('format-note').textContent=kind()==='file' ? 'General files keep their existing format. Arbitrary file types cannot be converted generically.' : formatNotes[format];
}
function message(text,progress) {
  $('status-area').hidden=false;
  $('status').textContent=text;
  $('cancel').hidden=!busy;
  if(typeof progress==='number') $('progress').value=progress;
  else $('progress').removeAttribute('value');
}
function fail(error) {
  setBusy(false); $('status-area').hidden=true;
  $('error').hidden=false; $('error').textContent=error.message;
}
function reset(focus=false) {
  generation++; clearTimeout(pollTimer);
  const previous=jobId; jobId=null;
  if(previous) api(`/api/jobs/${previous}/cancel`,{}).catch(()=>{});
  setBusy(false); $('result').hidden=true; $('save').hidden=true; $('status-area').hidden=true; $('error').hidden=true;
  $('form').hidden=false; $('download').hidden=false;
  if(focus) $('url').focus();
}
async function poll(token) {
  try {
    const job=await api(`/api/jobs/${jobId}`);
    if(token!==generation) return;
    if(job.state==='error' || job.state==='cancelled') {
      $('download').hidden=true;
      throw new Error(job.error || 'Download cancelled. Choose New link to start again.');
    }
    if(job.state==='ready') {
      setBusy(false); $('status-area').hidden=true; $('form').hidden=true; $('result').hidden=false;
      $('title').textContent=job.title; $('result-source').textContent=job.platform;
      $('quality').replaceChildren(); $('format').replaceChildren();
      const qualities=kind()==='video' ? [['best','Best available'],...job.qualities.map(q=>[q,`Up to ${q}p`])] : [['original','Source quality']];
      for(const [value,label] of qualities) $('quality').add(new Option(label,value));
      for(const value of job.formats || ['original']) $('format').add(new Option(formatLabels[value] || value.toUpperCase(),value));
      $('quality-note').textContent=kind()==='video' ? 'Source resolution is never upscaled. Conversion cannot recover quality already lost.' : kind()==='file' ? 'Your file is streamed unchanged.' : kind()==='profile' ? 'Uses the best avatar source exposed by the platform. No artificial sharpening or upscaling; Original preserves its bytes.' : 'A page may expose a compressed preview rather than the original image.';
      syncFormat(); $('download').hidden=false; $('save').hidden=true; $('result').focus(); return;
    }
    if(job.state==='done') {
      setBusy(false); $('download').hidden=true; $('save').hidden=false;
      $('quality').disabled=true; $('format').disabled=true;
      $('save').href=`/api/jobs/${jobId}/file`; $('save').download=job.filename;
      $('save').firstChild.textContent=`Save ${job.filename.split('.').pop().toUpperCase()} file `;
      message('Ready. Your file is within reach.',100); $('cancel').hidden=true;
      $('save').focus(); return;
    }
    const text=job.state==='queued' ? 'Queued. Your file is up next…' : job.stage==='converting' ? 'Converting to your chosen format…' : $('result').hidden ? 'Finding available media…' : 'Retrieving your file…';
    message(text,job.progress);
    pollTimer=setTimeout(()=>poll(token),900);
  } catch(error) { if(token===generation) fail(error); }
}
$('form').addEventListener('submit',async event=>{
  event.preventDefault(); if(busy) return;
  const detected=detect($('url').value.trim());
  if(selectedPlatform!=='auto' && detected && detected.name!==selectedPlatform) {
    fail(new Error(`This looks like ${detected.name==='Web' ? 'a website' : detected.name}, but ${selectedPlatform} is selected. Choose the matching tile or Auto.`)); return;
  }
  const token=++generation; $('error').hidden=true; setBusy(true); message('Connecting to the source…');
  try {
    const data=await api('/api/inspect',{url:$('url').value.trim(),kind:kind()});
    if(token!==generation){api(`/api/jobs/${data.id}/cancel`,{}).catch(()=>{});return;}
    jobId=data.id; poll(token);
  } catch(error) { if(token===generation) fail(error); }
});
$('download').addEventListener('click',async()=>{
  if(busy || !jobId) return;
  const token=generation; setBusy(true); $('error').hidden=true; message('Preparing your download…');
  try { await api(`/api/jobs/${jobId}/download`,{quality:audioFormats.has($('format').value) ? 'best' : $('quality').value,format:$('format').value}); if(token===generation) poll(token); }
  catch(error) { if(token===generation) fail(error); }
});
$('url').addEventListener('input',()=>{syncSource(); $('error').hidden=true;});
$('clear').addEventListener('click',()=>{$('url').value=''; syncSource(); $('error').hidden=true; $('url').focus();});
$('format').addEventListener('change',syncFormat);
for(const input of document.querySelectorAll('input[name="kind"]')) input.addEventListener('change',()=>{reset();syncHints();});
for(const tile of document.querySelectorAll('.platform-tile')) tile.addEventListener('click',()=>{
  if(busy) return;
  reset(); selectedPlatform=tile.dataset.platform; syncSource(); syncHints();
});
$('cancel').addEventListener('click',()=>reset(true)); $('reset').addEventListener('click',()=>reset(true));
function syncTheme() {
  const light=document.documentElement.dataset.theme==='light';
  $('theme-toggle').setAttribute('aria-pressed',String(light));
  $('theme-toggle').setAttribute('aria-label',`Switch to ${light ? 'dark' : 'light'} mode`);
  $('theme-label').textContent=light ? 'Dark' : 'Light';
  document.querySelector('meta[name="theme-color"]').content=light ? '#faf9fc' : '#000000';
}
$('theme-toggle').addEventListener('click',()=>{
  const theme=document.documentElement.dataset.theme==='light' ? 'dark' : 'light';
  document.documentElement.dataset.theme=theme;
  try {localStorage.setItem('dart-theme',theme);} catch { /* Theme still works without storage. */ }
  syncTheme();
});
window.addEventListener('storage',event=>{
  if(event.key==='dart-theme') {document.documentElement.dataset.theme=event.newValue==='light' ? 'light' : 'dark';syncTheme();}
});
// CSS media queries respond directly to viewport and orientation changes.
// Do not lock screen orientation: portrait, landscape and split-view remain usable.
syncTheme(); syncSource(); syncHints();
