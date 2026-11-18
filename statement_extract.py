"""Read-only, bounded PDF / image table extraction for the sales workspace.

Uploaded documents are processed in a temporary directory by the bundled Python.
No company ID, ledger write, archive write or external service is involved.
"""
import base64
import binascii
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading

_slot = threading.BoundedSemaphore(1)
MAX_BYTES = 10 * 1024 * 1024
ROOT = Path(__file__).resolve().parent


def extract_upload(payload):
    from db import ApiError
    name = str(payload.get('name', ''))
    extension = Path(name).suffix.lower()
    if extension not in ('.pdf', '.png', '.jpg', '.jpeg', '.webp'):
        raise ApiError(400, '该识别接口支持 PDF、PNG、JPG 和 WebP 文件。')
    encoded = payload.get('content_base64')
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
        raise ApiError(413, '文件内容无效或超过 10 MB。')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ApiError(400, '文件编码无效，请重新选择文件。')
    if not content or len(content) > MAX_BYTES:
        raise ApiError(400, '文件为空或超过 10 MB。')
    signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff', '.webp': b'RIFF'}
    if not content.startswith(signatures[extension]):
        raise ApiError(400, '文件内容与扩展名不一致，请重新导出或选择原文件。')
    if not _slot.acquire(blocking=False):
        raise ApiError(429, '另一份报表正在识别，请稍后重试。')
    try:
        interpreter = ROOT / 'runtime' / 'python' / 'python.exe'
        if not interpreter.is_file():
            interpreter = Path(sys.executable)
        with tempfile.TemporaryDirectory(prefix='ydz-sales-statement-') as folder:
            original = Path(folder) / ('statement' + extension)
            original.write_bytes(content)
            try:
                result = subprocess.run(
                    [str(interpreter), '-X', 'utf8', str(Path(__file__).resolve()), str(original)],
                    capture_output=True, timeout=210,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    encoding='utf-8', errors='replace',
                )
            except subprocess.TimeoutExpired:
                raise ApiError(408, '报表识别超时，请缩小或拆分文件后重试。')
            try:
                parsed = json.loads(result.stdout)
            except (ValueError, TypeError):
                raise ApiError(400, '报表识别组件未能完成读取，请检查文件或使用 Excel 原表。')
            if not parsed.get('ok'):
                raise ApiError(400, parsed.get('error', '文件识别失败。'))
            return {key: parsed[key] for key in ('rows', 'ocr', 'warnings')}
    finally:
        _slot.release()


def row_groups(items):
    groups = []
    for item in sorted(items, key=lambda x: (x['y'], x['left'])):
        if not item['text'].strip():
            continue
        tolerance = max(2, item['height'] * .55)
        if groups and abs(item['y'] - groups[-1]['y']) <= tolerance:
            groups[-1]['items'].append(item)
        else:
            groups.append({'y': item['y'], 'items': [item]})
    return [sorted(group['items'], key=lambda x: x['left']) for group in groups]


def native_items(page):
    """Retain cell coordinates; PDF drawing order need not equal row order."""
    textpage = page.get_textpage()
    chars = []
    try:
        if textpage.count_chars() > 120000:
            raise ValueError('单页文字过多，请缩小报表范围。')
        for index in range(textpage.count_chars()):
            char = textpage.get_text_range(index, 1)
            if not char.strip():
                continue
            # Loose boxes use the font line box, so commas, decimal points and
            # minus signs stay on the same row as digits instead of being lost.
            left, bottom, right, top = textpage.get_charbox(index, loose=True)
            chars.append({'text': char, 'left': left, 'right': right, 'y': -(bottom + top) / 2,
                          'height': max(1, top - bottom)})
    finally:
        textpage.close()
    items = []
    for line in row_groups(chars):
        current = None
        for char in line:
            width = max(1, char['right'] - char['left'])
            # Within-cell letters/digits stay together; a table gap starts a cell.
            if current is not None and char['left'] - current['right'] <= max(2.5, min(width, current['height']) * .8):
                current['text'] += char['text']
                current['right'] = max(current['right'], char['right'])
            else:
                current = dict(char)
                items.append(current)
    return items


HEADER = re.compile(r'项目|科目名称|科目编码|科目代码|科目编号|账户名称|期末|期初|年末|年初|借方|贷方|行次|方向|^金额$|^余额$|^资产$|^负债$|负债.*权益|^所有者权益$')


