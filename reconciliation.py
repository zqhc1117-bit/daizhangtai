"""Invoice/statement reconciliation; the HTTP layer archives parsed sources locally."""
import base64, binascii, hashlib, io, json, os, queue, re, shutil, subprocess, tempfile, threading, time, zipfile
from itertools import combinations
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path
from db import ApiError, audit, now

MAX_FILES = 30
MAX_EXPANDED = 40 * 1024 * 1024

def init_schema(conn):
    import account_rules
    account_rules.init_schema(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS reconciliation_records (
        id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id),
        invoice_number TEXT NOT NULL, transaction_json TEXT NOT NULL, invoice_json TEXT NOT NULL,
        account_name TEXT NOT NULL, status TEXT NOT NULL, review_note TEXT NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
        transaction_key TEXT NOT NULL, UNIQUE(client_id,invoice_number), UNIQUE(client_id,transaction_key))''')
    if 'batch_metadata_json' not in {r['name'] for r in conn.execute('PRAGMA table_info(reconciliation_records)')}:
        conn.execute("ALTER TABLE reconciliation_records ADD COLUMN batch_metadata_json TEXT NOT NULL DEFAULT '{}'")
    import source_archive
    source_archive.init_schema(conn)

def fen(value):
    if isinstance(value, bool): return None
    try:
        text = str(value).strip().replace(',', '').replace('¥','').replace('￥','')
        if not re.fullmatch(r'-?\d+(?:\.\d{1,2})?', text): return None
        n = Decimal(text) * 100
        return int(n) if abs(n) <= 900000000000000 else None
    except (InvalidOperation, ValueError): return None

def normalize(value):
    return str(value or '').strip()

def compare(t, i, account, account_needs_review=False):
    reasons=[]
    # OCR provenance is retained on records, but it is not itself a mismatch.
    # Missing fields and actual differences still require attention.
    transaction_date, invoice_date = normalize(t.get('date')), normalize(i.get('date'))
    if not transaction_date or not invoice_date:
        reasons.append('交易日期或开票日期缺失')
    else:
        try:
            if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', x) for x in (transaction_date, invoice_date)):
                raise ValueError('日期格式无效')
            transaction_day, invoice_day = date.fromisoformat(transaction_date), date.fromisoformat(invoice_date)
        except ValueError:
            reasons.append('交易日期或开票日期无效')
        else:
            same_month = (transaction_day.year, transaction_day.month) == (invoice_day.year, invoice_day.month)
            if not same_month and abs((transaction_day - invoice_day).days) > 30:
                reasons.append('交易日期与开票日期不在同月且相差超过30天')
    if not normalize(i.get('number')): reasons.append('发票号码缺失')
    a,b=fen(t.get('amount')),fen(i.get('amount'))
    if a is None or b is None: reasons.append('金额缺失或格式无效')
    elif a == 0 or b <= 0: reasons.append('交易金额不能为零，发票金额须为正数')
    elif abs(a) != b: reasons.append('金额不一致')
    # Incoming funds correspond to sales invoices; outgoing funds to purchase invoices.
    counter_role, own_role = ('buyer','seller') if a is not None and a > 0 else ('seller','buyer')
    labels={'buyer':'购买方','seller':'销售方'}
    if not normalize(t.get('counterparty')) or not normalize(i.get(counter_role)): reasons.append('对手方或发票'+labels[counter_role]+'缺失')
    elif normalize(t['counterparty']) != normalize(i[counter_role]): reasons.append('交易对手方与发票'+labels[counter_role]+'不一致')
    if not normalize(account) or not normalize(i.get(own_role)): reasons.append('本方名称或发票'+labels[own_role]+'缺失')
    elif normalize(account) != normalize(i[own_role]): reasons.append('本方名称与发票'+labels[own_role]+'不一致')
    return reasons

def multi_invoice_candidates(transactions, invoices, account, protected, done):
    """Return bounded, non-posting suggestions for multiple invoices per transaction.

    The legacy posting path still consumes only a single invoice_index. A group
    here is a possible association, not proof of payment or permission to post.
    """
    result=[]
    date_reasons={'交易日期或开票日期缺失','交易日期或开票日期无效',
                  '交易日期与开票日期不在同月且相差超过30天'}
    for ti,t in enumerate(transactions):
        if ti in done: continue
        amount=fen(t.get('amount'))
        if amount is None or amount==0: continue
        pool=[]
        for ii,inv in enumerate(invoices):
            if ii in protected: continue
            invoice_amount=fen(inv.get('amount'))
            if invoice_amount is None or invoice_amount<=0 or invoice_amount>=abs(amount): continue
            reasons=compare(t,inv,account)
            if any(reason not in date_reasons and reason!='金额不一致' for reason in reasons): continue
            pool.append((ii,invoice_amount,normalize(inv.get('number')),
                         [reason for reason in reasons if reason in date_reasons]))
        if len(pool)<2: continue
        # Refuse combinatorial searches rather than silently searching only a
        # subset and presenting an incomplete result as unique.
        if len(pool)>14:
            result.append({'transaction_index':ti,'status':'limit_exceeded','candidates':[],
                           'reason':'同一对手的候选发票超过14张，无法自动穷举组合'})
            continue
        found=[]
        for count in range(2,len(pool)+1):
            for group in combinations(pool,count):
                if sum(x[1] for x in group)!=abs(amount): continue
                if len({x[2] for x in group})!=len(group): continue
                found.append({'invoice_indices':[x[0] for x in group],
                              'amount_fen':sum(x[1] for x in group),
                              'date_warnings':sorted({reason for x in group for reason in x[3]})})
                if len(found)>=2: break
            if len(found)>=2: break
        if found:
            result.append({'transaction_index':ti,'status':'ambiguous' if len(found)>1 else 'candidate',
                           'candidates':found,'reason':'存在多个组合，仅显示前两组' if len(found)>1 else '金额与对手方一致，属于候选配对'})
    by_invoice={}
    for row in result:
        for group in row['candidates']:
            for ii in group['invoice_indices']:
                by_invoice.setdefault(ii,set()).add(row['transaction_index'])
    for row in result:
        if any(len(by_invoice[ii])>1 for group in row['candidates'] for ii in group['invoice_indices']):
            row['status']='ambiguous'
            row['reason']+='；发票同时出现在其他流水的候选组合中'
    return result

def match(transactions, invoices, account_name, account_needs_review=False, conn=None, client_id=None, receipts=None):
    if not isinstance(transactions,list) or not isinstance(invoices,list) or len(transactions)>500 or len(invoices)>500: raise ApiError(400,'记录格式无效或超过 500 条')
    if any(not isinstance(x,dict) for x in transactions+invoices): raise ApiError(400,'记录格式无效')
    if not isinstance(account_needs_review,bool): raise ApiError(400,'户名复核状态无效')
    receipts=[] if receipts is None else receipts
    receipt_matches=match_receipts(transactions,receipts)
    strict={}
    for ti,t in enumerate(transactions):
        # Review flags do not change which fields constitute the best candidate.
        strict[ti]=[ii for ii,i in enumerate(invoices) if not compare(dict(t,needs_review=False),dict(i,needs_review=False),account_name)]
    reverse_strict={ii:[ti for ti,ids in strict.items() if ii in ids] for ii in range(len(invoices))}
    protected={ii for ids in strict.values() for ii in ids}
    matches=[]; used=set(); done=set()
    for ti,ids in strict.items():
        if not ids: continue
        done.add(ti)
        if len(ids)!=1:
            matches.append({'transaction_index':ti,'invoice_index':None,'status':'review','reasons':['存在多个完全一致的发票，请人工选择'],'candidate_indices':ids});continue
        ii=ids[0]
        if len(reverse_strict[ii])!=1:
            matches.append({'transaction_index':ti,'invoice_index':ii,'status':'review','reasons':['同一发票对应多笔完全一致的交易，请人工确认']});continue
        reasons=compare(transactions[ti],invoices[ii],account_name,account_needs_review)
        matches.append({'transaction_index':ti,'invoice_index':ii,'status':'review' if reasons else 'matched','reasons':reasons});used.add(ii)
    partial={}
    for ti,t in enumerate(transactions):
        if ti in done: continue
        ids=[]
        for ii,i in enumerate(invoices):
            if ii in protected: continue
            a,b=fen(t.get('amount')),fen(i.get('amount'))
            same_amount=a is not None and b is not None and a!=0 and b>0 and abs(a)==b
            counter_role='buyer' if a is not None and a>0 else 'seller'
            same_name=bool(normalize(t.get('counterparty'))) and normalize(t.get('counterparty'))==normalize(i.get(counter_role))
            if same_amount or same_name: ids.append(ii)
        partial[ti]=ids
    reverse_partial={ii:[ti for ti,ids in partial.items() if ii in ids] for ii in range(len(invoices))}
    for ti,ids in partial.items():
        if not ids: continue
        if len(ids)!=1:
            matches.append({'transaction_index':ti,'invoice_index':None,'status':'review','reasons':['存在多个候选发票，请人工选择'],'candidate_indices':ids});continue
        ii=ids[0]
        if len(reverse_partial[ii])!=1:
            matches.append({'transaction_index':ti,'invoice_index':ii,'status':'review','reasons':['同一发票对应多笔候选交易，请人工确认']});continue
        reasons=compare(transactions[ti],invoices[ii],account_name,account_needs_review)
        matches.append({'transaction_index':ti,'invoice_index':ii,'status':'review','reasons':reasons or ['需要人工确认配对']});used.add(ii)
    # Classification is based on the actual invoice and declared business use.
    # Pairing a payment with an invoice does not establish income or expense.
    import account_rules
    def pending(basis):
        return {'account_code':None,'account_name':None,'basis':basis,
                'review':'请核实业务性质、发票和账套科目。','status':'review',
                'rule_ids':[],'source_urls':[]}
    tx_classes=[pending('未确认唯一匹配发票') for _ in transactions]
    inv_classes=[]
    own=normalize(account_name)
    if conn is not None and client_id:
        client=conn.execute('SELECT * FROM clients WHERE id=? AND deleted=0',(client_id,)).fetchone()
        try:
            profile=json.loads(client['profile_json'] or '{}') if client else {}
            full_name=profile.get('enterprise_name','')
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            profile={}
            full_name=''
        if not client or own not in {normalize(client['name']),normalize(full_name)}:
            own=''  # do not infer buying/selling role for the wrong book
    for inv in invoices:
        seller,buyer=normalize(inv.get('seller')),normalize(inv.get('buyer'))
        if not own or not seller or not buyer or seller==buyer or (seller!=own and buyer!=own):
            inv_classes.append(pending('无法确认本方买卖角色'))
        else:
            inv_classes.append(account_rules.classify(conn,client_id,inv,'sale' if seller==own else 'purchase'))
    for m in matches:
        ti,ii=m['transaction_index'],m.get('invoice_index')
        if m['status']=='matched' and ii is not None:
            inv=invoices[ii]
            role='sale' if normalize(inv.get('seller'))==own else 'purchase'
            if inv_classes[ii]['basis']=='无法确认本方买卖角色':
                tx_classes[ti]=dict(inv_classes[ii])
            else:
                tx_classes[ti]=account_rules.classify(conn,client_id,dict(inv,transaction_note=transactions[ti].get('note','')),role)
        else:
            tx_classes[ti]=pending('发票配对存在差异或多个候选')
    if conn is not None and client_id and own and client:
        import reconciliation_rules
        industry=client['industry'] if 'industry' in client.keys() else ''
        receipt_notes={}
        for pair in receipt_matches['matches']:
            if pair['status']=='matched':
                receipt=receipts[pair['receipt_index']]
                if normalize(receipt.get('own_name')) in ('',normalize(client['name']),normalize(full_name)):
                    receipt_notes[pair['transaction_index']]='；'.join(normalize(receipt.get(k)) for k in ('note','purpose'))
        suggestions=reconciliation_rules.suggest(transactions,invoices,matches,industry,fen,receipt_notes)
        for index,suggestion in enumerate(suggestions):
            # Recommendations use chart names; their codes follow this book's
            # standard rather than reusing a small-enterprise code verbatim.
            if suggestion.get('account_name'):
                account=_suggested_business_root(conn,client_id,suggestion)
                suggestion=dict(suggestion,account_code=account['code'] if account else None)
            if suggestion['status']=='suggested':
                tx_classes[index]=dict(suggestion)
            tx_classes[index]['rule_suggestion']=suggestion
    company_receipts=[]
    if own and conn is not None and client_id and client:
        from client_profile import DEFAULTS
        from reconciliation_advances import company_receipt
        client_details={'name':client['name'],'enterprise_name':full_name,
                        'enterprise_form':profile.get('enterprise_form',DEFAULTS['enterprise_form'])}
        company_receipts=[ti for ti,t in enumerate(transactions) if company_receipt(t,client_details)]
    return {'matches':sorted(matches,key=lambda m:m['transaction_index']),'receipt_matches':receipt_matches,'unmatched_transactions':[ti for ti,ids in partial.items() if not ids],'unmatched_invoices':[ii for ii in range(len(invoices)) if ii not in used], 'classification_transactions':tx_classes, 'classification_invoices':inv_classes,
            'company_receipts':company_receipts,
            'multi_invoice_candidates':multi_invoice_candidates(transactions,invoices,account_name,protected,done)}

def files_from(item):
    if not isinstance(item,dict): raise ApiError(400,'文件格式错误')
    name=str(item.get('name',''))
    if not name or len(name)>200 or '/' in name or '\\' in name or ':' in name: raise ApiError(400,'文件名无效')
    try: data=base64.b64decode(item.get('content_base64',''),validate=True)
    except (binascii.Error,TypeError,ValueError): raise ApiError(400,'文件编码无效')
    if not data: raise ApiError(400,'文件为空')
    ext=Path(name).suffix.lower()
    if ext!='.zip':
        if ext not in ('.pdf','.jpg','.jpeg','.png'): raise ApiError(400,'仅支持 PDF、图片或 ZIP')
        return [(name,data)]
    output=[]; total=0; seen=set()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries=z.infolist()
            if len(entries)>MAX_FILES: raise ApiError(400,'压缩包文件数量超过 30')
            for e in entries:
                if e.is_dir(): continue
                parts=e.filename.replace('\\','/').split('/')
                if any(p in ('..','') for p in parts) or e.filename.startswith('/') or ':' in e.filename or e.flag_bits & 1: raise ApiError(400,'压缩包含不安全路径或加密文件')
                if e.filename in seen: raise ApiError(400,'压缩包含同名成员，请整理后重新上传')
                seen.add(e.filename)
                total+=e.file_size
                if total>MAX_EXPANDED or e.file_size>10*1024*1024 or e.file_size>max(e.compress_size,1)*100: raise ApiError(400,'压缩包解压后过大')
                if Path(e.filename).suffix.lower() not in ('.pdf','.jpg','.jpeg','.png'): continue
                output.append((name+' / '+e.filename,z.read(e)))
    except (zipfile.BadZipFile,RuntimeError): raise ApiError(400,'压缩包无效或无法解压')
    return output

def extract_pdf(data):
    if not data.startswith(b'%PDF-'): return '', 'PDF 文件内容无效'
    try:
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data))
        if len(reader.pages)>30: return '', 'PDF 超过 30 页'
        return '\n'.join(page.extract_text() or '' for page in reader.pages), ''
    except ImportError: return '', '当前 Python 未安装 PDF 文本提取组件，请手工录入'
    except Exception: return '', 'PDF 无法提取文字，可能是扫描件或文件损坏'

STATEMENT_ROW=re.compile(r'(\d{4})(\d{2})(\d{2}) \d{2}:\d{2}:\d{2}')

def parse_statement_text(text, source):
    """Parse a text-layer bank statement (e.g. ICBC 账户明细清单); OCR is the fallback."""
    if '借/贷' not in text: return [],''
    own=re.search(r'账号[：:]\s*(\d{6,32})',text)
    holder=re.search(r'本方账号户名[：:]\s*(\S+)',text)
    if not own: return [],''
    starts=list(STATEMENT_ROW.finditer(text))
    pattern=re.compile(re.escape(own[1])+r'(?P<cp>\D*?)\s*(?P<cpacct>\d+)\s*(?P<bank>.*?)\s*\d{9}(?P<dir>[借贷])\s*(?P<amt>[\d,]+\.\d{2})\s*(?P<memo>.*?)\s*-?[\d,]+\.\d{2}$')
    rows=[]
    for index,start in enumerate(starts):
        chunk=text[start.end():starts[index+1].start() if index+1<len(starts) else len(text)]
        chunk=re.sub(r'\n(第\d+页|重要提示|合计|总计)[\s\S]*$','',chunk)
        found=pattern.match(chunk.replace('\n','').strip())
        if not found: return [],''
        value=Decimal(found['amt'].replace(',',''))*(-1 if found['dir']=='借' else 1)
        rows.append({'date':f'{start[1]}-{start[2]}-{start[3]}', 'amount':format(value,'.2f'), 'counterparty':found['cp'].strip(), 'note':found['memo'].strip(), 'source':source, 'needs_review':True, 'direction_needs_review':False})
    return rows,(holder[1].strip() if holder else '')

def is_bank_receipt(text):
    return '回单编号' in text and '回单' in text and '发票号码' not in text

RECEIPT_DIRECTION=re.compile(r'回单\s*[（(]\s*(收款|付款)\s*[)）]')

def parse_receipt(text, source):
    """Parse one bank electronic receipt (e.g. ICBC 业务回单) into a signed record."""
    field=lambda label: (re.search(label+r'[：:]([^\s：:]*)',text) or [None,''])[1].strip()
    direction=RECEIPT_DIRECTION.search(text)
    day=re.search(r'日期[：:]\s*(\d{4})-?(\d{2})-?(\d{2})',text)
    value=re.search(r'小写[：:]\s*[¥￥]?\s*([\d,]+\.\d{2})',text)
    if not (direction and day and value): return None
    incoming=direction[1]=='收款'
    amount=Decimal(value[1].replace(',',''))*(1 if incoming else -1)
    memo=field('摘要')
    receipt={'number':field('回单编号'), 'date':f'{day[1]}-{day[2]}-{day[3]}', 'amount':format(amount,'.2f'),
            'counterparty':field('付款人户名' if incoming else '收款人户名'),
            'own_name':field('收款人户名' if incoming else '付款人户名'), 'note':memo, 'source':source}
    # Keep the original note stable for matching old receipt-derived transactions.
    # Extra electronic-receipt fields can contain spaces or share a printed line.
    details=re.findall(r'(?:摘要|用途|附言|备注)[：:][ \t]*(.*?)(?=[ \t]+[^\s：:]+[：:]|\r?$)',text,re.M)
    purpose='；'.join(value.strip() for value in details if value.strip() and value.strip()!=memo)
    if purpose: receipt['purpose']=purpose
    return receipt

def match_receipts(transactions, receipts):
    """Pair receipts with statement rows on date, signed amount and counterparty."""
    if not isinstance(receipts,list) or len(receipts)>500 or any(not isinstance(x,dict) for x in receipts): raise ApiError(400,'回单记录格式无效')
    key=lambda x:(normalize(x.get('date')),fen(x.get('amount')))
    full=lambda t,r:key(t)==key(r) and key(r)[1] is not None and normalize(t.get('counterparty'))==normalize(r.get('counterparty'))
    result=[]; used=set()
    for ti,t in enumerate(transactions):
        if not isinstance(t,dict): continue
        exact=[ri for ri,r in enumerate(receipts) if full(t,r)]
        if len(exact)==1 and sum(full(x,receipts[exact[0]]) for x in transactions if isinstance(x,dict))==1:
            result.append({'transaction_index':ti,'receipt_index':exact[0],'status':'matched','reasons':[]}); used.add(exact[0]); continue
        if exact:
            result.append({'transaction_index':ti,'receipt_index':None,'status':'review','reasons':['存在多张完全一致的回单，请人工核对'],'candidate_indices':exact}); continue
        near=[ri for ri,r in enumerate(receipts) if key(t)==key(r) and key(r)[1] is not None]
        if near:
            result.append({'transaction_index':ti,'receipt_index':near[0] if len(near)==1 else None,'status':'review','reasons':['日期和金额一致，但交易对方与回单不一致'],'candidate_indices':near})
        else:
            result.append({'transaction_index':ti,'receipt_index':None,'status':'missing','reasons':['未找到对应回单']})
    return {'matches':result,'unmatched_receipts':[ri for ri in range(len(receipts)) if ri not in used and not any(ri in m.get('candidate_indices',[]) for m in result)]}

def parse_invoice(text, source):
    # Conservative field extraction: missing data never implies a match.
    compact=re.sub(r'[ \t]+','',text)
    number=re.search(r'发票号码[：:]?(\d{8,24})',compact)
    date=re.search(r'开票日期[：:]?(\d{4})年(\d{1,2})月(\d{1,2})日',compact)
    total=re.search(r'价税合计[\s\S]{0,100}?(?:小写|[¥￥])\s*[：:]?[¥￥]?\s*([\d,]+\.\d{2})',compact)
    names=re.findall(r'名称[：:]\s*([^\n\r]{2,100}?)(?=统一社会|纳税人识别号|名称[：:]|\n|$)',compact)
    buyer=names[0].strip() if len(names)>0 else ''
    seller=names[1].strip() if len(names)>1 else ''
    from invoice_details import extract_details
    details=extract_details(text)
    if not any((number,date,total,buyer,seller,*details.values())): return None
    return {'date':f'{date[1]}-{int(date[2]):02d}-{int(date[3]):02d}' if date else '', 'number':number[1] if number else '', 'amount':total[1].replace(',','') if total else '', 'buyer':buyer, 'seller':seller, 'source':source, 'needs_review':True, **details}

class OCRWorker:
    """One isolated OCR process per request, with a timeout for every page."""
    timeout=90

    def __init__(self):
        self.py=Path(__file__).resolve().parent/'runtime/python/python.exe'
        if not self.py.is_file():
            self.py=Path.home()/'AppData/Local/Programs/Python/Python312/python.exe'
        self.script=Path(__file__).with_name('reconciliation_ocr.py')
        self.proc=None
        self.lines=None
        self.stderr_state=None
        self.stderr_thread=None

    def close(self):
        proc=self.proc
        self.proc=None
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for stream in (proc.stdin,proc.stdout,getattr(proc,'stderr',None)):
                if stream is None: continue
                try: stream.close()
                except (OSError, ValueError): pass

    def run(self,image,kind,_memory_retry=False):
        if not self.py.is_file() or not self.script.is_file():
            return None,'本机 OCR 组件不可用，请手工录入'
        try:
            if self.proc is None:
                flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                env={**os.environ,**{key:'1' for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
                proc=subprocess.Popen([str(self.py),'-X','utf8',str(self.script),'--worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',bufsize=1,creationflags=flags,env=env)
                self.proc=proc
                lines=queue.Queue()
                self.lines=lines
                stderr_state={}
                self.stderr_state=stderr_state
                def read_errors():
                    # Retain only a category, never a native traceback, path or
                    # document content; also drain stderr to avoid pipe stalls.
                    from reconciliation_ocr import error_result
                    try:
                        while chunk:=proc.stderr.readline(4096):
                            code=error_result(RuntimeError(chunk))['error_code']
                            if code=='memory_allocation': stderr_state['code']=code
                    except (OSError,ValueError):
                        pass
                stderr_thread=threading.Thread(target=read_errors,daemon=True)
                self.stderr_thread=stderr_thread
                stderr_thread.start()
                def read_lines():
                    try:
                        for line in proc.stdout: lines.put(line)
                    finally: lines.put(None)
                threading.Thread(target=read_lines,daemon=True).start()
            request={'path':str(image.resolve()),'kind':kind}
            if _memory_retry: request['low_memory']=True
            if self.stderr_state is not None: self.stderr_state.clear()
            self.proc.stdin.write(json.dumps(request,ensure_ascii=False)+'\n')
            self.proc.stdin.flush()
            line=self.lines.get(timeout=self.timeout)
            if line is None:
                if self.stderr_thread: self.stderr_thread.join(timeout=.5)
                code=(self.stderr_state or {}).get('code')
                try: exit_code=self.proc.wait(timeout=.5)
                except subprocess.TimeoutExpired: exit_code=self.proc.poll()
                self.close()
                import logging
                logging.getLogger(__name__).warning('OCR native process exited code=%s exit_code=%s',code or 'recognition_failed',exit_code)
                if code=='memory_allocation':
                    if not _memory_retry: return self.run(image,kind,_memory_retry=True)
                    from reconciliation_ocr import ERROR_MESSAGES
                    return None,ERROR_MESSAGES[code]
                raise ValueError('worker exited')
            result=json.loads(line)
            if not isinstance(result,dict): raise ValueError('invalid worker response')
            if result.get('ok') is not True:
                # The worker's code is the only data trusted here; never echo
                # exception messages, paths, OCR content, or unknown codes.
                from reconciliation_ocr import ERROR_MESSAGES
                code=result.get('error_code')
                if code not in ERROR_MESSAGES: code='recognition_failed'
                diagnostic=result.get('diagnostic')
                diagnostic=diagnostic if isinstance(diagnostic,dict) else {}
                stage=diagnostic.get('stage')
                if stage not in ('initialize','recognize'): stage='unknown'
                attempts=diagnostic.get('attempts')
                if type(attempts) is not int or attempts not in (1,2): attempts=0
                exception_type=diagnostic.get('exception_type')
                if exception_type not in ('MemoryError','RuntimeError','ValueError','ImportError','OSError'): exception_type='unknown'
                import logging
                logging.getLogger(__name__).warning('OCR failure code=%s stage=%s attempts=%d exception_type=%s',code,stage,attempts,exception_type)
                if code not in ('no_text','no_fields','invalid_image'):
                    self.close()  # Discard a failed native session before the next page.
                return None,ERROR_MESSAGES[code]
            return result,''
        except queue.Empty:
            self.close()
            return None,'OCR 识别超时，请检查文件或手工补录'
        except (OSError,ValueError,TypeError,UnicodeError):
            self.close()
            return None,'OCR 不可用或识别失败，请检查文件或手工补录'


PDFIUM_RENDER='''import sys, pypdfium2
with pypdfium2.PdfDocument(sys.argv[1]) as pdf:
    for i in range(min(len(pdf),10)):
        page=pdf[i]
        bitmap=page.render(scale=130/72)
        image=bitmap.to_pil()
        try:
            image.save('%s-%02d.png'%(sys.argv[2],i+1))
        finally:
            image.close(); bitmap.close(); page.close()
