import io, json, queue, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import reconciliation

class OCRDiagnosticTests(unittest.TestCase):
    def run_worker_response(self, response):
        worker=reconciliation.OCRWorker()
        worker.py=Path(sys.executable)
        proc=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(),kill=Mock(),wait=Mock())
        worker.proc=proc; worker.lines=queue.Queue(); worker.lines.put(json.dumps(response))
        result,error=worker.run(Path('sample.png'),'invoice')
        return worker,proc,result,error
    def test_memory_error_becomes_actionable_and_process_is_restarted(self):
        worker,proc,result,error=self.run_worker_response({'ok':False,'error_code':'memory_allocation','error':'PRIVATE SECRET'})
        self.assertIsNone(result); self.assertIn('内存分配失败',error); self.assertIn('分批',error)
        self.assertNotIn('PRIVATE',error); self.assertIsNone(worker.proc); proc.kill.assert_called_once()
    def test_untrusted_worker_error_is_not_exposed(self):
        worker,proc,result,error=self.run_worker_response({'ok':False,'error_code':'unknown','error':'PRIVATE SECRET'})
        self.assertIsNone(result); self.assertIn('OCR 识别失败',error)
        self.assertNotIn('PRIVATE',error)
        worker.close()

    def test_native_memory_exit_retries_once_with_limited_threads(self):
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'native_failure.py'
            script.write_text("import sys,json,os\nfor line in sys.stdin:\n r=json.loads(line)\n if not r.get('low_memory'):\n  print('OpenBLAS error: Memory allocation still failed after 10 retries, giving up. PRIVATE',file=sys.stderr,flush=True)\n  sys.exit(1)\n print(json.dumps({'ok':True,'memory_recovery':True,'threads':os.environ['OPENBLAS_NUM_THREADS']}),flush=True)\n",encoding='utf-8')
            worker=reconciliation.OCRWorker();worker.py=Path(sys.executable);worker.script=script
            try:
                result,error=worker.run(Path(folder)/'synthetic.png','bank')
                self.assertFalse(error)
                self.assertTrue(result['memory_recovery'])
                self.assertEqual(result['threads'],'1')
            finally:worker.close()

    def test_repeated_native_memory_exit_stops_after_one_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            counter=Path(folder)/'calls.txt'
            script=Path(folder)/'native_failure.py'
            script.write_text("import sys\nfrom pathlib import Path\nfor line in sys.stdin:\n with Path("+repr(str(counter))+").open('a') as f:f.write('call\\n')\n print('OpenBLAS error: Memory allocation still failed after 10 retries PRIVATE',file=sys.stderr,flush=True)\n sys.exit(1)\n",encoding='utf-8')
            worker=reconciliation.OCRWorker();worker.py=Path(sys.executable);worker.script=script
            try:
                result,error=worker.run(Path(folder)/'synthetic.png','bank')
                self.assertIsNone(result)
                self.assertIn('内存分配失败',error)
                self.assertNotIn('PRIVATE',error)
                self.assertEqual(len(counter.read_text().splitlines()),2)
            finally:worker.close()

if __name__=='__main__': unittest.main()