def table_rows(items):
    lines = row_groups(items)
    candidates = [(i, [item for item in line if HEADER.search(item['text'])]) for i, line in enumerate(lines)]
    candidates = [(i, cells) for i, cells in candidates if len(cells) >= 2]
    if not candidates:
        return [[item['text'] for item in line] for line in lines]
    # Subheaders define numeric columns in two-row trial balance headers.
    numeric = [(i, cells) for i, cells in candidates if sum(bool(re.search(r'借方|贷方', c['text'])) for c in cells) >= 2]
    index, anchors = max(numeric or candidates, key=lambda pair: len(pair[1]))
    anchors = list(anchors)
    if numeric:
        for prior in lines[max(0, index - 2):index]:
            anchors += [item for item in prior if re.search(r'科目名称|账户名称|科目编码|科目代码|科目编号', item['text'])]
    anchors.sort(key=lambda c: c['left'])
    centers = [(a['left'] + a['right']) / 2 for a in anchors]
    result = []
    for line_index, line in enumerate(lines):
        if line_index < min(i for i, _ in candidates):
            result.append(['  '.join(item['text'] for item in line)])
            continue
        row = [''] * len(anchors)
        for cell in line:
            center = (cell['left'] + cell['right']) / 2
            column = min(range(len(centers)), key=lambda i: abs(centers[i] - center))
            if numeric and line_index < index and re.search(r'期末|期初|发生|累计', cell['text']):
                nearby = [i for i, x in enumerate(centers) if abs(x-center) < max(12, (cell['right']-cell['left']) * 1.4)]
                if nearby:
                    column = nearby[0]
            row[column] = (row[column] + ' ' + cell['text']).strip()
        result.append(row)
    return result


def ocr_items(image, engine):
    import numpy as np
    image.thumbnail((2600, 2600))
    result, _ = engine(np.asarray(image.convert('RGB')))
    items = []
    for box, text, confidence in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        items.append({'text': str(text), 'left': min(xs), 'right': max(xs), 'y': (min(ys)+max(ys))/2,
                      'height': max(ys)-min(ys), 'confidence': float(confidence)})
    return items


def extract_file(path):
    from PIL import Image, ImageOps
    engine = None
    used_ocr = False
    warnings = []

    def scan(image):
        nonlocal engine, used_ocr
        if engine is None:
            from reconciliation_ocr import create_engine
            engine = create_engine()
        used_ocr = True
        items = ocr_items(image, engine)
        if any(i.get('confidence', 1) < .85 for i in items):
            warnings.append('部分扫描文字置信度较低，请对照原文件核对科目和金额。')
        return items

    if path.suffix.lower() == '.pdf':
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(str(path))
        rows = []
        try:
            if len(document) > 30:
                raise ValueError('PDF 超过 30 页，请拆分后上传。')
            scan_count = 0
            for number in range(len(document)):
                page = document[number]
                try:
                    items = native_items(page)
                    text = ''.join(i['text'] for i in items)
                    if not re.search(r'资产|科目|负债|期末|借方|贷方', text):
                        scan_count += 1
                        if scan_count > 10:
                            raise ValueError('扫描页超过 10 页，请拆分后上传，避免遗漏报表。')
                        bitmap = page.render(scale=2)
                        image = bitmap.to_pil()
                        try:
                            items = scan(image)
                        finally:
                            image.close()
                            bitmap.close()
                    page_rows = table_rows(items)
                    if not page_rows:
                        raise ValueError(f'第 {number+1} 页未识别到文字，请提供清晰原件。')
                    rows.extend(page_rows)
                finally:
                    page.close()
        finally:
            document.close()
    else:
        with Image.open(path) as original:
            if original.width * original.height > 40000000:
                raise ValueError('图片分辨率过高，请缩小至 4000 万像素以内。')
            image = ImageOps.exif_transpose(original)
            try:
                rows = table_rows(scan(image))
            finally:
                if image is not original:
                    image.close()
    if not rows:
        raise ValueError('文件未识别到文字，请上传清晰的报表。')
    if len(rows) > 3000:
        raise ValueError('报表超过 3000 行，请拆分上传。')
    if used_ocr:
        warnings.append('此文件包含扫描识别结果，请核对小数点、负数和借贷方向。')
    return {'ok': True, 'rows': rows, 'ocr': used_ocr, 'warnings': list(dict.fromkeys(warnings))}


if __name__ == '__main__':
    try:
        with redirect_stdout(sys.stderr):
            output = extract_file(Path(sys.argv[1]))
    except ImportError:
        output = {'ok': False, 'error': '本机缺少 PDF / OCR 识别组件，请检查运行环境或上传 Excel 原表。'}
    except ValueError as exc:
        output = {'ok': False, 'error': str(exc)}
    except Exception:
        output = {'ok': False, 'error': '无法读取该文件，请检查是否加密、损坏，或上传清晰原件。'}
    print(json.dumps(output, ensure_ascii=False))
