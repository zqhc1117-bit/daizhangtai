"""Check the application's actual XLSX exporter with XML and an independent reader."""
import base64
import io
import json
from pathlib import Path
import subprocess
import unittest
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import load_workbook


class PayrollExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result=subprocess.run(['node','--input-type=module','-e',"""
import {createPayrollXlsx,payrollFilename} from './public/payroll-xlsx.js';
import {payrollReport} from './public/payroll-report.js';
import {payrollSlipMarkup} from './public/payroll.js';
const book={client:{id:1,name:'合成餐饮管理有限公司'},period:'2026-11'};
const person={client_id:1,period:'2026-11',employee_name:'合成员工甲',gross_fen:500000,pension_fen:58160,medical_fen:14840,unemployment_fen:3635,social_fen:76635,housing_fund_fen:15000,withheld_tax_fen:0,other_deduction_fen:0};
const people=Array.from({length:4},(_,i)=>({...person,employee_name:`合成员工${i+1}`,...(i===3?{gross_fen:700000,housing_fund_fen:30000,withheld_tax_fen:2801}:{})}));
const output={};
function add(name,b,rows){const current={...b,payroll:rows};output[name]={file:Buffer.from(createPayrollXlsx(current)).toString('base64'),report:payrollReport(current),markup:payrollSlipMarkup(rows,current),filename:payrollFilename(current)};}
add('november',book,people);
for(const [name,period] of [['december','2026-12'],['january','2027-01']])add(name,{...book,period},people.map(p=>({...p,period})));
add('other',book,[{...person,other_deduction_fen:1250}]);
add('legacy',book,[{...person,pension_fen:0,medical_fen:0,unemployment_fen:0}]);
add('scoped',book,[person,{...person,client_id:2,employee_name:'别的公司'},{...person,period:'2026-12',employee_name:'别的月份'}]);
add('text',{...book,client:{id:1,name:'=合成<&公司'}},[{...person,employee_name:'=1+1 <员工>'}]);
process.stdout.write(JSON.stringify(output));
"""],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',check=True)
        cls.cases=json.loads(result.stdout)

    def workbook(self,name,formulas=False):
        book=load_workbook(io.BytesIO(base64.b64decode(self.cases[name]['file'])),data_only=not formulas)
        self.addCleanup(book.close)
        return book

    def test_reference_layout_styles_and_all_four_people_totals(self):
        sheet=self.workbook('november').active
        self.assertEqual('合成餐饮管理有限公司工资表',sheet['A1'].value)
        self.assertEqual('2026年11月',sheet['A2'].value)
        self.assertEqual('单位：元',sheet['K3'].value)
        self.assertEqual('扣社保自付部分',sheet['D4'].value)
        self.assertEqual(['养老','医保','失业','小计'],[sheet.cell(5,col).value for col in range(4,8)])
        for area in ('A1:K1','A2:K2','D4:G4','A4:A5','H4:H5','A10:B10'):
            self.assertIn(area,sheet.merged_cells)
        self.assertEqual([5000,581.6,148.4,36.35,766.35,150,4083.65,None,4083.65],[sheet.cell(6,col).value for col in range(3,12)])
        self.assertEqual([22000,2326.4,593.6,145.4,3065.4,750,18184.6,28.01,18156.59],[sheet.cell(10,col).value for col in range(3,12)])
        self.assertEqual(5905.64,sheet['K9'].value)
        self.assertEqual('宋体',sheet['A1'].font.name)
        self.assertEqual('thin',sheet['C6'].border.left.style)
        self.assertEqual('#,##0.00',sheet['C6'].number_format)
        self.assertEqual('right',sheet['K6'].alignment.horizontal)
        self.assertTrue(sheet['K10'].font.bold)
        self.assertFalse(sheet.sheet_view.showGridLines)
        self.assertEqual('landscape',sheet.page_setup.orientation)
        self.assertEqual(1,sheet.page_setup.fitToWidth)
        self.assertEqual("'工资表'!$A$1:$K$10",str(sheet.print_area))

    def test_selected_month_year_and_filename_follow_the_company(self):
        for name,label,period in (('november','2026年11月','2026-11'),('december','2026年12月','2026-12'),('january','2027年1月','2027-01')):
            with self.subTest(name=name):
                self.assertEqual(label,self.workbook(name).active['A2'].value)
                self.assertIn(label,self.cases[name]['markup'])
                self.assertEqual(f'合成餐饮管理有限公司_{period}_工资表.xlsx',self.cases[name]['filename'])
        self.assertNotIn('2026年11月',self.cases['december']['markup'])

    def test_formulas_cached_values_and_optional_other_deductions(self):
        sheet=self.workbook('november',formulas=True).active
        self.assertEqual('=SUM(D6:F6)',sheet['G6'].value)
        self.assertEqual('=ROUND(C6-G6-H6,2)',sheet['I6'].value)
        self.assertEqual('=ROUND(I6-J6,2)',sheet['K6'].value)
        self.assertEqual('=SUM(K6:K9)',sheet['K10'].value)
        other=self.workbook('other').active
        self.assertEqual(12,other.max_column)
        self.assertEqual('其他扣款',other['I4'].value)
        self.assertEqual((12.5,4071.15,4071.15),(other['I6'].value,other['J6'].value,other['L6'].value))
        self.assertEqual('=ROUND(C6-G6-H6-I6,2)',self.workbook('other',formulas=True).active['J6'].value)

    def test_legacy_social_company_scope_and_text_are_preserved(self):
        sheet=self.workbook('legacy').active
        self.assertEqual([None,None,None],[sheet.cell(6,col).value for col in range(4,7)])
        self.assertEqual(766.35,sheet['G6'].value)
        self.assertEqual(4083.65,sheet['K6'].value)
        self.assertIn('明细待补填',sheet['A8'].value)
        scoped=self.workbook('scoped').active
        self.assertEqual('合计',scoped['A7'].value)
        self.assertNotIn('别的公司',self.cases['scoped']['markup'])
        self.assertNotIn('别的月份',self.cases['scoped']['markup'])
        text=self.workbook('text',formulas=True).active
        self.assertEqual('s',text['B6'].data_type)
        self.assertEqual('=1+1 <员工>',text['B6'].value)
        self.assertIn('&lt;员工&gt;',self.cases['text']['markup'])

    def test_generated_packages_have_valid_xml(self):
        for name,case in self.cases.items():
            with self.subTest(name=name),ZipFile(io.BytesIO(base64.b64decode(case['file']))) as archive:
                self.assertIsNone(archive.testzip())
                for member in archive.namelist():
                    if member.endswith(('.xml','.rels')):ElementTree.fromstring(archive.read(member))


if __name__=='__main__':unittest.main()
