"""Synthetic OCR failures and receipt/layout cases; no business DB is opened."""
import base64
import io
import json
import sys
import unittest
import weakref
from decimal import Decimal
from unittest.mock import Mock, patch

from PIL import Image
import reconciliation as reconcile
import reconciliation_ocr as ocr
from bank_receipt_ocr import parse_receipts, uppercase_amount
from invoice_pdf import parse_invoice_pdf, parse_layout_invoice


def items(lines, offset=0):
    return [{'text': text, 'x': 50, 'y': offset + n * 30, 'confidence': .99}
            for n, text in enumerate(lines)]


def receipt_lines(amount='CNY123.40', upper='人民币壹佰贰拾叁元肆角', heading='入账回单'):
    return [heading, '交易日期：2026年09月17日', '收款人：合成甲公司', '付款人：合成乙公司',
            '交易金额（小写）：' + amount, '交易金额（大写）：' + upper, '回单编号：SYNTHETIC-A']


def upload(name, data=b'image'):
    return {'name': name, 'content_base64': base64.b64encode(data).decode()}


class ReceiptTests(unittest.TestCase):
    def test_stacked_receipts_keep_fields_and_signed_amounts_separate(self):
        first = items(receipt_lines())
        second = items(receipt_lines('CNY0.04', '人民币肆分', '出账回单'), 300)
        result = parse_receipts(first + second)
        self.assertEqual([row['amount'] for row in result], ['123.40', '-0.04'])
        self.assertEqual([row['own_name'] for row in result], ['合成甲公司', '合成乙公司'])
        self.assertEqual([row['counterparty'] for row in result], ['合成乙公司', '合成甲公司'])

    def test_ocr_letter_zero_requires_matching_uppercase_amount(self):
        for upper, expected in [('人民币肆分', '0.04'), ('人民币伍分', ''), ('无法识别', '')]:
            with self.subTest(upper=upper):
                result = parse_receipts(items(receipt_lines('CNYO.04', upper)))[0]
                self.assertEqual(result['amount'], expected)
                self.assertTrue(result['warnings'])

    def test_ocr_yen_symbol_read_as_y_requires_matching_uppercase_amount(self):
        cases = [('人民币壹仟贰佰叁拾肆元伍角', '-1234.50'),
                 ('人民币壹仟贰佰叁拾肆元陆角', ''),
                 ('无法识别', '')]
        for upper, expected in cases:
            with self.subTest(upper=upper):
                result = parse_receipts(items(receipt_lines('Y1,234.50', upper, '出账回单')))[0]
                self.assertEqual(result['amount'], expected)
                self.assertTrue(result['warnings'])

    def test_incomplete_second_receipt_does_not_borrow_first_fields(self):
        rows = parse_receipts(items(receipt_lines()) + items(['入账回单', '回单编号：SYNTHETIC-B'], 300))
        self.assertEqual(rows[0]['amount'], '123.40')
        self.assertEqual(rows[1]['amount'], '')
        self.assertEqual(rows[1]['date'], '')
        self.assertTrue(rows[1]['warnings'])

    def test_missing_payer_for_interest_stays_empty(self):
        lines = [line for line in receipt_lines('CNY0.04', '人民币肆分') if not line.startswith('付款人')]
        result = parse_receipts(items(lines + ['业务类型：活期结息']))[0]
        self.assertEqual(result['counterparty'], '')
        self.assertEqual(result['amount'], '0.04')
        self.assertFalse(result['direction_needs_review'])

    def test_unknown_direction_and_side_by_side_blocks_are_not_guessed(self):
        self.assertEqual(parse_receipts(items(receipt_lines(heading='银行回单'))), [])
        self.assertEqual(parse_receipts(items(receipt_lines()) + items(receipt_lines())), [])

    def test_uppercase_amount_cross_check(self):
        self.assertEqual(uppercase_amount('人民币壹万零壹元零肆分'), Decimal('10001.04'))
        for invalid in ('人民币壹佰佰元整', '人民币秘密字段', '人民币负壹元整'):
            self.assertIsNone(uppercase_amount(invalid))


PLAIN = '电子发票（普通发票） 购买方信息 销售方信息 金额 税额\n*信息技术服务*咨询费'
LAYOUT = '''电子发票（普通发票）                 发票号码：12345678901234567890
                                          开票日期：2026年09月24日
购 名称：合成买方公司               销 名称：合成卖方公司
买                                 售
方                                 方
                              金额                     税额
     合    计                  ¥100.00                 ¥6.00
价税合计（大写）壹佰零陆元整              （小写）¥106.00
'''


