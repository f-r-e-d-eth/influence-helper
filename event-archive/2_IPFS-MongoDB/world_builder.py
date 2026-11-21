#!/usr/bin/env python3
"""Influence World Builder v0.1 — isolated, verified local reconstruction."""
import argparse
import gzip
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


def build(manifest_path, source, storage, gateway, end, verify_fingerprints, log, progress):
    started = time.monotonic()
    entries = validate(read(manifest_path))
    if end not in [e['end_block'] for e in entries[1:]]:
        raise ValueError('Select a checkpoint in the manifest')
    entries = [e for e in entries if e['end_block'] <= end]
    # Import the existing tested delta engine, not a second implementation.
    sys.path.insert(0, str(ENGINE))
    from delta import apply_package, unpack
    from pymongo import MongoClient
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    if not shutil.which('docker'):
        raise RuntimeError('Docker is required')
    run(['docker', 'info'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    storage.mkdir(parents=True, exist_ok=True)
    job = storage / ('build-'+uuid.uuid4().hex[:12]); job.mkdir()
    cache = job / 'downloads'; cache.mkdir()
    scratch = job / 'scratch'; scratch.mkdir()
    # Check every input before creating a database, and check package block boundaries.
    files = []
    for i, entry in enumerate(entries):
        path = acquire(entry, source, cache, gateway, log)
        if i:
            with unpack(path, scratch) as (manifest, _):
                if manifest['source_block'] != entry['end_block']-STEP or manifest['target_block'] != entry['end_block']:
                    raise ValueError('Delta block boundary mismatch')
        files.append(path)
        progress(i+1, len(entries)*2+3)
    name = 'influence-world-'+job.name.removeprefix('build-')
    log(f'Creating isolated MongoDB container {name} — Docker may download the image on first use. Please wait.')
    run(['docker','run','-d','--name',name,'--label','influence.world-builder=0.1',
         '-p','127.0.0.1::27017','-v',name+':/data/db','mongo:7','mongod','--wiredTigerCacheSizeGB','2'], stdout=subprocess.PIPE)
    port = run(['docker','port',name,'27017/tcp'],capture_output=True,text=True).stdout.strip().splitlines()[0].rsplit(':',1)[1]
    uri = 'mongodb://127.0.0.1:'+port
    info = dict(container=name, volume=name, uri=uri, database=DB, status='incomplete', verified_through=None, applied_through=None, fingerprints_verified=verify_fingerprints,
                timings={'fingerprints_seconds':0.0,'bulk_writes_seconds':0.0,'application_seconds':0.0})
    def save():
        tmp = job/'result.tmp'
        with tmp.open('w') as stream:
            json.dump(info,stream,indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,job/'result.json')
        fd=os.open(job,os.O_RDONLY); os.fsync(fd); os.close(fd)
    save()
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
        log('Restoring bootstrap — creating collections and indexes…')
        with files[0].open('rb') as stream:
            run(['docker','exec','-i',name,'mongorestore','--archive','--gzip','--stopOnError',
                 '--nsInclude','v12_reconstruction.*','--nsFrom','v12_reconstruction.*','--nsTo',DB+'.*'], stdin=stream)
        progress(len(entries)+2,len(entries)*2+3)
        db = client.get_database(DB,read_concern=ReadConcern('majority'),write_concern=WriteConcern(w='majority',j=True))
        for i, (entry, path) in enumerate(zip(entries[1:],files[1:]),1):
            log(f'Applying{" and verifying" if verify_fingerprints else " (benchmark, no fingerprints)"} through {entry["end_block"]:,}')
            details = []
            for key, label in (('completed_operations','operations'), ('target_documents','target documents')):
                if isinstance(entry.get(key), int): details.append(f'{entry[key]:,} {label}')
            if isinstance(entry.get('application_seconds'), (int,float)):
                details.append(f'previous verified application: {entry["application_seconds"]/60:.1f} min')
            if details: log(' · '.join(details))
            if isinstance(entry.get('comment'), str): log('Checkpoint note: '+entry['comment'])
            log('Reading, writing and verifying database fingerprints — please wait…' if verify_fingerprints else 'Writing delta with package and operation checks only — please wait…')
            result = apply_package(db,path,job/f'apply-{entry["end_block"]}.json',scratch,verify_fingerprints=verify_fingerprints)
            expected = 'applied_checkpoint_verified' if verify_fingerprints else 'applied_unverified_benchmark'
            if result['status'] != expected:
                raise RuntimeError('Delta application failed')
            metrics = result['metrics']; timings = info['timings']
            checks = sum(metrics[k].get('seconds',0) for k in ('starting_checkpoint','final_checkpoint'))
            writes = metrics['bulk_writes']['seconds']
            timings['fingerprints_seconds'] += checks; timings['bulk_writes_seconds'] += writes
            timings['application_seconds'] += result['application_seconds']
            log(f'Checkpoint: fingerprint scans {checks:.1f} s · bulk writes {writes:.1f} s · total {result["application_seconds"]:.1f} s')
            info['applied_through'] = entry['end_block']
            if verify_fingerprints: info['verified_through'] = entry['end_block']
            save()
            progress(len(entries)+2+i,len(entries)*2+3)
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
    info['cleanup_complete']=True; info['timings']['total_seconds']=time.monotonic()-started; save()
    progress(len(entries)*2+3,len(entries)*2+3)
    log('Archive ready — '+str(archive))
    log(f'Total fingerprint scans: {info["timings"]["fingerprints_seconds"]:.1f} s · bulk writes: {info["timings"]["bulk_writes_seconds"]:.1f} s')
    return info


def gui():
    from PySide6.QtCore import Qt, QThread, Signal, QSettings, QTimer
    from PySide6.QtGui import QPainter, QPixmap, QColor, QLinearGradient
    from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
        QLineEdit,QFileDialog,QComboBox,QProgressBar,QPlainTextEdit,QSlider,QMessageBox,QFrame,QCheckBox)

    class Worker(QThread):
        message=Signal(str); progress=Signal(int,int); result=Signal(object); failed=Signal(str)
        def __init__(self,args): super().__init__(); self.args=args
        def run(self):
            try: self.result.emit(build(*self.args,self.message.emit,self.progress.emit))
            except Exception as exc: self.failed.emit(str(exc))

    class Window(QWidget):
        def __init__(self):
            super().__init__(); self.setWindowTitle('Adalaris · Influence World Builder v0.1'); self.resize(940,760)
            self.settings=QSettings('InfluenceHelper','WorldBuilder'); self.worker=None
            self.image=QPixmap(str(self.settings.value('image',''))); self.opacity=int(self.settings.value('opacity',88))
            outer=QVBoxLayout(self); outer.setContentsMargins(28,28,28,28); outer.setSpacing(16)
            title=QLabel('ADALARIS  /  INFLUENCE WORLD BUILDER'); title.setStyleSheet('font-size: 28px; font-weight: 700; color: #e8f8ff'); outer.addWidget(title)
            sub=QLabel('V0.1  ·  RECONSTRUCT A VERIFIED LOCAL WORLD'); sub.setStyleSheet('color: #9ec7d8; font-size: 13px'); outer.addWidget(sub)
            self.panel=QFrame(); self.panel.setObjectName('panel'); outer.addWidget(self.panel); layout=QVBoxLayout(self.panel); layout.setSpacing(12); layout.setContentsMargins(20,20,20,20)
            self.manifest=self.field(layout,'Manifest JSON',str(self.settings.value('manifest','')),False)
            self.source=self.field(layout,'Local data folder (optional)',str(self.settings.value('source','')),True)
            self.storage=self.field(layout,'Build storage',str(self.settings.value('storage',Path.home()/'.local/share/influence-world-builder')),True)
            layout.addWidget(QLabel('IPFS gateway (optional; HTTPS)')); self.gateway=QLineEdit(str(self.settings.value('gateway','https://ipfs.io'))); layout.addWidget(self.gateway)
            row=QHBoxLayout(); self.target=QComboBox(); row.addWidget(QLabel('Target checkpoint')); row.addWidget(self.target,1); self.load_button=QPushButton('Load manifest'); row.addWidget(self.load_button); layout.addLayout(row); self.load_button.clicked.connect(self.load)
            self.skip=QCheckBox('Benchmark only — skip database fingerprint scans (UNVERIFIED archive)'); self.skip.setStyleSheet('color:#f7d38b'); layout.addWidget(self.skip)
            self.start=QPushButton('BUILD INFLUENCE WORLD'); self.start.setObjectName('build'); self.start.clicked.connect(self.start_build); layout.addWidget(self.start)
            self.bar=QProgressBar(); self.bar.setValue(0); layout.addWidget(self.bar)
            self.status=QLabel('Ready · select a verified manifest'); self.status.setWordWrap(True); layout.addWidget(self.status)
            self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500); outer.addWidget(self.log,1)
            row=QHBoxLayout(); image=QPushButton('Choose background'); image.clicked.connect(self.choose_image); row.addWidget(image); reset=QPushButton('Default background'); reset.clicked.connect(self.reset_image); row.addWidget(reset); row.addWidget(QLabel('Panel opacity')); slider=QSlider(Qt.Horizontal); slider.setRange(55,100); slider.setValue(self.opacity); slider.valueChanged.connect(self.change_opacity); row.addWidget(slider); outer.addLayout(row)
            self.phase_started=time.monotonic(); self.phase=''; self.timer=QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self.tick)
            self.style(); self.load()
        def field(self,layout,label,value,folder):
            layout.addWidget(QLabel(label)); row=QHBoxLayout(); edit=QLineEdit(value); row.addWidget(edit); button=QPushButton('Browse'); row.addWidget(button); layout.addLayout(row)
            def choose():
                p=QFileDialog.getExistingDirectory(self,label,edit.text()) if folder else QFileDialog.getOpenFileName(self,label,edit.text(),'JSON (*.json)')[0]
                if p: edit.setText(p)
            button.clicked.connect(choose); return edit
        def style(self):
            self.setStyleSheet('QLabel{color:#e1edf5} QFrame#panel{background-color:rgba(12,25,42,'+str(self.opacity*255//100)+');border:1px solid #405568;border-radius:14px} QLineEdit,QComboBox,QPlainTextEdit{background:#111f32;color:#edf6ff;border:1px solid #496179;border-radius:6px;padding:8px} QPushButton{background:#233e56;color:#edf6ff;border:1px solid #52748b;border-radius:7px;padding:9px} QPushButton#build{background:#176b61;font-weight:bold;padding:14px} QPushButton:disabled{color:#8294a5;background:#253342} QProgressBar{color:white;background:#18283a;border-radius:5px;text-align:center} QProgressBar::chunk{background:#258c7e;border-radius:5px}')
        def paintEvent(self,event):
            painter=QPainter(self); g=QLinearGradient(0,0,self.width(),self.height()); g.setColorAt(0,QColor('#153c50')); g.setColorAt(1,QColor('#100f29')); painter.fillRect(self.rect(),g)
            if not self.image.isNull():
                scaled=self.image.scaled(self.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation); painter.drawPixmap((self.width()-scaled.width())//2,(self.height()-scaled.height())//2,scaled)
        def choose_image(self):
            p=QFileDialog.getOpenFileName(self,'Background','','Images (*.png *.jpg *.jpeg *.webp)')[0]
            if p:
                pix=QPixmap(p)
                if not pix.isNull(): self.image=pix; self.settings.setValue('image',p); self.update()
        def reset_image(self): self.image=QPixmap(); self.settings.remove('image'); self.update()
        def change_opacity(self,v): self.opacity=v; self.settings.setValue('opacity',v); self.style()
        def load(self):
            self.target.clear()
            if not self.manifest.text(): return
            try:
                for e in validate(read(Path(self.manifest.text())))[1:]: self.target.addItem(f'{e["end_block"]:,}',e['end_block'])
                self.target.setCurrentIndex(self.target.count()-1); self.status.setText('Manifest loaded · choose a target')
            except Exception as exc: self.status.setText(str(exc))
        def tick(self):
            if self.worker and self.worker.isRunning():
                elapsed=int(time.monotonic()-self.phase_started)
                self.status.setText(f'{self.phase} · elapsed {elapsed//60} min {elapsed%60:02d} s')
        def message(self, msg):
            self.log.appendPlainText(msg)
            self.phase=msg; self.phase_started=time.monotonic(); self.status.setText(msg)
        def ready(self, result):
            self.timer.stop(); self.bar.setValue(self.bar.maximum())
            self.status.setText(('Verified archive ready: ' if result['fingerprints_verified'] else 'UNVERIFIED benchmark archive: ')+result['archive'])
        def failed(self, error):
            self.timer.stop(); self.status.setText('Stopped: '+error); self.log.appendPlainText('ERROR: '+error)
        def start_build(self):
            if self.worker and self.worker.isRunning(): return
            if self.target.currentData() is None: self.status.setText('Load a valid manifest first'); return
            for k in ('manifest','source','storage','gateway'): self.settings.setValue(k,getattr(self,k).text())
            args=(Path(self.manifest.text()),Path(self.source.text() or self.manifest.text()).parent if not self.source.text() else Path(self.source.text()),Path(self.storage.text()).expanduser(),self.gateway.text().strip(),self.target.currentData(),not self.skip.isChecked())
            self.start.setEnabled(False); self.log.clear(); self.bar.setValue(0); self.status.setText('Building a NEW isolated database…')
            self.worker=Worker(args); self.worker.message.connect(self.message); self.worker.progress.connect(lambda n,t:(self.bar.setMaximum(t),self.bar.setValue(n))); self.worker.result.connect(self.ready); self.worker.failed.connect(self.failed); self.worker.finished.connect(lambda:self.start.setEnabled(True)); self.phase='Preparing build'; self.phase_started=time.monotonic(); self.timer.start(); self.worker.start()
        def closeEvent(self,event):
            if self.worker and self.worker.isRunning(): QMessageBox.information(self,'Build running','Wait for the build to finish before closing.'); event.ignore()
            else: event.accept()

    app=QApplication(sys.argv); window=Window(); window.show(); return app.exec()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command')
    m=sub.add_parser('manifest'); m.add_argument('--data',type=Path,required=True); m.add_argument('--end',type=int,required=True); m.add_argument('--output',type=Path,required=True); m.add_argument('--cids',type=Path)
    args=parser.parse_args()
    if args.command=='manifest': make_manifest(args.data,args.end,args.output,args.cids)
    else: sys.exit(gui())
