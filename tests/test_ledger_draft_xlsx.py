"""Independent XLSX reader verifies formulas, cached totals, formatting and isolation."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from decimal import Decimal
from zipfile import ZipFile

from openpyxl import load_workbook


class LedgerDraftWorkbookTests(unittest.TestCase):
    def test_five_subject_workbooks(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='ledger-draft-test-') as directory:
            subprocess.run(['node', str(root / 'tests/test_ledger_draft.mjs'), '--emit-dir', directory], cwd=root, check=True)
            fixtures = json.loads((Path(directory) / 'fixtures.json').read_text(encoding='utf-8'))
            for fixture in fixtures:
                path = Path(directory) / fixture['file']
                with ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                formulas = load_workbook(path, data_only=False)
                values = load_workbook(path, data_only=True)
                expense = fixture['account']['name'] == '管理费用'
                detail_name = '费用发生清单' if expense else '明细账（结存清单）'
                self.assertEqual(formulas.sheetnames, [detail_name, '总分类账'])
                detail, general = formulas.worksheets
                cached_detail, cached_general = values.worksheets
                total_row = len(fixture['draft']['rows']) + 5
                self.assertEqual(detail.freeze_panes, 'A5')
                self.assertEqual(detail['A4'].fill.fgColor.rgb, 'FF2C3E50')
                if total_row > 6:
                    self.assertEqual(detail['A6'].fill.fgColor.rgb, 'FFF4F7FA')
                self.assertEqual(detail[f'A{total_row}'].fill.fgColor.rgb, 'FFD6EAF8')
                self.assertEqual(detail['D5'].data_type, 's', 'Untrusted text must never become a formula.')
                if fixture.get('kind') == 'cosmetics':
                    self.assertEqual(len(fixture['draft']['rows']), 8)
                    self.assertEqual({cached_detail[f'C{row}'].value for row in range(5, total_row)}, {'口红', '粉饼', '眼线笔'})
                    self.assertEqual({cached_detail[f'F{row}'].value for row in range(5, total_row)}, {'支', '盒'})
                    self.assertEqual(detail['D5'].value, '哑光口红')
                elif fixture.get('kind') == 'household':
                    self.assertEqual(len(fixture['draft']['rows']), 8)
                    self.assertEqual({cached_detail[f'C{row}'].value for row in range(5, total_row)}, {'锅', '扫把', '盆'})
                    self.assertEqual([cached_detail[f'F{row}'].value for row in range(5, total_row)], ['口'] * 3 + ['把'] * 3 + ['个'] * 2)
                    self.assertEqual([cached_detail[f'G{row}'].value for row in range(5, total_row)], [2, 3, 3, 3, 3, 3, 3, 4])
                    self.assertEqual([cached_detail[f'H{row}'].value for row in range(5, total_row)], [128, 89, 69, 15, 22, 18, 18, 12.75])
                    self.assertEqual(cached_general['F5'].value, 1000)
                elif not fixture.get('kind'):
                    self.assertEqual(detail['E5'].value, '<型号 & 测试>')
                if expense:
                    self.assertIn('费用发生清单（待核对拟稿）', detail['A1'].value)
                    self.assertEqual([detail[f'{col}4'].value for col in 'CDEFGH'], ['费用类别', '费用项目', '用途、归属及期间说明', '单位', '数量或期数', '单项金额 (元)'])
                    self.assertEqual(detail[f'D{total_row}'].value, '管理费用合计')
                    self.assertIn('费用汇总口径', general['A1'].value)
                    self.assertIn('非人数', general['D4'].value)
                    self.assertEqual(general['F4'].value, '费用汇总金额 (元)')
                    self.assertIn('非员工人数', detail['A3'].value)
                    headings = ' '.join(str(cell.value or '') for ws in [detail, general] for cells in list(ws.rows)[:4] for cell in cells)
                    for forbidden in ['结存', '总账余额', '规格型号', '商品/项目名称', '单价']:
                        self.assertNotIn(forbidden, headings)
                    self.assertNotIn('结存', general['C5'].value)
                    if fixture.get('kind') == 'salary':
                        self.assertEqual(detail['D5'].value, '工资薪金（职工薪酬，归属待核对）')
                        self.assertEqual(cached_detail['G5'].value, 1)
                        self.assertEqual(cached_detail['H5'].value, 1000)
                        self.assertEqual(cached_general['F5'].value, 1000)
                        self.assertNotIn('未识别', detail['E5'].value)
                self.assertEqual(detail['I5'].number_format, '#,##0.00')
                computed = Decimal('0')
                for row in range(5, total_row):
                    self.assertEqual(detail[f'I{row}'].value, f'=ROUND(G{row}*H{row},2)')
                    amount = Decimal(str(cached_detail[f'G{row}'].value)) * Decimal(str(cached_detail[f'H{row}'].value))
                    self.assertEqual(amount, Decimal(str(cached_detail[f'I{row}'].value)))
                    computed += amount
                self.assertEqual(computed, Decimal(fixture['totals']['target']) / 100)
                self.assertEqual(detail[f'I{total_row}'].value, f'=SUM(I5:I{total_row-1})')
                self.assertEqual(general['F5'].value, f"='{detail_name}'!I{total_row}")
                self.assertEqual(general['D5'].value, f"='{detail_name}'!G{total_row}")
                self.assertEqual(cached_general['F5'].value, cached_detail[f'I{total_row}'].value)
                self.assertEqual(cached_general['D5'].value, cached_detail[f'G{total_row}'].value)
                self.assertEqual(general['E5'].value, '贷' if fixture['account']['code'] == '2241' else '借')
                self.assertEqual(formulas.calculation.calcMode, 'auto')
                self.assertEqual(cached_detail[f'I{total_row+1}'].value, 0)
                self.assertIn('待核对拟稿', detail['A3'].value)
                formulas.close()
                values.close()


if __name__ == '__main__':
    unittest.main()
