"""Prepare uploaded invoice originals for two-up printing."""
import base64
from collections import OrderedDict
from contextlib import closing
import io
import json
import lzma
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import zlib

from db import ApiError


MB = 1024 * 1024
MAX_UPLOAD = 32 * MB
MAX_GROUP_BODY = 16 * MB + 32 * 1024
MAX_GROUP_FILES = 8
MAX_EXPANDED = 96 * MB
MAX_PAGES = 120
MAX_ENTRIES = 1500
MAX_OUTPUT = 36 * MB
MAX_PREVIEW_PACK = 96 * MB
# 182 mm printable width at just over 300 dpi.
PRINT_PIXELS = 2200
IMAGE_FORMATS = {
    '.jpg': 'JPEG', '.jpeg': 'JPEG', '.jfif': 'JPEG', '.png': 'PNG',
    '.webp': 'WEBP', '.bmp': 'BMP', '.gif': 'GIF', '.tif': 'TIFF', '.tiff': 'TIFF',
}
ARCHIVES = {'.zip', '.7z', '.rar'}
SUPPORTED = ARCHIVES | IMAGE_FORMATS.keys() | {'.pdf'}
_workers = threading.BoundedSemaphore(2)
_hidden_window = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class BatchLimit(ValueError):
    """A resource limit invalidates the whole uploaded file, including its prefix."""


def validate_upload(name, content):
    if not isinstance(name, str) or not name or len(name) > 255 or any(c in name for c in ('/', '\\', '\0')):
        raise ApiError(400, '文件名无效')
    if Path(name).suffix.lower() not in SUPPORTED:
        raise ApiError(400, '请选择 PDF、图片或 ZIP、RAR、7Z 压缩包')
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_UPLOAD:
        raise ApiError(413, '文件为空或超过 32 MB')


def prepare_upload(name, content):
    validate_upload(name, content)
    return run_renderer([name], content)


def prepare_upload_group(boundary, content):
    if not re.fullmatch(r"[A-Za-z0-9'()+_,./:=? -]{1,70}", boundary or ''):
        raise ApiError(400, '批量上传格式无效')
    if not 0 < len(content) <= MAX_GROUP_BODY:
        raise ApiError(413, '本批上传文件过大或为空')
    return run_renderer(['--multipart', boundary], content)


def run_renderer(arguments, content):
    result = json.loads(renderer_output(arguments, content))
    if 'error' in result:
        raise ApiError(400, result['error'])
    return result


def renderer_output(arguments, content):
    """Reuse one isolated renderer for the files in a request."""
    if not _workers.acquire(blocking=False):
        raise ApiError(429, '工作台正在排版其他文件，请稍后重新添加此文件')
    try:
        interpreter = Path(__file__).parent / 'runtime' / 'python' / 'python.exe'
        try:
            run = subprocess.run(
                [str(interpreter if interpreter.is_file() else sys.executable), '-X', 'utf8', '-B', __file__, *arguments],
                input=content, capture_output=True, timeout=120, creationflags=_hidden_window,
            )
        except subprocess.TimeoutExpired:
            raise ApiError(408, '排版超时，请拆分文件后重试')
        if run.returncode:
            raise ApiError(400, '文件读取失败，请检查原件或拆分后重新添加')
        return run.stdout
    finally:
        _workers.release()


