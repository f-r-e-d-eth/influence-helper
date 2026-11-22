"""Adalaris reconstruction coordinator: ordered import, bounded prefetch."""
import fcntl, hashlib, json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
from manifest import digest, read, validate, fetch_manifest, STEP
from downloader import Prefetch, Cancelled
from importer import run, restore_bootstrap, export_archive
from settings import load_settings, validate_settings, scheduled, next_check, enough_space
ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'delta-engine'
STEP = 100_000
DB = 'v12_disposable_world_builder'
APP_HOME = Path.home() / 'Adalaris'
DATA = APP_HOME / 'data'
WORLDS = APP_HOME / 'worlds'
BACKGROUNDS = APP_HOME / 'backgrounds'
SETTINGS = APP_HOME / 'settings.json'
GATEWAY = 'https://ipfs.filebase.io'
MANIFEST_URL = 'https://raw.githubusercontent.com/f-r-e-d-eth/influence-helper/main/event-archive/2_IPFS-MongoDB/manifest.json'


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
        if journal.get('status') not in ('applied_checkpoint_verified', 'applied_unverified_benchmark', 'applied_verification_deferred'):
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
    if os.environ.get('ADALARIS_MANIFEST_URL'):
        return (None, DATA, storage, GATEWAY, None, verify_fingerprints, None)
    manifest = DATA / 'manifest.json'
    if not manifest.is_file():
        return (None, DATA, storage, GATEWAY, None, verify_fingerprints, None)
    end = validate(read(manifest))[-1]['end_block']
    return (manifest, DATA, storage, GATEWAY, end, verify_fingerprints, None)


def build(manifest_path, source, storage, gateway, end, verify_fingerprints, log, progress, pause=None, resume=None, stats=None, download_stats=None):
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
                raw = manifest_path.read_bytes() if manifest_path is not None else fetch_manifest(os.environ.get('ADALARIS_MANIFEST_URL') or MANIFEST_URL)
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            if end is None: end=validate(json.loads(raw))[-1]['end_block']
            manifest_path = pinned
        return build_locked(manifest_path, source, gateway, end, verify_fingerprints, log, progress, pause, resume, job, stats, download_stats)


def build_locked(manifest_path, source, gateway, end, verify_fingerprints, log, progress, pause, resume, job, stats=None, download_stats=None):
    started = time.monotonic()
    manifest_bytes = manifest_path.read_bytes()
    entries = validate(json.loads(manifest_bytes))
    if end not in [e['end_block'] for e in entries[1:]]:
        raise ValueError('Select a checkpoint in the manifest')
    entries = [e for e in entries if e['end_block'] <= end]
    # Use the consumer-local engine; producer code remains unchanged.
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
        if 'settings' not in info.get('config', {}):
            raise ValueError('Resume v0.2 paused builds using the archived v0.2 application')
        settings = validate_settings(info['config']['settings'])
    else:
        settings = load_settings(SETTINGS)
    config['settings'] = settings
    if resume:
        if (info.get('status') != 'paused' or info.get('config') != config or
                info.get('container') != name or info.get('volume') != name or info.get('database') != DB):
            raise ValueError('Not a matching safely paused build')
        last = info.get('applied_through')
        if last not in [e['end_block'] for e in entries]:
            raise ValueError('Invalid paused checkpoint')
        expected = ('applied_checkpoint_verified', 'applied_verification_deferred') if verify_fingerprints else ('applied_unverified_benchmark',)
        for entry in entries[1:]:
            journal = job/f'apply-{entry["end_block"]}.json'
            if entry['end_block'] <= last:
                record = read(journal)
                if record['status'] not in expected or record['package_sha256'] != entry['sha256']:
                    raise ValueError('Paused journal mismatch')
            elif journal.exists():
                raise ValueError('Unexpected later journal; cannot safely resume')
        identity = json.loads(run(['docker','inspect',name],capture_output=True,text=True).stdout)[0]
        if (identity['Id'] != info['container_id'] or identity['State']['Running'] or
                identity['State']['ExitCode'] != 0 or not any(m.get('Name') == name and m.get('Destination') == '/data/db' for m in identity['Mounts'])):
            raise ValueError('Paused container missing, changed, or not cleanly stopped')
    cache = job / 'downloads'; cache.mkdir(exist_ok=True)
    scratch = job / 'scratch'; scratch.mkdir(exist_ok=True)
    if info is None: info={}
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
                   activity=last_activity(job,last), verified=info.get('verified_through'),
                   recovery=list(info.get('recovery_archives',[])), recovery_status=info.get('recovery_status','Waiting'),
                   next_verification=next_check(last,end,settings) if verify_fingerprints else None,
                   free_bytes=shutil.disk_usage(job).free))
    # A legacy paused build may have cached all packages. Remove only known
    # generated cache files; original input files are never removed.
    for entry in entries:
        path=cache/entry['filename']
        if path.is_file(): path.unlink()
    last=info['applied_through'] if resume else None
    pending=[e for e in entries if last is None or e['end_block']>last]
    if resume and verify_fingerprints and last==end:
        pending=[entries[-1]]  # recheck last fingerprint before export
    with Prefetch(pending,source,cache,gateway,log,download_stats or (lambda data:None),
                  capacity=settings['download']['prefetch_deltas'], attempts=settings['download']['max_retries']) as pipeline:
        return reconstruct(pipeline, entries, end, verify_fingerprints, info, config, name, job, cache, scratch,
                           resume, pause, stats, report, log, progress, started, apply_package, unpack, checkpoint,
                           MongoClient, ReadConcern, WriteConcern)


