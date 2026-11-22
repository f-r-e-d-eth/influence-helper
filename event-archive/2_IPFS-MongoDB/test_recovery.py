import json
import tempfile
import unittest
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from settings import DEFAULTS, load_settings, validate_settings, scheduled, next_check, enough_space
import coordinator

class ReconstructionTests(unittest.TestCase):
    def test_scheduled_archives_and_final_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp); cache=job/'downloads'; scratch=job/'scratch'; cache.mkdir(); scratch.mkdir()
            entries=[dict(end_block=b,bytes=1,filename=str(b)) for b in [-1,99999,199999,299999,399999,499999,599999]]
            pipeline=SimpleNamespace(take=lambda entry,*a: entry['end_block'], discard=lambda p:None, close=lambda:None)
            calls=[]; exports=[]; reports=[]
            @contextmanager
            def unpack(block,scratch): yield {'source_block':block-100000,'target_block':block},None
            def apply(db,block,journal,scratch,**kw):
                calls.append((block,kw['verify_source'],kw['verify_target']))
                return dict(status='applied_checkpoint_verified' if kw['verify_target'] else 'applied_verification_deferred',
                            metrics={'starting_checkpoint':{},'final_checkpoint':{},'bulk_writes':{'seconds':0}},application_seconds=0)
            def export(name,db,path):
                exports.append(path.name); path.write_bytes(b'dump'); return 'hash'
            def run(args,**kw): return SimpleNamespace(stdout='127.0.0.1:12345' if args[1]=='port' else 'container-id')
            class Client:
                def __init__(self,*a,**kw): self.admin=SimpleNamespace(command=lambda *a:None)
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def get_database(self,*a,**kw): return SimpleNamespace(command=lambda x:{'dataSize':100})
            def identity(manifest,side): return manifest[side+'_block']
            info={}; config={'settings':validate_settings(DEFAULTS)}
            with patch.dict(sys.modules,{'delta':SimpleNamespace(checkpoint_identity=identity)}), patch.multiple(coordinator,run=run,restore_bootstrap=lambda *a:None,export_archive=export,enough_space=lambda *a:True):
                result=coordinator.reconstruct(pipeline,entries,599999,True,info,config,'test',job,cache,scratch,
                    None,None,None,lambda stage,entry=None:reports.append(stage),lambda x:None,lambda *a:None,0,
                    apply,unpack,None,Client,lambda *a:None,lambda **kw:None)
            self.assertEqual(calls,[(99999,True,False),(199999,False,True),(299999,False,False),(399999,False,True),(499999,False,False),(599999,False,True)])
            self.assertEqual(exports,['recovery-199999.archive.gz','recovery-399999.archive.gz','adalaris-599999.archive.gz'])
            self.assertEqual(result['verified_through'],599999)
            self.assertEqual(result['recovery_archives'],[])
            self.assertEqual(list(job.glob('recovery-*')),[])

class SettingsTests(unittest.TestCase):
    def test_schedule(self):
        settings=validate_settings(DEFAULTS)
        self.assertEqual([b for b in range(99999,600000,100000) if scheduled(b,599999,settings)], [199999,399999])
        self.assertEqual([next_check(b,599999,settings) for b in (-1,99999,199999,399999,599999)], [199999,199999,399999,599999,None])

    def test_user_settings_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            self.assertEqual(load_settings(p),DEFAULTS)
            p.write_text(json.dumps({'checkpoint':{'interval_blocks':1000000}}))
            before=p.read_bytes()
            self.assertEqual(load_settings(p)['checkpoint']['interval_blocks'],1000000)
            self.assertEqual(p.read_bytes(),before)

    def test_invalid_settings(self):
        for config in ({'checkpoint':{'interval_blocks':1}}, {'checkpoint':{'keep_checkpoints':0}},
                       {'verification':{'verify_final_database':False}}, {'download':{'prefetch_deltas':True}}):
            with self.assertRaises(ValueError): validate_settings(config)

    def test_space(self):
        with tempfile.TemporaryDirectory() as tmp, patch('settings.shutil.disk_usage') as usage:
            usage.return_value.free=9*1024**3
            self.assertFalse(enough_space(tmp,1024,1024,DEFAULTS))
            usage.return_value.free=20*1024**3
            self.assertTrue(enough_space(tmp,1024,1024,DEFAULTS))

if __name__=='__main__': unittest.main()
