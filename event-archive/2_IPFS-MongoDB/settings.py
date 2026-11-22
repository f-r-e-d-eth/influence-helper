"""Persistent user settings and recovery checkpoint policy."""
import json
import os
import shutil
from pathlib import Path

GIB = 1024**3
DEFAULTS = {
    'checkpoint': {'_comment': 'Save a verified recovery archive at this block interval when space permits.',
                   'enabled': True, 'interval_blocks': 200000, 'automatic_storage_check': True,
                   'minimum_free_gib': 10, 'keep_checkpoints': 1},
    'verification': {'_comment': 'Full final and recovery checkpoint checks are mandatory in verified mode.',
                     'verify_every_delta': False, 'verify_checkpoints': True, 'verify_final_database': True},
    'download': {'_comment': 'Waiting packages including download in flight; attempts per package.',
                 'prefetch_deltas': 1, 'max_retries': 3},
}

def validate_settings(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS) - {'_comment'}:
        raise ValueError('Unknown settings section')
    result = {}
    for section, defaults in DEFAULTS.items():
        supplied = raw.get(section, {})
        if not isinstance(supplied, dict) or set(supplied) - set(defaults):
            raise ValueError(f'Unknown {section} setting')
        result[section] = {}
        for key, default in defaults.items():
            value = supplied.get(key, default)
            if type(value) is not type(default):
                raise ValueError(f'Invalid type: {section}.{key}')
            result[section][key] = value
    c, v, d = (result[k] for k in ('checkpoint','verification','download'))
    if c['interval_blocks'] < 100000 or c['interval_blocks'] % 100000 or c['minimum_free_gib'] < 1 or not 1 <= c['keep_checkpoints'] <= 10:
        raise ValueError('Invalid checkpoint interval, free space or retention')
    if not v['verify_checkpoints'] or not v['verify_final_database']:
        raise ValueError('Checkpoint and final verification cannot be disabled in verified mode')
    if not 1 <= d['prefetch_deltas'] <= 8 or not 1 <= d['max_retries'] <= 10:
        raise ValueError('Invalid download settings')
    return result

def load_settings(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x') as stream:
            json.dump(DEFAULTS, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        pass
    return validate_settings(json.loads(path.read_text()))

def scheduled(block, end, settings):
    c = settings['checkpoint']
    return c['enabled'] and block < end and (block+1) % c['interval_blocks'] == 0

def next_check(block, end, settings):
    if block is None: block = -1
    if block >= end: return None
    if settings['verification']['verify_every_delta']: return min(block+100000,end)
    c = settings['checkpoint']; interval = c['interval_blocks']
    return min(((block+1)//interval+1)*interval-1,end) if c['enabled'] else end

def enough_space(folder, logical_bytes, pending_bytes, settings):
    # Conservative estimate, not a guarantee: room for new dump, final dump,
    # anticipated downloads, and workspace reserve while retaining old archive.
    c = settings['checkpoint']
    if not c['automatic_storage_check']: return True
    estimate = 2 * max(logical_bytes, 64*1024**2) + pending_bytes + c['minimum_free_gib']*GIB
    return shutil.disk_usage(folder).free >= estimate