class NativeInvoiceTests(unittest.TestCase):
    def test_split_drawing_order_uses_layout_without_losing_item_or_tax(self):
        record = parse_layout_invoice(LAYOUT, PLAIN, 'synthetic.pdf')
        self.assertEqual((record['buyer'], record['seller']), ('合成买方公司', '合成卖方公司'))
        self.assertEqual((record['amount'], record['net_amount'], record['tax_amount']), ('106.00', '100.00', '6.00'))
        self.assertEqual(record['item_name'], '*信息技术服务*咨询费')
        self.assertTrue(record['needs_review'])

    def test_ambiguous_missing_or_invalid_fields_fall_back(self):
        for layout in (LAYOUT.replace('2026年09月24日', '2026年02月30日'),
                       LAYOUT + '发票号码：99999999999999999999',
                       LAYOUT.replace('销 名称', '名称'),
                       LAYOUT.replace('（小写）¥106.00', '')):
            self.assertIsNone(parse_layout_invoice(layout, PLAIN, 'synthetic.pdf'))
        self.assertIsNone(parse_layout_invoice(LAYOUT, '', 'synthetic.pdf'))

    def test_tax_amounts_require_aligned_ordered_labels(self):
        for layout in (LAYOUT.replace('金额                     税额', '税额                     金额'),
                       LAYOUT.replace('¥100.00                 ¥6.00', '¥99.00                  ¥6.00')):
            result=parse_layout_invoice(layout, PLAIN, 'synthetic.pdf')
            self.assertEqual((result['net_amount'],result['tax_amount']),('',''))

    def test_pdf_layout_path_and_original_text_fast_path_both_skip_ocr(self):
        page = Mock()
        page.extract_text.side_effect = lambda **kwargs: LAYOUT if kwargs else PLAIN
        with patch('pypdf.PdfReader', return_value=Mock(pages=[page])):
            expected = parse_invoice_pdf(b'%PDF-fake', 'native.pdf')
            with patch.object(reconcile, 'extract_pdf', return_value=(PLAIN, '')), patch.object(reconcile, 'ocr_file', side_effect=AssertionError('Unexpected OCR')):
                result = reconcile.parse({'invoices': [upload('native.pdf', b'%PDF-fake')]})
        self.assertEqual(result['invoices'], [expected])
        self.assertFalse(result['warnings'])
        with patch.object(reconcile, 'extract_pdf', return_value=('native text', '')), patch.object(reconcile, 'parse_invoice', return_value=expected), patch('invoice_pdf.parse_invoice_pdf', side_effect=AssertionError('Already parsed')), patch.object(reconcile, 'ocr_file', side_effect=AssertionError('Unexpected OCR')):
            self.assertEqual(reconcile.parse({'invoices': [upload('native.pdf', b'%PDF-fake')]})['invoices'], [expected])


class RecoveryTests(unittest.TestCase):
    def test_cached_engine_is_freed_before_memory_retry_initialization(self):
        class Engine:
            pass
        engines=[]
        modes=[]
        def create(low_memory=False):
            if low_memory:
                self.assertIsNone(engines[0]())
            engine=Engine()
            engine.ident=len(engines)+1
            engines.append(weakref.ref(engine))
            modes.append(low_memory)
            return engine
        def run(path,kind,engine,**kwargs):
            if path=='memory' and engine.ident==1:
                raise MemoryError('PRIVATE')
            return {'ok':True}
        requests=''.join(json.dumps({'path':name,'kind':'bank'})+'\n' for name in ('good','memory','next'))
        output=io.StringIO()
        with patch.object(ocr,'create_engine',create), patch.object(ocr,'run_one',run), patch.object(sys,'stdin',io.StringIO(requests)), patch.object(sys,'stdout',output):
            ocr.worker()
        results=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(all(row['ok'] for row in results))
        self.assertEqual(modes,[False,True,False])
        self.assertTrue(results[1]['memory_recovery'])

    def test_memory_failure_retries_once_then_restores_quality_for_next_file(self):
        engine = object()
        reduced = object()
        with patch.object(ocr, 'create_engine', return_value=reduced) as create, patch.object(ocr, 'run_one', side_effect=[MemoryError('PRIVATE'), {'ok': True}]) as run:
            result, cached = ocr.run_request({'path': 'synthetic.png', 'kind': 'bank'}, engine)
        self.assertTrue(result['ok'])
        self.assertTrue(result['memory_recovery'])
        self.assertEqual(result['diagnostic']['attempts'], 2)
        self.assertIsNone(cached)
        create.assert_called_once_with(low_memory=True)
        self.assertEqual(run.call_args.kwargs['max_side'], 1280)

    def test_repeated_memory_failure_is_one_final_safe_error(self):
        with patch.object(ocr, 'create_engine', return_value=object()), patch.object(ocr, 'run_one', side_effect=RuntimeError('Failed to allocate PRIVATE')) as run:
            result, cached = ocr.run_request({'path': 'synthetic.png', 'kind': 'bank'}, object())
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result['error_code'], 'memory_allocation')
        self.assertNotIn('PRIVATE', str(result))
        self.assertIsNone(cached)

    def test_engine_initialization_memory_failure_also_recovers(self):
        with patch.object(ocr, 'create_engine', side_effect=[MemoryError(), object()]), patch.object(ocr, 'run_one', return_value={'ok': True}):
            result, cached = ocr.run_request({'path': 'synthetic.png', 'kind': 'invoice'}, None)
        self.assertTrue(result['ok'])
        self.assertIsNone(cached)

    def test_non_memory_failure_is_not_retried(self):
        with patch.object(ocr, 'run_one', side_effect=ValueError('PRIVATE')) as run:
            result, _ = ocr.run_request({'path': 'synthetic.png', 'kind': 'bank'}, object())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result['error_code'], 'recognition_failed')
        self.assertNotIn('PRIVATE', str(result))

    def test_empty_ocr_is_failed_even_when_engine_returns_normally(self):
        engine = Mock(return_value=([], None))
        result = ocr.recognize(Image.new('RGB', (100, 100)), 'bank', engine)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'no_text')

    def test_successful_text_with_unsupported_bank_layout_is_failed(self):
        engine = Mock(return_value=([([[0, 0], [20, 0], [20, 10], [0, 10]], '合成文本', .99)], None))
        result = ocr.recognize(Image.new('RGB', (100, 100)), 'bank', engine)
        self.assertEqual(result['error_code'], 'no_fields')


