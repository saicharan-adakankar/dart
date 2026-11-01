'use strict';
const $ = id => document.getElementById(id);
let jobId = null, pollTimer = null, generation = 0, busy = false;
const names = {video: 'video', photo: 'photo', profile: 'profile picture', file: 'file'};
const placeholders = {video: 'Paste a video link here', photo: 'Paste an image or photo page link', profile: 'Paste a profile or direct avatar link', file: 'Paste a direct file link here'};
function kind() { return document.querySelector('input[name="kind"]:checked').value; }
function source(value) {
  try {
    const host = new URL(value).hostname.toLowerCase().replace(/^www\./, '');
    for (const [name, domains] of Object.entries({YouTube:['youtube.com','youtu.be'], Instagram:['instagram.com'],Facebook:['facebook.com','fb.watch'],X:['x.com','twitter.com']})) {
      if(domains.some(d => host === d || host.endsWith('.' + d))) return name;
    }
    return host;
  } catch { return ''; }
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await r.json();
  if(!r.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}
function setBusy(value) {
  busy=value;
  $('inspect').disabled=value;
  $('download').disabled=value;
  $('url').disabled=value;
  $('types').disabled=value;
  $('clear').disabled=value;
}
function message(text, progress) {
  $('status-area').hidden=false;
  $('status').textContent=text;
  $('cancel').hidden=!busy;
  if(typeof progress === 'number') $('progress').value=progress;
  else $('progress').removeAttribute('value');
}
function fail(error) {
  setBusy(false); $('status-area').hidden=true;
  $('error').hidden=false; $('error').textContent=error.message;
}
async function reset() {
  generation++; clearTimeout(pollTimer);
  const previous=jobId; jobId=null;
  if(previous) api(`/api/jobs/${previous}/cancel`,{}).catch(()=>{});
  setBusy(false); $('result').hidden=true; $('save').hidden=true; $('status-area').hidden=true; $('error').hidden=true;
  $('form').hidden=false; $('url').disabled=false; $('url').focus();
}
async function poll(token) {
  try {
    const job=await api(`/api/jobs/${jobId}`);
    if(token!==generation) return;
    if(job.state==='error' || job.state==='cancelled') throw new Error(job.error || 'Download cancelled.');
    if(job.state==='ready') {
      setBusy(false); $('status-area').hidden=true; $('form').hidden=true; $('result').hidden=false;
      $('title').textContent=job.title; $('result-source').textContent=job.platform;
      $('quality').replaceChildren();
      const qualities=kind()==='video' ? [['best','Best available'],...job.qualities.map(q=>[q,`Up to ${q}p`])] : [['original','Source file · unchanged']];
      for(const [v,label] of qualities) $('quality').add(new Option(label,v));
      $('quality-note').textContent=kind()==='video' ? 'Source streams are merged without re-encoding. Container and codec depend on availability.' : 'No resizing or conversion. A page may provide a compressed image rather than the original.';
      $('download').hidden=false; $('save').hidden=true; return;
    }
    if(job.state==='done') {
      setBusy(false); $('download').hidden=true; $('save').hidden=false;
      $('save').href=`/api/jobs/${jobId}/file`; $('save').download=job.filename;
      message('Ready. Save your file below.',100); $('cancel').hidden=true;
      $('save').focus(); return;
    }
    message(job.state==='queued' ? 'Queued. Your file is up next…' : $('result').hidden ? 'Finding available media…' : 'Retrieving your file…', job.progress);
    pollTimer=setTimeout(()=>poll(token),900);
  } catch(e) { if(token===generation) fail(e); }
}
$('form').addEventListener('submit',async e=>{
  e.preventDefault(); if(busy) return;
  const token=++generation; $('error').hidden=true; setBusy(true); message('Connecting to the source…');
  try { const data=await api('/api/inspect',{url:$('url').value.trim(),kind:kind()}); if(token!==generation){api(`/api/jobs/${data.id}/cancel`,{}).catch(()=>{});return;} jobId=data.id; poll(token); }
  catch(e) { if(token===generation) fail(e); }
});
$('download').addEventListener('click',async()=>{
  if(busy || !jobId) return;
  const token=generation; setBusy(true); $('error').hidden=true; message('Preparing your download…');
  try { await api(`/api/jobs/${jobId}/download`,{quality:$('quality').value}); if(token===generation) poll(token); }
  catch(e) { if(token===generation) fail(e); }
});
$('url').addEventListener('input',()=>{ $('source').textContent=source($('url').value); $('clear').hidden=!$('url').value; $('error').hidden=true; });
$('clear').addEventListener('click',()=>{ $('url').value=''; $('url').dispatchEvent(new Event('input')); $('url').focus(); });
document.querySelectorAll('input[name="kind"]').forEach(input=>input.addEventListener('change',()=>{
  reset(); $('url').placeholder=placeholders[kind()]; $('inspect').firstElementChild.textContent=`Find my ${names[kind()]}`;
  $('hint').textContent=kind()==='profile' ? 'Public avatar metadata or a direct image link.' : kind()==='file' ? 'A direct file URL, rather than a webpage.' : 'Public links only. We’ll detect the source.';
}));
$('cancel').addEventListener('click',reset); $('reset').addEventListener('click',reset);
