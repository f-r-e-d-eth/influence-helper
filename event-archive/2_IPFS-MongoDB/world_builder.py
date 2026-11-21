#!/usr/bin/env python3
"""Influence World Builder v0.1 — isolated, verified local reconstruction."""
import argparse
import gzip
import fcntl
import threading
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT.parent / 'v12' / 'v12-delta'
STEP = 100_000
DB = 'v12_disposable_world_builder'
APP_HOME = Path.home() / 'Adalaris'
DATA = APP_HOME / 'data'
WORLDS = APP_HOME / 'worlds'
BACKGROUNDS = APP_HOME / 'backgrounds'
GATEWAY = 'https://ipfs.io'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def validate(manifest):
    if manifest.get('format') != 'influence-v12-world' or manifest.get('version') != 1:
        raise ValueError('Unsupported manifest format')
    entries = [manifest['bootstrap'], *manifest['deltas']]
    for i, entry in enumerate(entries):
        end = i * STEP - 1
        expected = 'v12-bootstrap.archive.gz' if not i else f'v12-delta-{end-STEP+1}-{end}.tar.gz'
        if entry['filename'] != expected or entry['end_block'] != end:
            raise ValueError('Non-contiguous or unexpected checkpoint filename')
        if type(entry['bytes']) is not int or entry['bytes'] <= 0 or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError('Invalid file size or SHA256')
        if entry.get('cid') and not re.fullmatch(r'b[a-z2-7]+|Qm[1-9A-HJ-NP-Za-km-z]{44}', entry['cid']):
            raise ValueError('Invalid IPFS CID')
    if len(entries) < 2:
        raise ValueError('At least one verified delta is required')
    return entries


def make_manifest(data, end, output, cid_file=None):
    if end < STEP-1 or (end+1) % STEP:
        raise ValueError('Endpoint must be a 100,000-block checkpoint')
    cid_map = read(cid_file) if cid_file else {}
    bootstrap = data / 'snapshots/v12-bootstrap.archive.gz'
    record = read(data / 'snapshots/v12-bootstrap.json')
    def entry(path, block, expected_hash, expected_bytes):
        size = path.stat().st_size
        if size != expected_bytes or digest(path) != expected_hash:
            raise ValueError(f'Integrity mismatch: {path.name}')
        result = dict(filename=path.name, end_block=block, bytes=size, sha256=expected_hash)
        remote = cid_map.get(path.name)
        if remote:
            if remote['bytes'] != size:
                raise ValueError(f'IPFS catalog size mismatch: {path.name}; upload the matching file')
            result['cid'] = remote['cid']
        return result
    manifest = dict(format='influence-v12-world', version=1,
                    bootstrap=entry(bootstrap, -1, record['archive_sha256'], record['archive_bytes']), deltas=[])
    for block in range(STEP-1, end+1, STEP):
        path = data / 'deltas' / f'v12-delta-{block-STEP+1}-{block}.tar.gz'
        report = read(Path(str(path)+'.report.json'))
        journal = read(data / 'verification' / f'apply-{block}.json')
        if journal['status'] != 'applied_checkpoint_verified' or journal['package_sha256'] != report['package_sha256']:
            raise ValueError(f'Delta not verified: {block}')
        item = entry(path, block, report['package_sha256'], report['compressed_bytes'])
        for key in ('source_documents', 'target_documents', 'generation_seconds'):
            if key in report:
                item[key] = report[key]
        for key in ('completed_operations', 'application_seconds'):
            if key in journal:
                item[key] = journal[key]
        manifest['deltas'].append(item)
    validate(manifest)
    with output.open('x') as stream:
        json.dump(manifest, stream, indent=2)
        stream.write('\n')


