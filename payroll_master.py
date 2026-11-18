"""Update a company's monthly block in an existing payroll workbook.

Uses the standard library so the portable server needs no Excel installation.
Unchanged ZIP members are copied verbatim, including comments and WPS metadata.
"""
import base64
import binascii
from copy import copy, deepcopy
import datetime as dt
import hashlib
import io
import json
import posixpath
import re
import secrets
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile, ZIP_DEFLATED

from db import ApiError, audit, now

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
N = {'s': NS}
ET.register_namespace('', NS)
KEY = 'payroll_master_file_id'
MAX_FILE = 10 * 1024 * 1024
LEGACY_NOTE = '部分历史记录仅保存社保合计，养老、医保、失业明细待补填。'


def tag(name):
    return '{' + NS + '}' + name


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def xml(node):
    # Other server modules/readers may register a different default namespace.
    # Keep stable main-namespace tags so a saved revision can be edited again.
    node = deepcopy(node)
    for element in node.iter():
        if element.tag.startswith('{' + NS + '}'):
            element.tag = element.tag.split('}', 1)[1]
    node.set('xmlns', NS)
    return ET.tostring(node, encoding='unicode')


def section(source, name, node, before=None):
    pattern = rf'<(?:\w+:)?{name}\b[^>]*(?:/>|>.*?</(?:\w+:)?{name}>)'
    replacement = xml(node) if node is not None else ''
    if re.search(pattern, source, re.S):
        return re.sub(pattern, lambda _: replacement, source, count=1, flags=re.S)
    if node is not None and before:
        return source.replace(before, replacement + before, 1)
    return source


def column(ref):
    return re.match(r'[A-Z]+', ref).group()


def row_number(ref):
    return int(re.search(r'\d+', ref).group())


def read_cell(cell, strings):
    if cell is None:
        return ''
    if cell.get('t') == 'inlineStr':
        return ''.join(t.text or '' for t in cell.findall('.//' + tag('t')))
    value = cell.find(tag('v'))
    if value is None or value.text is None:
        return ''
    if cell.get('t') == 's':
        return strings[int(value.text)]
    return value.text


def month(value, date1904=False):
    value = str(value or '').strip()
    match = re.match(r'^(\d{4})[年./\-]+(\d{1,2})(?:[月./\-\s]|$)', value)
    if match and 1 <= int(match[2]) <= 12:
        return f'{match[1]}-{int(match[2]):02d}'
    try:
        serial = float(value)
        if 1 <= serial <= 100000:
            date = dt.datetime(1904, 1, 1) if date1904 else dt.datetime(1899, 12, 30)
            return (date + dt.timedelta(days=serial)).strftime('%Y-%m')
    except (ValueError, OverflowError):
        pass
    return None


