"""Monthly master exports: preservation, formulas, company isolation and persistence."""
import base64
from copy import copy
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import payroll_master as pm
from db import ApiError
from openpyxl import load_workbook
import test_http as http_suite


COMPANY = '合成工资测试有限公司'
PERSON = {'employee_name': '测试人员甲', 'gross_fen': 700000, 'social_fen': 76635,
          'pension_fen': 58160, 'medical_fen': 14840, 'unemployment_fen': 3635,
          'housing_fund_fen': 30000, 'withheld_tax_fen': 2801, 'other_deduction_fen': 0}


def fixture(company=COMPANY):
    script = """
import {createPayrollXlsx} from './public/payroll-xlsx.js';
const p=JSON.parse(process.argv[1]);
const b={client:{id:1,name:process.argv[2]},period:'2026-10',payroll:[{...p,period:'2026-10',client_id:1}]};
process.stdout.write(Buffer.from(createPayrollXlsx(b)).toString('base64'));
"""
    raw = base64.b64decode(subprocess.check_output(['node', '--input-type=module', '-e', script, json.dumps(PERSON), company], cwd=ROOT))
    output = io.BytesIO()
    with ZipFile(io.BytesIO(raw)) as src, ZipFile(output, 'w') as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == 'xl/workbook.xml':
                data = data.replace(b'</sheets>', '<sheet name="其他公司" sheetId="2" r:id="rId3"/></sheets>'.encode())
            if info.filename == 'xl/_rels/workbook.xml.rels':
                data = data.replace(b'</Relationships>', f'<Relationship Id="rId3" Type="{pm.REL}/worksheet" Target="worksheets/sheet2.xml"/></Relationships>'.encode())
            if info.filename == '[Content_Types].xml':
                data = data.replace(b'</Types>', b'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            dst.writestr(copy(info), data)
        dst.writestr('xl/worksheets/sheet2.xml', src.read('xl/worksheets/sheet1.xml').replace(company.encode(), '另一家测试有限公司'.encode()))
        dst.writestr('customXml/preserved.xml', b'<preserved>Keep original metadata</preserved>')
    return output.getvalue()


def read(content, values=False):
    return load_workbook(io.BytesIO(content), data_only=values)


class PayrollMasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = fixture()

    def export(self, source=None, period='2026-11', people=None):
        return pm.MasterWorkbook(source or self.source).export(COMPANY, period, people or [PERSON])

    def test_append_preserves_other_sheets_metadata_history_and_styles(self):
        output, result = self.export()
        original = read(self.source); actual = read(output); values = read(output, True)
        row = result['start_row']
        self.assertEqual(10, row)
        self.assertEqual('2026年11月', actual.active.cell(row+1, 1).value)
        self.assertEqual(original.active['C6'].style_id, actual.active.cell(row+5, 3).style_id)
        self.assertEqual('=ROUND(I15-J15,2)', actual.active['K15'].value)
        self.assertEqual(5905.64, values.active['K15'].value)
        self.assertEqual(5905.64, values.active['K16'].value)
        for r in range(1, 8):
            self.assertEqual([c.value for c in original.active[r]], [c.value for c in actual.active[r]])
        with ZipFile(io.BytesIO(self.source)) as before, ZipFile(io.BytesIO(output)) as after:
            for name in before.namelist():
                if name not in ('xl/workbook.xml', 'xl/worksheets/sheet1.xml'):
                    self.assertEqual(before.read(name), after.read(name), name)

    def test_repeat_growth_shrink_other_deduction_and_legacy_social(self):
        output, first = self.export()
        output, second = self.export(output, people=[PERSON, {**PERSON, 'employee_name': '新增人员', 'other_deduction_fen': 1234}])
        self.assertTrue(second['updated']); self.assertEqual(first['start_row'], second['start_row'])
        block = pm.MasterWorkbook(output).sheets[0]['blocks'][-1]
        self.assertEqual(2, len(pm.MasterWorkbook(output).sheets[0]['blocks']))
        sheet = read(output, True).active
        self.assertEqual(12.34, sheet.cell(block['start']+6, 12).value)
        self.assertEqual(11798.94, sheet.cell(block['total'], 11).value)
        output, third = self.export(output, people=[{**PERSON, 'pension_fen': 0, 'medical_fen': 0, 'unemployment_fen': 0}])
        sheet = read(output, True).active
        self.assertIsNone(sheet.cell(third['start_row']+5, 4).value)
        self.assertEqual(766.35, sheet.cell(third['start_row']+5, 7).value)
        self.assertEqual(pm.LEGACY_NOTE, sheet.cell(third['end_row'], 1).value)
        self.assertIsNone(sheet.cell(third['start_row']+6, 12).value)

    def test_backfill_then_grow_earlier_month_moves_later_formulas(self):
        output, _ = self.export(period='2027-01')
        output, result = self.export(output, period='2026-12')
        self.assertEqual('2026-12', result['period'])
        output, _ = self.export(output, period='2026-10', people=[PERSON, {**PERSON, 'employee_name': '新增人员'}])
        book = pm.MasterWorkbook(output)
        self.assertEqual(['2026-10', '2026-12', '2027-01'], [b['period'] for b in book.sheets[0]['blocks']])
        sheet = read(output).active; values = read(output, True).active
        for b in book.sheets[0]['blocks']:
            r = b['start']+5
            self.assertEqual(f'=ROUND(I{r}-J{r},2)', sheet.cell(r, 11).value)
            self.assertEqual(5905.64, values.cell(r, 11).value)
        for a, b in zip(book.sheets[0]['blocks'], book.sheets[0]['blocks'][1:]):
            self.assertGreater(b['start']-a['end']-1, 0)

    def test_matching_duplicates_and_invalid_files(self):
        book = pm.MasterWorkbook(self.source)
        self.assertEqual('工资表', book.preview(COMPANY, '2026-11')['sheet_name'])
        self.assertEqual('', book.preview('完全未匹配公司', '2026-11')['sheet_name'])
        with self.assertRaises(ApiError): book.export(COMPANY, '2026-11', [PERSON], '其他公司')
        with self.assertRaises(ApiError): pm.MasterWorkbook(b'not an xlsx')
        self.assertEqual('2026-10', pm.month('2026--10-1'))
        self.assertEqual('2026-10', pm.month('2026年10月'))
        self.assertEqual('2026-10', pm.month('46296'))

    def test_formula_references_respect_other_sheets_and_string_literals(self):
        formula = 'SUM(A10:A20)+\'工资表\'!$K$15+\'其他公司\'!A30+IF(A1="A20",1,0)'
        self.assertEqual('SUM(A10:A23)+\'工资表\'!$K$18+\'其他公司\'!A30+IF(A1="A20",1,0)', pm.shift_refs(formula, 15, 3, '工资表'))


class PayrollMasterHTTPTests(unittest.TestCase):
    setUp = http_suite.HTTPTests.setUp
    tearDown = http_suite.HTTPTests.tearDown
    setup = http_suite.HTTPTests.setup
    create_client = http_suite.HTTPTests.create_client

    def test_upload_export_download_persist_conflict_and_backup(self):
        self.setup(); client = self.create_client('MASTER', 'small2013'); cid = client['id']
        prefix = f'/api/books/{cid}/payroll'
        status, result, _ = self.client.call(prefix, {**PERSON, 'period': '2026-11', 'expense_account': '5602'})
        self.assertEqual(200, status, result)
        source = fixture(client['name'])
        body = {'period': '2026-11', 'content_base64': base64.b64encode(source).decode()}
        status, preview, _ = self.client.call(prefix+'/master/preview', body)
        self.assertEqual(200, status, preview); self.assertIsNone(preview['master_version'])
        body.update(master_version=preview['master_version'], sheet_name=preview['sheet_name'])
        self.assertEqual(403, self.client.call(prefix+'/master/export', body, csrf=False)[0])
        status, result, _ = self.client.call(prefix+'/master/export', body)
        self.assertEqual(200, status, result); self.assertFalse(result['updated'])
        status, content, _ = self.client.call(result['download_url'])
        self.assertEqual(200, status); self.assertEqual(5905.64, read(content, True).active['K15'].value)
        self.assertEqual(409, self.client.call(prefix+'/master/export', body)[0])
        status, preview, _ = self.client.call(prefix+'/master/preview?period=2026-11')
        self.assertEqual(200, status); self.assertEqual(result['file_id'], preview['master_version'])
        status, repeated, _ = self.client.call(prefix+'/master/export', {'period': '2026-11', 'sheet_name': preview['sheet_name'], 'master_version': preview['master_version']})
        self.assertEqual(200, status, repeated); self.assertTrue(repeated['updated'])
        self.assertNotEqual(result['file_id'], repeated['file_id'])
        self.assertEqual(content, self.client.call(result['download_url'])[1])
        self.assertEqual(400, self.client.call(prefix+'/master/export', {'period': '2026-12', 'master_version': repeated['file_id']})[0])
        status, archive, _ = self.client.call('/api/backup')
        self.assertEqual(200, status)
        with ZipFile(io.BytesIO(archive)) as backup:
            manifest = json.loads(backup.read('manifest.json'))
            self.assertEqual({result['file_id'], repeated['file_id']}, {f['id'] for f in manifest['files']})
        latest = repeated['file_id']
        body = {'period': '2026-11', 'sheet_name': '工资表', 'master_version': latest}
        other = http_suite.Client(self.base)
        status, _, _ = other.call('/api/login', {'username': 'test2', 'password': 'TestOnly-2-Strong!'})
        self.assertEqual(200, status)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda c: c.call(prefix+'/master/export', body), [self.client, other]))
        self.assertEqual([200,409], sorted(r[0] for r in responses))


if __name__ == '__main__':
    unittest.main()
