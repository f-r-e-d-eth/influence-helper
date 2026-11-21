"""MongoDB operations used by the reconstruction coordinator."""
import gzip
import os
import subprocess
from manifest import digest

def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def restore_bootstrap(name, path, database):
    with path.open('rb') as stream:
        run(['docker','exec','-i',name,'mongorestore','--archive','--gzip','--stopOnError',
             '--nsInclude','v12_reconstruction.*','--nsFrom','v12_reconstruction.*','--nsTo',database+'.*'], stdin=stream)

def export_archive(name, database, archive):
    partial=archive.with_suffix('.gz.part')
    with partial.open('xb') as stream:
        run(['docker','exec',name,'mongodump','--db',database,'--archive','--gzip'],stdout=stream)
        stream.flush(); os.fsync(stream.fileno())
    with gzip.open(partial,'rb') as stream:
        if not stream.read(1): raise ValueError('Empty database archive')
        while stream.read(1024*1024): pass
    checksum=digest(partial)
    os.replace(partial,archive)
    return checksum