class MasterWorkbook:
    def __init__(self, content):
        try:
            self.archive = ZipFile(io.BytesIO(content))
            entries = self.archive.infolist()
            if (len(entries) > 5000 or len({i.filename for i in entries}) != len(entries)
                    or sum(i.file_size for i in entries) > 80 * 1024 * 1024):
                raise ApiError(400, '工资大表文件过大或目录无效')
            self.parts = {i.filename: self.archive.read(i) for i in entries}
            if any(b'<!DOCTYPE' in v.upper() or b'<!ENTITY' in v.upper()
                   for k, v in self.parts.items() if k.endswith('.xml')):
                raise ApiError(400, '工资大表含不支持的 XML 定义')
            book = ET.fromstring(self.parts['xl/workbook.xml'])
            props = book.find(tag('workbookPr'))
            self.date1904 = props is not None and props.get('date1904') in ('1', 'true')
            self.strings = []
            if 'xl/sharedStrings.xml' in self.parts:
                root = ET.fromstring(self.parts['xl/sharedStrings.xml'])
                self.strings = [''.join(t.text or '' for t in si.iter(tag('t'))) for si in root]
            relationships = ET.fromstring(self.parts['xl/_rels/workbook.xml.rels'])
            paths = {r.get('Id'): posixpath.normpath(posixpath.join('xl', r.get('Target'))).lstrip('/')
                     if not r.get('Target', '').startswith('/') else r.get('Target').lstrip('/')
                     for r in relationships if r.get('TargetMode') != 'External'}
            self.sheets = []
            for index, item in enumerate(book.find(tag('sheets'))):
                path = paths[item.get('{' + REL + '}id')]
                root = ET.fromstring(self.parts[path])
                data = root.find(tag('sheetData'))
                if data is None:
                    continue
                cells = {c.get('r'): c for row in data for c in row}
                blocks = []
                for row in data:
                    r = int(row.get('r'))
                    title = read_cell(cells.get(f'A{r}'), self.strings)
                    if not isinstance(title, str) or not normalized(title).endswith('工资表'):
                        continue
                    # Match the payroll structure, not incidental notes elsewhere.
                    if normalized(read_cell(cells.get(f'B{r+3}'), self.strings)) != '姓名':
                        continue
                    total = next((int(other.get('r')) for other in data
                                  if int(other.get('r')) >= r + 5 and
                                  normalized(read_cell(cells.get('A' + other.get('r')), self.strings)) == '合计'), None)
                    if total is None:
                        continue
                    end = total
                    if read_cell(cells.get(f'A{total+1}'), self.strings) == LEGACY_NOTE:
                        end += 1
                    blocks.append({'start': r, 'total': total, 'end': end,
                                   'company': normalized(title)[:-3],
                                   'period': month(read_cell(cells.get(f'A{r+1}'), self.strings), self.date1904)})
                self.sheets.append({'name': item.get('name'), 'path': path, 'index': index,
                                    'id': item.get('sheetId'), 'root': root, 'cells': cells, 'blocks': blocks})
            if not any(s['blocks'] for s in self.sheets):
                raise ApiError(400, '未找到公司工资表，请选择含公司名称、月份、姓名和合计行的工资大表')
        except ApiError:
            raise
        except (BadZipFile, KeyError, ValueError, IndexError, AttributeError, ET.ParseError, RuntimeError):
            raise ApiError(400, '无法读取工资大表，请选择有效的 .xlsx 文件')

    def match(self, company):
        name = normalized(company)
        exact = [s for s in self.sheets if any(b['company'] == name for b in s['blocks'])]
        if exact:
            return exact, '公司全称'
        abbreviated = [s for s in self.sheets if s['blocks'] and
                       len(short := re.sub(r'\([^)]*\)', '', normalized(s['name']))) >= 2 and short in name]
        return abbreviated, '工作表简称'

    def preview(self, company, period):
        matches, kind = self.match(company)
        selected = matches[0]['name'] if len(matches) == 1 else ''
        return {'sheet_name': selected, 'match_kind': kind if selected else '',
                'sheets': [{'name': s['name'], 'company': s['blocks'][-1]['company'],
                            'existing_months': sum(b['period'] == period for b in s['blocks']),
                            'last_period': s['blocks'][-1]['period']}
                           for s in self.sheets if s['blocks']]}

    def export(self, company, period, records, sheet_name=''):
        matches, _ = self.match(company)
        if not sheet_name:
            if len(matches) != 1:
                raise ApiError(400, '没有唯一匹配的公司，请先选择对应工作表')
            sheet_name = matches[0]['name']
        sheet = next((s for s in self.sheets if s['name'] == sheet_name and s['blocks']), None)
        if sheet is None:
            raise ApiError(400, '请选择工资大表中有效的公司工作表')
        # An exact full-name match cannot accidentally be redirected to another company.
        if len(matches) == 1 and any(b['company'] == normalized(company) for b in matches[0]['blocks']) and sheet != matches[0]:
            raise ApiError(400, '所选工作表与当前公司名称不一致')
        blocks = sheet['blocks']
        existing = [b for b in blocks if b['period'] == period]
        if len(existing) > 1:
            raise ApiError(409, '该公司在大表中有多份相同月份，请先整理重复月份后再导出')
        template = existing[0] if existing else blocks[-1]
        # Follow the last observed gap; never let adjacent month blocks touch.
        gap = max(1, min(20, blocks[-1]['start'] - blocks[-2]['end'] - 1)) if len(blocks) > 1 else 2
        legacy = any(p['social_fen'] and not any(p[k] for k in ('pension_fen', 'medical_fen', 'unemployment_fen')) for p in records)
        has_other = any(p['other_deduction_fen'] for p in records)
        width = 12 if has_other else 11
        new_length = 6 + len(records) + int(legacy)
        root = deepcopy(sheet['root'])
        data = root.find(tag('sheetData'))
        cut = delta = 0
        if existing:
            start = template['start']
            delta = max(0, new_length - (template['end'] - start + 1))
            cut = template['end'] + 1
        else:
            following = next((b for b in blocks if b['period'] and b['period'] > period), None)
            if following:
                start = cut = following['start']
                delta = new_length + gap
            else:
                # Ignore formatted empty rows/XFD columns; retain genuine notes below the last table.
                last_content = max([blocks[-1]['end']] + [int(r.get('r')) for r in data
                                   if any(read_cell(c, self.strings) or c.find(tag('f')) is not None for c in r)])
                start = last_content + gap + 1
        if start + new_length > 1048576:
            raise ApiError(400, '工资工作表行数已达 Excel 上限')
        if existing:
            # Only replace the selected month's payroll columns; side notes survive.
            old_width = 12 if normalized(read_cell(sheet['cells'].get(f'L{start+3}'), self.strings)) == '其他扣款' else 11
            for row in list(data):
                if start <= int(row.get('r')) <= template['end']:
                    for cell in list(row):
                        if column(cell.get('r')) in [chr(65 + i) for i in range(old_width)]:
                            row.remove(cell)
            merges = root.find(tag('mergeCells'))
            if merges is not None:
                for merge in list(merges):
                    a, b = (merge.get('ref').split(':') * 2)[:2]
                    if start <= row_number(a) <= row_number(b) <= template['end'] and column(b) in [chr(65+i) for i in range(old_width)]:
                        merges.remove(merge)
        if delta:
            shift_sheet(root, cut, delta, sheet['name'])
        new_rows, new_merges = build_block(sheet, template, company, period, records, start, width, legacy, self.date1904)
        by_row = {int(r.get('r')): r for r in data}
        for row in new_rows:
            r = int(row.get('r'))
            if r in by_row:
                old = by_row[r]
                for c in list(old):
                    if column(c.get('r')) in [chr(65+i) for i in range(width)]:
                        old.remove(c)
                old.attrib.update(row.attrib)
                old.extend(list(row))
                old[:] = sorted(old, key=lambda c: (len(column(c.get('r'))), column(c.get('r'))))
            else:
                by_row[r] = row
        data[:] = [by_row[r] for r in sorted(by_row)]
        merges = root.find(tag('mergeCells'))
        if merges is None:
            merges = ET.Element(tag('mergeCells'))
        for ref in new_merges:
            ET.SubElement(merges, tag('mergeCell'), {'ref': ref})
        merges.set('count', str(len(merges)))
        original = self.parts[sheet['path']].decode('utf-8')
        updated = section(original, 'sheetData', data)
        updated = section(updated, 'mergeCells', merges, '</sheetData>') if '<mergeCells' in original else updated.replace('</sheetData>', '</sheetData>' + xml(merges), 1)
        dimension = root.find(tag('dimension'))
        if dimension is not None:
            old_end = dimension.get('ref', 'A1').split(':')[-1]
            last_col = max(column(old_end), chr(64 + width), key=lambda c: (len(c), c))
            dimension.set('ref', f'A1:{last_col}{max(row_number(old_end), start+new_length-1)}')
            updated = section(updated, 'dimension', dimension)
        if delta:
            for name in ('conditionalFormatting', 'dataValidations', 'hyperlinks', 'autoFilter', 'rowBreaks', 'sheetViews'):
                # Conditional formatting may have multiple independent rules.
                nodes = iter(root.findall(tag(name)))
                updated = re.sub(rf'<{name}\b[^>]*(?:/>|>.*?</{name}>)', lambda _: xml(next(nodes)), updated, flags=re.S)
        if has_other:
            cols = root.find(tag('cols'))
            if cols is not None:
                # Split any broad styled-column range before assigning L its own width.
                split = []
                for c in cols:
                    lo, hi = int(c.get('min')), int(c.get('max'))
                    if lo <= 12 <= hi:
                        if lo < 12:
                            left = deepcopy(c); left.set('max', '11'); split.append(left)
                        if hi > 12:
                            right = deepcopy(c); right.set('min', '13'); split.append(right)
                    else:
                        split.append(c)
                split.append(ET.Element(tag('col'), {'min': '12', 'max': '12', 'width': '14', 'customWidth': '1'}))
                cols[:] = sorted(split, key=lambda c: int(c.get('min')))
                updated = section(updated, 'cols', cols)
        changes = {sheet['path']: updated.encode('utf-8')}
        if delta:
            changes.update(shift_related(self.parts, sheet, cut, delta))
        workbook_text = changes.get('xl/workbook.xml', self.parts['xl/workbook.xml']).decode('utf-8')
        # Extend this sheet's print area without changing other sheets' settings.
        def print_area(match):
            text = match.group()
            if f'localSheetId="{sheet["index"]}"' not in text or 'name="_xlnm.Print_Area"' not in text:
                return text
            return re.sub(r'(\$[A-Z]+\$\d+:)\$[A-Z]+\$\d+', lambda m: m[1] + f'${chr(64+width)}${max(row_number(dimension.get("ref").split(":")[-1]) if dimension is not None else 1, start+new_length-1)}', text)
        workbook_text = re.sub(r'<definedName\b[^>]*>.*?</definedName>', print_area, workbook_text, flags=re.S)
        calc = ET.Element(tag('calcPr'), {'calcMode': 'auto', 'fullCalcOnLoad': '1', 'forceFullCalc': '1'})
        workbook_text = section(workbook_text, 'calcPr', calc, '</workbook>')
        changes['xl/workbook.xml'] = workbook_text.encode('utf-8')
        # A stale calc chain can refer to replaced rows. Excel rebuilds it on open.
        for path, pattern in (('xl/_rels/workbook.xml.rels', r'<Relationship\b[^>]*\bType="[^"]*/calcChain"[^>]*/>'),
                              ('[Content_Types].xml', r'<Override\b[^>]*\bPartName="/xl/calcChain.xml"[^>]*/>')):
            changes[path] = re.sub(pattern, '', self.parts[path].decode('utf-8')).encode('utf-8')
        output = io.BytesIO()
        with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
            for info in self.archive.infolist():
                if info.filename != 'xl/calcChain.xml':
                    archive.writestr(copy(info), changes.get(info.filename, self.parts[info.filename]))
        return output.getvalue(), {'sheet_name': sheet_name, 'period': period,
                                  'start_row': start, 'end_row': start+new_length-1,
                                  'employee_count': len(records), 'updated': bool(existing), 'gap_rows': gap}