class ArchivePreviews:
    """Short-lived, session-scoped raster pages held in memory."""
    ttl = 2 * 60 * 60
    max_bytes = 256 * MB
    max_batches = 128

    def __init__(self):
        self._batches = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def _remove(self, key):
        self._bytes -= len(self._batches.pop(key)['packet'])

    def _expire(self, now):
        # Access renews the lease and moves the batch to the end of the LRU.
        while self._batches:
            key, batch = next(iter(self._batches.items()))
            if now - batch['accessed'] < self.ttl:
                break
            self._remove(key)

    def add(self, name, content, owner):
        validate_upload(name, content)
        if Path(name).suffix.lower() not in ARCHIVES:
            raise ApiError(400, '请选择 ZIP、RAR 或 7Z 压缩包')
        packet = renderer_output(['--archive-preview', name], content)
        with zipfile.ZipFile(io.BytesIO(packet)) as archive:
            result = json.loads(archive.read('result.json'))
        if 'error' in result:
            raise ApiError(400, result['error'])
        if not result['pages']:
            return result
        if len(packet) > self.max_bytes:
            raise ApiError(400, '本批票面过大，请分包添加')
        key = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._expire(now)
            while self._batches and (self._bytes + len(packet) > self.max_bytes
                                     or len(self._batches) >= self.max_batches):
                self._remove(next(iter(self._batches)))
            self._batches[key] = {'owner': owner, 'packet': packet, 'accessed': now}
            self._bytes += len(packet)
        return {**result, 'batch': key}

    def page(self, key, page, size, owner):
        if size not in ('preview', 'print') or not 0 <= page < MAX_PAGES:
            raise ApiError(404, '票面不存在')
        with self._lock:
            now = time.monotonic()
            self._expire(now)
            batch = self._batches.get(key)
            if batch is None or batch['owner'] != owner:
                raise ApiError(404, '打印批次不存在或已过期，请重新上传')
            batch['accessed'] = now
            self._batches.move_to_end(key)
            packet = batch['packet']
        with zipfile.ZipFile(io.BytesIO(packet)) as archive:
            try:
                return archive.read(f'{page}-{size}.jpg')
            except KeyError:
                raise ApiError(404, '票面不存在') from None


def as_zip(data, extension):
    """Windows bsdtar can read RAR / 7z. Repack to a bounded ZIP stream, never extract paths."""
    if extension == '.zip':
        return data
    magic = b"7z\xbc\xaf\x27\x1c" if extension == '.7z' else b'Rar!\x1a\x07'
    if not data.startswith(magic):
        raise ValueError('压缩包内容与扩展名不符')
    tar = (Path(os.environ['SystemRoot']) / 'System32' / 'tar.exe'
           if os.name == 'nt' else shutil.which('bsdtar'))
    if not tar or not Path(tar).is_file():
        raise ValueError('此主机缺少 RAR / 7Z 解压组件，请使用 ZIP 或直接添加票面文件')
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        with subprocess.Popen(
            [str(tar), '-cf', '-', '--format=zip', '--options', 'zip:compression=store', '@-'],
            stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_hidden_window,
        ) as process:
            timer = threading.Timer(45, process.kill)
            timer.daemon = True
            timer.start()
            try:
                result = process.stdout.read(MAX_EXPANDED + 1)
                if len(result) > MAX_EXPANDED:
                    raise BatchLimit('压缩包展开后超过 96 MB，请分包添加')
                if process.wait():
                    raise ValueError('压缩包损坏、已加密或使用了不支持的压缩方式')
                return result
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()


def member_name(info):
    name = info.filename
    if not info.flag_bits & 0x800 and not name.isascii():
        # Legacy Chinese ZIP tools store GBK bytes without a UTF-8 flag.
        try:
            name = name.encode('cp437').decode('gbk')
        except UnicodeError:
            pass
    return name.replace('\\', '/')


def filename_order(name):
    return [int(part) if i % 2 else part
            for i, part in enumerate(re.split(r'([0-9]{1,18})', name.casefold()))]


