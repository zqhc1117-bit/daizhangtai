"""Validate the real browser-generated statutory XLSX packages and reader support."""
import base64
import io
import json
from pathlib import Path
import subprocess
import unittest
from xml.etree import ElementTree
from zipfile import ZipFile

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None


class StatutoryXlsxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['node', '--input-type=module', '-e', """
import {createStatutoryBalanceXlsx, createStatutoryProfitXlsx} from './public/statement-xlsx.js';
const statement = {
  company: '合成测试公司', period: '2026-09', date: '2026-09-30',
  balance_sheet: {rows: [
    {asset_name: '货币资金', asset_line: '1', asset_ending_fen: 24648020,
     asset_begin_fen: 0, equity_name: '短期借款', equity_line: '31',
     equity_ending_fen: null, equity_begin_fen: null},
  ]},
  profit: {rows: [
    {name: '营业收入', line: '1', month_fen: null, ytd_fen: 15520000},
    {name: '净利润', line: '32', month_fen: 0, ytd_fen: 14648020},
    {name: '财务费用', line: '12', month_fen: -420, ytd_fen: -420},
  ]},
};
process.stdout.write(JSON.stringify({
  balance: Buffer.from(createStatutoryBalanceXlsx(statement)).toString('base64'),
  profit: Buffer.from(createStatutoryProfitXlsx(statement)).toString('base64'),
}));
"""],
            cwd=root, capture_output=True, text=True, encoding='utf-8', check=True,
        )
        cls.workbooks = {
            name: base64.b64decode(data)
            for name, data in json.loads(result.stdout).items()
        }

    def test_exported_package_contains_well_formed_xml_and_default_fills(self):
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        for name, data in self.workbooks.items():
            with self.subTest(workbook=name), ZipFile(io.BytesIO(data)) as archive:
                self.assertIsNone(archive.testzip())
                for member in archive.namelist():
                    if member.endswith(('.xml', '.rels')):
                        with self.subTest(part=member):
                            ElementTree.fromstring(archive.read(member))
                styles = ElementTree.fromstring(archive.read('xl/styles.xml'))
                fills = styles.find('s:fills', ns)
                self.assertEqual(int(fills.attrib['count']), len(fills))
                self.assertEqual(
                    [fill.find('s:patternFill', ns).attrib['patternType'] for fill in fills],
                    ['none', 'gray125'],
                )

    @unittest.skipIf(load_workbook is None, 'openpyxl is needed for reader compatibility')
    def test_openpyxl_reads_amounts_blanks_styles_and_merges(self):
        for name, data in self.workbooks.items():
            with self.subTest(workbook=name):
                book = load_workbook(io.BytesIO(data), data_only=True)
                try:
                    sheet = book.active
                    self.assertEqual(sheet['A2'].value, '编制单位：合成测试公司')
                    self.assertEqual(sheet['C5'].number_format, '#,##0.00')
                    self.assertEqual(sheet['C5'].alignment.horizontal, 'right')
                    self.assertEqual(sheet['C5'].border.left.style, 'thin')
                    if name == 'profit':
                        self.assertIn('A1:D1', sheet.merged_cells)
                        self.assertIsNone(sheet['C5'].value)
                        self.assertEqual(sheet['D5'].value, 155200)
                        self.assertEqual(sheet['C6'].value, 0)
                        self.assertEqual(sheet['D6'].value, 146480.2)
                        self.assertEqual(sheet['C7'].value, -4.2)
                        self.assertEqual(sheet['D7'].value, -4.2)
                    else:
                        self.assertIn('A1:H1', sheet.merged_cells)
                        self.assertEqual(sheet['C5'].value, 246480.2)
                        self.assertEqual(sheet['D5'].value, 0)
                        self.assertIsNone(sheet['G5'].value)
                finally:
                    book.close()


if __name__ == '__main__':
    unittest.main()