CELL_REF = re.compile(r"(?<![\w.])(?:(?P<sheet>'(?:[^']|'')+'|[\w\u0080-\uffff]+)!)?(?P<refs>\$?[A-Z]{1,3}\$?[1-9]\d*(?::\$?[A-Z]{1,3}\$?[1-9]\d*)?)(?![\w(])")


def shift_refs(value, cut, delta, sheet_name, local=True):
    def replace(match):
        qualifier = match['sheet']
        if qualifier:
            if qualifier.strip("'").replace("''", "'") != sheet_name:
                return match.group()
        elif not local:
            return match.group()
        refs = re.sub(r'(\$?[A-Z]{1,3}\$?)([1-9]\d*)', lambda m: m[1]+str(int(m[2])+delta if int(m[2]) >= cut else int(m[2])), match['refs'])
        return (qualifier + '!' if qualifier else '') + refs
    # Never rewrite references appearing in Excel string literals.
    return ''.join(part if i % 2 else CELL_REF.sub(replace, part) for i, part in enumerate(re.split(r'("(?:[^"]|"")*")', value or '')))


def shift_sheet(root, cut, delta, name):
    for node in root.iter():
        for attribute in ('ref', 'sqref', 'activeCell', 'topLeftCell'):
            if attribute in node.attrib:
                node.set(attribute, shift_refs(node.get(attribute), cut, delta, name))
        if node.tag == tag('row') and int(node.get('r')) >= cut:
            node.set('r', str(int(node.get('r')) + delta))
        if node.tag == tag('c'):
            node.set('r', shift_refs(node.get('r'), cut, delta, name))
        if node.tag in (tag('f'), tag('formula'), tag('formula1'), tag('formula2')) and node.text:
            node.text = shift_refs(node.text, cut, delta, name)
        if node.tag == tag('brk') and int(node.get('id', 0)) >= cut:
            node.set('id', str(int(node.get('id')) + delta))