'''


def ocr_file(data, ext, kind, source, worker=None, page_progress=None, receipt_records=None):
    """Recognize a file, retaining completed pages if a later page fails."""
    owned=worker is None
    worker=worker or OCRWorker()
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    try:
        with tempfile.TemporaryDirectory(prefix='ydz-reconcile-') as folder:
            folder=Path(folder); original=folder/('input'+ext); original.write_bytes(data)
            images=[original]
            if ext=='.pdf':
                converter=shutil.which('pdftoppm')
                if converter:
                    command=[converter,'-f','1','-l','10','-r','130','-png',str(original),str(folder/'page')]
                elif worker.py.is_file():
                    command=[str(worker.py),'-X','utf8','-c',PDFIUM_RENDER,str(original),str(folder/'page')]
                else: return [],'本机 PDF 图像转换组件不可用，请手工录入',[]
                try:
                    converted=subprocess.run(command,capture_output=True,timeout=60,creationflags=flags)
                    images=sorted(folder.glob('page-*.png'))
                    if converted.returncode or not images: return [],'PDF 图像转换失败，请手工录入',[]
                except (OSError,subprocess.TimeoutExpired): return [],'PDF 图像转换失败或超时，请手工录入',[]
            found=[]; accounts=[]; issues=[]
            for page,image in enumerate(images,1):
                prefix='第 %d 页：'%page if ext=='.pdf' else ''
                page_started=time.monotonic()
                result,error=worker.run(image,'bank' if kind=='statement' else 'invoice')
                if page_progress:
                    page_progress(page, time.monotonic()-page_started, result.get('orientation_candidates_tried') if isinstance(result,dict) else None)
                if error:
                    issues.append(prefix+error)
                    # Keep completed pages and attempt subsequent pages independently.
                    continue
                fields=result.get('fields') if isinstance(result,dict) else None
                if not isinstance(fields,dict):
                    issues.append(prefix+'OCR 返回的数据无效')
                    continue
                if result.get('memory_recovery'):
                    issues.append(prefix+'OCR 已降低分辨率重试，请对照原件核对识别结果')
                before=len(found)
                page_receipts=[]
                if kind=='statement':
                    if fields.get('account_name'): accounts.append(str(fields['account_name']).strip())
                    for row in fields.get('receipts',[]):
                        if not isinstance(row,dict): continue
                        receipt={key:row.get(key) or '' for key in ('number','date','amount','counterparty','own_name','note')}
                        if row.get('purpose'): receipt['purpose']=row['purpose']
                        receipt.update(source=source,needs_review=True,direction_needs_review=row.get('direction_needs_review',True) is not False)
                        page_receipts.append(receipt)
                        if receipt['own_name']: accounts.append(receipt['own_name'])
                        issues.extend(prefix+warning for warning in row.get('warnings',[]) if isinstance(warning,str))
                    if receipt_records is not None:
                        receipt_records.extend(page_receipts)
                    else:
                        found.extend({'date':row['date'],'amount':row['amount'],'counterparty':row['counterparty'],'note':row['note'],'source':source,'needs_review':True,'direction_needs_review':row['direction_needs_review']} for row in page_receipts)
                    for row in fields.get('transactions',[]):
                        if isinstance(row,dict):
                            found.append({'date':row.get('date') or '', 'amount':row.get('amount') or '', 'counterparty':row.get('counterparty') or '', 'note':row.get('description') or '', 'source':source, 'needs_review':True, 'direction_needs_review':row.get('direction_needs_review',True) is not False})
                else:
                    record={'date':fields.get('invoice_date') or '', 'amount':fields.get('total_amount') or '', 'seller':fields.get('seller_name') or '', 'buyer':fields.get('buyer_name') or '', 'number':fields.get('invoice_number') or '', 'source':source, 'needs_review':True, **{k:fields.get(k) or '' for k in ('item_name','net_amount','tax_amount','business_purpose','invoice_type')}}
                    if any(record[k] for k in ('date','amount','seller','buyer','number','item_name','net_amount','tax_amount')):
                        found.append(record)
                    if result.get('orientation_confident') is False:
                        issues.append(prefix+'发票方向或内容无法确定，请检查原件')
                if len(found)==before and not page_receipts:
                    issues.append(prefix+'未识别到'+('交易记录或回单字段' if kind=='statement' else '发票字段')+'，请检查文件或手工录入')
            return found,'；'.join(dict.fromkeys(issues)),accounts
    finally:
        if owned: worker.close()


def upload_groups(payload):
    """Normalize new multi-statement uploads and legacy single-file requests."""
    if 'statements' in payload:
        statements=payload['statements']
        if payload.get('statement'): raise ApiError(400,'请勿同时提交单份和多份交易清单')
    else:
        statements=[payload['statement']] if payload.get('statement') else []
    invoices=payload.get('invoices',[])
    if not isinstance(statements,list) or len(statements)>MAX_FILES: raise ApiError(400,'交易清单文件数量无效')
    if not isinstance(invoices,list) or len(invoices)>MAX_FILES: raise ApiError(400,'发票文件数量无效')
    if len(statements)+len(invoices)>MAX_FILES: raise ApiError(400,'文件数量超过 30')
    return [('statement',item) for item in statements]+[('invoice',item) for item in invoices]


def parse(payload, progress=None):
    tx=[]; invoices=[]; warnings=[]; accounts=[]; receipts=[]
    groups=upload_groups(payload)
    count=0
    worker=OCRWorker()
    try:
        for kind,item in groups:
            files=files_from(item);count+=len(files)
            if count>MAX_FILES: raise ApiError(400,'文件数量超过 30')
            if not files: warnings.append(str(item.get('name','ZIP'))+'：未找到可处理文件')
            for name,data in files:
                if progress: progress('start', name=name, kind=kind)
                warning_start=len(warnings)
                records_start=len(tx)+len(invoices)+len(receipts)
                def finish_file():
                    file_warnings=list(dict.fromkeys(warnings[warning_start:]))
                    warnings[warning_start:]=file_warnings
                    recognized_count=len(tx)+len(invoices)+len(receipts)-records_start
                    status='failed' if not recognized_count else ('partial' if file_warnings else 'completed')
                    if progress:
                        progress('end',status=status,recognized_count=recognized_count,warnings=file_warnings)
                ext=Path(name).suffix.lower()
                text,error=extract_pdf(data) if ext=='.pdf' else ('','')
                if kind=='statement' and text.strip():
                    if '出账' in text and '入账' in text:
                        from bank_statement_pdf import parse_statement_pdf
                        table=parse_statement_pdf(data,name)
                        if table is not None:
                            tx.extend(table['transactions']); accounts.append(table['account_name'])
                            warnings.extend(name+'：'+message for message in table['warnings'])
                            if not table['account_name']: warnings.append(name+'：未识别到银行户名，请检查并填写本方公司名称')
                            if not table['transactions']: warnings.append(name+'：未识别到交易记录，请检查文件或手工录入')
                            finish_file()
                            continue
                    rows,holder=parse_statement_text(text,name)
                    if rows:
                        tx.extend(rows); accounts.append(holder)
                        if not holder: warnings.append(name+'：未识别到银行户名，请检查并填写本方公司名称')
                        finish_file()
                        continue
                if is_bank_receipt(text):
                    # Several electronic receipts can share a PDF. Keep each
                    # transaction's remarks and identifiers inside its own block.
                    starts=[m.start() for m in RECEIPT_DIRECTION.finditer(text)]
                    cuts=[0,*starts[1:],len(text)]
                    for start,end in zip(cuts,cuts[1:]):
                        receipt=parse_receipt(text[start:end],name)
                        if receipt: receipts.append(receipt)
                        else: warnings.append(name+'：银行回单字段识别不完整，请手工核对')
                    finish_file()
                    continue
                record=parse_invoice(text,name) if kind=='invoice' and text.strip() else None
                if kind=='invoice' and ext=='.pdf' and text.strip() and not (record and all(record.get(k) for k in ('number','date','amount','buyer','seller'))):
                    from invoice_pdf import parse_invoice_pdf
                    record=parse_invoice_pdf(data,name) or record
                if record and all(record.get(k) for k in ('number','date','amount','buyer','seller')):
                    invoices.append(record)
                    finish_file()
                    continue
                if ext=='.pdf' and not data.startswith(b'%PDF-'):
                    warnings.append(name+'：PDF 内容无效')
                    finish_file()
                    continue
                page_callback=(lambda page,elapsed,passes: progress('page', page=page, elapsed_seconds=elapsed, orientation_passes=passes)) if progress else None
                receipt_start=len(receipts)
                entries,ocr_error,read_accounts=ocr_file(data,ext,kind,name,worker,page_callback,receipt_records=receipts)
                accounts.extend(read_accounts)
                if kind=='statement': tx.extend(entries)
                else: invoices.extend(entries)
                if ocr_error: warnings.append(name+'：'+ocr_error)
                if not entries and len(receipts)==receipt_start:
                    if not ocr_error:
                        warnings.append(name+'：未识别到'+('交易记录' if kind=='statement' else '发票字段')+'，请检查文件或手工录入')
                elif kind=='invoice':
                    labels={'number':'发票号码','date':'开票日期','amount':'价税合计','buyer':'购买方','seller':'销售方'}
                    for row in entries:
                        missing=[label for key,label in labels.items() if not normalize(row.get(key))]
                        if missing: warnings.append(name+'：发票缺少'+ '、'.join(missing)+'，请检查原件并补充')
                else:
                    if not any(read_accounts): warnings.append(name+'：未识别到银行户名，请检查并填写本方公司名称')
                    unclear=[str(index) for index,row in enumerate(entries,1) if row.get('direction_needs_review')]
                    if unclear: warnings.append(name+'：第 '+ '、'.join(unclear)+' 条收付款方向无法确认，请对照原件确认金额正负')
                    # A blank counterparty can be legitimate (e.g. bank fees).
                    # Surface only fields required for interpreting every transaction.
                    for key,label in (('date','交易日期'),('amount','金额')):
                        missing=[str(index) for index,row in enumerate(entries,1) if not normalize(row.get(key))]
                        if missing: warnings.append(name+'：第 '+ '、'.join(missing)+' 条交易缺少'+label+'，请检查原件')
                if ext=='.pdf':
                    try:
                        from pypdf import PdfReader
                        page_count=len(PdfReader(io.BytesIO(data)).pages)
                    except Exception:
                        page_count=None
                    if page_count is None:
                        warnings.append(name+'：无法确定 PDF 页数，扫描件最多识别前 10 页，请检查是否有遗漏')
                    elif page_count>10:
                        warnings.append(name+'：扫描 PDF 仅识别前 10 页，后续页面未处理')
                finish_file()
    finally:
        worker.close()
    transactions_from_receipts=bool(receipts and not tx)
    if transactions_from_receipts:
        tx=[{'date':r['date'],'amount':r['amount'],'counterparty':r['counterparty'],'note':r['note'],'source':r['source'],'needs_review':True,'direction_needs_review':r.get('direction_needs_review',False)} for r in receipts]
        accounts.extend(r['own_name'] for r in receipts)
        warnings.append('未提供收支交易清单，已按 %d 张银行回单生成交易记录'%len(receipts))
    names=set(x for x in accounts if x)
    if len(names)>1: warnings.append('交易清单页识别到不同户名，请人工核对并填写')
    result={'transactions':tx,'invoices':invoices,'receipts':receipts,'warnings':warnings,'account_name':next(iter(names)) if len(names)==1 else ''}
    if payload.get('include_bank_metadata') is True:
        result.update(account_names=sorted(names),transactions_from_receipts=transactions_from_receipts)
    return result

def clean_row(row, keys):
    if not isinstance(row,dict): raise ApiError(400,'记录格式错误')
    out={}
    for k in keys:
        v=row.get(k,'')
        if not isinstance(v,(str,int,float)) or len(str(v))>500: raise ApiError(400,'记录字段无效')
        out[k]=str(v).strip()
    return out

def save(conn,payload,user,_in_transaction=False):
    try: cid=int(payload.get('client_id'))
    except (TypeError,ValueError): raise ApiError(400,'请先选择客户')
    client=conn.execute('SELECT id,name,profile_json FROM clients WHERE id=? AND deleted=0',(cid,)).fetchone()
    if not client: raise ApiError(400,'客户不存在')
    raw_t,raw_i=payload.get('transaction'),payload.get('invoice')
    if isinstance(raw_t,dict) and raw_t.get('direction_needs_review'):
        raise ApiError(400,'请先核实并确认流水收付款方向')
    t=clean_row(raw_t,('date','amount','counterparty','note','source'))
    invoice_keys=('date','amount','seller','buyer','number','source','item_name','net_amount','tax_amount','business_purpose','invoice_type')
    i=clean_row(raw_i,invoice_keys)
    t['needs_review']=bool(raw_t.get('needs_review',False));i['needs_review']=bool(raw_i.get('needs_review',False))
    account=str(payload.get('account_name','')).strip()
    try: full_name=json.loads(client['profile_json'] or '{}').get('enterprise_name','')
    except (ValueError,AttributeError,TypeError): full_name=''
    if not normalize(account) or normalize(account) not in {normalize(client['name']),normalize(full_name)}: raise ApiError(400,'本方名称必须与所选客户名称或企业全称一致')
    if not i['number'] or len(i['number'])>80: raise ApiError(400,'请填写发票号码，用于防止重复')
    if not t['date'] or not t['counterparty'] or fen(t['amount']) is None or not i['date'] or not i['seller'] or not i['buyer'] or fen(i['amount']) is None: raise ApiError(400,'请完整填写交易和发票信息')
    account_needs_review=payload.get('account_needs_review',False)
    if not isinstance(account_needs_review,bool): raise ApiError(400,'户名复核状态无效')
    reasons=compare(t,i,account,account_needs_review)
    batch=payload.get('batch')
    if payload.get('manual_pair') is True: reasons.append('人工指定配对')
    elif isinstance(batch,dict):
        txs,ivs=batch.get('transactions'),batch.get('invoices')
        ti,ii=batch.get('transaction_index'),batch.get('invoice_index')
        if not isinstance(txs,list) or not isinstance(ivs,list) or isinstance(ti,bool) or isinstance(ii,bool) or not isinstance(ti,int) or not isinstance(ii,int) or ti<0 or ii<0 or ti>=len(txs) or ii>=len(ivs): raise ApiError(400,'核对批次无效')
        if not isinstance(txs[ti],dict) or not isinstance(ivs[ii],dict): raise ApiError(400,'核对批次无效')
        if any(str(txs[ti].get(k,'')).strip()!=t[k] for k in ('date','amount','counterparty','note','source')) or any(str(ivs[ii].get(k,'')).strip()!=i[k] for k in invoice_keys): raise ApiError(400,'核对批次与提交记录不一致，请重新核对')
        if bool(txs[ti].get('needs_review'))!=t['needs_review'] or bool(ivs[ii].get('needs_review'))!=i['needs_review']: raise ApiError(400,'复核状态已改变，请重新核对')
        result=match(txs,ivs,account,account_needs_review)
        if not any(m['transaction_index']==ti and m['invoice_index']==ii and m['status']=='matched' for m in result['matches']): reasons.append('配对并非唯一匹配，需要人工确认')
    else: reasons.append('缺少完整核对批次，需要人工确认')
    note=str(payload.get('review_note','')).strip()
    if len(note)>500: raise ApiError(400,'审核意见过长')
    if reasons and (payload.get('approved_review') is not True or not note): raise ApiError(400,'存在差异，请确认人工审核并填写原因',reasons)
    status='reviewed' if reasons else 'matched'
    transaction_key=hashlib.sha256(json.dumps([t['date'],fen(t['amount']),t['counterparty'],t['note']],ensure_ascii=False).encode()).hexdigest()
    import finance
    import source_archive
    source_archive.validate(conn,cid,payload.get('archive_id'))
    voucher=payload.get('voucher')
    if voucher is not None and not isinstance(voucher,dict): raise ApiError(400,'凭证内容无效')
    try:
        from contextlib import nullcontext
        with (nullcontext() if _in_transaction else finance._write(conn)):
            cur=conn.execute('INSERT INTO reconciliation_records(client_id,invoice_number,transaction_json,invoice_json,account_name,status,review_note,user_id,created_at,transaction_key) VALUES(?,?,?,?,?,?,?,?,?,?)',(cid,i['number'],json.dumps(t,ensure_ascii=False),json.dumps(i,ensure_ascii=False),account,status,note,user['id'],now(),transaction_key))
            ident=cur.lastrowid
            audit(conn,user,'reconcile_save','reconciliation_records',ident,{'client_id':cid,'status':status})
            posted=_post_confirmed(conn,ident,cid,voucher,user) if voucher is not None else None
            source_archive.attach(conn,cid,payload.get('archive_id'),user,t,i,
                                  record_id=ident,voucher_id=posted['voucher_id'] if posted else None,
                                  basis={'matching_basis':reasons or ['流水日期、金额及本方/对手方与发票唯一匹配'],
                                         'review_note':note,'account_name':account,'manual_pair':payload.get('manual_pair') is True})
    except Exception as e:
        import sqlite3
        if isinstance(e,sqlite3.IntegrityError): raise ApiError(409,'该客户的发票号码或相同交易记录已保存，请勿重复提交')
        raise
    result={'id':ident,'status':status}
    if posted: result.update(posted)
    return result

def _resolve_voucher_lines(conn,cid,lines,transaction,user):
    """Resolve explicitly requested counterparty details within the posting transaction."""
    import finance
    if not isinstance(lines,list) or not 2<=len(lines)<=500:
        raise ApiError(400,'凭证需要2至500条分录')
    resolved=[]
    for line in lines:
        if not isinstance(line,dict): raise ApiError(400,'凭证分录格式错误')
        item=dict(line)
        business_parent=item.pop('business_parent_code',None)
        business_name=item.pop('business_sub_account',None)
        if business_parent is not None:
            if item.get('account_code') or item.get('counterparty_parent_code'):
                raise ApiError(400,'业务明细不能同时指定已有科目和上级科目')
            parent=finance._account(conn,cid,business_parent)
            if finance._cash(parent): raise ApiError(400,'业务明细不能使用货币资金科目')
            name=finance._text(business_name,'业务明细名称',80)
            matches=[dict(row) for row in conn.execute('SELECT * FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? AND name=? ORDER BY code',
                                                     (cid,business_parent+'%',business_parent,name))]
            leaves=[row for row in matches if not conn.execute('SELECT 1 FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? LIMIT 1',
                                                               (cid,row['code']+'%',row['code'])).fetchone()]
            if len(leaves)>1: raise ApiError(409,'业务科目下同名明细不唯一')
            target=leaves[0] if leaves else finance._create_account(conn,cid,{'parent_code':business_parent,'name':name},user)
            item['account_code']=target['code']
        parent_code=item.pop('counterparty_parent_code',None)
        if parent_code is not None:
            if item.get('account_code'): raise ApiError(400,'往来明细不能同时指定已有科目和上级科目')
            if not isinstance(parent_code,str) or not parent_code.strip(): raise ApiError(400,'请选择往来明细的上级科目')
            parent_code=parent_code.strip()
            parent=finance._account(conn,cid,parent_code)
            if parent['report_key'] not in ('receivable','prepaid','other_asset','payable','advance','other_liability'):
                raise ApiError(400,'请选择往来性质的上级科目')
            name=normalize(transaction.get('counterparty'))
            if not name: raise ApiError(400,'请先核实流水对手方名称')
            matches=conn.execute('SELECT * FROM accounts WHERE client_id=? AND code LIKE ? AND length(code)=? AND name=? ORDER BY code',
                                 (cid,parent_code+'%',len(parent_code)+2,name)).fetchall()
            if len(matches)>1: raise ApiError(409,'该上级科目下有多个同名明细，请先核对')
            if matches:
                account=dict(matches[0])
                if conn.execute('SELECT 1 FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? LIMIT 1',(cid,account['code']+'%',account['code'])).fetchone():
                    raise ApiError(409,'同名往来科目不是末级科目，请先核对')
            else:
                account=finance._create_account(conn,cid,{'parent_code':parent_code,'name':name},user)
            item['account_code']=account['code']
        resolved.append(item)
    return resolved

def _transaction_key(transaction):
    return hashlib.sha256(json.dumps([normalize(transaction.get('date')),fen(transaction.get('amount')),
                                     normalize(transaction.get('counterparty')),normalize(transaction.get('note'))],ensure_ascii=False).encode()).hexdigest()

def _posting_result(conn,cid,voucher_id,already_posted=False,transaction=None):
    """Read the actual saved voucher, including repeat calls and linked receipts."""
    import finance
    voucher=conn.execute("SELECT * FROM vouchers WHERE id=? AND client_id=? AND status='active'",(voucher_id,cid)).fetchone()
    if not voucher: raise ApiError(409,'对应凭证不存在或已作废')
    lines=[dict(row) for row in conn.execute('SELECT l.*,a.name account_name,a.report_key FROM accounting_voucher_lines l JOIN accounts a ON a.client_id=l.client_id AND a.code=l.account_code WHERE l.voucher_id=? ORDER BY l.id',(voucher_id,))]
    if transaction is not None:
        value=fen(transaction.get('amount'))
        cash_net=sum(row['debit_fen']-row['credit_fen'] for row in lines if row['report_key']=='cash')
        if (not value or cash_net!=value or voucher['date']!=normalize(transaction.get('date')) or
                (normalize(voucher['counterparty']) and normalize(voucher['counterparty'])!=normalize(transaction.get('counterparty'))[:200])):
            raise ApiError(409,'已入账凭证的日期、金额或收付方向与本次流水不一致')
    return {'voucher_id':voucher_id,'already_posted':already_posted,'voucher_ref':'记 %s-%04d'%(voucher['period'],voucher['number']),'summary':voucher['summary'],
            'voucher_date':voucher['date'],'period':voucher['period'],'posted_lines':lines}

def _post_confirmed(conn,record_id,cid,voucher,user):
    """Post only user-supplied voucher lines; caller owns the write transaction."""
    import finance
    finance._client(conn,cid)
    record=conn.execute('SELECT * FROM reconciliation_records WHERE id=? AND client_id=?',(record_id,cid)).fetchone()
    if not record: raise ApiError(404,'核对记录不存在或不属于所选账套')
    existing=conn.execute("SELECT id FROM vouchers WHERE client_id=? AND source_type='reconciliation' AND source_id=? AND status='active'",(cid,str(record_id))).fetchone()
    if existing:
        import source_archive
        source_archive.post_record(conn,cid,record_id,existing['id'])
        return _posting_result(conn,cid,existing['id'],True,json.loads(record['transaction_json']))
    if record['status'] not in ('matched','reviewed'): raise ApiError(409,'只有核对完成的记录可以制证')
    if conn.execute("SELECT 1 FROM vouchers WHERE client_id=? AND source_type='reconciliation_invoice' AND source_id=? AND status='active'",(cid,record['invoice_number'])).fetchone():
        raise ApiError(409,'该发票已有公司往来凭证，请查看已保存的核对凭证')
    if not isinstance(voucher,dict): raise ApiError(400,'请填写完整凭证分录')
    date=finance._date(voucher.get('date')); finance._open(conn,cid,date[:7])
    summary=finance._text(voucher.get('summary'),'摘要')
    transaction=json.loads(record['transaction_json'])
    lines=finance._lines(conn,cid,_resolve_voucher_lines(conn,cid,voucher.get('lines'),transaction,user))
    tx=fen(transaction.get('amount'))
    cash=[line for line in lines if line['is_cash']]
    if not tx or finance._cash_net(lines)!=tx:
        raise ApiError(400,'货币资金分录净额必须与流水收付款金额及方向一致')
    note=voucher.get('duplicate_review_note','')
    if not isinstance(note,str) or len(note.strip())>500: raise ApiError(400,'重复核验说明须为不超过500字的文本')
    note=note.strip(); candidates={}
    for line in cash:
        rows=conn.execute('''SELECT DISTINCT v.id voucher_id,v.date,v.number,v.source_type,v.summary
            FROM vouchers v JOIN voucher_lines l ON l.voucher_id=v.id
            WHERE v.client_id=? AND v.status='active' AND v.date IN (?,?) AND l.account_code=? AND l.debit_fen=? AND l.credit_fen=? ORDER BY v.id''',
            (cid,date,transaction.get('date'),line['account_code'],line['debit_fen'],line['credit_fen'])).fetchall()
        for row in rows: candidates[row['voucher_id']]=dict(row)
    if candidates and not note:
        raise ApiError(409,'发现日期、金额及资金科目相同的凭证，请核对是否重复',{'code':'possible_duplicate','candidates':list(candidates.values())})
    vid=finance._insert_voucher(conn,cid,date,summary,lines,user,'reconciliation',record_id,counterparty=normalize(transaction.get('counterparty')))
    import source_archive
    source_archive.post_record(conn,cid,record_id,vid)
    audit(conn,user,'reconcile_post','reconciliation_records',record_id,{'client_id':cid,'voucher_id':vid,'duplicate_candidates':list(candidates.values()),'duplicate_review_note':note if candidates else ''})
    return _posting_result(conn,cid,vid,False,transaction)

def post_confirmed(conn,record_id,payload,user):
    import finance
    if isinstance(payload.get('client_id'),bool): raise ApiError(400,'客户无效')
    try: cid=int(payload.get('client_id'))
    except (TypeError,ValueError): raise ApiError(400,'请先选择客户')
    with finance._write(conn):
        result=_post_confirmed(conn,record_id,cid,payload.get('voucher'),user)
        return {'id':record_id,**result}

def records(conn,client_id):
    try: cid=int(client_id)
    except (ValueError,TypeError): raise ApiError(400,'请先选择客户')
    rows=conn.execute('''SELECT r.*,u.name username,v.id voucher_id,
        (SELECT l.account_code FROM voucher_lines l WHERE l.voucher_id=v.id AND
         (l.account_code LIKE '1122%' OR l.account_code LIKE '2202%') LIMIT 1) counterparty_account_code
        FROM reconciliation_records r JOIN users u ON u.id=r.user_id
        LEFT JOIN vouchers v ON v.client_id=r.client_id AND v.source_type='reconciliation' AND v.source_id=CAST(r.id AS TEXT) AND v.status='active'
        WHERE r.client_id=? ORDER BY r.id DESC LIMIT 500''',(cid,)).fetchall()
    results=[]
    for r in rows:
        metadata=json.loads(r['batch_metadata_json'] or '{}'); transaction=json.loads(r['transaction_json'])
        item={'id':r['id'],'transaction':transaction,'invoice':json.loads(r['invoice_json']),'account_name':r['account_name'],
              'batch_metadata':metadata,'evidence':metadata.get('evidence',{}),'status':r['status'],'review_note':r['review_note'],
              'created_at':r['created_at'],'username':r['username'],'voucher_id':None,'posting_status':'unposted','counterparty_account_code':r['counterparty_account_code']}
        voucher_id=r['voucher_id'] or metadata.get('linked_voucher_id')
        if voucher_id:
            try: item.update(_posting_result(conn,cid,voucher_id,True,transaction),posting_status='posted')
            except ApiError: pass
        import source_archive
        item['archive_ids']=source_archive.archive_ids(conn,cid,r['id'])
        item['archive_id']=item['archive_ids'][-1] if item['archive_ids'] else None
        results.append(item)
    postings,total=_history_postings(conn,cid,results)
    return {'records':results,'postings':postings,'postings_total':total,
            'postings_limit':500,'postings_truncated':total>len(postings)}


def _history_object(value):
    try: result=json.loads(value or '{}')
    except (ValueError,TypeError): return {}
    return result if isinstance(result,dict) else {}


def _history_postings(conn,cid,records):
    """List real source vouchers across periods without creating business rows.

    Receipt posting has no reconciliation_records row. Keep that legacy API
    unchanged and expose the actual postings separately, one item per voucher.
    """
    where="client_id=? AND status='active' AND source_type IN ('reconciliation','bank_receipt','bank_statement','reconciliation_invoice')"
    total=conn.execute('SELECT COUNT(*) FROM vouchers WHERE '+where,(cid,)).fetchone()[0]
    if not total: return [],0
    rows=conn.execute('SELECT v.*, EXISTS(SELECT 1 FROM book_periods p WHERE p.client_id=v.client_id AND p.period>=v.period AND p.closed=1) period_closed FROM vouchers v WHERE '+where+' ORDER BY created_at DESC,id DESC LIMIT 500',(cid,)).fetchall()
    known={item['voucher_id']:item for item in records if item.get('voucher_id')}
    client=conn.execute('SELECT name FROM clients WHERE id=?',(cid,)).fetchone()
    results=[]
    for row in rows:
        vid=row['id']
        item={**_posting_result(conn,cid,vid,True),'status':'posted','posting_status':'posted',
              'source_type':row['source_type'],'source_id':row['source_id'],
              'editable':not row['reviewed'] and not row['period_closed'],
              'username':'','created_at':row['created_at'],'archive_ids':[],'archive_id':None,
              'record_id':None,'transaction':None,'invoice':None,'receipt':None,
              'account_name':client['name'] if client else '', 'transaction_source':''}
        author=conn.execute('SELECT name FROM users WHERE id=?',(row['created_by'],)).fetchone()
        if author: item['username']=author['name']
        links=conn.execute('SELECT archive_id,record_id,final_json,basis_json FROM source_archive_links WHERE client_id=? AND voucher_id=? ORDER BY id',(cid,vid)).fetchall()
        for link in links:
            if link['archive_id'] not in item['archive_ids']: item['archive_ids'].append(link['archive_id'])
            if link['record_id'] and not item['record_id']: item['record_id']=link['record_id']
            final,basis=_history_object(link['final_json']),_history_object(link['basis_json'])
            for key in ('transaction','invoice','receipt'):
                if not item[key] and isinstance(final.get(key),dict) and final[key]:
                    item[key]=final[key]
                    if key=='transaction': item['transaction_source']='archive'
            if basis.get('account_name'): item['account_name']=basis['account_name']
        if item['archive_ids']: item['archive_id']=item['archive_ids'][-1]
        record=known.get(vid)
        if not record and row['source_type']=='reconciliation':
            stored=conn.execute('SELECT * FROM reconciliation_records WHERE client_id=? AND CAST(id AS TEXT)=?',(cid,row['source_id'])).fetchone()
            if stored:
                record={'id':stored['id'],'transaction':_history_object(stored['transaction_json']),
                        'invoice':_history_object(stored['invoice_json']),'account_name':stored['account_name']}
        if record:
            item['record_id']=item['record_id'] or record['id']
            for key in ('transaction','invoice'):
                if not item[key] and isinstance(record.get(key),dict) and record[key]:
                    item[key]=record[key]
                    if key=='transaction': item['transaction_source']='reconciliation_record'
            if record.get('account_name'): item['account_name']=record['account_name']
        if row['source_type']=='bank_receipt' and (not item['transaction'] or not item['receipt']):
            audit_row=conn.execute("SELECT detail FROM audit_logs WHERE action='reconcile_receipt_post' AND entity='vouchers' AND entity_id=? ORDER BY id LIMIT 1",(str(vid),)).fetchone()
            detail=_history_object(audit_row['detail']) if audit_row else {}
            if not item['transaction'] and isinstance(detail.get('transaction'),dict) and detail['transaction']:
                item['transaction']=detail['transaction'];item['transaction_source']='audit'
            if not item['receipt'] and isinstance(detail.get('matched_receipt'),dict): item['receipt']=detail['matched_receipt']
        if row['source_type']=='bank_statement' and not item['transaction']:
            audit_row=conn.execute("SELECT detail FROM audit_logs WHERE action='reconcile_statement_post' AND entity='vouchers' AND entity_id=? ORDER BY id LIMIT 1",(str(vid),)).fetchone()
            detail=_history_object(audit_row['detail']) if audit_row else {}
            if isinstance(detail.get('transaction'),dict) and detail['transaction']:
                item['transaction']=detail['transaction'];item['transaction_source']='audit'
        if row['source_type']=='reconciliation_invoice' and not item['invoice']:
            audit_row=conn.execute("SELECT detail FROM audit_logs WHERE action='reconcile_invoice_post' AND entity='vouchers' AND entity_id=? ORDER BY id LIMIT 1",(str(vid),)).fetchone()
            if audit_row: item['invoice']=_history_object(audit_row['detail']).get('invoice')
        if not item['transaction'] and row['source_type']!='reconciliation_invoice':
            net=sum(line['debit_fen']-line['credit_fen'] for line in item['posted_lines'] if line['report_key']=='cash')
            item['transaction']={'date':row['date'],'amount':format(Decimal(net)/100,'.2f'),
                                 'counterparty':row['counterparty'],'note':row['summary'],'source':''}
            item['transaction_source']='voucher'
        results.append(item)
    return results,total


def _has_children(conn,cid,code):
    return bool(conn.execute('SELECT 1 FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? LIMIT 1',(cid,code+'%',code)).fetchone())


def _cash_leaves(conn,cid):
    import finance
    rows=[dict(row) for row in conn.execute("SELECT * FROM accounts WHERE client_id=? AND report_key='cash' ORDER BY code",(cid,))]
    return [row for row in rows if not _has_children(conn,cid,row['code']) and finance._cash(row)]


def _require_business_leaf(conn,cid,code):
    """A manually chosen business account is used exactly as submitted."""
    import finance
    if not isinstance(code,str) or not code.strip():
        raise ApiError(400,'请选择本账套的末级科目。')
    try: account=finance._account(conn,cid,code.strip())
    except ApiError: raise ApiError(400,'所选科目不在本账套中，请重新选择末级科目。')
    if _has_children(conn,cid,account['code']):
        raise ApiError(400,'科目 %s %s 不是末级科目，请改选其下的具体末级科目。'%(account['code'],account['name']))
    if finance._cash(account):
        raise ApiError(400,'业务科目不能选择库存现金或银行存款。请选择费用、收入或往来末级科目；资金科目请单独选择。')
    if account['report_key']=='prior_adjustment':
        raise ApiError(400,'以前年度损益调整科目不能用于本期入账，请改选其他末级科目。')
    return account


def _require_funding_account(conn,cid,code):
    leaves=_cash_leaves(conn,cid)
    if not leaves:
        raise ApiError(400,'本账套没有可用的银行存款或现金末级科目，请先设置资金科目后再入账。')
    if not isinstance(code,str) or not code.strip():
        if len(leaves)==1: return leaves[0]
        names='、'.join('%s %s'%(row['code'],row['name']) for row in leaves[:8])
        raise ApiError(400,'本账套有多个资金科目，请选择实际收付款账户后再入账：'+names)
    found=next((row for row in leaves if row['code']==code.strip()),None)
    if not found:
        raise ApiError(400,'请选择本账套的银行存款或现金末级科目作为资金科目。')
    return found


def _suggested_business_root(conn,cid,suggestion):
    import finance
    name=normalize(suggestion.get('account_name'))
    # Rules describe small-book semantic roots. Map them through the book's
    # template; identical numeric codes can mean entirely different accounts.
    standard=conn.execute('SELECT standard FROM clients WHERE id=?',(cid,)).fetchone()[0]
    code=str(suggestion.get('account_code') or '')
    rule=next((r for r in finance._template('small2013') if r[0]==code and r[1]==name),None)
    target=next((r for r in finance._template(standard) if rule and r[1:]==rule[1:]),None)
    account=None
    if target:
        row=conn.execute('SELECT * FROM accounts WHERE client_id=? AND code=?',(cid,target[0])).fetchone()
        if row and tuple(row[k] for k in ('direction','category','report_key'))==target[2:] and finance.account_name_matches(conn,cid,row['code'],row['name'],name):
            account=dict(row)
    elif not rule and name:
        named=conn.execute('SELECT * FROM accounts WHERE client_id=? AND name=?',(cid,name)).fetchall()
        if len(named)==1: account=dict(named[0])
    return account


def _recommended_business_account(conn,cid,transaction):
    """Resolve the visible rule suggestion to one existing leaf. Never creates accounts."""
    import finance, reconciliation_rules
    suggestion=reconciliation_rules.suggest([transaction],[],[],'',fen)[0]
    if suggestion.get('status')=='review' or not (suggestion.get('account_name') or suggestion.get('account_code')):
        raise ApiError(400,'当前流水没有可采纳的推荐科目，请手动选择本账套末级科目。推荐不会自动入账。')
    name=normalize(suggestion.get('account_name'))
    account=_suggested_business_root(conn,cid,suggestion)
    label=name or str(suggestion.get('account_code') or '')
    if not account:
        raise ApiError(400,'推荐科目“%s”不在本账套中，请手动选择末级科目。'%label)
    sub=normalize(suggestion.get('sub_account'))
    if sub:
        children=[dict(row) for row in conn.execute('SELECT * FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? AND name=? ORDER BY code',(cid,account['code']+'%',account['code'],sub))]
        leaves=[row for row in children if not _has_children(conn,cid,row['code'])]
        if len(leaves)!=1:
            raise ApiError(400,'推荐明细“%s”在本账套中不存在或不唯一，请手动选择末级科目。系统不会自动新建科目。'%sub)
        account=leaves[0]
    elif _has_children(conn,cid,account['code']):
        raise ApiError(400,'推荐科目 %s %s 不是末级科目，请手动选择其下的具体末级科目。'%(account['code'],account['name']))
    if finance._cash(account) or account['report_key']=='prior_adjustment':
        raise ApiError(400,'推荐科目不能直接作为业务科目入账，请手动选择其他末级科目。')
    return account,suggestion


def _existing_statement_posting(conn,cid,transaction,*,include_receipts=True):
    key=_transaction_key(transaction)
    row=conn.execute("SELECT id FROM vouchers WHERE client_id=? AND source_type='bank_statement' AND source_id=? AND status='active'",(cid,key)).fetchone()
    if row: return _posting_result(conn,cid,row['id'],True,transaction)
    record=conn.execute('SELECT id FROM reconciliation_records WHERE client_id=? AND transaction_key=?',(cid,key)).fetchone()
    if record:
        linked=conn.execute("SELECT id FROM vouchers WHERE client_id=? AND source_type='reconciliation' AND source_id=? AND status='active'",(cid,str(record['id']))).fetchone()
        if linked: return _posting_result(conn,cid,linked['id'],True,transaction)
    if not include_receipts: return None
    rows=conn.execute("SELECT v.id,a.detail FROM vouchers v JOIN audit_logs a ON a.entity='vouchers' AND a.entity_id=CAST(v.id AS TEXT) AND a.action='reconcile_receipt_post' WHERE v.client_id=? AND v.source_type='bank_receipt' AND v.status='active' AND v.date=?",(cid,normalize(transaction.get('date'))))
    for item in rows:
        detail=_history_object(item['detail'])
        if detail.get('transaction_key')==key:
            return _posting_result(conn,cid,item['id'],True,transaction)
    return None


def _reject_matched_document(conn,cid,payload,transaction,account_name):
    """Statement confirmation is only for rows that are not already paired to a document."""
    txs=payload.get('transactions')
    if txs is None: return
    if not isinstance(txs,list) or len(txs)>500 or any(not isinstance(row,dict) for row in txs):
        raise ApiError(400,'流水清单格式无效')
    index=payload.get('transaction_index')
    if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(txs):
        raise ApiError(400,'流水位置无效，请重新核对后再入账。')
    current=txs[index]
    for key in ('date','amount','counterparty','note'):
        if normalize(current.get(key))!=normalize(transaction.get(key)):
            raise ApiError(400,'流水内容已变化，请重新核对后再入账。')
    if current.get('direction_needs_review'):
        raise ApiError(400,'请先确认收付款方向：收款金额填正数，付款金额填负数。')
    invoices=payload.get('invoices') or []
    receipts=payload.get('receipts') or []
    if not isinstance(invoices,list) or not isinstance(receipts,list):
        raise ApiError(400,'发票或回单格式无效')
    matched=match(txs,invoices,account_name,False,conn,cid,receipts)
    if any(row['transaction_index']==index and row['status']=='matched' for row in matched['matches']):
        raise ApiError(409,'该流水已与发票唯一匹配，请按发票核对结果入账，不要重复确认科目。')
    if any(row['transaction_index']==index and row['status']=='matched' for row in matched['receipt_matches']['matches']):
        raise ApiError(409,'该流水已与银行回单匹配，请按回单入账，不要重复确认科目。')


def post_unmatched_statement(conn,payload,user):
    """Post one unmatched statement only after the operator confirms the accounts.

    A rule suggestion never posts by itself. Manual account choices are not
    replaced by that suggestion.
    """
    import finance, source_archive
    if not isinstance(payload,dict): raise ApiError(400,'入账内容无效')
    if payload.get('confirmed') is not True:
        raise ApiError(400,'请先确认分录后再入账。推荐科目不会自动写入账套。')
    mode=payload.get('mode')
    if mode not in ('recommended','manual'):
        raise ApiError(400,'请选择采用推荐科目，或手动选择末级科目。')
    if isinstance(payload.get('client_id'),bool): raise ApiError(400,'客户无效')
    try: cid=int(payload.get('client_id'))
    except (TypeError,ValueError): raise ApiError(400,'请先选择客户账套')
    client=finance._client(conn,cid)
    raw=payload.get('transaction')
    if isinstance(raw,dict) and raw.get('direction_needs_review'):
        raise ApiError(400,'请先确认收付款方向：收款金额填正数，付款金额填负数。')
    transaction=clean_row(raw,('date','amount','counterparty','note','source'))
    if not normalize(transaction.get('counterparty')) and not normalize(transaction.get('note')):
        raise ApiError(400,'请补充交易对方或摘要，作为这条流水的入账依据。')
    amount=fen(transaction.get('amount'))
    if not amount:
        raise ApiError(400,'流水金额无效或为零，请修改后再入账。')
    date=finance._date(transaction.get('date'))
    account_name=normalize(payload.get('account_name'))
    full=normalize(client.get('enterprise_name'))
    if not account_name or account_name not in {normalize(client['name']),full}:
        raise ApiError(400,'本方名称必须与客户名称或企业全称一致')
    note=payload.get('duplicate_review_note','')
    if not isinstance(note,str) or len(note.strip())>500:
        raise ApiError(400,'重复核验说明须为不超过500字的文本')
    note=note.strip()
    source_archive.validate(conn,cid,payload.get('archive_id'))
    _reject_matched_document(conn,cid,payload,transaction,account_name)
    with finance._write(conn):
        finance._open(conn,cid,date[:7])
        existing=_existing_statement_posting(conn,cid,transaction)
        if existing:
            source_archive.attach(conn,cid,payload.get('archive_id'),user,transaction,voucher_id=existing['voucher_id'],
                                  basis={'account_name':account_name,'mode':mode,'already_posted':True})
            return dict(existing,status='posted',mode=mode,reason='该流水已入账，未按本次科目重复生成凭证。')
        if mode=='manual':
            business=_require_business_leaf(conn,cid,payload.get('business_account_code'))
            suggestion=None
        else:
            from reconciliation_advances import company_receipt
            if company_receipt(transaction,client):
                raise ApiError(409,'请重新核对流水，并在生成的公司往来凭证中确认科目')
            business,suggestion=_recommended_business_account(conn,cid,transaction)
            submitted=payload.get('business_account_code')
            if not isinstance(submitted,str) or submitted.strip()!=business['code']:
                raise ApiError(400,'采用推荐时只能使用 %s %s。若要使用其他科目，请改为手动选择；推荐不会覆盖手选科目。'%(business['code'],business['name']))
        bank=_require_funding_account(conn,cid,payload.get('bank_account_code'))
        if bank['code']==business['code']:
            raise ApiError(400,'业务科目与资金科目不能相同。')
        value=abs(amount)
        bank_line=finance._entry(bank['code'],value if amount>0 else 0,value if amount<0 else 0)
        business_line=finance._entry(business['code'],value if amount<0 else 0,value if amount>0 else 0)
        summary=(('收款 ' if amount>0 else '付款 ')+normalize(transaction.get('counterparty'))+' '+normalize(transaction.get('note'))).strip()[:200]
        if 'summary' in payload:
            summary=finance._text(payload['summary'],'摘要')
            for line in (bank_line,business_line): line['summary']=summary
        else:
            for line in (bank_line,business_line): line['summary']=finance.account_category_summary(conn,cid,line)
        lines=[bank_line,business_line] if amount>0 else [business_line,bank_line]
        checked=finance._lines(conn,cid,lines)
        cash_net=sum(line['debit_fen']-line['credit_fen'] for line in checked if line['is_cash'])
        if cash_net!=amount or sum(line['debit_fen'] for line in checked)!=sum(line['credit_fen'] for line in checked):
            raise ApiError(400,'分录借贷或收付方向与流水不一致，凭证未保存。')
        candidates=[dict(row) for row in conn.execute('''SELECT DISTINCT v.id voucher_id,v.date,v.number,v.source_type,v.summary
            FROM vouchers v JOIN voucher_lines l ON l.voucher_id=v.id
            WHERE v.client_id=? AND v.status='active' AND v.date=? AND l.account_code=? AND l.debit_fen=? AND l.credit_fen=?
            ORDER BY v.id''',(cid,date,bank['code'],bank_line['debit_fen'],bank_line['credit_fen']))]
        if candidates and not note:
            raise ApiError(409,'发现日期、金额及资金科目相同的凭证，请核对是否重复',{'code':'possible_duplicate','candidates':candidates})
        key=_transaction_key(transaction)
        vid=finance._insert_voucher(conn,cid,date,summary,checked,user,'bank_statement',key,counterparty=normalize(transaction.get('counterparty')))
        source_archive.attach(conn,cid,payload.get('archive_id'),user,transaction,voucher_id=vid,
                              basis={'account_name':account_name,'mode':mode,'business_account_code':business['code'],
                                     'bank_account_code':bank['code'],'recommendation':suggestion,'duplicate_review_note':note if candidates else ''})
        audit(conn,user,'reconcile_statement_post','vouchers',vid,{
            'client_id':cid,'mode':mode,'business_account_code':business['code'],'bank_account_code':bank['code'],
            'transaction_key':key,'transaction':transaction,'duplicate_candidates':candidates,
            'duplicate_review_note':note if candidates else ''})
        result=_posting_result(conn,cid,vid,False,transaction)
    return dict(result,status='posted',mode=mode,business_account_code=business['code'],bank_account_code=bank['code'],reason='')


def post_record(conn, record_id, payload, user):
    """Post a previously saved reconciliation as a settlement, not invoice recognition."""
    import finance
    if isinstance(payload.get('client_id'),bool): raise ApiError(400,'客户无效')
    try: cid=int(payload.get('client_id'))
    except (TypeError,ValueError): raise ApiError(400,'请先选择客户')
    bank_code=payload.get('bank_account')
    if not isinstance(bank_code,str) or not bank_code.strip(): raise ApiError(400,'请选择银行或现金末级科目')
    bank_code=bank_code.strip()
    note=payload.get('duplicate_review_note','')
    if not isinstance(note,str) or len(note.strip())>500: raise ApiError(400,'重复核验说明须为不超过500字的文本')
    note=note.strip()
    with finance._write(conn):
        finance._client(conn,cid)
        record=conn.execute('SELECT * FROM reconciliation_records WHERE id=? AND client_id=?',(record_id,cid)).fetchone()
        if not record: raise ApiError(404,'核对记录不存在或不属于所选账套')
        existing=conn.execute("SELECT id FROM vouchers WHERE client_id=? AND source_type='reconciliation' AND source_id=? AND status='active'",(cid,str(record_id))).fetchone()
        if existing:
            import source_archive
            source_archive.post_record(conn,cid,record_id,existing['id'])
            lines=conn.execute("SELECT account_code FROM voucher_lines WHERE voucher_id=? AND (account_code LIKE '1122%' OR account_code LIKE '2202%') LIMIT 1",(existing['id'],)).fetchone()
            return {'id':record_id,'voucher_id':existing['id'],'counterparty_account_code':lines['account_code'] if lines else None,'already_posted':True}
        t=json.loads(record['transaction_json']); i=json.loads(record['invoice_json'])
        tx=fen(t.get('amount')); inv=fen(i.get('amount'))
        if tx is None or not tx or inv is None or inv<=0 or abs(tx)!=inv:
            raise ApiError(409,'流水金额与发票价税合计不一致或无效，不能自动入账，请先核对')
        counter=normalize(t.get('counterparty')); account=normalize(record['account_name'])
        expected_counter=normalize(i.get('buyer') if tx>0 else i.get('seller'))
        expected_own=normalize(i.get('seller') if tx>0 else i.get('buyer'))
        if not counter or counter!=expected_counter or not account or account!=expected_own:
            raise ApiError(409,'流水对手方或本方与发票买卖方不一致，不能自动入账，请先核对')
        date=finance._date(t.get('date')); finance._open(conn,cid,date[:7])
        bank=finance._account(conn,cid,bank_code)
        if not finance._cash(bank) or conn.execute('SELECT 1 FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? LIMIT 1',(cid,bank_code+'%',bank_code)).fetchone():
            raise ApiError(400,'请选择本账套的银行或现金末级科目')
        debit,credit=(abs(tx),0) if tx>0 else (0,abs(tx))
        candidates=[dict(x) for x in conn.execute("""SELECT DISTINCT v.id voucher_id,v.date,v.number,v.source_type,v.summary,
            (SELECT GROUP_CONCAT(a.code || ' ' || a.name, '；') FROM voucher_lines other
             JOIN accounts a ON a.client_id=other.client_id AND a.code=other.account_code
             WHERE other.voucher_id=v.id AND other.account_code<>l.account_code) counter_accounts
            FROM vouchers v JOIN voucher_lines l ON l.voucher_id=v.id
            WHERE v.client_id=? AND v.status='active' AND v.date=? AND l.account_code=? AND l.debit_fen=? AND l.credit_fen=?
            ORDER BY v.id""",(cid,date,bank_code,debit,credit))]
        if candidates and not note:
            raise ApiError(409,'发现日期、金额及银行科目相同的凭证，可能重复；请核对是否为不同流水',{'code':'possible_duplicate','candidates':candidates})
        parent_code='1122' if tx>0 else '2202'
        parent=finance._account(conn,cid,parent_code)
        if parent['report_key']!=('receivable' if tx>0 else 'payable'):
            raise ApiError(409,'往来上级科目分类不匹配，请核对账套科目')
        matches=conn.execute('SELECT * FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? AND name=? ORDER BY code',(cid,parent_code+'%',parent_code,counter)).fetchall()
        if len(matches)>1: raise ApiError(409,'该往来单位存在多个同名明细科目，请先核对')
        if matches:
            target=dict(matches[0])
            if len(target['code'])!=len(parent_code)+2 or conn.execute('SELECT 1 FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? LIMIT 1',(cid,target['code']+'%',target['code'])).fetchone():
                raise ApiError(409,'同名往来科目不是直属末级科目，请先核对')
        else:
            target=finance._create_account(conn,cid,{'parent_code':parent_code,'name':counter},user)
        code=target['code']; value=abs(tx)
        lines=([finance._entry(bank_code,value),finance._entry(code,credit=value)] if tx>0 else
               [finance._entry(code,value),finance._entry(bank_code,credit=value)])
        summary=('收款 ' if tx>0 else '付款 ')+counter
        lines=finance.with_account_summaries(conn,cid,lines)
        vid=finance._insert_voucher(conn,cid,date,summary,lines,user,'reconciliation',record_id)
        import source_archive
        source_archive.post_record(conn,cid,record_id,vid)
        audit(conn,user,'reconcile_post','reconciliation_records',record_id,{'client_id':cid,'voucher_id':vid,'counterparty_account_code':code,'duplicate_candidates':candidates,'duplicate_review_note':note if candidates else ''})
        return {'id':record_id,'voucher_id':vid,'counterparty_account_code':code,'already_posted':False}