def screenshot_invoice_box(image):
    """Find a single landscape invoice in a tall phone capture with screen controls."""
    if image.width < 120 or image.height < 1.6 * image.width:
        return None

    from PIL import Image, ImageChops
    probe = image
    if image.width > 900 or image.height > 2200:
        probe = image.copy()
        probe.thumbnail((900, 2200), Image.Resampling.LANCZOS)
    width, height = probe.size
    red, green, blue = probe.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    # Detect text and red rules while ignoring light gray viewer backgrounds.
    lightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    colored = ImageChops.subtract(lightest, darkest).point(lambda value: 255 if value >= 12 else 0)
    colored = ImageChops.darker(colored, darkest.point(lambda value: 255 if value < 240 else 0))
    ink = ImageChops.lighter(colored, darkest.point(lambda value: 255 if value < 210 else 0))
    pixels = ink.tobytes()
    row_counts = [pixels.count(b'\xff', start, start + width)
                  for start in range(0, len(pixels), width)]
    bands = []
    gap = round(width * .04)
    min_single_row = max(5, round(width * .012))
    for row, count in enumerate(row_counts):
        if count < 2:
            continue
        adjacent_ink = (row > 0 and row_counts[row - 1] >= 2
                        or row + 1 < height and row_counts[row + 1] >= 2)
        if count < min_single_row and not adjacent_ink:
            continue
        if bands and row - bands[-1][1] <= gap:
            bands[-1] = (bands[-1][0], row + 1)
        else:
            bands.append((row, row + 1))

    # A phone UI may have a clock, toolbar and home indicator at its ends.
    # A second large band or marks farther into the page make cropping ambiguous.
    body = [(top, bottom) for top, bottom in bands
            if bottom - top > width * .10 or (bottom > height * .17 and top < height * .84)]
    if len(body) != 1:
        return None
    top, bottom = body[0]
    left, _, right, _ = ink.crop((0, top, width, bottom)).getbbox()
    span = right - left
    if span < width * .55 or not .25 * span <= bottom - top <= .86 * span:
        return None
    wide_rows = [row for row in range(top, bottom) if row_counts[row] >= span * .66]
    line_gap = max(2, round(span * .01))
    if sum(b - a > line_gap for a, b in zip(wide_rows, wide_rows[1:])) < 2:
        return None
    if wide_rows[-1] - wide_rows[0] < span * .18:
        return None

    # Keep a little space around the ink, stopping at a gray viewer edge.
    pad = max(2, round(span * .022))
    margin_pixels = darkest.point(lambda value: 255 if value < 243 else 0).tobytes()

    def paper_margin(row):
        start = row * width
        return margin_pixels.count(b'\xff', start + left, start + right) < span * .8

    top_limit, bottom_limit = max(0, top - pad), min(height, bottom + pad)
    while top > top_limit and paper_margin(top - 1):
        top -= 1
    while bottom < bottom_limit and paper_margin(bottom):
        bottom += 1
    return (
        math.floor(max(0, left - pad) * image.width / width),
        math.floor(top * image.height / height),
        math.ceil(min(width, right + pad) * image.width / width),
        math.ceil(bottom * image.height / height),
    )