def acquire(entry, source, cache, gateway, log):
    name = entry['filename']
    candidates = [source / name, source / 'snapshots' / name, source / 'deltas' / name, cache / name]
    def check(path):
        return path.is_file() and path.stat().st_size == entry['bytes'] and digest(path) == entry['sha256']
    for path in candidates:
        if path.is_file():
            log(f'Checking {name}')
            if check(path):
                return path
    if not gateway or not entry.get('cid'):
        raise ValueError(f'No matching local file or IPFS CID for {name}')
    if not gateway.startswith('https://'):
        raise ValueError('IPFS gateway must use HTTPS')
    dest = cache / name
    partial = cache / (name+'.'+uuid.uuid4().hex+'.part')
    log(f'Downloading {name} from IPFS')
    try:
        with urllib.request.urlopen(gateway.rstrip('/')+'/ipfs/'+entry['cid'], timeout=60) as response, partial.open('xb') as out:
            count = 0
            while chunk := response.read(1024*1024):
                count += len(chunk)
                if count > entry['bytes']:
                    raise ValueError('Download exceeds manifest size')
                out.write(chunk)
        if not check(partial):
            raise ValueError(f'Download integrity failure: {name}')
        os.replace(partial, dest)
        return dest
    finally:
        partial.unlink(missing_ok=True)


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def collection_activity(journal, limit=10):
    """Rank actual journal counters, not event types or inferred actions."""
    metrics = journal.get('metrics', {})
    def ranked(section, key):
        rows = [(name, value.get(key, 0)) for name, value in
                metrics.get(section, {}).get('collections', {}).items()
                if isinstance(value, dict)]
        return sorted(((name, n) for name, n in rows if type(n) is int and n > 0),
                      key=lambda item: (-item[1], item[0]))[:limit]
    return dict(scanned=ranked('final_checkpoint', 'documents_scanned'),
                writes=ranked('bulk_writes', 'completed_operations'),
                scans_available=bool(metrics.get('final_checkpoint', {}).get('collections')))


def last_activity(job, block):
    if type(block) is not int or block < 0: return None
    try:
        journal = read(job/f'apply-{block}.json')
        if journal.get('status') not in ('applied_checkpoint_verified', 'applied_unverified_benchmark'):
            return None
        return dict(block=block, **collection_activity(journal))
    except (OSError, ValueError):
        return None


def background_files(folder):
    """Stable alphabetical rotation of supported image files."""
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.bmp')),
                  key=lambda p: (p.name.casefold(), p.name))


def find_paused(storage):
    candidates = []
    for path in storage.glob('build-*/result.json'):
        if not re.fullmatch(r'build-[0-9a-f]{12}', path.parent.name):
            continue
        try:
            record = read(path)
            if record.get('status') == 'paused':
                candidates.append((path.stat().st_mtime_ns, str(path), path, record))
        except (OSError, ValueError):
            continue
    if not candidates:
        return None
    _, _, path, record = min(candidates)
    return path, record


def automatic_args(storage, verify_fingerprints):
    paused = find_paused(storage)
    if paused:
        path, record = paused
        c = record['config']
        return (Path(c['manifest']), Path(c['source']), storage, c['gateway'],
                c['end'], c['verify_fingerprints'], path)
    manifest = DATA / 'manifest.json'
    if not manifest.is_file():
        manifest = ROOT / 'manifest.json'
    if not manifest.is_file():
        raise ValueError(f'No published manifest installed. Expected {DATA}/manifest.json')
    end = validate(read(manifest))[-1]['end_block']
    return (manifest, DATA, storage, GATEWAY, end, verify_fingerprints, None)


