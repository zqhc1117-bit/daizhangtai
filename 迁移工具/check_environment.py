from pathlib import Path
import hashlib, sqlite3, sys
from PIL import Image
from pypdf import PdfReader
import pypdfium2, numpy, cv2, onnxruntime
from rapidocr_onnxruntime import RapidOCR
root = Path(__file__).resolve().parent.parent
assert Path(sys.executable).resolve() == (root/'runtime/python/python.exe').resolve()
RapidOCR(rec_batch_num=1)
print('随包 Python、PDF 解析、PDF 图像转换、OCR 模型：正常')
with sqlite3.connect((root/'data/app.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    for path, size, expected in db.execute('SELECT path,size,sha256 FROM files'):
        item = (root/'data/files'/path).resolve()
        assert item.parent == (root/'data/files').resolve()
        assert item.stat().st_size==size
        with item.open('rb') as stream:
            assert hashlib.file_digest(stream,'sha256').hexdigest()==expected
print('账套数据库、账务关联、全部已登记附件：正常')
print('检查完成。请双击“一键启动工作台.cmd”。')