def shift_related(parts, sheet, cut, delta):
    changes = {}
    for path, content in parts.items():
        if (path.startswith('xl/worksheets/') and path.endswith('.xml') and path != sheet['path']) or path == 'xl/workbook.xml':
            text = content.decode('utf-8')
            text = re.sub(r'<(f|definedName)\b[^>]*>.*?</\1>', lambda m: shift_refs(m.group(), cut, delta, sheet['name'], local=False), text, flags=re.S)
            if text.encode('utf-8') != content:
                changes[path] = text.encode('utf-8')
    relationship_path = posixpath.join(posixpath.dirname(sheet['path']), '_rels', posixpath.basename(sheet['path']) + '.rels')
    if relationship_path in parts:
        for rel in ET.fromstring(parts[relationship_path]):
            if rel.get('TargetMode') == 'External':
                continue
            path = posixpath.normpath(posixpath.join(posixpath.dirname(sheet['path']), rel.get('Target'))).lstrip('/')
            if path not in parts or not path.endswith(('.xml', '.vml')):
                continue
            text = parts[path].decode('utf-8')
            if rel.get('Type', '').endswith(('/comments', '/table')):
                text = re.sub(r'\bref="([^"]+)"', lambda m: 'ref="' + shift_refs(m[1], cut, delta, sheet['name']) + '"', text)
            if rel.get('Type', '').endswith(('/drawing', '/vmlDrawing')):
                text = re.sub(r'(<(?:\w+:)?(?:row|Row)>)(\d+)(</(?:\w+:)?(?:row|Row)>)', lambda m: m[1]+str(int(m[2])+delta if int(m[2])+1 >= cut else int(m[2]))+m[3], text)
                def anchor(m):
                    values = [int(v.strip()) for v in m[2].split(',')]
                    for i in (2, 6):
                        if values[i] + 1 >= cut:
                            values[i] += delta
                    return m[1] + ', '.join(map(str, values)) + m[3]
                text = re.sub(r'(<(?:\w+:)?Anchor>)([\d,\s]+)(</(?:\w+:)?Anchor>)', anchor, text)
            if text.encode('utf-8') != parts[path]:
                changes[path] = text.encode('utf-8')
    return changes