class PrintBatch:
    def __init__(self, assets=None):
        self.pages = []
        self.skipped = []
        self.entries = self.expanded = self.page_count = self.output_bytes = 0
        self.assets = assets

    def skip(self, name, reason):
        self.skipped.append({'name': name, 'reason': reason})

    def read(self, name, data, depth=0):
        ext = Path(name).suffix.lower()
        try:
            if ext in ARCHIVES:
                if depth > 2:
                    raise ValueError('压缩包嵌套过深，请单独添加此包')
                self.read_archive(name, as_zip(data, ext), depth)
            elif ext == '.pdf':
                self.read_pdf(name, data)
            elif ext in IMAGE_FORMATS:
                self.read_image(name, data, IMAGE_FORMATS[ext])
            else:
                self.skip(name, '不支持的文件格式')
        except BatchLimit:
            raise
        except (EOFError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
            self.skip(name, str(error) if type(error) is ValueError else '文件损坏或无法读取')

    def read_archive(self, name, data, depth):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            self.entries += len(entries)
            if self.entries > MAX_ENTRIES:
                raise BatchLimit('压缩包条目过多，请分包添加')
            for info in sorted(entries, key=lambda e: filename_order(member_name(e))):
                parts = [p for p in member_name(info).split('/') if p != '.']
                path = '/'.join(parts)
                if info.is_dir() or any((p.startswith('.') and p != '..') or p == '__MACOSX' for p in parts):
                    continue
                label = name + ' / ' + path
                if (any(p in ('', '..') for p in parts) or ':' in path
                        or stat.S_ISLNK(info.external_attr >> 16)):
                    self.skip(label, '跳过无效路径或链接')
                    continue
                if Path(path).suffix.lower() not in SUPPORTED:
                    self.skip(label, '不支持的文件格式')
                    continue
                if info.flag_bits & 1:
                    self.skip(label, '请先解除文件加密')
                    continue
                if info.file_size > MAX_UPLOAD:
                    self.skip(label, '单个文件超过 32 MB')
                    continue
                self.expanded += info.file_size
                if self.expanded > MAX_EXPANDED:
                    raise BatchLimit('压缩包展开后超过 96 MB，请分包添加')
                try:
                    content = archive.read(info)
                except (EOFError, OSError, RuntimeError, zipfile.BadZipFile, zlib.error, lzma.LZMAError):
                    self.skip(label, '文件损坏或压缩方式不支持')
                    continue
                self.read(label, content, depth + 1)

    def reserve_pages(self, count):
        self.page_count += count
        if self.page_count > MAX_PAGES:
            raise BatchLimit(f'单个文件或压缩包超过 {MAX_PAGES} 个票面，请拆分后添加')

    def read_pdf(self, name, data):
        import pypdfium2 as pdfium
        try:
            doc = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as error:
            if error.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
                raise ValueError('PDF 已加密，请先解除密码')
            raise ValueError('PDF 损坏或无法读取')
        with doc:
            count = len(doc)
            self.reserve_pages(count)
            doc.init_forms()
            for index in range(count):
                try:
                    with closing(doc[index]) as page:
                        width, height = page.get_size()
                        if not all(math.isfinite(x) and x > 0 for x in (width, height)):
                            raise ValueError('PDF 页尺寸无效')
                        scale = min(PRINT_PIXELS / width, 3400 / height)
                        # RGBX lets Pillow share the render buffer until the ticket is cropped.
                        with closing(page.render(scale=scale, draw_annots=True, prefer_bgrx=True,
                                                 rev_byteorder=True)) as bitmap:
                            self.add_page(name, index + 1, count, bitmap.to_pil())
                except pdfium.PdfiumError:
                    self.skip(f'{name} · 第 {index + 1} 页', 'PDF 页无法渲染')

    def read_image(self, name, data, expected_format):
        from PIL import Image, ImageOps, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(data), formats=[expected_format]) as source:
                count = source.n_frames if expected_format == 'TIFF' else 1
                self.reserve_pages(count)
                for index in range(count):
                    source.seek(index)
                    if source.width * source.height > 60_000_000:
                        raise ValueError('图片像素过大，请缩小至 6000 万像素以内')
                    if expected_format == 'TIFF':
                        # The reader still needs its original dimensions to seek the next frame.
                        frame = ImageOps.exif_transpose(source)
                    else:
                        ImageOps.exif_transpose(source, in_place=True)
                        frame = source
                    frame.thumbnail((3400, 3400), Image.Resampling.LANCZOS)
                    if frame.has_transparency_data:
                        rgba = frame.convert('RGBA')
                        frame = Image.new('RGB', rgba.size, 'white')
                        frame.paste(rgba, mask=rgba.getchannel('A'))
                    if frame.mode != 'RGB':
                        frame = frame.convert('RGB')
                    if box := screenshot_invoice_box(frame):
                        frame = frame.crop(box)
                    self.add_page(name, index + 1, count, frame)
        except (UnidentifiedImageError, SyntaxError):
            raise ValueError('图片损坏或内容与扩展名不符')
        except Image.DecompressionBombError:
            raise ValueError('图片像素过大，请缩小后添加')

    def add_page(self, name, number, count, image):
        from PIL import Image
        # Only trim the near-white outer border. Keep a buffer around the
        # outermost mark, including stamps, QR codes and the issuer's signature.
        ink = [255] * 250 + [0] * 6
        mask = ink * 3
        if image.mode == 'RGBX':
            mask += [0] * 256  # The padding channel carries no color.
        box = image.point(mask).getbbox()
        if box:
            left, top, right, bottom = box
            pad = max(2, round(max(image.size) * .008))
            image = image.crop((max(0, left - pad), max(0, top - pad),
                                min(image.width, right + pad), min(image.height, bottom + pad)))
        elif all(low == 255 for low, _ in image.getextrema()[:3]):
            self.skip(f'{name} · 第 {number} 页', '空白页')
            return
        image.thumbnail((PRINT_PIXELS, PRINT_PIXELS), Image.Resampling.LANCZOS)
        page = {'name': name, 'number': number, 'count': count,
                'width': image.width, 'height': image.height}
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=95, subsampling=0, optimize=self.assets is not None)
        if self.assets is not None:
            # Keep the full-resolution JPEG for printing. Only transmit a smaller
            # version of the current sheet while the operator reviews the archive.
            thumbnail = io.BytesIO()
            image.thumbnail((720, 720), Image.Resampling.LANCZOS)
            image.save(thumbnail, 'JPEG', quality=84, subsampling=0, optimize=True)
            self.output_bytes += output.tell() + thumbnail.tell()
            if self.output_bytes > MAX_PREVIEW_PACK:
                raise BatchLimit('票面图片总量过大，请拆分文件后添加')
            index = len(self.pages)
            self.assets.writestr(f'{index}-print.jpg', output.getvalue())
            self.assets.writestr(f'{index}-preview.jpg', thumbnail.getvalue())
        else:
            self.output_bytes += output.tell()
            if self.output_bytes > MAX_OUTPUT:
                raise BatchLimit('票面图片总量过大，请拆分文件后添加')
            page['jpeg'] = base64.b64encode(output.getbuffer()).decode('ascii')
        self.pages.append(page)


