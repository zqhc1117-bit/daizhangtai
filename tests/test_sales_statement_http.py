import base64
from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import db
import server
import statement_extract as extract
from test_http import Client


class SalesStatementHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sales-http-')
        self.app=server.Application(self.temp.name,demo=True)
        self.http=server.make_server('127.0.0.1',0,self.app)
        self.http.RequestHandlerClass.log_message=lambda *args:None
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_address[1]}'
        self.client=Client(self.base)
        code,body,_=self.client.call('/api/login',{'username':'demo1','password':'DemoOnly123!'})
        self.assertEqual(code,200,body)
        self.path='/api/sales/statement-text'
        self.payload={'name':'report.pdf','content_base64':base64.b64encode(b'%PDF-1.4\nfixture').decode()}

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=5);self.temp.cleanup()

    def test_authentication_csrf_and_origin(self):
        self.assertEqual(Client(self.base).call(self.path,self.payload)[0],401)
        self.assertEqual(self.client.call(self.path,self.payload,csrf=False)[0],403)
        self.assertEqual(self.client.call(self.path,self.payload,headers={'Origin':'https://example.invalid'})[0],403)

    def test_bad_files_and_busy_worker(self):
        for payload in [{'name':'x.docx','content_base64':'QQ=='},{'name':'x.pdf','content_base64':'@@@'},{'name':'x.pdf','content_base64':'QQ=='}]:
            self.assertEqual(self.client.call(self.path,payload)[0],400)
        self.assertEqual(self.client.call(self.path,{'name':'large.pdf','content_base64':'a'*(14*1024*1024)})[0],413)
        extract._slot.acquire()
        try:self.assertEqual(self.client.call(self.path,self.payload)[0],429)
        finally:extract._slot.release()

    def test_read_only_extraction_is_outside_write_lock_and_cleans_tempfile(self):
        rows=[['项目','期末余额'],['货币资金','80,000.00'],['实收资本','80,000.00']]
        temporary=[]
        def run(command,**kwargs):
            temporary.append(Path(command[-1]));self.assertTrue(temporary[-1].exists())
            self.assertEqual(temporary[-1].read_bytes(),b'%PDF-1.4\nfixture')
            return subprocess.CompletedProcess(command,0,json.dumps({'ok':True,'rows':rows,'ocr':False,'warnings':[]}), '')
        with closing(db.connect(self.app.db_path)) as conn:
            before={t:conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('clients','files','vouchers','audit_logs')}
        # Holding the accounting write lock must not block recognition.
        with self.app.lock, patch.object(extract.subprocess,'run',side_effect=run):
            code,result,_=self.client.call(self.path,self.payload)
        self.assertEqual(code,200,result);self.assertEqual(result['rows'],rows)
        self.assertFalse(temporary[0].exists())
        with closing(db.connect(self.app.db_path)) as conn:
            after={t:conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in before}
        self.assertEqual(before,after)

    def test_timeout_releases_worker_and_removes_temporary_upload(self):
        paths=[]
        def timeout(command,**kwargs):
            paths.append(Path(command[-1]));raise subprocess.TimeoutExpired(command,210)
        with patch.object(extract.subprocess,'run',side_effect=timeout):
            self.assertEqual(self.client.call(self.path,self.payload)[0],408)
        self.assertFalse(paths[0].exists());self.assertTrue(extract._slot.acquire(blocking=False));extract._slot.release()


class SpatialRowsTests(unittest.TestCase):
    def test_native_punctuation_uses_font_line_boxes(self):
        class TextPage:
            def count_chars(self):return 9
            def get_text_range(self,index,count):return '80,000.00'[index]
            def get_charbox(self,index,loose=False):
                if not loose:raise AssertionError('Tight punctuation boxes drop decimal points.')
                return (index*8,0,(index+1)*8,16)
            def close(self):pass
        class Page:
            def get_textpage(self):return TextPage()
        self.assertEqual(extract.native_items(Page())[0]['text'],'80,000.00')

    def test_grid_keeps_blank_amounts_and_side_by_side_names(self):
        def item(text,x,y):return {'text':text,'left':x,'right':x+30,'y':y,'height':12}
        rows=extract.table_rows([item(t,x,0) for t,x in [('资产',0),('行次',100),('期末余额',200),('年初余额',300),('负债和所有者权益',400),('行次',500),('期末余额',600),('年初余额',700)]]+[item(t,x,30) for t,x in [('货币资金',0),('1',100),('80,000.00',200),('应付账款',400),('2',500),('20,000.00',600)]])
        self.assertEqual(rows[1],['货币资金','1','80,000.00','','应付账款','2','20,000.00',''])


if __name__=='__main__':unittest.main()