def build_block(sheet, template, company, period, records, start, width, legacy, date1904):
    cells = sheet['cells']
    row_templates = {int(r.get('r')): r for r in sheet['root'].find(tag('sheetData'))}
    old = template['start']
    rows = []
    def row(r, source):
        attrs = dict(row_templates.get(source, ET.Element('row')).attrib)
        attrs.update(r=str(r), spans=f'1:{width}')
        attrs.pop('hidden', None)
        return ET.Element(tag('row'), attrs)
    def cell(r, col, value=None, formula=None, source_row=None, source_col=None):
        source = cells.get(f'{source_col or col}{source_row}')
        attrs = {'r': f'{col}{r}'}
        if source is not None and source.get('s'):
            attrs['s'] = source.get('s')
        c = ET.Element(tag('c'), attrs)
        if formula:
            ET.SubElement(c, tag('f')).text = formula
        if value is not None:
            if isinstance(value, str):
                c.set('t', 'inlineStr')
                ET.SubElement(ET.SubElement(c, tag('is')), tag('t'), {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}).text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
            else:
                ET.SubElement(c, tag('v')).text = str(value)
        return c
    year, mon = map(int, period.split('-'))
    original_date = read_cell(cells.get(f'A{old+1}'), []) if cells.get(f'A{old+1}') is not None and cells[f'A{old+1}'].get('t') not in ('s', 'inlineStr') else ''
    numeric_date = bool(re.fullmatch(r'\d+(?:\.\d+)?', original_date))
    date = (dt.date(year, mon, 1) - (dt.date(1904, 1, 1) if date1904 else dt.date(1899, 12, 30))).days if numeric_date else f'{year}年{mon}月'
    headers = ['序号', '姓名', '工资总额', '扣社保自付部分', None, None, None, '扣公积金      自付部分', '应发工资', '扣个税', '应实发工资'] + (['其他扣款'] if width == 12 else [])
    for offset in range(5):
        r = start + offset
        line = row(r, old + offset)
        for i in range(width):
            col = chr(65+i)
            value = None
            if offset == 0 and i == 0: value = company + '工资表'
            if offset == 1 and i == 0: value = date
            if offset == 2 and i == width-1: value = '单位:元'
            if offset == 3: value = headers[i]
            if offset == 4 and 3 <= i <= 6: value = ['养老', '医保', '失业', '小计'][i-3]
            source_col = 'H' if i == 11 and offset >= 3 else 'K' if i == 11 else col
            line.append(cell(r, col, value, source_row=old+offset, source_col=source_col))
        rows.append(line)
    totals = [0] * (width - 2)
    detail_keys = ['pension_fen', 'medical_fen', 'unemployment_fen']
    for index, person in enumerate(records):
        r = start + 5 + index
        gross, social, housing, tax, other = (person[k] for k in ('gross_fen', 'social_fen', 'housing_fund_fen', 'withheld_tax_fen', 'other_deduction_fen'))
        details = [person[k] for k in detail_keys]
        if social and not any(details): details = [None] * 3
        values = [gross, *details, social, housing, gross-social-housing-other, tax, gross-social-housing-other-tax] + ([other] if width == 12 else [])
        line = row(r, old+5)
        line.append(cell(r, 'A', index+1, source_row=old+5))
        line.append(cell(r, 'B', person['employee_name'], source_row=old+5))
        for i, value in enumerate(values):
            col = chr(67+i)
            if value is None: totals[i] = None
            elif totals[i] is not None: totals[i] += value
            formula = None
            if col == 'G' and all(v is not None for v in details): formula = f'SUM(D{r}:F{r})'
            if col == 'I': formula = f'ROUND(C{r}-G{r}-H{r}' + (f'-L{r}' if width == 12 else '') + ',2)'
            if col == 'K': formula = f'ROUND(I{r}-J{r},2)'
            # Decimal strings are serialized as numeric XML, not floating-point arithmetic.
            number = None if value is None or (col == 'J' and value == 0) else value
            c = cell(r, col, formula=formula, source_row=old+5, source_col='C' if col == 'L' else col)
            if number is not None: ET.SubElement(c, tag('v')).text = f'{number//100}.{number%100:02d}'
            line.append(c)
        rows.append(line)
    total = start + 5 + len(records)
    line = row(total, template['total'])
    line.append(cell(total, 'A', '合计', source_row=template['total']))
    line.append(cell(total, 'B', source_row=template['total']))
    for i, value in enumerate(totals):
        col = chr(67+i)
        c = cell(total, col, formula=f'SUM({col}{start+5}:{col}{total-1})' if value is not None else None,
                 source_row=template['total'], source_col='C' if col == 'L' else col)
        if value is not None: ET.SubElement(c, tag('v')).text = f'{value//100}.{value%100:02d}'
        line.append(c)
    rows.append(line)
    last = chr(64+width)
    merges = [f'A{start}:{last}{start}', f'A{start+1}:{last}{start+1}', f'D{start+3}:G{start+3}', f'A{total}:B{total}']
    # Preserve the source's vertical header merges.
    for merge in sheet['root'].findall('s:mergeCells/s:mergeCell', N):
        ref = merge.get('ref')
        if ref in {f'{c}{old+3}:{c}{old+4}' for c in 'ABCHIJKL'}:
            merges.append(re.sub(r'\d+', lambda m: str(int(m[0])+start-old), ref))
    if width == 12: merges.append(f'L{start+3}:L{start+4}')
    if legacy:
        line = row(total+1, old+5)
        line.append(cell(total+1, 'A', LEGACY_NOTE, source_row=old+5, source_col='B'))
        rows.append(line); merges.append(f'A{total+1}:{last}{total+1}')
    return rows, list(dict.fromkeys(merges))


