"""Invoice originals keep their order, visible marks and page boundaries."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import invoice_print
from db import ApiError


def archive_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return output.getvalue()


def phone_screenshot(invoice):
    from PIL import Image, ImageDraw
    width = invoice.width + 50
    height = width * 2
    top = round(width * .24)
    screenshot = Image.new('RGB', (width, height), '#fdfdfd')
    draw = ImageDraw.Draw(screenshot)
    draw.text((width // 10, width // 25), '14:53', fill='black')
    draw.rectangle((width - 85, width // 25, width - 48, width // 25 + 8), fill='black')
    draw.line((28, top - 72, 17, top - 60, 28, top - 48), fill='#3d3d3d', width=2)
    draw.rectangle((6, top - 12, width - 7, top + invoice.height + 65), fill='#eeedf2')
    draw.line((8, top - 12, width - 9, top - 12), fill='#dbdbdf', width=max(1, width // 160))
    screenshot.paste(invoice, (25, top))
    draw.rounded_rectangle((width // 3, height - 24, width * 2 // 3, height - 18), 3, fill='black')
    return screenshot


class PrintTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.original = Image.new('RGB', (640, 900), 'white')
        self.original.paste('#182e45', (40, 30, 600, 330))
        self.image = io.BytesIO()
        self.original.save(self.image, 'PNG')

    def test_mixed_zip_nested_folders_and_natural_page_order(self):
        pdf = io.BytesIO()
        self.original.save(pdf, 'PDF', save_all=True, append_images=[self.original])
        nested = archive_bytes([('发票2.pdf', pdf.getvalue()), ('发票10.PNG', self.image.getvalue())])
        data = archive_bytes([('./九月/原件.zip', nested), ('readme.xml', b'info'),
                              ('坏票.pdf', b'not a pdf'), ('__MACOSX/._test.png', b'metadata')])
        result = invoice_print.prepare_upload('报销.zip', data)
        self.assertEqual([(p['name'].split('/')[-1].strip(), p['number']) for p in result['pages']],
                         [('发票2.pdf', 1), ('发票2.pdf', 2), ('发票10.PNG', 1)])
        self.assertTrue(all(1.75 < p['width'] / p['height'] < 1.9 for p in result['pages']))
        self.assertEqual(len(result['skipped']), 2)

    def test_transparency_orientation_and_blank_pages(self):
        from PIL import Image
        rotated = self.original.transpose(Image.Transpose.ROTATE_90)
        exif = Image.Exif()
        exif[274] = 6
        photo = io.BytesIO()
        rotated.save(photo, 'JPEG', exif=exif)
        transparent = Image.new('RGBA', self.original.size, (0, 0, 0, 0))
        transparent.paste('#182e45', (40, 30, 600, 330))
        png = io.BytesIO()
        transparent.save(png, 'PNG')
        tiff = io.BytesIO()
        self.original.save(tiff, 'TIFF', save_all=True,
                           append_images=[Image.new('RGB', self.original.size, 'white')])
        data = archive_bytes([('1.jpg', photo.getvalue()), ('2.png', png.getvalue()), ('3.tif', tiff.getvalue())])
        result = invoice_print.prepare_upload('扫描.zip', data)
        self.assertEqual(len(result['pages']), 3)
        self.assertTrue(all(p['width'] > p['height'] for p in result['pages']))
        self.assertEqual(result['skipped'], [{'name': '扫描.zip / 3.tif · 第 2 页', 'reason': '空白页'}])

    def test_archive_limits_do_not_return_partial_batches(self):
        data = archive_bytes([('1.png', self.image.getvalue()), ('2.png', self.image.getvalue())])
        with patch.object(invoice_print, 'MAX_PAGES', 1):
            with self.assertRaises(invoice_print.BatchLimit):
                invoice_print.PrintBatch().read('两张.zip', data)
        with patch.object(invoice_print, 'MAX_EXPANDED', 100):
            with self.assertRaises(invoice_print.BatchLimit):
                invoice_print.PrintBatch().read('两张.zip', data)

    def test_archive_preview_metadata_and_session_scoped_images(self):
        from PIL import Image, ImageChops
        import base64
        buffer = io.BytesIO()
        self.original.resize((1280, 1800)).save(buffer, 'PNG')
        upload = archive_bytes([('发票.png', buffer.getvalue())])
        store = invoice_print.ArchivePreviews()
        owner = (1, 1, 'session')
        result = store.add('48张示例.zip', upload, owner)
        key = result['batch']
        self.assertEqual(len(result['pages']), 1)
        self.assertNotIn('jpeg', result['pages'][0])
        preview = store.page(key, 0, 'preview', owner)
        full = store.page(key, 0, 'print', owner)
        with Image.open(io.BytesIO(preview)) as image:
            self.assertEqual(max(image.size), 720)
        legacy = invoice_print.prepare_upload('48张示例.zip', upload)['pages'][0]['jpeg']
        with Image.open(io.BytesIO(full)) as image, Image.open(io.BytesIO(base64.b64decode(legacy))) as old:
            self.assertEqual(image.size, old.size)
            self.assertIsNone(ImageChops.difference(image, old).getbbox())
        with self.assertRaises(ApiError):
            store.page(key, 0, 'preview', (1, 1, 'another session'))
        with self.assertRaises(ApiError):
            store.page(key, 0, 'preview', (2, 1, 'session'))

    def test_phone_screenshot_keeps_invoice_header_table_and_footer(self):
        import base64
        from PIL import Image, ImageDraw
        invoice = Image.new('RGB', (710, 490), 'white')
        draw = ImageDraw.Draw(invoice)
        draw.rectangle((16, 12, 65, 61), fill='#115533')
        draw.rectangle((12, 75, 691, 439), outline='#c89b9b', width=2)
        for y in (124, 371, 420):
            draw.line((12, y, 691, y), fill='#c89b9b', width=2)
        draw.rectangle((62, 455, 115, 463), fill='#223373')
        screenshot = phone_screenshot(invoice)
        self.assertGreater(invoice_print.screenshot_invoice_box(screenshot)[1], screenshot.width * .23)
        photo = io.BytesIO()
        screenshot.save(photo, 'JPEG', quality=86)
        result = invoice_print.prepare_upload('手机截图.jpg', photo.getvalue())
        self.assertEqual(len(result['pages']), 1)
        page = result['pages'][0]
        self.assertGreater(page['width'] / page['height'], 1.4)
        with Image.open(io.BytesIO(base64.b64decode(page['jpeg']))) as cropped:
            self.assertLess(cropped.height, 490)
            # Distinct colors near each end stand in for the QR code and issuer.
            colors = [color for _, color in cropped.getcolors(cropped.width * cropped.height)]
            self.assertTrue(any(g > r * 2 and g > b for r, g, b in colors
                                if r + g + b < 200))
            self.assertTrue(any(b > r * 2 and b > g * 1.5 for r, g, b in colors
                                if r + g + b < 220))
        # Extra content away from the screen controls must keep its place.
        ImageDraw.Draw(screenshot).rectangle((40, 840, 135, 865), fill='black')
        self.assertIsNone(invoice_print.screenshot_invoice_box(screenshot))


if __name__ == '__main__':
    unittest.main()
