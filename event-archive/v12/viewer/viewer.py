#!/usr/bin/env python3
"""Read-only Influence V12 dashboard. Run: python3 viewer.py --host 0.0.0.0 --target 599999

Open http://<server-ip>:5012 on your LAN. No dependencies beyond Python 3.
Use --data to select another v12_data directory. Do not expose to the internet.
"""
import argparse
import json
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INTERVAL = 100_000


def read_json(path):
    try:
        with path.open('rb') as stream:
            data = stream.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            return {}
        obj = json.loads(data)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def collect(root, target=None):
    snapshots, deltas, verification = (root / p for p in ('snapshots', 'deltas', 'verification'))
    progress = read_json(snapshots / 'progress.json')
    rows = {}
    for directory, pattern, kind in (
        (snapshots, r'v12-block-(\d+)\.json', 'snapshot'),
        (deltas, r'v12-delta-\d+-(\d+)\.tar\.gz\.report\.json', 'delta'),
        (verification, r'apply-(\d+)\.json', 'apply'),
    ):
        for path in directory.glob('*.json'):
            match = re.fullmatch(pattern, path.name)
            if not match:
                continue
            end = int(match[1])
            report = read_json(path)
            if not report:
                continue
            row = rows.setdefault(end, {'end': end})
            if kind == 'snapshot':
                row.update(snapshot=True, archive_bytes=report.get('archive_bytes'),
                           retrieve=report.get('retrieve_seconds'), drain=report.get('drain_seconds'),
                           snapshot_present=(snapshots / f'v12-block-{end}.archive.gz').is_file())
            elif kind == 'delta':
                row.update(delta=True, delta_bytes=report.get('compressed_bytes'),
                           generation=report.get('generation_seconds'), documents=report.get('target_documents'))
            else:
                row.update(status=report.get('status'), application=report.get('application_seconds'),
                           operations=report.get('completed_operations'))
    verified = -1
    while rows.get(verified + INTERVAL, {}).get('status') == 'applied_checkpoint_verified':
        verified += INTERVAL
    completed = progress.get('last_completed')
    if not isinstance(completed, int):
        completed = None
    usage = shutil.disk_usage(root if root.exists() else ROOT)
    def size(directory, pattern):
        total = 0
        for path in directory.glob(pattern):
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total
    return dict(completed=completed, verified=verified, target=target,
                producer_status=progress.get('status', 'No progress file'),
                free=usage.free, snapshots=size(snapshots, '*.archive.gz'),
                deltas=size(deltas, '*.tar.gz'), rows=sorted(rows.values(), key=lambda r: r['end'], reverse=True),
                refreshed=datetime.now(timezone.utc).isoformat())


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Influence | V12 Observer</title>
<style>
:root{color-scheme:dark;--panel:rgba(12,22,36,.88);--accent:#69e6cd}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:#07101e;color:#e4edf7;font:15px system-ui,sans-serif}#back{position:fixed;inset:0;z-index:-1;background:radial-gradient(ellipse at 75% 15%,#264b65,transparent 60%),linear-gradient(135deg,#091421,#180e32);background-size:cover;background-position:center}main{max-width:1250px;margin:auto;padding:28px}header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:20px}h1{font-size:27px;margin:0;letter-spacing:.03em}h2{font-size:17px;margin:0 0 14px}small,.muted{color:#aabbd0}button,select,label.pick{background:#182a40;border:1px solid #476078;color:inherit;border-radius:8px;padding:8px 12px;cursor:pointer}input[type=range]{accent-color:var(--accent);width:95px}.settings{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.panel{background:var(--panel);border:1px solid #ffffff24;border-radius:14px;padding:18px;box-shadow:0 8px 25px #0002}.number{font-size:29px;font-weight:650;font-variant-numeric:tabular-nums;margin:6px 0}.track{height:7px;border-radius:8px;background:#ffffff20;overflow:hidden;margin:10px 0}.fill{height:100%;background:var(--accent);width:0;transition:width .4s}.section{margin-top:16px}.scroll{overflow:auto;max-height:65vh}table{border-collapse:collapse;width:100%;white-space:nowrap;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:10px;border-bottom:1px solid #ffffff15}th{position:sticky;top:0;background:#162337;color:#aabbd0;font-size:12px}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}.good{color:#69e6cd}.warn{color:#ffd28a}.bad{color:#ff9c9c}#message{margin-top:12px}#bars{display:grid;gap:8px}.barrow{display:grid;grid-template-columns:95px 1fr 105px;align-items:center;gap:10px;font-size:12px}.bar{display:flex;height:12px;border-radius:5px;overflow:hidden;background:#ffffff12}.bar i{display:block;height:100%}.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;margin:12px 0;color:#c5d0de}@media(max-width:600px){main{padding:12px}.number{font-size:24px}}
</style></head><body><div id="back"></div><main><header><div><h1>INFLUENCE · V12 Observer</h1><small>MAINNET RECONSTRUCTION / READ-ONLY</small></div><div class="settings"><select id="theme" aria-label="Background"><option value="nebula">Nebula</option><option value="ocean">Ocean</option><option value="ember">Ember</option><option value="plain">Midnight</option><option value="custom">My image</option></select><label class="pick">Choose image<input id="image" type="file" accept="image/*" hidden></label><label>Panel opacity <input id="opacity" type="range" min="55" max="100" value="88"></label></div></header>
<div class="grid"><div class="panel"><small>RECONSTRUCTED THROUGH</small><div class="number" id="completed">—</div><div class="track"><div class="fill" id="p1"></div></div><small id="producer">Waiting for reports</small></div><div class="panel"><small>VERIFIED THROUGH</small><div class="number" id="verified">—</div><div class="track"><div class="fill" id="p2"></div></div><small id="lag">—</small></div><div class="panel"><small>FREE DISK SPACE</small><div class="number" id="free">—</div><small id="target">—</small></div><div class="panel"><small>RETAINED ARCHIVES</small><div class="number" id="size">—</div><small id="breakdown">—</small></div></div>
<div id="message" role="status" class="muted"></div><div class="panel section"><h2>Processing duration · latest 12 checkpoints</h2><div class="legend"><span style="color:#70b6ff">■ Retrieve</span><span style="color:#69e6cd">■ Drain</span><span style="color:#e5b4ff">■ Delta</span><span style="color:#ffc17e">■ Apply + verify</span></div><div id="bars"></div></div>
<div class="panel section"><h2>Checkpoint history</h2><div class="scroll"><table><thead><tr><th>Block</th><th>Status</th><th>Retrieve</th><th>Drain</th><th>Delta</th><th>Apply + verify</th><th>Snapshot</th><th>Delta size</th><th>Operations</th></tr></thead><tbody id="history"></tbody></table></div></div></main>
<script>
const $=id=>document.getElementById(id), fmt=n=>typeof n==='number'?n.toLocaleString('en-US'):'—', secs=n=>{if(typeof n!=='number'||!Number.isFinite(n)||n<0)return '—';if(n<60)return n.toFixed(1)+' s';const t=Math.round(n),h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=t%60;return h?h+' h '+m+' min':m+' min '+s+' s'};
function bytes(n){if(typeof n!=='number')return '—';for(const [d,u] of [[2**40,'TiB'],[2**30,'GiB'],[2**20,'MiB'],[1024,'KiB']])if(n>=d)return (n/d).toFixed(1)+' '+u;return n+' B'}
function get(k,d){try{return localStorage.getItem(k)??d}catch{return d}}function put(k,v){try{localStorage.setItem(k,v);return true}catch{return false}}
const themes={nebula:'radial-gradient(ellipse at 75% 15%,#264b65,transparent 60%),linear-gradient(135deg,#091421,#180e32)',ocean:'linear-gradient(135deg,#052030,#125b68,#07152e)',ember:'linear-gradient(135deg,#1c101c,#693826,#101728)',plain:'#07101e'};
function appearance(){let t=$('theme').value;let img=get('v12-image','');$('back').style.background=t==='custom'&&img?'url("'+img+'") center/cover no-repeat':themes[t]||themes.nebula;document.documentElement.style.setProperty('--panel',`rgba(12,22,36,${$('opacity').value/100})`);put('v12-theme',t);put('v12-opacity',$('opacity').value)}
$('theme').value=get('v12-theme','nebula');$('opacity').value=get('v12-opacity','88');$('theme').onchange=appearance;$('opacity').oninput=appearance;appearance();
$('image').onchange=()=>{let f=$('image').files[0];if(!f)return;let url=URL.createObjectURL(f),im=new Image();im.onload=()=>{let c=document.createElement('canvas'),scale=Math.min(1,1920/Math.max(im.width,im.height));c.width=Math.max(1,Math.round(im.width*scale));c.height=Math.max(1,Math.round(im.height*scale));c.getContext('2d').drawImage(im,0,0,c.width,c.height);URL.revokeObjectURL(url);if(!put('v12-image',c.toDataURL('image/jpeg',.8))){alert('Could not save image in browser storage');return}$('theme').value='custom';appearance()};im.onerror=()=>{URL.revokeObjectURL(url);alert('Unable to open image')};im.src=url};
function render(d){$('completed').textContent=fmt(d.completed);$('verified').textContent=fmt(d.verified);$('producer').textContent=d.producer_status;$('lag').textContent=d.completed===null?'Waiting for producer':fmt(Math.max(0,d.completed-d.verified))+' blocks behind';$('free').textContent=bytes(d.free);$('size').textContent=bytes(d.snapshots+d.deltas);$('breakdown').textContent=bytes(d.snapshots)+' snapshots · '+bytes(d.deltas)+' deltas';$('target').textContent=d.target===null?'Target not specified':'Target: '+fmt(d.target);for(const [id,n] of [['p1',d.completed],['p2',d.verified]])$(id).style.width=d.target!==null&&n!==null?Math.max(0,Math.min(100,100*(n+1)/(d.target+1)))+'%':'0%';
$('history').replaceChildren();for(const r of d.rows){let tr=document.createElement('tr'),verified=r.status==='applied_checkpoint_verified',status=verified?'Verified':r.status==='failed_restore_required'?'Failed':r.status==='applying'?'Applying':r.delta?'Delta ready':'Snapshot ready';for(const [i,value] of [fmt(r.end),status,secs(r.retrieve),secs(r.drain),secs(r.generation),secs(r.application),r.snapshot?(r.snapshot_present?bytes(r.archive_bytes):'Removed'):'—',bytes(r.delta_bytes),fmt(r.operations)].entries()){let td=document.createElement('td');td.textContent=value;if(i===1)td.className=verified?'good':status==='Failed'?'bad':'warn';tr.append(td)}$('history').append(tr)}
const keys=['retrieve','drain','generation','application'],colors=['#70b6ff','#69e6cd','#e5b4ff','#ffc17e'],recent=d.rows.slice(0,12).reverse(),total=r=>keys.reduce((s,k)=>s+(typeof r[k]==='number'?r[k]:0),0),max=Math.max(1,...recent.map(total));$('bars').replaceChildren();for(const r of recent){const line=document.createElement('div'),label=document.createElement('span'),bar=document.createElement('div'),value=document.createElement('span');line.className='barrow';bar.className='bar';label.textContent=fmt(r.end);value.textContent=secs(total(r));keys.forEach((k,i)=>{let seg=document.createElement('i');seg.style.width=(100*(r[k]||0)/max)+'%';seg.style.background=colors[i];seg.title=k+': '+secs(r[k]);bar.append(seg)});line.append(label,bar,value);$('bars').append(line)}$('message').textContent='Reports read at '+new Date(d.refreshed).toLocaleTimeString('en-GB')+' · refresh every 10 s · worker liveness not inferred';}
async function refresh(){try{let r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);render(await r.json())}catch(e){$('message').textContent='Refresh failed; displayed values may be stale: '+e.message}finally{setTimeout(refresh,10000)}}refresh();
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5012)
    parser.add_argument('--data', type=Path, default=ROOT.parent / 'v12_data')
    parser.add_argument('--target', type=int)
    args = parser.parse_args()
    if args.target is not None and (args.target < 0 or (args.target + 1) % INTERVAL):
        parser.error('target must end on a 100,000-block boundary')
    lock = threading.Lock()
    cached, expires = None, 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal cached, expires
            if self.path == '/':
                body, mime = PAGE.encode(), 'text/html; charset=utf-8'
            elif self.path == '/api/status':
                with lock:
                    if time.monotonic() >= expires:
                        cached = json.dumps(collect(args.data, args.target), allow_nan=False).encode()
                        expires = time.monotonic() + 10
                    body = cached
                mime = 'application/json; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    print(f'V12 Observer: http://{args.host}:{args.port} · data: {args.data}', flush=True)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
