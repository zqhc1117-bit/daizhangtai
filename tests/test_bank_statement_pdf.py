"""Synthetic PDFs with real cells, blank amounts and wrapped transaction text."""
import base64
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bank_statement_pdf
import reconciliation
from db import ApiError

try:
    import pypdf
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen.canvas import Canvas
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False


def statement_pdf(pages=None, count=None, unsupported_page=False):
    """Reproduce the bank's segmented rules and vertically centered cells."""
    pages = pages or [[{'date': '2026-09-17\n15:09:29', 'credit': '7,500.00', 'balance': '7,500.00',
                       'counterparty': '合成物业有\n限公司', 'type': '转账收入'},
                      {'date': '2026-09-21\n02:03:30', 'credit': '0.04', 'balance': '7,500.04',
                       'counterparty': '应付利息-单位\n活期存款利息-\n自动计提',
                       'note': '收息，结息账号\n:\n12345678901234\n0', 'type': '账户结息'}]]
    if count is None: count = sum(map(len, pages))
    output = io.BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    canvas = Canvas(output, pagesize=(600, 840))
    keys = ['date', 'debit', 'credit', 'balance', 'note', 'counterparty', 'account', 'type']
    labels = ['交易日期', '借方（出账）', '贷方（入账）', '余额', '摘要', '收（付）方名称', '收（付）方账号', '交易类型']
    edges = [16, 68, 154, 240, 326, 394, 462, 531, 579]
    for rows in pages:
        canvas.setFont('STSong-Light', 10)
        canvas.drawString(40, 780, '用户所属公司: 合成企业有限公司')
        canvas.drawString(40, 764, f'记录数: {count}')
        canvas.setFont('STSong-Light', 9)
        for index, label in enumerate(labels):
            left, right = edges[index:index + 2]
            if 1 <= index <= 3: canvas.drawRightString(right - 1, 729, label)
            else: canvas.drawString(left + 1, 729, label)
            canvas.line(left, 739, right, 739)
            canvas.line(left, 725, right, 725)
        top = 725
        for row in rows:
            height = max(len(str(v).splitlines()) for v in row.values()) * 10 + 12
            for index, key in enumerate(keys):
                left, right = edges[index:index + 2]
                lines = str(row.get(key, '')).splitlines()
                y = top - height / 2 + (len(lines) - 1) * 5 - 3
                for value in lines:
                    if key in ('debit', 'credit', 'balance'): canvas.drawRightString(right - 1, y, value)
                    else: canvas.drawString(left + 1, y, value)
                    y -= 10
                canvas.line(right, top - height, left, top - height)
            top -= height
        canvas.drawString(540, 15, '页码 1/1')
        canvas.showPage()
    if unsupported_page:
        canvas.setFont('STSong-Light', 10)
        canvas.drawString(40, 740, '另一页尚未支持的交易格式')
        canvas.showPage()
    canvas.save()
    return output.getvalue()


@unittest.skipUnless(PDF_AVAILABLE, 'PDF fixture needs pypdf and reportlab')
class StatementPDFTests(unittest.TestCase):
    def parse(self, **kwargs):
        return bank_statement_pdf.parse_statement_pdf(statement_pdf(**kwargs), '合成流水.pdf')

    def test_incoming_columns_and_wrapped_cells(self):
        result = self.parse()
        self.assertEqual(result['account_name'], '合成企业有限公司')
        self.assertEqual(result['warnings'], [])
        rows = result['transactions']
        self.assertEqual([row['amount'] for row in rows], ['7500.00', '0.04'])
        self.assertEqual([row['date'] for row in rows], ['2026-09-17', '2026-09-21'])
        self.assertEqual(rows[0]['counterparty'], '合成物业有限公司')
        self.assertEqual(rows[0]['note'], '转账收入')
        self.assertEqual(rows[1]['counterparty'], '应付利息-单位活期存款利息-自动计提')
        self.assertEqual(rows[1]['note'], '收息，结息账号:123456789012340；账户结息')
        self.assertTrue(all(not row['direction_needs_review'] for row in rows))

    def test_outgoing_amount_does_not_use_the_balance_column(self):
        result = self.parse(pages=[[{'date': '2026-09-22', 'debit': '1,234,567.89',
                                    'credit': '0.00', 'balance': '9,999,999.99', 'counterparty': '合成供应商'}]])
        self.assertEqual(result['transactions'][0]['amount'], '-1234567.89')
        self.assertFalse(result['transactions'][0]['direction_needs_review'])

    def test_conflicting_or_missing_amounts_keep_row_and_require_review(self):
        for amounts in ({'debit': '1.00', 'credit': '2.00'}, {'balance': '9.00'},
                        {'credit': '-2.00'}, {'credit': '1.2O'}):
            with self.subTest(amounts=amounts):
                result = self.parse(pages=[[{'date': '2026-09-22', **amounts}]])
                self.assertEqual(len(result['transactions']), 1)
                self.assertEqual(result['transactions'][0]['amount'], '')
                self.assertTrue(result['transactions'][0]['direction_needs_review'])
                self.assertTrue(result['warnings'])

    def test_invalid_date_and_declared_count_mismatch_are_reported(self):
        result = self.parse(pages=[[{'date': '2026-02-30', 'credit': '1.00'}]], count=2)
        self.assertEqual(result['transactions'][0]['date'], '')
        self.assertEqual(len(result['warnings']), 2)

    def test_all_pages_and_sources_are_retained(self):
        result = self.parse(pages=[[{'date': '2026-09-22', 'credit': '1.00'}],
                                   [{'date': '2026-09-23', 'debit': '2.00'}]])
        self.assertEqual([row['amount'] for row in result['transactions']], ['1.00', '-2.00'])
        self.assertTrue(all(row['source'] == '合成流水.pdf' for row in result['transactions']))
        self.assertEqual(result['warnings'], [])
        self.assertIsNone(self.parse(unsupported_page=True))

    def test_real_pdf_parser_path_skips_ocr_and_combines_multiple_statements(self):
        content = base64.b64encode(statement_pdf()).decode('ascii')
        events = []
        with patch.object(reconciliation, 'ocr_file', side_effect=AssertionError('Unexpected OCR')):
            parsed = reconciliation.parse({'statements': [
                {'name': name, 'content_base64': content} for name in ('流水一.pdf', '流水二.pdf')]},
                lambda event, **detail: events.append((event, detail)))
        self.assertEqual(len(parsed['transactions']), 4)
        self.assertEqual({row['source'] for row in parsed['transactions']}, {'流水一.pdf', '流水二.pdf'})
        self.assertEqual([e[1]['name'] for e in events if e[0] == 'start'], ['流水一.pdf', '流水二.pdf'])
        self.assertEqual(parsed['warnings'], [])


class StatementUploadTests(unittest.TestCase):
    def test_legacy_single_statement_and_new_list_are_equivalent(self):
        item = {'name': 'single.pdf'}
        self.assertEqual(reconciliation.upload_groups({'statement': item}),
                         reconciliation.upload_groups({'statements': [item]}))

    def test_bad_lists_and_total_limits_rejected_before_parsing(self):
        for payload in ({'statements': {}}, {'statements': None}, {'statements': [{}] * 31},
                        {'statements': [{}] * 20, 'invoices': [{}] * 11},
                        {'statement': {'name': 'legacy.pdf'}, 'statements': [{'name': 'new.pdf'}]}):
            with self.subTest(payload=payload), self.assertRaises(ApiError):
                reconciliation.upload_groups(payload)


if __name__ == '__main__': unittest.main()
