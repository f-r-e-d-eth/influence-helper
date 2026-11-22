"""Bounded, ordered prefetch of integrity-checked packages."""
import queue
import threading
import time
import urllib.request
import os
import shutil
import uuid
from manifest import digest

class Cancelled(Exception):
    pass

def acquire(entry, source, cache, gateway, log, stop, notify):
    name=entry['filename']
    def check(path):
        return path.is_file() and path.stat().st_size==entry['bytes'] and digest(path)==entry['sha256']
    for path in (source/name,source/'snapshots'/name,source/'deltas'/name,cache/name):
        if stop.is_set(): raise Cancelled()
        if check(path):
            notify(dict(file=name,received=entry['bytes'],total=entry['bytes'],speed=0,status='Local file verified'))
            return path
    if not gateway.startswith('https://') or not entry.get('cid'):
        raise ValueError(f'No matching local file or HTTPS IPFS CID for {name}')
    dest=cache/name
    for attempt in range(3):
        partial=cache/(name+'.'+uuid.uuid4().hex+'.part')
        started=time.monotonic(); count=0
        try:
            if stop.is_set(): raise Cancelled()
            log(f'Downloading {name} (attempt {attempt+1}/3)')
            with urllib.request.urlopen(gateway.rstrip('/')+'/ipfs/'+entry['cid'],timeout=20) as response, partial.open('xb') as out:
                if not response.geturl().startswith('https://'): raise ValueError('Insecure redirect')
                while True:
                    if stop.is_set(): raise Cancelled()
                    chunk=response.read(1024*1024)
                    if not chunk: break
                    count+=len(chunk)
                    if count>entry['bytes']: raise ValueError('Download exceeds manifest size')
                    out.write(chunk)
                    notify(dict(file=name,received=count,total=entry['bytes'],speed=count/max(.001,time.monotonic()-started),status='Downloading'))
                out.flush(); os.fsync(out.fileno())
            if not check(partial): raise ValueError('Package integrity mismatch')
            if stop.is_set(): raise Cancelled()
            os.replace(partial,dest)
            notify(dict(file=name,received=count,total=entry['bytes'],speed=count/max(.001,time.monotonic()-started),status='Verified'))
            return dest
        except Cancelled: raise
        except Exception:
            if attempt==2: raise
            if stop.wait(2**attempt): raise Cancelled()
        finally:
            partial.unlink(missing_ok=True)

class Prefetch:
    """Two reserved waiting slots, including the in-flight download."""
    def __init__(self, entries, source, cache, gateway, log, notify=lambda data:None, capacity=2, acquire_fn=acquire):
        self.entries=list(entries); self.source=source; self.cache=cache; self.gateway=gateway
        self.log=log; self.notify=lambda data:notify(dict(data,free_bytes=shutil.disk_usage(cache).free)); self.acquire=acquire_fn
        self.stop=threading.Event(); self.slots=threading.Semaphore(capacity)
        self.ready=queue.Queue(); self.error=None; self.finished=threading.Event()
        self.thread=threading.Thread(target=self._produce,name='Adalaris download')
    def __enter__(self):
        self.thread.start(); return self
    def _produce(self):
        try:
            for entry in self.entries:
                while not self.stop.is_set():
                    if self.slots.acquire(timeout=.2): break
                else: return
                if self.stop.is_set(): return
                path=self.acquire(entry,self.source,self.cache,self.gateway,self.log,self.stop,
                    lambda data:self.notify(dict(data,buffered=self.ready.qsize())))
                self.ready.put((entry,path))
                self.notify(dict(status='Package ready',buffered=self.ready.qsize(),file=entry['filename'],received=entry.get('bytes',0),total=entry.get('bytes',0)))
        except Cancelled: pass
        except Exception as exc: self.error=exc
        finally: self.finished.set()
    def take(self, entry, pause=None):
        while True:
            if pause is not None and pause.is_set(): raise Cancelled()
            try: actual,path=self.ready.get(timeout=.2)
            except queue.Empty:
                if self.finished.is_set():
                    if self.error: raise self.error
                    raise RuntimeError('Download worker ended before requested package')
                continue
            self.slots.release()
            if actual!=entry: raise RuntimeError('Out-of-order package')
            return path
    def discard(self,path):
        # Never touch a user-supplied source file.
        if path.parent==self.cache: path.unlink(missing_ok=True)
    def close(self):
        self.stop.set()
        self.thread.join()
        # Only the dedicated per-build cache is owned by this worker.
        while True:
            try: _,path=self.ready.get_nowait()
            except queue.Empty: break
            self.discard(path)
    def __exit__(self,*args): self.close()
