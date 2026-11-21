import threading
import tempfile
import unittest
from pathlib import Path
from downloader import Prefetch, Cancelled

class PipelineTests(unittest.TestCase):
    def test_bounded_order_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cache=root/'cache'; cache.mkdir()
            entries=[dict(filename=f'{i}.gz') for i in range(6)]
            started=[]; two=threading.Event(); three=threading.Event()
            def acquire(entry,source,cache,gateway,log,stop,notify):
                started.append(entry)
                p=cache/entry['filename']; p.write_bytes(b'data')
                if len(started)==2: two.set()
                if len(started)==3: three.set()
                return p
            with Prefetch(entries,root,cache,'',lambda x:None,acquire_fn=acquire) as p:
                self.assertTrue(two.wait(2))
                self.assertFalse(three.wait(.1))
                first=p.take(entries[0])
                self.assertTrue(three.wait(2))
                self.assertTrue(first.exists())
                p.discard(first)
                self.assertFalse(first.exists())
                for entry in entries[1:]: p.discard(p.take(entry))
            self.assertEqual(list(cache.iterdir()),[])
    def test_pause_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cache=root/'cache'; cache.mkdir()
            original=root/'local.gz'; original.write_bytes(b'original')
            pause=threading.Event(); pause.set()
            with Prefetch([dict(filename='local.gz')],root,cache,'',lambda x:None,
                          acquire_fn=lambda *args:original) as p:
                with self.assertRaises(Cancelled): p.take(dict(filename='local.gz'),pause)
                p.discard(original)
            self.assertEqual(original.read_bytes(),b'original')
    def test_download_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def fail(*args): raise ValueError('bad hash')
            with Prefetch([dict(filename='x')],root,root,'',lambda x:None,acquire_fn=fail) as p:
                with self.assertRaisesRegex(ValueError,'bad hash'): p.take(dict(filename='x'))

if __name__=='__main__': unittest.main()