class ImportProgressTests(unittest.TestCase):
    def test_empty_partial_and_failed_files_are_distinct(self):
        events = []
        partial = {'ok': True, 'orientation_confident': False, 'fields': {'invoice_number': '12345678'}}
        empty = {'ok': True, 'fields': {}}
        with patch.object(reconcile.OCRWorker, 'run', side_effect=[(None, 'OCR 识别失败'), (partial, ''), (empty, '')]):
            result = reconcile.parse({'invoices': [upload('bad.png'), upload('partial.png'), upload('empty.png')]}, lambda event, **kw: events.append((event, kw)))
        ends = [kw for event, kw in events if event == 'end']
        self.assertEqual([x['status'] for x in ends], ['failed', 'partial', 'failed'])
        self.assertEqual([x['recognized_count'] for x in ends], [0, 1, 0])
        self.assertEqual(len([w for w in result['warnings'] if 'bad.png' in w]), 1)

    def test_ocr_receipts_do_not_duplicate_existing_statement_rows(self):
        rows = parse_receipts(items(receipt_lines() + ['用途：九月份报销']))
        scan = {'ok': True, 'fields': {'receipts': rows, 'transactions': []}}
        with patch.object(reconcile.OCRWorker, 'run', return_value=(scan, '')):
            result = reconcile.parse({'statements': [upload('receipts.png')]})
        self.assertEqual(len(result['receipts']), 1)
        self.assertEqual(len(result['transactions']), 1)
        self.assertEqual(result['transactions'][0]['amount'], '123.40')
        self.assertEqual(result['receipts'][0]['purpose'], '九月份报销')
        statement = {'ok': True, 'fields': {'account_name': '合成甲公司', 'transactions': [{'date': '2026-09-17', 'amount': '123.40', 'counterparty': '合成乙公司', 'direction_needs_review': False}]}}
        with patch.object(reconcile.OCRWorker, 'run', side_effect=[(statement, ''), (scan, '')]):
            result = reconcile.parse({'statements': [upload('statement.png'), upload('receipts.png')]})
        self.assertEqual(len(result['receipts']), 1)
        self.assertEqual(len(result['transactions']), 1)

    def test_native_receipt_remarks_stay_in_their_own_block(self):
        transfer='电子回单（付款）\n回单编号：R1\n日期：20261019\n小写：7037.08\n摘要：付款'
        other=transfer.replace('R1','R2').replace('7037.08','20.00')
        text=transfer+'\n附言：9月份 差旅报销\n'+other+'\n请勿重复报销'
        with patch.object(reconcile,'extract_pdf',return_value=(text,'')):
            result=reconcile.parse({'statements':[upload('receipts.pdf',b'%PDF-')]})
        first,second=result['receipts']
        self.assertEqual((first['number'],first['note'],first['purpose']),('R1','付款','9月份 差旅报销'))
        self.assertEqual((second['number'],second['note']),('R2','付款'))
        self.assertNotIn('purpose',second)


if __name__ == '__main__':
    unittest.main()