def reconstruct(pipeline, entries, end, verify_fingerprints, info, config, name, job, cache, scratch,
                resume, pause, stats, report, log, progress, started, apply_package, unpack, checkpoint,
                MongoClient, ReadConcern, WriteConcern):
    from delta import checkpoint_identity
    settings = config['settings']
    if not resume:
        bootstrap=pipeline.take(entries[0])
        report('Preparing MongoDB')
        log(f'Creating isolated MongoDB container {name} — Docker may download the image on first use. Please wait.')
        container_id = run(['docker','run','-d','--name',name,'--label','influence.world-builder=0.3',
             '-p','127.0.0.1::27017','-v',name+':/data/db','mongo:7','mongod','--wiredTigerCacheSizeGB','2'], capture_output=True,text=True).stdout.strip()
        info.update(dict(container=name, container_id=container_id, volume=name, database=DB, status='incomplete',
                    verified_through=None, applied_through=None, fingerprints_verified=False, config=config,
                    recovery_archives=[], recovery_status='Waiting' if verify_fingerprints and settings['checkpoint']['enabled'] else 'Disabled',
                    timings={'fingerprints_seconds':0.0,'bulk_writes_seconds':0.0,'application_seconds':0.0}))
    def save():
        tmp = job/'result.tmp'
        with tmp.open('w') as stream:
            json.dump(info,stream,indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,job/'result.json')
        fd=os.open(job,os.O_RDONLY); os.fsync(fd); os.close(fd)
    def save_recovery(block):
        archive=job/f'recovery-{block}.archive.gz'
        checksum=export_archive(name,DB,archive)
        record=dict(block=block,archive=archive.name,bytes=archive.stat().st_size,sha256=checksum)
        info['recovery_archives'].append(record)
        info['recovery_status']='Saved'; save()
        # Only remove old, recorded archives after the replacement is durable.
        while len(info['recovery_archives']) > settings['checkpoint']['keep_checkpoints']:
            old=info['recovery_archives'][0]
            if old['archive'] != f"recovery-{old['block']}.archive.gz": raise ValueError('Unexpected recovery filename')
            (job/old['archive']).unlink(missing_ok=True)
            info['recovery_archives'].pop(0); save()
        log(f'Recovery archive saved at {block:,} ({record["bytes"] / 1024**2:.1f} MiB)')
    info['status']='incomplete'; save()
    if resume:
        log(f'Restarting paused container {name}')
        run(['docker','start',name])
    port = run(['docker','port',name,'27017/tcp'],capture_output=True,text=True).stdout.strip().splitlines()[0].rsplit(':',1)[1]
    uri = 'mongodb://127.0.0.1:'+port
    info['uri']=uri; save()
    def pause_at_boundary(force=False):
        if not force and (not pause or not pause.is_set()): return False
        pipeline.close()
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
        progress(0,len(entries)+2)
        if not resume:
            report('Restoring bootstrap', entries[0])
            log('Restoring bootstrap — creating collections and indexes…')
            restore_bootstrap(name,bootstrap,DB)
            info['applied_through']=-1; save()
            pipeline.discard(bootstrap)
        progress(1,len(entries)+2)
        db = client.get_database(DB,read_concern=ReadConcern('majority'),write_concern=WriteConcern(w='majority',j=True))
        # Trust only a verified target from this uninterrupted process.
        # On resume, the first pending delta performs a full source scan.
        previous_expected = None
        source_scan = verify_fingerprints
        resume_identity = None
        if resume and info['applied_through'] >= 0:
            resume_identity = read(job/f"apply-{info['applied_through']}.json")['expected_target_checkpoint']
        if resume and verify_fingerprints and info['applied_through'] == end:
            log('Rechecking final checkpoint after resume before export…')
            measured = {}
            path=pipeline.take(entries[-1])
            with unpack(path, scratch) as (manifest, _):
                if manifest['target_block']!=end or manifest['source_block']!=end-STEP: raise ValueError('Delta block boundary mismatch')
                if resume_identity != json.loads(json.dumps(checkpoint_identity(manifest,'target'))): raise ValueError('Paused final checkpoint continuity mismatch')
                checkpoint(db, manifest, 'target', scratch, 32*1024*1024, measured)
            info['timings']['fingerprints_seconds'] += measured['seconds']
            info['verified_through']=end
            save(); pipeline.discard(path)
        for i, entry in enumerate(entries[1:],1):
            if entry['end_block'] <= info['applied_through']:
                progress(i+1,len(entries)+2)
                continue
            if pause_at_boundary(): return info
            report('Waiting for delta', entry)
            try:
                path=pipeline.take(entry,pause)
            except Cancelled:
                if pause_at_boundary(force=True): return info
            except Exception as exc:
                log(f'Download failed; preserving last completed checkpoint: {exc}')
                if pause_at_boundary(force=True): return info
            if pause and pause.is_set():
                pipeline.discard(path)
                if pause_at_boundary(): return info
            with unpack(path,scratch) as (manifest,_):
                if manifest['source_block']!=entry['end_block']-STEP or manifest['target_block']!=entry['end_block']:
                    raise ValueError('Delta block boundary mismatch')
                target_identity=checkpoint_identity(manifest,'target')
                if previous_expected is not None and previous_expected != checkpoint_identity(manifest,'source'):
                    raise ValueError('Delta checkpoint continuity mismatch')
                if resume_identity is not None and resume_identity != json.loads(json.dumps(checkpoint_identity(manifest,'source'))):
                    raise ValueError('Paused delta checkpoint continuity mismatch')
            block=entry['end_block']
            recovery_due=verify_fingerprints and scheduled(block,end,settings)
            target_scan=verify_fingerprints and (block==end or recovery_due or settings['verification']['verify_every_delta'])
            report('Applying delta', entry)
            log(f'Applying{" and verifying" if target_scan else " (fingerprint deferred)" if verify_fingerprints else " (benchmark, no fingerprints)"} through {block:,}')
            details = []
            for key, label in (('completed_operations','operations'), ('target_documents','target documents')):
                if isinstance(entry.get(key), int): details.append(f'{entry[key]:,} {label}')
            if isinstance(entry.get('application_seconds'), (int,float)):
                details.append(f'previous verified application: {entry["application_seconds"]/60:.1f} min')
            if details: log(' · '.join(details))
            if isinstance(entry.get('comment'), str): log('Checkpoint note: '+entry['comment'])
            log('Reading, writing and verifying database fingerprints — please wait…' if target_scan or source_scan else 'Writing delta with package and operation checks only — please wait…')
            result = apply_package(db,path,job/f'apply-{entry["end_block"]}.json',scratch,
                                   verify_fingerprints=verify_fingerprints, verify_source=source_scan,
                                   verify_target=target_scan, expected_source=previous_expected, deferred=verify_fingerprints)
            expected = 'applied_checkpoint_verified' if target_scan else 'applied_verification_deferred' if verify_fingerprints else 'applied_unverified_benchmark'
            if result['status'] != expected:
                raise RuntimeError('Delta application failed')
            previous_expected = target_identity
            resume_identity = None
            source_scan = False
            metrics = result['metrics']; timings = info['timings']
            checks = sum(metrics[k].get('seconds',0) for k in ('starting_checkpoint','final_checkpoint'))
            writes = metrics['bulk_writes']['seconds']
            timings['fingerprints_seconds'] += checks; timings['bulk_writes_seconds'] += writes
            timings['application_seconds'] += result['application_seconds']
            log(f'Checkpoint: fingerprint scans {checks:.1f} s · bulk writes {writes:.1f} s · total {result["application_seconds"]:.1f} s')
            info['applied_through'] = entry['end_block']
            if target_scan: info['verified_through'] = block
            save()
            pipeline.discard(path)
            if recovery_due:
                logical=int(db.command('dbStats').get('dataSize',0))
                waiting=sorted((e['bytes'] for e in entries if e['end_block']>block),reverse=True)[:settings['download']['prefetch_deltas']+1]
                if enough_space(job,logical,sum(waiting),settings):
                    info['recovery_status']='Saving'; report('Saving recovery archive',entry)
                    save_recovery(block)
                else:
                    info['recovery_status']='Skipped: insufficient free space'; save()
                    log('Recovery archive skipped — insufficient estimated free space; previous archive retained')
            report('Checkpoint complete', entry)
            progress(i+1,len(entries)+2)
    if pause_at_boundary(): return info
    pipeline.close()
    if verify_fingerprints and info['verified_through'] != end:
        raise RuntimeError('Final fingerprint has not been verified')
    report('Exporting database')
    log('Exporting final database as compressed MongoDB archive…')
    export_started = time.monotonic()
    archive = job / (f'adalaris-{end}.archive.gz' if verify_fingerprints else f'adalaris-{end}-UNVERIFIED.archive.gz')
    checksum=export_archive(name,DB,archive)
    info.update(archive=str(archive),archive_bytes=archive.stat().st_size,archive_sha256=checksum,fingerprints_verified=verify_fingerprints,
                status='exported_verified' if verify_fingerprints else 'exported_unverified_benchmark',cleanup_complete=False)
    info['timings']['export_seconds']=time.monotonic()-export_started; save()
    if verify_fingerprints:
        info['recovery_status']='Final archive saved'; save()
        for old in list(info['recovery_archives']):
            if old['archive'] != f"recovery-{old['block']}.archive.gz": raise ValueError('Unexpected recovery filename')
            (job/old['archive']).unlink(missing_ok=True)
            info['recovery_archives'].remove(old); save()
    progress(len(entries)+1,len(entries)+2)
    log('Archive saved and gzip checked. Removing temporary MongoDB container and volume…')
    run(['docker','stop','-t','120',name]); run(['docker','rm','-v',name]); run(['docker','volume','rm',name])
    shutil.rmtree(cache); shutil.rmtree(scratch)
    info['cleanup_complete']=True; info['uri']=None; info['timings']['total_seconds']=info['timings'].get('active_seconds',0)+time.monotonic()-started; save()
    progress(len(entries)+2,len(entries)+2)
    report('Complete')
    log('Archive ready — '+str(archive))
    log(f'Total fingerprint scans: {info["timings"]["fingerprints_seconds"]:.1f} s · bulk writes: {info["timings"]["bulk_writes_seconds"]:.1f} s')
    return info