def build(manifest_path, source, storage, gateway, end, verify_fingerprints, log, progress, pause=None, resume=None, stats=None):
    storage = storage.resolve()
    if resume is None:
        storage.mkdir(parents=True, exist_ok=True)
        job = storage / ('build-'+uuid.uuid4().hex[:12]); job.mkdir()
    else:
        job = resume.resolve().parent
        if resume.name != 'result.json' or not re.fullmatch(r'build-[0-9a-f]{12}', job.name):
            raise ValueError('Select a paused build result.json')
    with (job/'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if resume is None:
            pinned = job/'manifest.json'
            with pinned.open('xb') as stream:
                stream.write(manifest_path.read_bytes()); stream.flush(); os.fsync(stream.fileno())
            manifest_path = pinned
        return build_locked(manifest_path, source, gateway, end, verify_fingerprints, log, progress, pause, resume, job, stats)


def build_locked(manifest_path, source, gateway, end, verify_fingerprints, log, progress, pause, resume, job, stats=None):
    started = time.monotonic()
    manifest_bytes = manifest_path.read_bytes()
    entries = validate(json.loads(manifest_bytes))
    if end not in [e['end_block'] for e in entries[1:]]:
        raise ValueError('Select a checkpoint in the manifest')
    entries = [e for e in entries if e['end_block'] <= end]
    # Import the existing tested delta engine, not a second implementation.
    sys.path.insert(0, str(ENGINE))
    from delta import apply_package, unpack, checkpoint
    from pymongo import MongoClient
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    if not shutil.which('docker'):
        raise RuntimeError('Docker is required')
    run(['docker', 'info'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    config = dict(manifest=str(manifest_path.resolve()), source=str(source.resolve()), gateway=gateway,
                  end=end, verify_fingerprints=verify_fingerprints, manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    name = 'influence-world-'+job.name.removeprefix('build-')
    info = read(job/'result.json') if resume else None
    if resume:
        if (info.get('status') != 'paused' or info.get('config') != config or
                info.get('container') != name or info.get('volume') != name or info.get('database') != DB):
            raise ValueError('Not a matching safely paused build')
        last = info.get('applied_through')
        if last not in [e['end_block'] for e in entries]:
            raise ValueError('Invalid paused checkpoint')
        expected = 'applied_checkpoint_verified' if verify_fingerprints else 'applied_unverified_benchmark'
        for entry in entries[1:]:
            journal = job/f'apply-{entry["end_block"]}.json'
            if entry['end_block'] <= last:
                record = read(journal)
                if record['status'] != expected or record['package_sha256'] != entry['sha256']:
                    raise ValueError('Paused journal mismatch')
            elif journal.exists():
                raise ValueError('Unexpected later journal; cannot safely resume')
        identity = json.loads(run(['docker','inspect',name],capture_output=True,text=True).stdout)[0]
        if (identity['Id'] != info['container_id'] or identity['State']['Running'] or
                identity['State']['ExitCode'] != 0 or not any(m.get('Name') == name and m.get('Destination') == '/data/db' for m in identity['Mounts'])):
            raise ValueError('Paused container missing, changed, or not cleanly stopped')
    cache = job / 'downloads'; cache.mkdir(exist_ok=True)
    scratch = job / 'scratch'; scratch.mkdir(exist_ok=True)
    def report(stage, entry=None):
        if not stats: return
        entry = entry or {}
        last = info.get('applied_through') if info else None
        done = [e for e in entries[1:] if last is not None and e['end_block'] <= last]
        ops = [e.get('completed_operations') for e in done]
        stats(dict(stage=stage, target=end, applied=last, checkpoints=len(done), total_checkpoints=len(entries)-1,
                   operations_done=sum(ops) if all(type(n) is int for n in ops) else None,
                   documents=done[-1].get('target_documents') if done else None,
                   current=entry, timings=dict(info['timings']) if info else {},
                   verification=verify_fingerprints, input_bytes=sum(e['bytes'] for e in entries),
                   archive_bytes=info.get('archive_bytes') if info else None,
                   activity=last_activity(job,last)))
    # Check every input before creating a database, and check package block boundaries.
    files = []
    for i, entry in enumerate(entries):
        report('Checking input', entry)
        path = acquire(entry, source, cache, gateway, log)
        if i:
            with unpack(path, scratch) as (manifest, _):
                if manifest['source_block'] != entry['end_block']-STEP or manifest['target_block'] != entry['end_block']:
                    raise ValueError('Delta block boundary mismatch')
        files.append(path)
        progress(i+1, len(entries)*2+3)
    if not resume:
        report('Preparing MongoDB')
        log(f'Creating isolated MongoDB container {name} — Docker may download the image on first use. Please wait.')
        container_id = run(['docker','run','-d','--name',name,'--label','influence.world-builder=0.1',
             '-p','127.0.0.1::27017','-v',name+':/data/db','mongo:7','mongod','--wiredTigerCacheSizeGB','2'], capture_output=True,text=True).stdout.strip()
        info = dict(container=name, container_id=container_id, volume=name, database=DB, status='incomplete',
                    verified_through=None, applied_through=None, fingerprints_verified=verify_fingerprints, config=config,
                    timings={'fingerprints_seconds':0.0,'bulk_writes_seconds':0.0,'application_seconds':0.0})
    def save():
        tmp = job/'result.tmp'
        with tmp.open('w') as stream:
            json.dump(info,stream,indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,job/'result.json')
        fd=os.open(job,os.O_RDONLY); os.fsync(fd); os.close(fd)
    info['status']='incomplete'; save()
    if resume:
        log(f'Restarting paused container {name}')
        run(['docker','start',name])
    port = run(['docker','port',name,'27017/tcp'],capture_output=True,text=True).stdout.strip().splitlines()[0].rsplit(':',1)[1]
    uri = 'mongodb://127.0.0.1:'+port
    info['uri']=uri; save()
    def pause_at_boundary():
        if not pause or not pause.is_set(): return False
        log('Pausing at a completed checkpoint; stopping MongoDB…')
        run(['docker','stop','-t','120',name])
        state = json.loads(run(['docker','inspect',name],capture_output=True,text=True).stdout)[0]['State']
        if state['Running'] or state['ExitCode'] != 0:
            raise RuntimeError('MongoDB did not stop cleanly; resume refused')
        info.update(status='paused',uri=None)
        info['timings']['active_seconds']=info['timings'].get('active_seconds',0)+time.monotonic()-started
        save()
        report('Paused')
        log(f'PAUSED safely — resume using {job}/result.json')
        return True
    log(f'Build record: {job}/result.json')
    log('Waiting for MongoDB to become ready…')
    with MongoClient(uri,serverSelectionTimeoutMS=2000) as client:
        for attempt in range(30):
            try:
                client.admin.command('ping'); break
            except Exception:
                if attempt == 29: raise
                time.sleep(1)
        progress(len(entries)+1,len(entries)*2+3)
        if not resume:
            report('Restoring bootstrap', entries[0])
            log('Restoring bootstrap — creating collections and indexes…')
            with files[0].open('rb') as stream:
                run(['docker','exec','-i',name,'mongorestore','--archive','--gzip','--stopOnError',
                     '--nsInclude','v12_reconstruction.*','--nsFrom','v12_reconstruction.*','--nsTo',DB+'.*'], stdin=stream)
            info['applied_through']=-1; save()
        progress(len(entries)+2,len(entries)*2+3)
        db = client.get_database(DB,read_concern=ReadConcern('majority'),write_concern=WriteConcern(w='majority',j=True))
        # Trust only a verified target from this uninterrupted process.
        # On resume, the first pending delta performs a full source scan.
        previous_verified = None
        if resume and verify_fingerprints and info['applied_through'] == end:
            log('Rechecking final checkpoint after resume before export…')
            measured = {}
            with unpack(files[-1], scratch) as (manifest, _):
                checkpoint(db, manifest, 'target', scratch, 32*1024*1024, measured)
            info['timings']['fingerprints_seconds'] += measured['seconds']
            save()
        for i, (entry, path) in enumerate(zip(entries[1:],files[1:]),1):
            if entry['end_block'] <= info['applied_through']:
                progress(len(entries)+2+i,len(entries)*2+3)
                continue
            if pause_at_boundary(): return info
            report('Applying delta', entry)
            log(f'Applying{" and verifying" if verify_fingerprints else " (benchmark, no fingerprints)"} through {entry["end_block"]:,}')
            details = []
            for key, label in (('completed_operations','operations'), ('target_documents','target documents')):
                if isinstance(entry.get(key), int): details.append(f'{entry[key]:,} {label}')
            if isinstance(entry.get('application_seconds'), (int,float)):
                details.append(f'previous verified application: {entry["application_seconds"]/60:.1f} min')
            if details: log(' · '.join(details))
            if isinstance(entry.get('comment'), str): log('Checkpoint note: '+entry['comment'])
            log('Reading, writing and verifying database fingerprints — please wait…' if verify_fingerprints else 'Writing delta with package and operation checks only — please wait…')
            if previous_verified is not None:
                log('Reusing the previous verified fingerprint as the starting state')
            result = apply_package(db,path,job/f'apply-{entry["end_block"]}.json',scratch,
                                   verify_fingerprints=verify_fingerprints,
                                   previous_verified_checkpoint=previous_verified)
            expected = 'applied_checkpoint_verified' if verify_fingerprints else 'applied_unverified_benchmark'
            if result['status'] != expected:
                raise RuntimeError('Delta application failed')
            previous_verified = result.get('verified_target_checkpoint') if verify_fingerprints else None
            metrics = result['metrics']; timings = info['timings']
            checks = sum(metrics[k].get('seconds',0) for k in ('starting_checkpoint','final_checkpoint'))
            writes = metrics['bulk_writes']['seconds']
            timings['fingerprints_seconds'] += checks; timings['bulk_writes_seconds'] += writes
            timings['application_seconds'] += result['application_seconds']
            log(f'Checkpoint: fingerprint scans {checks:.1f} s · bulk writes {writes:.1f} s · total {result["application_seconds"]:.1f} s')
            info['applied_through'] = entry['end_block']
            if verify_fingerprints: info['verified_through'] = entry['end_block']
            save()
            report('Checkpoint complete', entry)
            progress(len(entries)+2+i,len(entries)*2+3)
    if pause_at_boundary(): return info
    report('Exporting database')
    log('Exporting final database as compressed MongoDB archive…')
    export_started = time.monotonic()
    archive = job / (f'adalaris-{end}.archive.gz' if verify_fingerprints else f'adalaris-{end}-UNVERIFIED.archive.gz')
    partial = archive.with_suffix('.gz.part')
    with partial.open('xb') as stream:
        run(['docker','exec',name,'mongodump','--db',DB,'--archive','--gzip'],stdout=stream)
        stream.flush(); os.fsync(stream.fileno())
    # Check gzip integrity before destroying the live copy; this is not a restore test.
    with gzip.open(partial,'rb') as stream:
        if not stream.read(1): raise ValueError('Empty database archive')
        while stream.read(1024*1024): pass
    checksum = digest(partial)
    os.replace(partial,archive)
    info.update(archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=checksum,
                status='exported_verified' if verify_fingerprints else 'exported_unverified_benchmark',cleanup_complete=False)
    info['timings']['export_seconds']=time.monotonic()-export_started; save()
    progress(len(entries)*2+2,len(entries)*2+3)
    log('Archive saved and gzip checked. Removing temporary MongoDB container and volume…')
    run(['docker','stop','-t','120',name]); run(['docker','rm','-v',name]); run(['docker','volume','rm',name])
    shutil.rmtree(cache); shutil.rmtree(scratch)
    info['cleanup_complete']=True; info['uri']=None; info['timings']['total_seconds']=info['timings'].get('active_seconds',0)+time.monotonic()-started; save()
    progress(len(entries)*2+3,len(entries)*2+3)
    report('Complete')
    log('Archive ready — '+str(archive))
    log(f'Total fingerprint scans: {info["timings"]["fingerprints_seconds"]:.1f} s · bulk writes: {info["timings"]["bulk_writes_seconds"]:.1f} s')
    return info


def gui():
    from PySide6.QtCore import Qt, QThread, Signal, QSettings, QTimer
    from PySide6.QtGui import QPainter, QPixmap, QColor, QLinearGradient
    from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
        QProgressBar,QPlainTextEdit,QMessageBox,QFrame,QCheckBox,QGridLayout,QSlider)

    class Worker(QThread):
        message=Signal(str); progress=Signal(int,int); result=Signal(object); failed=Signal(str); stats=Signal(object)
        def __init__(self,args): super().__init__(); self.args=args; self.pause=threading.Event()
        def run(self):
            try: self.result.emit(build(*self.args[:6],self.message.emit,self.progress.emit,pause=self.pause,resume=self.args[6],stats=self.stats.emit))
            except Exception as exc: self.failed.emit(str(exc))

    class Window(QWidget):
        def __init__(self):
            super().__init__(); self.setWindowTitle('Adalaris · Influence World Builder v0.1'); self.resize(1120,900)
            self.settings=QSettings('InfluenceHelper','WorldBuilder'); self.worker=None
            self.image=QPixmap(); self.background_index=None; self.opacity=max(0,min(100,int(self.settings.value('opacity',88))))
            self.set_background(0)
            outer=QVBoxLayout(self); outer.setContentsMargins(28,28,28,28); outer.setSpacing(16)
            title=QLabel('ADALARIS  /  INFLUENCE WORLD BUILDER'); title.setStyleSheet('font-size: 28px; font-weight: 700; color: #e8f8ff'); outer.addWidget(title)
            sub=QLabel('V0.1  ·  RECONSTRUCT A VERIFIED LOCAL WORLD'); sub.setStyleSheet('color: #9ec7d8; font-size: 13px'); outer.addWidget(sub)
            self.panel=QFrame(); self.panel.setObjectName('panel'); outer.addWidget(self.panel); layout=QVBoxLayout(self.panel); layout.setSpacing(12); layout.setContentsMargins(20,20,20,20)
            self.storage = WORLDS
            self.skip=QCheckBox('Benchmark only — skip database fingerprints (UNVERIFIED archive)'); self.skip.setStyleSheet('color:#f7d38b'); layout.addWidget(self.skip)
            self.start=QPushButton('RUN'); self.start.setObjectName('build'); self.start.clicked.connect(self.action); layout.addWidget(self.start)
            self.bar=QProgressBar(); self.bar.setValue(0); layout.addWidget(self.bar)
            self.status=QLabel('Ready'); self.status.setWordWrap(True); layout.addWidget(self.status)
            self.dashboard=QLabel(); self.dashboard.setWordWrap(True); self.dashboard.setStyleSheet('font-size:15px; padding:12px; color:#d8f3f5'); layout.addWidget(self.dashboard)
            self.activity_title=QLabel('LAST COMPLETED DELTA · COLLECTION ACTIVITY'); self.activity_title.setStyleSheet('font-size:16px;font-weight:bold;color:#d8f3f5'); layout.addWidget(self.activity_title)
            charts=QHBoxLayout(); charts.setSpacing(20); layout.addLayout(charts)
            self.scan_chart=self.make_chart(charts,'Documents scanned · final fingerprint','#67cfbd')
            self.write_chart=self.make_chart(charts,'Write operations · applied delta','#e0ae69')
            self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500); outer.addWidget(self.log,1)
            controls=QHBoxLayout(); controls.addWidget(QLabel('Panel opacity'))
            self.opacity_slider=QSlider(Qt.Horizontal); self.opacity_slider.setRange(0,100); self.opacity_slider.setValue(self.opacity)
            self.opacity_slider.setMaximumWidth(260); controls.addWidget(self.opacity_slider)
            self.opacity_value=QLabel(f'{self.opacity}%'); controls.addWidget(self.opacity_value); controls.addStretch(1); outer.addLayout(controls)
            self.opacity_slider.valueChanged.connect(self.change_opacity)
            self.phase_started=time.monotonic(); self.phase=''; self.timer=QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self.tick)
            self.style(); self.refresh_idle()
        def set_background(self, completed):
            if self.background_index == completed: return
            self.background_index=completed
            try:
                BACKGROUNDS.mkdir(parents=True,exist_ok=True)
                paths=background_files(BACKGROUNDS)
            except OSError:
                paths=[]
            for offset in range(len(paths)):
                pix=QPixmap(str(paths[(completed+offset)%len(paths)]))
                if not pix.isNull():
                    self.image=pix; self.update(); return
            self.image=QPixmap(); self.update()
        def change_opacity(self,value):
            self.opacity=value; self.settings.setValue('opacity',value); self.opacity_value.setText(f'{value}%'); self.style(); self.update()
        def style(self):
            self.setStyleSheet('QLabel{color:#e1edf5} QFrame#panel{background-color:rgba(12,25,42,'+str(self.opacity*255//100)+');border:1px solid #405568;border-radius:14px} QPlainTextEdit{background:rgba(17,31,50,'+str(self.opacity*255//100)+');color:#edf6ff;border:1px solid #496179;border-radius:6px;padding:8px} QFrame#activityChart{background:rgba(16,34,50,'+str(self.opacity*180//100)+');border-radius:8px} QPushButton{background:#233e56;color:#edf6ff;border:1px solid #52748b;border-radius:7px;padding:9px} QPushButton#build{background:#176b61;font-weight:bold;padding:14px} QPushButton:disabled{color:#8294a5;background:#253342} QProgressBar{color:white;background:#18283a;border-radius:5px;text-align:center} QProgressBar::chunk{background:#258c7e;border-radius:5px}')
        def paintEvent(self,event):
            painter=QPainter(self); g=QLinearGradient(0,0,self.width(),self.height()); g.setColorAt(0,QColor('#153c50')); g.setColorAt(1,QColor('#100f29')); painter.fillRect(self.rect(),g)
            if not self.image.isNull():
                scaled=self.image.scaled(self.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation); painter.drawPixmap((self.width()-scaled.width())//2,(self.height()-scaled.height())//2,scaled)
        def refresh_idle(self):
            try:
                args=automatic_args(self.storage,not self.skip.isChecked())
                self.start.setEnabled(True)
                self.skip.setEnabled(args[6] is None)
                if args[6]:
                    record=read(args[6]); self.skip.setChecked(not args[5])
                    self.start.setText('RESUME')
                    self.status.setText('Paused build found · ready to resume')
                    last=record.get('applied_through')
                    self.show_stats(dict(stage='Paused',target=args[4],applied=last,
                        checkpoints=(last+1)//STEP if last is not None else 0,total_checkpoints=(args[4]+1)//STEP,
                        verification=args[5],timings=record.get('timings',{}),activity=last_activity(args[6].parent,last)))
                else:
                    self.start.setText('RUN')
                    entries=validate(read(args[0]))
                    self.status.setText(f'Ready · {len(entries)-1} available checkpoints')
                    self.show_stats(dict(stage='Ready',target=args[4],total_checkpoints=len(entries)-1,
                        checkpoints=0,input_bytes=sum(e['bytes'] for e in entries),verification=args[5]))
            except Exception as exc:
                self.start.setText('RUN'); self.start.setEnabled(True); self.skip.setEnabled(True)
                self.status.setText(str(exc))
        def make_chart(self, parent, title, color):
            panel=QFrame(); panel.setObjectName('activityChart')
            parent.addWidget(panel,1); column=QVBoxLayout(panel); column.setContentsMargins(10,8,10,8); column.setSpacing(4)
            heading=QLabel(title); heading.setStyleSheet('font-weight:bold;color:#c9e7ef'); column.addWidget(heading)
            note=QLabel('No completed delta yet'); note.setStyleSheet('color:#a7bdc8'); column.addWidget(note)
            grid=QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(3); grid.setColumnStretch(1,1); column.addLayout(grid)
            rows=[]
            for i in range(10):
                name=QLabel(); name.setTextFormat(Qt.PlainText); name.setStyleSheet('font-size:11px;color:#e2eef4')
                bar=QProgressBar(); bar.setRange(0,1000); bar.setTextVisible(False); bar.setFixedHeight(9)
                bar.setStyleSheet('QProgressBar{background:#253747;border:0;border-radius:4px} QProgressBar::chunk{background:'+color+';border-radius:4px}')
                value=QLabel(); value.setAlignment(Qt.AlignRight); value.setStyleSheet('font-size:11px;color:#e2eef4')
                for col,widget in enumerate((name,bar,value)): grid.addWidget(widget,i,col); widget.hide()
                rows.append((name,bar,value))
            column.addStretch(1)
            return note,rows
        def draw_chart(self, chart, values, empty):
            note,rows=chart
            note.setText(empty); note.setVisible(not values)
            maximum=values[0][1] if values else 1
            for i,(name,bar,value) in enumerate(rows):
                visible=i<len(values)
                for widget in (name,bar,value): widget.setVisible(visible)
                if visible:
                    label,count=values[i]
                    name.setText(label.removeprefix('Component_')); name.setToolTip(label)
                    bar.setValue(max(1,round(1000*count/maximum)))
                    value.setText(f'{count:,}')
        def show_activity(self, activity):
            if not activity:
                self.activity_title.setText('LAST COMPLETED DELTA · COLLECTION ACTIVITY')
                self.draw_chart(self.scan_chart,[],'No completed delta yet')
                self.draw_chart(self.write_chart,[],'No completed delta yet')
                return
            self.activity_title.setText(f'LAST COMPLETED DELTA · BLOCK {activity["block"]:,} · TOP 10')
            self.draw_chart(self.scan_chart,activity['scanned'],'No documents scanned' if activity['scans_available'] else 'Fingerprint scans skipped')
            self.draw_chart(self.write_chart,activity['writes'],'No write operations')
        def show_stats(self, data):
            self.set_background(data.get('checkpoints',0))
            def number(n): return f'{n:,}' if type(n) is int and n>=0 else '—'
            def size(n): return f'{n/1024**2:,.1f} MiB' if type(n) is int else '—'
            def duration(n):
                if not isinstance(n,(int,float)): return '—'
                return f'{int(n)//60} min {int(n)%60:02d} s' if n>=60 else f'{n:.1f} s'
            e=data.get('current',{}); t=data.get('timings',{})
            rows=[
                f"Target block: {number(data.get('target'))}     ·     Completed: {number(data.get('applied'))}",
                f"Checkpoints: {data.get('checkpoints',0)} / {data.get('total_checkpoints',0)}     ·     {'Verified mode' if data.get('verification') else 'Benchmark mode'}",
                f"Current file: {e.get('filename','—')}     ·     {size(e.get('bytes'))}",
                f"Expected target documents: {number(e.get('target_documents'))}     ·     Planned operations: {number(e.get('completed_operations'))}",
                f"Completed documents (manifest): {number(data.get('documents'))}     ·     Completed operations (manifest): {number(data.get('operations_done'))}",
                f"Fingerprint time: {duration(t.get('fingerprints_seconds'))}     ·     Write time: {duration(t.get('bulk_writes_seconds'))}",
                f"Input package size: {size(data.get('input_bytes'))}     ·     Export: {size(data.get('archive_bytes'))}",
            ]
            self.dashboard.setText('\n'.join(rows))
            self.dashboard.setTextFormat(Qt.PlainText)
            self.show_activity(data.get('activity'))
        def tick(self):
            if self.worker and self.worker.isRunning():
                elapsed=int(time.monotonic()-self.phase_started)
                self.status.setText(f'{self.phase} · elapsed {elapsed//60} min {elapsed%60:02d} s')
        def message(self, msg):
            self.log.appendPlainText(msg)
            self.phase=msg; self.phase_started=time.monotonic(); self.status.setText(msg)
        def ready(self, result):
            self.timer.stop()
            if result['status']=='paused':
                self.status.setText('Paused safely · you can close Adalaris'); return
            self.bar.setValue(self.bar.maximum())
            self.status.setText(('Verified archive ready: ' if result['fingerprints_verified'] else 'UNVERIFIED benchmark archive: ')+result['archive'])
        def failed(self, error):
            self.timer.stop(); self.status.setText('Stopped: '+error); self.log.appendPlainText('ERROR: '+error)
        def action(self):
            if self.worker and self.worker.isRunning():
                self.worker.pause.set(); self.start.setText('PAUSING…'); self.start.setEnabled(False)
                self.message('Pause requested · waiting for the next safe checkpoint')
                return
            try:
                args=automatic_args(self.storage,not self.skip.isChecked())
                self.launch(args)
            except Exception as exc: self.failed(str(exc))
        def finished(self):
            self.start.setEnabled(True)
            paused=find_paused(self.storage)
            self.start.setText('RESUME' if paused else 'RUN')
            self.skip.setEnabled(not paused)
            if paused: self.skip.setChecked(not paused[1]['config']['verify_fingerprints'])
        def launch(self,args):
            self.background_index=None
            self.start.setText('PAUSE'); self.start.setEnabled(True); self.skip.setEnabled(False)
            self.log.clear(); self.bar.setValue(0); self.status.setText('Resuming build…' if args[6] else 'Building a new isolated database…')
            self.worker=Worker(args); self.worker.message.connect(self.message); self.worker.progress.connect(lambda n,t:(self.bar.setMaximum(t),self.bar.setValue(n))); self.worker.stats.connect(self.show_stats); self.worker.result.connect(self.ready); self.worker.failed.connect(self.failed); self.worker.finished.connect(self.finished); self.phase='Preparing build'; self.phase_started=time.monotonic(); self.timer.start(); self.worker.start()
        def closeEvent(self,event):
            if self.worker and self.worker.isRunning(): QMessageBox.information(self,'Build running','Use PAUSE and wait for confirmation before closing.'); event.ignore()
            else: event.accept()

    app=QApplication(sys.argv); window=Window(); window.show(); return app.exec()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command')
    m=sub.add_parser('manifest'); m.add_argument('--data',type=Path,required=True); m.add_argument('--end',type=int,required=True); m.add_argument('--output',type=Path,required=True); m.add_argument('--cids',type=Path)
    args=parser.parse_args()
    if args.command=='manifest': make_manifest(args.data,args.end,args.output,args.cids)
    else: sys.exit(gui())