def current_file(conn):
    value = conn.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
    return conn.execute('SELECT * FROM files WHERE id=?', (json.loads(value[0]),)).fetchone() if value else None


def save_master(conn, files_dir, content, user, detail=None):
    """Register an immutable revision; normal application backup includes every revision."""
    name = '记账户工资大表.xlsx'
    filename = secrets.token_hex(24) + '.xlsx'
    target = files_dir / filename
    try:
        with target.open('xb') as stream:
            stream.write(content)
        with conn:
            ident = conn.execute('INSERT INTO files(name,path,size,sha256,user_id,created_at) VALUES(?,?,?,?,?,?)',
                                 (name, filename, len(content), hashlib.sha256(content).hexdigest(), (user or {}).get('id'), now())).lastrowid
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=settings.version+1', (KEY, json.dumps(ident)))
            audit(conn, user, 'export' if detail else 'import', 'payroll_master', ident, detail or {'name': name})
        return {'file_id': ident, 'name': name, 'download_url': f'/api/files/{ident}'}
    except Exception:
        target.unlink(missing_ok=True)
        raise


def handle(conn, files_dir, cid, action, payload):
    import finance
    client = finance._client(conn, cid)
    period = finance._period(payload.get('period'))
    records = [dict(p) for p in conn.execute('SELECT * FROM payroll WHERE client_id=? AND period=? ORDER BY employee_name,id', (cid, period))]
    current = current_file(conn)
    version = current['id'] if current else None
    if action == 'export' and payload.get('master_version') != version:
        raise ApiError(409, '工资大表已被其他导出更新，请关闭窗口后重新导出')
    if payload.get('content_base64') is not None:
        try:
            content = base64.b64decode(payload['content_base64'], validate=True)
        except (ValueError, TypeError, binascii.Error):
            raise ApiError(400, '工资大表文件内容无效')
        if not content or len(content) > MAX_FILE:
            raise ApiError(413, '请选择不超过 10MB 的工资大表')
    elif current:
        path = (files_dir / current['path']).resolve()
        if path.parent != files_dir.resolve() or not path.is_file():
            raise ApiError(404, '保存的工资大表文件缺失，请重新选择文件')
        content = path.read_bytes()
    else:
        if action == 'export':
            raise ApiError(400, '请先选择工资大表文件')
        return {'configured': False, 'master_version': None, 'sheets': [], 'sheet_name': '', 'employee_count': len(records)}
    workbook = MasterWorkbook(content)
    if action == 'preview':
        return {'configured': True, 'master_version': version, 'name': '记账户工资大表.xlsx',
                'employee_count': len(records), **workbook.preview(client['name'], period)}
    if not records:
        raise ApiError(400, '本月没有工资记录，请先保存人员工资')
    output, result = workbook.export(client['name'], period, records, payload.get('sheet_name', ''))
    return output, result