def render_upload(name, content, assets=None):
    batch = PrintBatch(assets)
    batch.read(name, content)
    if not (batch.pages or batch.skipped):
        raise ValueError('压缩包中没有可打印的 PDF 或图片')
    return {'pages': batch.pages, 'skipped': batch.skipped}, batch.output_bytes


def render_group(boundary, content):
    from email import policy
    from email.parser import BytesParser

    headers = f'Content-Type: multipart/form-data; boundary="{boundary}"\r\n\r\n'.encode('ascii')
    message = BytesParser(policy=policy.default).parsebytes(headers + content)
    if not message.is_multipart() or message.defects:
        raise ValueError('批量上传格式无效')
    parts = list(message.iter_parts())
    if not 1 <= len(parts) <= MAX_GROUP_FILES:
        raise ValueError('批量上传文件数量无效')

    results = []
    total_output = 0
    for part in parts:
        if (part.get_content_disposition() != 'form-data' or part.is_multipart() or
                part.get_param('name', header='Content-Disposition') != 'file'):
            raise ValueError('批量上传格式无效')
        name, data = part.get_filename(), part.get_payload(decode=True)
        try:
            validate_upload(name, data)
            result, output_bytes = render_upload(name, data)
        except (ApiError, ValueError) as error:
            results.append({'error': error.message if isinstance(error, ApiError) else str(error)})
            continue
        total_output += output_bytes
        if total_output > MAX_OUTPUT:
            raise BatchLimit('本批票面图片总量过大')
        results.append(result)
    return {'files': results}


def main():
    if sys.argv[1] == '--archive-preview':
        packet = io.BytesIO()
        with zipfile.ZipFile(packet, 'w') as assets:
            try:
                result, _ = render_upload(sys.argv[2], sys.stdin.buffer.read(MAX_UPLOAD + 1), assets)
            except ValueError as error:
                result = {'error': str(error)}
            assets.writestr('result.json', json.dumps(result, ensure_ascii=True))
        sys.stdout.buffer.write(packet.getbuffer())
        return

    try:
        if sys.argv[1] == '--multipart':
            result = render_group(sys.argv[2], sys.stdin.buffer.read(MAX_GROUP_BODY + 1))
        else:
            result, _ = render_upload(sys.argv[1], sys.stdin.buffer.read(MAX_UPLOAD + 1))
    except ValueError as error:
        result = {'error': str(error)}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
