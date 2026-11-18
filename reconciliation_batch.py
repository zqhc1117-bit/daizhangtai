"""Reconciliation batch suggestions and atomic confirmation."""
import hashlib, json, re
from collections import Counter, defaultdict
import account_rules, finance, reconciliation
import reconciliation_advances as advances
from db import ApiError, audit

# Only an explicit standalone use statement is eligible; account names and
# invoice item hints alone do not establish how a purchase or sale was used.
NOTE_USES={
    '主营业务销售','主营销售','主营业务服务','其他业务销售',
    '购入商品用于转售','商品入库待售','商品用于转售',
    '生产用原材料','材料验收入库','原材料入库',
    '符合固定资产确认条件','确认为固定资产',
    '行政管理用途','管理部门使用','行政办公使用',
    '销售推广用途','销售部门使用','广告宣传用途',
    '生产车间间接费用','车间间接费用','直接生产成本','直接生产耗用',
    '符合无形资产确认条件','确认为无形资产',
}

NOTE_ALIASES = {
    '销售广告宣传费': ('广告宣传用途','广告'),
    '销售广告费': ('广告宣传用途','广告'),
    '销售推广费': ('销售推广用途','推广'),
    '管理部门办公用品': ('行政办公使用','办公用品'),
    '管理部门办公用品费': ('行政办公使用','办公用品'),
}


def _note_classification(conn,cid,t,invoice):
    """Classify with an explicit, unambiguous use in the raw bank note."""
    if reconciliation.normalize(invoice.get('business_purpose')): return None
    raw=reconciliation.normalize(t.get('note'))
    if not raw or any(x in raw for x in ('不','非','未','待','可能','拟','假设','或者','或许')): return None
    segments=[x.strip() for x in re.split(r'[,，;；。\n]+',raw) if x.strip()]
    segments=[re.sub(r'^(?:业务用途|用途)\s*[:：]\s*','',x) for x in segments]
    item=reconciliation.normalize(invoice.get('item_name'))
    uses=[x if x in NOTE_USES else NOTE_ALIASES[x][0] for x in segments
          if x in NOTE_USES or (x in NOTE_ALIASES and NOTE_ALIASES[x][1] in item)]
    if not uses: return None
    role='sale' if reconciliation.fen(t.get('amount'))>0 else 'purchase'
    # Do not ignore free-form clauses that might contradict the use statement.
    # Other segments must be a neutral payment label or a short item label
    # grounded in an existing item hint that is actually present on the invoice.
    hints=[r['keyword'] for r in conn.execute('''SELECT DISTINCT h.keyword FROM invoice_item_hints h
        JOIN invoice_account_rules r ON r.id=h.rule_id WHERE r.role=?''',(role,)) if r['keyword'] in item]
    for segment in segments:
        if segment in NOTE_USES or (segment in NOTE_ALIASES and NOTE_ALIASES[segment][1] in item) or segment in ('付款','支付','采购','收款'): continue
        if not any(segment in (hint,hint+'费',hint+'服务',hint+'服务费',hint+'款') for hint in hints): return None
    classification=account_rules.classify(conn,cid,dict(invoice,business_purpose='；'.join(uses)),role)
    if classification.get('status')!='suggested': return None
    return classification,{'source':'transaction_note','raw_note':raw,'uses':uses,
                           'rule_ids':classification['rule_ids'],'source_urls':classification['source_urls']}


def _leaf(conn, cid, code):
    if not isinstance(code,str) or not code.strip(): return None
    row=conn.execute('SELECT * FROM accounts WHERE client_id=? AND code=?',(cid,code.strip())).fetchone()
    if not row or conn.execute('SELECT 1 FROM accounts WHERE client_id=? AND code LIKE ? AND code<>?',(cid,code.strip()+'%',code.strip())).fetchone(): return None
    return dict(row)


def _classification_inputs(invoice):
    return {key:('' if invoice.get(key) is None else str(invoice.get(key)).strip()) for key in
            ('item_name','business_purpose','net_amount','tax_amount','invoice_type')}


def _suggestion_context(transactions):
    # Batch ratios depend on all receipt and supplier amounts, not just the
    # invoice being updated.  Preserve only the fields used by those rules.
    return [{key:('' if t.get(key) is None else str(t.get(key)).strip()) for key in
             ('date','amount','counterparty','note','direction_needs_review')} for t in transactions]


def _existing_record(conn,cid,t,i):
    """Reuse the same source identity; active posting details are read separately."""
    row=conn.execute('SELECT * FROM reconciliation_records WHERE client_id=? AND invoice_number=?',
                     (cid,str(i.get('number','')).strip())).fetchone()
    if not row: return None, ''
    old_t=json.loads(row['transaction_json']); old_i=json.loads(row['invoice_json'])
    same_t=(all(reconciliation.normalize(old_t.get(k))==reconciliation.normalize(t.get(k))
                for k in ('date','counterparty','note')) and reconciliation.fen(old_t.get('amount'))==reconciliation.fen(t.get('amount')))
    same_i=(all(reconciliation.normalize(old_i.get(k))==reconciliation.normalize(i.get(k))
                for k in ('date','seller','buyer','number')) and reconciliation.fen(old_i.get('amount'))==reconciliation.fen(i.get('amount')))
    if not same_t or not same_i or row['status'] not in ('matched','reviewed'):
        return None, '该发票已有不同核对记录，请核实后处理'
    return dict(row), ''


def _store_match(conn,payload,draft,row,user):
    """Save a match or update supplemental invoice information on the same match."""
    ti,ii=row['transaction_index'],row['invoice_index']
    t=payload['transactions'][ti]; i=payload['invoices'][ii]
    existing,error=_existing_record(conn,draft['client_id'],t,i)
    if error: raise ApiError(409,error)
    if existing:
        keys=('date','amount','seller','buyer','number','source','item_name','net_amount','tax_amount','business_purpose','invoice_type')
        cleaned=reconciliation.clean_row(i,keys)
        cleaned['needs_review']=bool(i.get('needs_review',False))
        old=json.loads(existing['invoice_json'])
        if old!=cleaned:
            conn.execute('UPDATE reconciliation_records SET invoice_json=? WHERE id=?',
                         (json.dumps(cleaned,ensure_ascii=False),existing['id']))
            audit(conn,user,'reconcile_update_invoice','reconciliation_records',existing['id'],{'before':old,'after':cleaned})
        result={'id':existing['id'],'status':existing['status']}
        if row.get('voucher'):
            result.update(reconciliation._post_confirmed(conn,existing['id'],draft['client_id'],row['voucher'],user))
    else:
        record={'client_id':draft['client_id'],'account_name':payload['account_name'],
                'account_needs_review':payload.get('account_needs_review',False),
                'transaction':dict(t,direction_needs_review=False) if row.get('automatic_posting') else t,'invoice':i,
                'batch':{'transactions':payload['transactions'],'invoices':payload['invoices'],
                         'transaction_index':ti,'invoice_index':ii}}
        if row.get('voucher'): record['voucher']=row['voucher']
        result=reconciliation.save(conn,record,user,_in_transaction=True)
    previous=json.loads(existing['batch_metadata_json'] or '{}') if existing else {}
    supplied=payload.get('evidence',[])
    evidence=supplied[ti] if ti<len(supplied) else previous.get('evidence',{})
    metadata={'evidence':evidence,'classification':row.get('classification'),
              'reason':row.get('reason',''), 'classification_inputs':_classification_inputs(i),
              'suggestion_context':_suggestion_context(payload['transactions'])}
    if row.get('automatic_posting'):
        metadata['automatic_posting']=row['automatic_posting']
    if row.get('linked_voucher_id'):
        metadata['linked_voucher_id']=row['linked_voucher_id']
        result.update(reconciliation._posting_result(conn,draft['client_id'],row['linked_voucher_id'],True,t))
    if row.get('expense_classification'):
        metadata['expense_classification']=row['expense_classification']
    if metadata!=previous:
        conn.execute('UPDATE reconciliation_records SET batch_metadata_json=? WHERE id=?',
                     (json.dumps(metadata,ensure_ascii=False),result['id']))
        audit(conn,user,'reconcile_batch_metadata','reconciliation_records',result['id'],{'before':previous,'after':metadata})
    _attach_sources(conn,payload,row,result,user)
    return result


def _attach_sources(conn,payload,row,result,user):
    import source_archive
    if not payload.get('archive_id'): return
    ti,ii=row['transaction_index'],row.get('invoice_index')
    receipt=None
    receipts=payload.get('receipts') or []
    if receipts:
        matched=reconciliation.match_receipts(payload['transactions'],receipts)['matches']
        pair=next((item for item in matched if item['transaction_index']==ti and item['status']=='matched'),None)
        if pair: receipt=receipts[pair['receipt_index']]
    supplied=payload.get('evidence') or []
    source_archive.attach(conn,int(payload['client_id']),payload['archive_id'],user,
                          payload['transactions'][ti],payload['invoices'][ii] if ii is not None else None,receipt,
                          record_id=result.get('id'),voucher_id=result.get('voucher_id'),
                          basis={'matching_basis':row.get('basis') or ['流水与发票唯一匹配'],
                                 'evidence':supplied[ti] if ti<len(supplied) else {},
                                 'classification':row.get('classification'),
                                 'automatic_posting':row.get('automatic_posting'),
                                 'account_name':payload.get('account_name','')})


def _asof(conn,cid,code,date,include_date=False):
    opening=conn.execute('SELECT debit_fen-credit_fen FROM opening_balances WHERE client_id=? AND account_code=?',(cid,code)).fetchone()
    value=opening[0] if opening else 0
    value+=conn.execute("""SELECT COALESCE(SUM(l.debit_fen-l.credit_fen),0) FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id
        WHERE v.client_id=? AND v.status='active' AND l.account_code=? AND (v.date<? OR (? AND v.date=?))""",(cid,code,date,int(include_date),date)).fetchone()[0]
    return value


def _history(conn,cid,t,i,bank,date):
    """Use only a unique, identical business pattern from a prior confirmed record."""
    item=reconciliation.normalize(i.get('item_name')); purpose=reconciliation.normalize(i.get('business_purpose'))
    note=reconciliation.normalize(t.get('note')); counter=reconciliation.normalize(t.get('counterparty'))
    tx=reconciliation.fen(t.get('amount')); tax=reconciliation.fen(i.get('tax_amount')); net=reconciliation.fen(i.get('net_amount'))
    if not all((item,purpose,note,counter,tx)) or tax!=0 or net!=abs(tx): return None
    candidates={}
    rows=conn.execute("""SELECT r.id,r.transaction_json,r.invoice_json,v.id vid,v.date FROM reconciliation_records r
       JOIN vouchers v ON v.client_id=r.client_id AND v.source_type='reconciliation' AND v.source_id=CAST(r.id AS TEXT)
       WHERE r.client_id=? AND v.status='active' AND v.reviewed=1 AND v.date<? ORDER BY v.date DESC,v.id DESC LIMIT 500""",(cid,date))
    for row in rows:
        oldt=json.loads(row['transaction_json']); oldi=json.loads(row['invoice_json']); oldamt=reconciliation.fen(oldt.get('amount'))
        if not oldamt or (oldamt>0)!=(tx>0) or reconciliation.normalize(oldt.get('counterparty'))!=counter or reconciliation.normalize(oldt.get('note'))!=note: continue
        if reconciliation.normalize(oldi.get('item_name'))!=item or reconciliation.normalize(oldi.get('business_purpose'))!=purpose: continue
        if reconciliation.fen(oldi.get('tax_amount'))!=0 or reconciliation.fen(oldi.get('net_amount'))!=abs(oldamt): continue
        lines=[dict(x) for x in conn.execute('SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY id',(row['vid'],))]
        if len(lines)!=2: continue
        cash=[x for x in lines if x['account_code']==bank['code']]
        other=[x for x in lines if x['account_code']!=bank['code']]
        if len(cash)!=1 or len(other)!=1 or cash[0]['debit_fen']-cash[0]['credit_fen']!=oldamt: continue
        a=_leaf(conn,cid,other[0]['account_code'])
        if not a or finance._cash(a) or other[0]['debit_fen']-other[0]['credit_fen']!=-oldamt: continue
        if a['category']=='liability' and a['report_key']!='payable': continue
        if a['report_key'] in ('receivable','payable','prepaid','advance') and a['name']!=counter: continue
        candidates.setdefault(a['code'],row['vid'])
    if len(candidates)!=1: return None
    code,vid=next(iter(candidates.items())); value=abs(tx)
    lines=[finance._entry(bank['code'],value if tx>0 else 0,value if tx<0 else 0),finance._entry(code,value if tx<0 else 0,value if tx>0 else 0)]
    return lines, ['与历史核对凭证 %s 的对方、摘要、发票项目、用途、收付方向和零税额一致；拟沿用其科目，仍需核实本次确认时点和是否已有入账。'%vid]


def _draft(conn,cid,t,i,classification,e):
    reasons=[]; basis=[]
    def missing(message): reasons.append(message)
    if t.get('direction_needs_review'): missing('流水收付款方向尚未确认')
    try: date=finance._date(t.get('date'))
    except ApiError: date=''; missing('流水日期无效')
    tx=reconciliation.fen(t.get('amount')); total=reconciliation.fen(i.get('amount'))
    if not tx or not total or total<=0 or abs(tx)!=total: missing('流水与发票金额须一致且有效')
    bank=_leaf(conn,cid,e.get('bank_account_code'))
    # An outgoing amount proves a payment, not which asset funded it.  In
    # particular, a sole bank account in the book is not evidence of payment
    # from that account.  Keep the match and classification for review instead
    # of posting an invented credit entry.
    if tx is not None and tx>0 and not e.get('bank_account_code'):
        banks=[dict(x) for x in conn.execute("SELECT * FROM accounts WHERE client_id=? AND code LIKE '1002%' ORDER BY code",(cid,)) if _leaf(conn,cid,x['code'])]
        if len(banks)==1: bank=banks[0]; basis.append('账套仅有一个银行末级科目，拟使用 '+bank['code']+' '+bank['name']+'，请核对实际账户')
    if not bank or not finance._cash(bank): missing('请核实实际资金来源（银行、现金、个人代付等）；代付或往来需依据凭证人工处理')
    treatment=e.get('treatment')
    note=str(e.get('evidence_note') or '').strip()
    if not treatment and not reasons:
        historical=_history(conn,cid,t,i,bank,date)
        if historical:
            other=next(line for line in historical[0] if line['account_code']!=bank['code'])
            account=_leaf(conn,cid,other['account_code'])
            if account['report_key'] in ('prepaid','advance') and not ('合同' in reconciliation.normalize(t.get('note')) and '预付' in reconciliation.normalize(t.get('note'))):
                missing('沿用预付款科目前须核实本次合同预付依据')
            if account['report_key'] in ('receivable','payable'):
                if (account['report_key']=='payable' and tx>=0) or (account['report_key']=='receivable' and tx<=0): missing('历史往来科目与本次收付款方向不符')
                bal=_asof(conn,cid,account['code'],date)
                outstanding=bal if account['report_key']=='receivable' else -bal
                if outstanding<abs(tx): missing('历史往来科目余额不足，请核实本次业务和原凭证')
            else:
                outstanding=conn.execute("SELECT code,report_key FROM accounts WHERE client_id=? AND name=? AND report_key IN ('receivable','payable')",(cid,reconciliation.normalize(t.get('counterparty')))).fetchall()
                if any((_asof(conn,cid,a['code'],date,True)>0 if a['report_key']=='receivable' else _asof(conn,cid,a['code'],date,True)<0) for a in outstanding if _leaf(conn,cid,a['code'])):
                    missing('该公司存在未结应收或应付，请核实是否应冲销原有往来，避免重复确认')
            if not reasons: return historical[0],[],basis+historical[1]
        transaction_note=reconciliation.normalize(t.get('note'))
        if tx<0 and '合同' in transaction_note and ('预付' in transaction_note or '预付款' in transaction_note):
            treatment='contract_prepayment'; note='流水附言：'+transaction_note
        elif classification.get('status')=='suggested':
            treatment='recognize_invoice'; note='依据已识别的业务科目；拟直接确认当期业务，须核实是否已入账及确认时点'
            # Existing same-company outstanding may signal a prior recognition.
            outstanding=conn.execute("SELECT code,report_key FROM accounts WHERE client_id=? AND name=? AND report_key IN ('receivable','payable')",(cid,reconciliation.normalize(t.get('counterparty')))).fetchall()
            if any((_asof(conn,cid,a['code'],date,True)>0 if a['report_key']=='receivable' else _asof(conn,cid,a['code'],date,True)<0) for a in outstanding if _leaf(conn,cid,a['code'])):
                missing('该公司存在未结应收或应付，请核实是否应冲销原有往来，避免重复确认')
    if treatment not in ('contract_prepayment','settle_payable','settle_receivable','recognize_invoice'):
        if classification.get('status')=='review':
            missing('已匹配发票，但科目尚不能确定：'+classification.get('basis','请核实业务性质'))
        else:
            missing('请选择业务处理依据：合同预付款、冲销已有应付/应收，或确认发票业务')
    elif len(note)<2 or len(note)>500: missing('请填写具体合同、原凭证或业务确认依据')
    lines=[]
    if not reasons:
        value=abs(tx)
        bankline=finance._entry(bank['code'],value if tx>0 else 0,value if tx<0 else 0)
        if treatment=='contract_prepayment':
            if tx>=0: missing('合同预付款仅适用于付款；请核实收付款及合同')
            else:
                parent=conn.execute("SELECT * FROM accounts WHERE client_id=? AND report_key='prepaid' AND length(code)=4 ORDER BY code",(cid,)).fetchall()
                if len(parent)!=1: missing('账套中需有唯一预付账款上级科目')
                else:
                    lines=[{'counterparty_parent_code':parent[0]['code'],'debit_fen':value,'credit_fen':0},bankline]
                    basis.append('合同预付款依据：'+note)
        elif treatment in ('settle_payable','settle_receivable'):
            target=_leaf(conn,cid,e.get('settlement_account_code'))
            desired='payable' if treatment=='settle_payable' else 'receivable'
            if (treatment=='settle_payable' and tx>=0) or (treatment=='settle_receivable' and tx<=0): missing('往来冲销方向与收付款不符')
            elif not target or target['report_key']!=desired or target['name']!=reconciliation.normalize(t.get('counterparty')):
                missing('请选择本账套中与对方公司同名的应付/应收末级科目')
            else:
                balance=_asof(conn,cid,target['code'],date)
                outstanding=-balance if desired=='payable' else balance
                if outstanding<value: missing('该公司在流水日期之前的未结往来余额不足，请核实原凭证和期间')
                else:
                    lines=([finance._entry(target['code'],value),bankline] if tx<0 else [bankline,finance._entry(target['code'],credit=value)])
                    basis.append('原有往来冲销依据：'+note+'；流水日前该明细余额 '+str(outstanding)+' 分')
        elif treatment=='recognize_invoice':
            code=classification.get('account_code')
            account=_leaf(conn,cid,code)
            net=reconciliation.fen(i.get('net_amount')); tax=reconciliation.fen(i.get('tax_amount'))
            if classification.get('status')!='suggested' or not account: missing('发票项目和具体用途未能确定唯一业务末级科目')
            elif account['category']=='liability' or account['report_key'] in ('receivable','prepaid','advance'):
                missing('该科目属于往来或负债，不能直接确认发票业务；请依据原有凭证和余额处理')
            if net is None or tax is None or net<0 or tax<0 or net+tax!=value: missing('请核实发票不含税金额与税额，且两者合计应等于价税合计')
            gross_cost=bool(tax and tx<0 and finance._client(conn,cid).get('taxpayer_type')=='small'
                            and i.get('invoice_type')=='ordinary')
            taxacc=None
            if tax and not gross_cost:
                taxacc=_leaf(conn,cid,e.get('tax_account_code'))
                if not taxacc or taxacc['report_key']!='tax_payable': missing('请明确选择正确的应交税费末级科目')
                expected='output' if tx>0 else 'deductible_input'
                if e.get('tax_treatment')!=expected: missing('请核实税额是否为销项税或可抵扣进项税')
            if not reasons:
                if tx<0:
                    lines=[finance._entry(account['code'],value if gross_cost else net)] if net or gross_cost else []
                    if tax and not gross_cost: lines.append(finance._entry(taxacc['code'],tax))
                    lines.append(bankline)
                else:
                    lines=[bankline]
                    if net: lines.append(finance._entry(account['code'],credit=net))
                    if tax: lines.append(finance._entry(taxacc['code'],credit=tax))
                basis.extend([classification.get('basis',''), '业务确认及税务处理依据：'+note])
                if gross_cost: basis.append('小规模纳税人取得普通发票，价税合计计入所选业务科目')
    return lines, reasons, basis


def _funding_account(conn,cid,evidence):
    explicit=_leaf(conn,cid,evidence.get('bank_account_code'))
    if explicit and finance._cash(explicit): return explicit,'explicit'
    banks=[dict(row) for row in conn.execute("SELECT * FROM accounts WHERE client_id=? AND (code LIKE '1002%' OR name LIKE '%银行存款%') ORDER BY CASE WHEN code='1002' THEN 0 ELSE 1 END,code",(cid,))
           if _leaf(conn,cid,row['code']) and finance._cash(dict(row))]
    if not banks: raise ApiError(409,'账套没有可用的银行存款末级科目')
    return banks[0],'automatic_default'


def _target_line(conn,cid,code,name='',sub=''):
    """Return an existing leaf or a deferred, safe named child without writes."""
    sub=reconciliation.normalize(sub)[:80]
    account=conn.execute('SELECT * FROM accounts WHERE client_id=? AND code=?',(cid,str(code or ''))).fetchone()
    if name and (not account or account['name']!=name):
        named=conn.execute('SELECT * FROM accounts WHERE client_id=? AND name=? ORDER BY length(code),code',(cid,name)).fetchall()
        account=named[0] if named else None
    if not account or finance._cash(dict(account)) or account['report_key']=='prior_adjustment': return None,None
    account=dict(account); code=account['code']; leaf=_leaf(conn,cid,code)
    if sub:
        named=[dict(row) for row in conn.execute('SELECT * FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? AND name=? ORDER BY code',(cid,code+'%',code,sub)) if _leaf(conn,cid,row['code'])]
        if len(named)==1: return named[0],{'account_code':named[0]['code']}
        if len(named)>1: return None,None
    if leaf and not sub: return leaf,{'account_code':code}
    used=conn.execute('SELECT 1 FROM voucher_lines WHERE client_id=? AND account_code=?',(cid,code)).fetchone() or conn.execute('SELECT 1 FROM opening_balances WHERE client_id=? AND account_code=? AND (debit_fen<>0 OR credit_fen<>0)',(cid,code)).fetchone()
    if sub and not used and len(code)<=18:
        suffixes=[row['code'][len(code):] for row in conn.execute('SELECT code FROM accounts WHERE client_id=? AND code LIKE ? AND code<>?',(cid,code+'%',code))]
        if max([int(value[:2].ljust(2,'9')) for value in suffixes if value.isdigit()],default=0)<99:
            return account,{'business_parent_code':code,'business_sub_account':sub[:80]}
    if leaf: return leaf,{'account_code':code}
    return None,None


def _record_posting(conn,cid,record,transaction):
    if not record: return None
    direct=conn.execute("SELECT id FROM vouchers WHERE client_id=? AND source_type='reconciliation' AND source_id=? AND status='active'",(cid,str(record['id']))).fetchone()
    try: linked=json.loads(record.get('batch_metadata_json') or '{}').get('linked_voucher_id')
    except (ValueError,TypeError,AttributeError): linked=None
    if direct: return reconciliation._posting_result(conn,cid,direct['id'],True,transaction)
    if linked and conn.execute("SELECT 1 FROM vouchers WHERE id=? AND client_id=? AND status='active'",(linked,cid)).fetchone():
        return reconciliation._posting_result(conn,cid,linked,True,transaction)
    return None


def _receipt_posting(conn,cid,transaction,receipt=None,*,use_transaction_key=True):
    if receipt:
        source=normalize_receipt_source(transaction,receipt)
        row=conn.execute("SELECT id FROM vouchers WHERE client_id=? AND source_type='bank_receipt' AND source_id=? AND status='active'",(cid,source)).fetchone()
        if row: return reconciliation._posting_result(conn,cid,row['id'],True,transaction)
    if not use_transaction_key: return None
    fingerprint=reconciliation._transaction_key(transaction)
    rows=conn.execute("SELECT v.id,a.detail FROM vouchers v JOIN audit_logs a ON a.entity='vouchers' AND a.entity_id=CAST(v.id AS TEXT) AND a.action='reconcile_receipt_post' WHERE v.client_id=? AND v.source_type='bank_receipt' AND v.status='active' AND v.date=?",(cid,transaction.get('date')))
    for row in rows:
        try: detail=json.loads(row['detail'])
        except (ValueError,TypeError): continue
        if detail.get('transaction_key')==fingerprint:
            return reconciliation._posting_result(conn,cid,row['id'],True,transaction)
    return None


def normalize_receipt_source(transaction,receipt):
    number=reconciliation.normalize(receipt.get('number'))
    return reconciliation.normalize(transaction.get('date'))+':'+(number or reconciliation._transaction_key(transaction))


def _automatic_business_lines(conn,cid,invoice,value,target,line,evidence,provenance):
    net=reconciliation.fen(invoice.get('net_amount')) if invoice else None
    tax=reconciliation.fen(invoice.get('tax_amount')) if invoice else None
    reliable=bool(invoice and net is not None and tax is not None and net>=0 and tax>=0 and net+tax==abs(value))
    small_ordinary=bool(invoice and value<0 and finance._client(conn,cid).get('taxpayer_type')=='small' and invoice.get('invoice_type')=='ordinary')
    tax_account=None
    if reliable and tax and not small_ordinary and target['report_key'] not in ('receivable','payable','prepaid','advance','other_liability','tax_payable'):
        explicit=_leaf(conn,cid,evidence.get('tax_account_code'))
        if explicit and explicit['report_key']=='tax_payable': tax_account=explicit
        else:
            leaves=[dict(row) for row in conn.execute("SELECT * FROM accounts WHERE client_id=? AND report_key='tax_payable' ORDER BY code",(cid,)) if _leaf(conn,cid,row['code'])]
            named=[row for row in leaves if ('销项税' if value>0 else '进项税') in row['name']]
            if len(named)==1: tax_account=named[0]
            elif not named and len(leaves)==1 and leaves[0]['code']=='2221': tax_account=leaves[0]
    business_amount=net if tax_account else abs(value)
    lines=[dict(line,debit_fen=business_amount if value<0 else 0,credit_fen=business_amount if value>0 else 0)] if business_amount else []
    if tax_account:
        lines.append(finance._entry(tax_account['code'],tax if value<0 else 0,tax if value>0 else 0))
        provenance.update(tax_mode='split',net_fen=net,tax_fen=tax,tax_account_code=tax_account['code'])
    elif small_ordinary: provenance['tax_mode']='gross_small_ordinary'
    elif not reliable: provenance['tax_mode']='gross_unreliable_or_missing_tax'
    elif tax: provenance['tax_mode']='gross_no_unique_tax_account'
    return lines


def _automatic_draft(conn,cid,transaction,invoice,classification,evidence,source='invoice',matched_receipt=None):
    """A unique document match posts its signed gross amount under the chosen policy."""
    date=finance._date(transaction.get('date'))
    value=reconciliation.fen(transaction.get('amount'))
    total=reconciliation.fen(invoice.get('amount')) if invoice else abs(value or 0)
    if not value or not total or total<=0 or abs(value)!=total:
        raise ApiError(409,'匹配金额无效或与流水金额不一致')
    counter=reconciliation.normalize(transaction.get('counterparty'))
    blank_receipt_match=(source=='receipt' and isinstance(matched_receipt,dict) and
                         not reconciliation.normalize(matched_receipt.get('counterparty')) and
                         reconciliation.normalize(matched_receipt.get('date'))==date and
                         reconciliation.fen(matched_receipt.get('amount'))==value)
    if not counter and not blank_receipt_match: raise ApiError(409,'流水交易对方名称缺失')
    bank,funding_source=_funding_account(conn,cid,evidence)
    selected=evidence.get('business_account_code')
    transaction_note=reconciliation.normalize(transaction.get('note'))
    receipt_note=reconciliation.normalize((matched_receipt or {}).get('note'))
    # An exact bank receipt may legitimately omit the other party (interest,
    # bank service fees). A purpose label names the business detail only;
    # the original counterparty stays blank for identity and audit purposes.
    detail_name=counter or transaction_note or receipt_note or ('回单收款（未提供交易对方）' if value>0 else '回单付款（未提供交易对方）')
    bank_use=(classification.get('rule_suggestion') or {}).get('status')=='suggested'
    uncertain_note=(not selected and not bank_use and not reconciliation.normalize((invoice or {}).get('business_purpose')) and
                    (any(word in transaction_note for word in ('不','非','未','待','可能','拟','假设','或者','或许','实际为')) or
                     sum(1 for use in NOTE_USES if use in transaction_note)>1))
    if uncertain_note:
        # A unique document still posts, but a negated or conflicting purpose
        # cannot substantiate the contrary income/expense recommendation.
        classification.clear()
        classification.update(status='review',account_code=None,account_name=None,
                              basis='流水用途包含否定或冲突，保留原始备注并按匹配金额挂往来',
                              source='uncertain_transaction_note',rule_ids=[],source_urls=[])
    suggestion=classification.get('rule_suggestion') or {}
    code=selected or classification.get('account_code') or suggestion.get('account_code')
    name='' if selected or bank_use else classification.get('account_name') or suggestion.get('account_name') or ''
    sub=classification.get('sub_account') or suggestion.get('sub_account') or ''
    target,line=_target_line(conn,cid,code,name,reconciliation.normalize(sub))
    if not target and code:
        target,line=_target_line(conn,cid,code,name,detail_name)
    subject_source='selected_account' if selected and target else 'recommendation'
    if not target and bank_use:
        raise ApiError(409,'报销款归入管理费用，请选择本账套可入账的管理费用明细科目')
    if not target:
        fallback='1221' if value<0 else '2241'
        target,line=_target_line(conn,cid,fallback,'其他应收款' if value<0 else '其他应付款',detail_name)
        subject_source='other_receivable_fallback' if value<0 else 'other_payable_fallback'
    if not target: raise ApiError(409,'账套没有可用的业务或往来末级科目')
    provenance={'policy':'matched_document_direct_post','matched_source':source,'funding_source':funding_source,
                'funding_account_code':bank['code'],'subject_source':subject_source,'subject_account_code':target['code'],
                'subject_name':line.get('business_sub_account') or target['name'],'amount_fen':abs(value),
                'source_net_amount':None if not invoice else invoice.get('net_amount'),
                'source_tax_amount':None if not invoice else invoice.get('tax_amount'),'tax_mode':'gross'}
    if not counter:
        provenance.update(counterparty_missing=True,detail_name=detail_name,
                          detail_name_source='transaction_note' if transaction_note else 'receipt_note' if receipt_note else 'missing_counterparty_label',
                          raw_transaction_note=transaction_note,raw_receipt_note=receipt_note)
    if uncertain_note: provenance['classification_source']='uncertain_transaction_note'
    lines=_automatic_business_lines(conn,cid,invoice,value,target,line,evidence,provenance)
    bank_line=finance._entry(bank['code'],max(value,0),max(-value,0))
    lines=([bank_line]+lines) if value>0 else (lines+[bank_line])
    note=transaction_note or receipt_note
    lines=finance.with_account_summaries(conn,cid,lines)
    basis=['流水与'+('发票' if source=='invoice' else '银行回单')+'唯一匹配，按匹配金额预填凭证',
           ('已选资金科目：' if funding_source=='explicit' else '自动默认资金科目：')+bank['code']+' '+bank['name']]
    if provenance['tax_mode'].startswith('gross_unreliable'): basis.append('未补造不含税金额或税额，按匹配总额入账')
    if uncertain_note: basis.append(classification['basis'])
    if not counter: basis.append('流水及匹配回单未提供交易对方，按原始用途或缺名标识记业务明细，保留原空值')
    return {'date':date,'summary':(('收款 ' if value>0 else '付款 ')+counter+' '+note).strip()[:200],'lines':lines},basis,provenance


def _existing_invoice_posting(conn,cid,invoice):
    number=reconciliation.normalize(invoice.get('number'))
    row=conn.execute("""SELECT id FROM vouchers WHERE client_id=? AND source_type='reconciliation_invoice'
        AND source_id=? AND status='active'""",(cid,number)).fetchone()
    if row:
        return reconciliation._posting_result(conn,cid,row['id'],True)
    row=conn.execute("""SELECT v.id FROM invoices i JOIN vouchers v ON v.id=i.voucher_id AND v.client_id=i.client_id
        WHERE i.client_id=? AND i.number=? AND v.status='active'""",(cid,number)).fetchone()
    if row:
        return reconciliation._posting_result(conn,cid,row['id'],True)
    record=conn.execute('SELECT * FROM reconciliation_records WHERE client_id=? AND invoice_number=?',(cid,number)).fetchone()
    return _record_posting(conn,cid,dict(record),None) if record else None


def _company_pairs(statement_rows,invoice_rows):
    incoming=defaultdict(list)
    issued=defaultdict(list)
    for row in statement_rows.values():
        incoming[row['counterparty'],row['amount_fen']].append(row)
    for row in invoice_rows.values():
        if row['amount_fen'] and row['amount_fen']>0 and reconciliation.normalize(row['invoice'].get('number')):
            issued[row['counterparty'],row['amount_fen']].append(row)
    pairs={}
    for key in incoming.keys() & issued.keys():
        payments, invoices=incoming[key],issued[key]
        if len(payments)==len(invoices)==1:
            payment, invoice=payments[0],invoices[0]
            pairs['statement',payment['transaction_index']]=invoice
            pairs['invoice',invoice['invoice_index']]=payment
        else:
            for row in payments+invoices:
                if row['status']!='posted':
                    row['reason']='本批有多笔同一公司同金额的流水或发票，请核对后再确认'
    return pairs


def _company_subject(conn,cid,key,party,settlement):
    if settlement:
        return {'account_code':settlement['account_code']}
    parent=advances.account_root(conn,cid,key)
    if len(party)>80:
        raise ApiError(409,'对方公司名称超出明细科目长度，请核对名称或手动选择科目')
    target,line=_target_line(conn,cid,parent['code'],sub=party)
    if not target or target['report_key']!=key:
        raise ApiError(409,'本账套未能确定唯一的公司往来明细科目，请核对科目设置')
    return line


def _company_draft(conn,cid,row,evidence,settlement,is_invoice):
    date=row['source_date']
    finance._open(conn,cid,date[:7])
    value,party=row['amount_fen'],row['counterparty']
    key=('advance' if settlement else 'prepaid') if is_invoice else ('prepaid' if settlement else 'advance')
    subject=_company_subject(conn,cid,key,party,settlement)
    label=finance.account_category_summary(conn,cid,subject)
    provenance={'policy':'corporate_advance','amount_fen':value,'subject_account_code':subject.get('account_code') or subject['business_parent_code'],
                'subject_name':label,'settlement':settlement}
    if settlement:
        basis=[f'同一公司金额一致，冲销{label}，对应 {settlement["reference"]}']
    else:
        basis=[f'公司开票未查到对应收款，挂{label}' if is_invoice else f'收到其他公司打款，挂{label}']
    if is_invoice:
        invoice=row['invoice']
        classification=account_rules.classify(conn,cid,invoice,'sale')
        account_code=evidence.get('business_account_code') or classification.get('account_code')
        income,line=_target_line(conn,cid,account_code)
        if not income or income['category']!='income':
            income_name=classification.get('account_name') or '主营业务收入'
            income,line=_target_line(conn,cid,'',income_name)
            if not income:
                income,line=_target_line(conn,cid,'',income_name,party)
        if not income or income['category']!='income':
            raise ApiError(409,'本账套未能确定可用的销售收入科目，请核对科目设置')
        provenance.update(matched_source='invoice',tax_mode='gross',source_net_amount=invoice.get('net_amount'),source_tax_amount=invoice.get('tax_amount'))
        credits=_automatic_business_lines(conn,cid,invoice,value,income,line,evidence,provenance)
        if provenance['tax_mode']=='gross_unreliable_or_missing_tax':
            basis.append('按发票价税合计预填，请核对不含税金额和税额')
        elif provenance['tax_mode']=='gross_no_unique_tax_account':
            basis.append('请在确认凭证时核对销项税科目和分录')
        lines=[dict(subject,debit_fen=value,credit_fen=0),*credits]
        summary=f'开票 {party} {invoice["number"]}'
    else:
        bank,funding_source=_funding_account(conn,cid,evidence)
        provenance.update(matched_source='receipt' if row.get('receipt_index') is not None else 'statement',
                          funding_source=funding_source,funding_account_code=bank['code'])
        lines=[finance._entry(bank['code'],debit=value),dict(subject,debit_fen=0,credit_fen=value)]
        summary=f'收款 {party} {reconciliation.normalize(row["transaction"].get("note"))}'
    row.update(status='ready',voucher={'date':date,'summary':summary.strip()[:200],
               'lines':finance.with_account_summaries(conn,cid,lines)},basis=basis,automatic_posting=provenance,
               classification={'status':'suggested','account_code':provenance['subject_account_code'],'account_name':label,
                               'basis':basis[0],'source':'corporate_advance'})


def _company_rows(conn,client,payload,receipt_matches):
    cid=client['id']
    statement_rows={}
    invoice_rows={}
    evidence=payload.get('evidence') or []
    receipts=payload.get('receipts') or []
    for ti,t in enumerate(payload['transactions']):
        if not advances.company_receipt(t,client): continue
        pair=receipt_matches.get(ti,{})
        ri=pair['receipt_index'] if pair.get('status')=='matched' else None
        row=statement_rows[ti]={'document':'company_statement','transaction_index':ti,'invoice_index':None,'receipt_index':ri,
                               'status':'review','matched':False,'voucher':None,'reason':'','basis':[],
                               'classification':None,'classification_evidence':None,'transaction':t,
                               'counterparty':reconciliation.normalize(t.get('counterparty')),'amount_fen':reconciliation.fen(t.get('amount'))}
        try:
            row['source_date']=finance._date(t.get('date'))
            if t.get('direction_needs_review'):
                raise ApiError(409,'请先确认流水收付款方向')
            receipt=receipts[ri] if ri is not None else None
            own=reconciliation.normalize((receipt or {}).get('own_name'))
            if own and own not in {reconciliation.normalize(client[k]) for k in ('name','enterprise_name')}:
                raise ApiError(409,'回单本方名称与账套不一致')
            posted=_receipt_posting(conn,cid,t,receipt) if receipt else None
            posted=posted or reconciliation._existing_statement_posting(conn,cid,t)
            if posted: row.update(posted,status='posted')
        except ApiError as error:
            row['reason']=error.message

    numbers=Counter(reconciliation.normalize(invoice.get('number')) for invoice in payload['invoices'])
    for ii,invoice in enumerate(payload['invoices']):
        if not advances.company_invoice(invoice,client): continue
        row=invoice_rows[ii]={'document':'company_invoice','invoice_index':ii,'status':'review','voucher':None,'reason':'',
                              'basis':[],'invoice':invoice,'counterparty':reconciliation.normalize(invoice.get('buyer')),
                              'amount_fen':reconciliation.fen(invoice.get('amount'))}
        try:
            number=finance._text(reconciliation.normalize(invoice.get('number')),'发票号码',80)
            row['source_date']=finance._date(invoice.get('date'))
            if not row['amount_fen'] or row['amount_fen']<0:
                raise ApiError(400,'发票价税合计须为正数，请核对票面金额')
            posted=_existing_invoice_posting(conn,cid,invoice)
            if posted: row.update(posted,status='posted')
            elif numbers[number]>1:
                raise ApiError(409,'本批发票号码重复，请保留一张后重新核对')
        except ApiError as error:
            row['reason']=error.message

    pairs=_company_pairs(statement_rows,invoice_rows)
    source_errors={id(row) for row in [*statement_rows.values(),*invoice_rows.values()] if row['reason']}
    history={}
    claims=defaultdict(list)
    for kind,rows in (('statement',statement_rows),('invoice',invoice_rows)):
        is_invoice=kind=='invoice'
        key='advance' if is_invoice else 'prepaid'
        for index,row in rows.items():
            pair=pairs.get((kind,index))
            if pair:
                row['paired_index']=pair['transaction_index' if is_invoice else 'invoice_index']
            if row['status']=='posted' or row['reason']: continue
            try:
                date,party,value=row['source_date'],row['counterparty'],row['amount_fen']
                settlement=None
                if pair:
                    other_source=pair.get('source_date')
                    if not other_source or id(pair) in source_errors:
                        raise ApiError(409,'本批对应的流水或发票仍需核对')
                    other_first=other_source<date or (is_invoice and other_source==date)
                    if other_first and pair['status']!='posted':
                        raise ApiError(409,'请先确认本批对应的'+('收款凭证' if is_invoice else '开票凭证'))
                    if not other_first and pair['status']=='posted':
                        raise ApiError(409,'本批对应单据已在较晚日期入账，请核对两笔往来后处理')
                if not pair or pair['status']=='posted':
                    if (key,party) not in history:
                        history[key,party]=advances.open_items(conn,client,party,key)
                    settlement=advances.match_open(history[key,party],key,date,value,pair['voucher_id'] if pair else None)
                    if pair and not settlement:
                        raise ApiError(409,'本批对应凭证没有可冲销的同金额往来，请核对其分录')
                ev=(evidence[index] if index<len(evidence) else {}) if not is_invoice else (
                    evidence[pair['transaction_index']] if pair and pair['transaction_index']<len(evidence) else {})
                _company_draft(conn,cid,row,ev,settlement,is_invoice)
                if settlement:
                    claims[key,party,settlement['account_code'],settlement['voucher_id']].append(row)
            except ApiError as error:
                row['reason']=error.message
    for rows in claims.values():
        if len(rows)>1:
            for row in rows:
                row.update(status='review',voucher=None,reason='本批多条记录对应同一笔历史往来，请核对后再确认',basis=[])
                row.pop('automatic_posting',None)
                row['classification']=None
    return statement_rows,invoice_rows


def preview(conn,payload):
    if not isinstance(payload,dict): raise ApiError(400,'核对批次无效')
    if isinstance(payload.get('client_id'),bool): raise ApiError(400,'客户无效')
    try: cid=int(payload.get('client_id'))
    except (TypeError,ValueError): raise ApiError(400,'请选择客户账套')
    client=finance._client(conn,cid)
    import source_archive
    source_archive.validate(conn,cid,payload.get('archive_id'))
    txs=payload.get('transactions'); ivs=payload.get('invoices')
    if not isinstance(txs,list) or not isinstance(ivs,list) or not (txs or ivs) or max(len(txs),len(ivs))>500:
        raise ApiError(400,'请提供流水或发票，分别最多 500 条')
    evidence=payload.get('evidence',[])
    allowed={'treatment','bank_account_code','settlement_account_code','tax_treatment','tax_account_code','evidence_note','duplicate_review_note','business_account_code'}
    if not isinstance(evidence,list) or len(evidence)>len(txs) or any(not isinstance(item,dict) or any(key not in allowed or not isinstance(value,str) or len(value)>500 for key,value in item.items()) for item in evidence):
        raise ApiError(400,'业务依据格式无效')
    account=reconciliation.normalize(payload.get('account_name'))
    full=client.get('enterprise_name','')
    if not account or account not in {reconciliation.normalize(client['name']),reconciliation.normalize(full)}: raise ApiError(400,'本方名称必须与客户名称或企业全称一致')
    receipts=payload.get('receipts') or []
    out=reconciliation.match(txs,ivs,account,payload.get('account_needs_review',False),conn,cid,receipts)
    matches={match['transaction_index']:match for match in out['matches']}
    receipt_matches={row['transaction_index']:row for row in out['receipt_matches']['matches']}
    company_statements, company_invoices=_company_rows(conn,client,payload,receipt_matches)
    rows=[]
    for index,t in enumerate(txs):
        if index in company_statements:
            rows.append(company_statements[index]); continue
        match=matches.get(index); ii=match.get('invoice_index') if match else None
        row={'transaction_index':index,'invoice_index':ii,'matched':bool(match and match['status']=='matched' and ii is not None),
             'status':'review','reason':'','counterparty':reconciliation.normalize(t.get('counterparty')),
             'amount_fen':reconciliation.fen(t.get('amount')),'voucher':None,'expense_classification':None,
             'basis':[],'classification':dict(out['classification_transactions'][index]),'classification_evidence':None}
        if not row['matched']:
            row['reason']='；'.join((match or {}).get('reasons') or ['未找到唯一匹配发票'])
            rows.append(row); continue
        inv=ivs[ii]; ev=evidence[index] if index<len(evidence) else {}
        try:
            existing,error=_existing_record(conn,cid,t,inv)
            if error: raise ApiError(409,error)
            posted=_record_posting(conn,cid,existing,t)
            receipt_pair=receipt_matches.get(index,{})
            receipt=receipts[receipt_pair['receipt_index']] if receipt_pair.get('status')=='matched' else None
            if not posted: posted=_receipt_posting(conn,cid,t,receipt)
            if not posted: posted=reconciliation._existing_statement_posting(conn,cid,t)
            if posted:
                row.update(posted,status='posted',id=existing['id'] if existing else None)
                if not existing or not conn.execute("SELECT 1 FROM vouchers WHERE id=? AND source_type='reconciliation' AND source_id=?",(posted['voucher_id'],str(existing['id']))).fetchone():
                    row['linked_voucher_id']=posted['voucher_id']
                if existing:
                    meta=json.loads(existing.get('batch_metadata_json') or '{}')
                    row['classification']=meta.get('classification') or row['classification']
                    row['automatic_posting']=meta.get('automatic_posting') or {'policy':'matched_document_direct_post','matched_source':'invoice','linked_voucher_id':posted['voucher_id']}
                else: row['automatic_posting']={'policy':'matched_document_direct_post','matched_source':'invoice','linked_voucher_id':posted['voucher_id']}
                rows.append(row); continue
            classification=row['classification']
            suggestion=classification.get('rule_suggestion') or {}
            if suggestion.get('status')!='suggested':
                if existing:
                    meta=json.loads(existing.get('batch_metadata_json') or '{}')
                    old=meta.get('classification') or {}
                    if meta.get('classification_inputs')==_classification_inputs(inv) and old.get('account_code'):
                        classification=dict(old)
                note_classification=_note_classification(conn,cid,t,inv)
                if note_classification and not ev.get('business_account_code'):
                    classification,row['classification_evidence']=note_classification
                suggestion=classification.get('rule_suggestion') or suggestion
            if suggestion.get('status')=='suggested' or (suggestion.get('account_code') and
                    (not classification.get('account_code') or classification.get('status')!='suggested')):
                classification=dict(suggestion,status='suggested',rule_suggestion=suggestion)
                row['classification_evidence']={'source':'accepted_rule_suggestion','account_code':suggestion['account_code'],'basis':suggestion.get('basis','')}
            elif suggestion: classification['rule_suggestion']=suggestion
            voucher,basis,provenance=_automatic_draft(conn,cid,t,inv,classification,ev)
            finance._open(conn,cid,voucher['date'][:7])
            for line in voucher['lines']:
                if line.get('account_code')!=provenance['funding_account_code']: continue
                duplicates=conn.execute("SELECT DISTINCT v.id FROM vouchers v JOIN voucher_lines l ON l.voucher_id=v.id WHERE v.client_id=? AND v.status='active' AND v.date=? AND l.account_code=? AND l.debit_fen=? AND l.credit_fen=?",(cid,voucher['date'],line['account_code'],line['debit_fen'],line['credit_fen'])).fetchall()
                if duplicates:
                    # Source identity and the transaction fingerprint prevent
                    # duplicates. Different uniquely matched documents may
                    # legitimately have the same date and cash amount.
                    voucher['duplicate_review_note']=ev.get('duplicate_review_note','').strip() or '按用户匹配即入账策略：不同单据或交易标识的同日同额独立流水'
                    provenance['duplicate_policy']='automatic_distinct_matched_source'
                    provenance['same_amount_voucher_ids']=[item['id'] for item in duplicates]
            if provenance.get('classification_source')=='uncertain_transaction_note':
                row['classification_evidence']={'source':'uncertain_transaction_note','raw_note':reconciliation.normalize(t.get('note'))}
            elif provenance['subject_source'].endswith('_fallback'):
                classification=dict(classification,status='suggested',account_code=provenance['subject_account_code'],account_name=provenance['subject_name'],source=provenance['subject_source'])
            row.update(status='ready',voucher=voucher,basis=basis,classification=classification,automatic_posting=provenance)
        except (ApiError,ValueError,TypeError,AttributeError) as error:
            row['reason']=getattr(error,'message','已保存匹配数据异常，请重新核对')
        rows.append(row)
    result={'client_id':cid,'client_name':client['name'],'rows':rows,'invoice_rows':list(company_invoices.values()),
            'unmatched_invoices':[ii for ii in out['unmatched_invoices'] if ii not in company_invoices]}
    token_data={'input':{key:payload.get(key) for key in ('client_id','account_name','account_needs_review','transactions','invoices','receipts','evidence','archive_id')},'result':result}
    result['preview_token']=hashlib.sha256(json.dumps(token_data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def _store_company(conn,cid,payload,row,user):
    import source_archive
    is_invoice=row['document']=='company_invoice'
    transaction=row.get('transaction')
    invoice=row.get('invoice')
    ri=row.get('receipt_index')
    receipt=payload['receipts'][ri] if ri is not None else None
    provenance=row['automatic_posting']
    basis={'matching_basis':row['basis'],'automatic_posting':provenance,'account_name':payload['account_name']}
    archive_id=payload.get('archive_id')
    voucher=row['voucher']
    party=row['counterparty']
    value=row['amount_fen']
    lines=finance._lines(conn,cid,reconciliation._resolve_voucher_lines(conn,cid,voucher.get('lines'),transaction or {'counterparty':party},user))
    settlement=provenance.get('settlement')
    if settlement:
        movement=sum(line['debit_fen']-line['credit_fen'] for line in lines if line['account_code']==settlement['account_code'])
        if movement!=(value if is_invoice else -value):
            provenance=dict(provenance,settlement=None,suggested_settlement=settlement)
            basis['automatic_posting']=provenance
            basis['matching_basis']=['已调整预填往来分录，按确认后的科目入账']
    note=finance._text(voucher.get('duplicate_review_note'),'重复核验说明',500) if voucher.get('duplicate_review_note') else ''
    duplicates=[]
    if is_invoice:
        if any(line['is_cash'] for line in lines) or sum(line['debit_fen'] for line in lines)!=value:
            raise ApiError(400,'开票凭证须按发票价税合计填写非资金分录')
        source_type='reconciliation_invoice'
        source_id=reconciliation.normalize(invoice.get('number'))
        audit_action='reconcile_invoice_post'
        detail={'invoice':invoice}
    else:
        if finance._cash_net(lines)!=value:
            raise ApiError(400,'货币资金分录净额必须与流水收付款金额及方向一致')
        key=reconciliation._transaction_key(transaction)
        source_type='bank_receipt' if receipt else 'bank_statement'
        source_id=normalize_receipt_source(transaction,receipt) if receipt else key
        audit_action='reconcile_receipt_post' if receipt else 'reconcile_statement_post'
        detail={'transaction_key':key,'transaction':transaction,'matched_receipt':receipt}
        if not receipt:
            for line in lines:
                if not line['is_cash']: continue
                duplicates.extend(dict(item) for item in conn.execute('''SELECT DISTINCT v.id voucher_id,v.date,v.number,v.source_type,v.summary
                    FROM vouchers v JOIN accounting_voucher_lines l ON l.voucher_id=v.id
                    WHERE v.client_id=? AND v.status='active' AND v.date=? AND l.account_code=? AND l.debit_fen=? AND l.credit_fen=?
                    ORDER BY v.id''',(cid,voucher['date'],line['account_code'],line['debit_fen'],line['credit_fen'])))
            if duplicates and not note:
                raise ApiError(409,'发现日期、金额及资金科目相同的凭证，请核对是否重复',{'code':'possible_duplicate','candidates':duplicates})
    vid=finance._insert_voucher(conn,cid,voucher['date'],voucher.get('summary'),lines,user,source_type,source_id,
                               attachment_count=int(bool(invoice or receipt)),counterparty=party)
    source_archive.attach(conn,cid,archive_id,user,transaction,invoice,receipt,voucher_id=vid,basis=basis)
    audit(conn,user,audit_action,'vouchers',vid,{**detail,'automatic_posting':provenance,
          'duplicate_candidates':duplicates,'duplicate_review_note':note})
    result=reconciliation._posting_result(conn,cid,vid,False,transaction)
    return dict(result,status='posted',automatic_posting=provenance)


def save_batch(conn,payload,user):
    if payload.get('confirmed') is not True: raise ApiError(400,'请先核对并确认整批分录')
    with finance._write(conn):
        draft=preview(conn,payload)
        if payload.get('preview_token')!=draft['preview_token']: raise ApiError(409,'预览或账套状态已变化，请重新核对')
        if 'selected_invoice_index' in payload:
            ii=payload['selected_invoice_index']
            if type(ii) is not int or 'selected_indices' in payload:
                raise ApiError(400,'请选择一张待确认的公司发票')
            row=next((row for row in draft['invoice_rows'] if row['invoice_index']==ii),None)
            if not row or row['status']!='ready':
                raise ApiError(409,'所选发票仍需核对或已入账，请重新预览')
            row['voucher']=_confirmed_draft(payload,row['voucher'])
            row['automatic_posting']['operator_confirmed']=True
            saved=_store_company(conn,draft['client_id'],payload,row,user)
            return {'client_id':draft['client_id'],'client_name':draft['client_name'],'periods':[saved['period']],
                    'results':[dict(saved,invoice_index=ii)]}
        selected=payload.get('selected_indices')
        if selected is None: selected=[r['transaction_index'] for r in draft['rows'] if r['status'] in ('ready','posted')]
        if not isinstance(selected,list) or not selected or any(type(x) is not int for x in selected) or len(set(selected))!=len(selected): raise ApiError(400,'请选择需要保存的就绪项目')
        by_index={r['transaction_index']:r for r in draft['rows']}
        if any(i not in by_index or by_index[i]['status'] not in ('ready','posted') for i in selected): raise ApiError(409,'所选项目仍需核对，未保存任何记录')
        selected_rows=[by_index[i] for i in selected]
        if 'voucher' in payload:
            if len(selected_rows)!=1 or selected_rows[0]['status']!='ready': raise ApiError(409,'请选择一条待确认的发票核对记录')
            row=selected_rows[0]
            row['voucher']=_confirmed_draft(payload,row['voucher'])
            row['automatic_posting']=dict(row['automatic_posting'],operator_confirmed=True)
        results=[]
        for row in selected_rows:
            ti,ii=row['transaction_index'],row['invoice_index']
            company=row.get('document')=='company_statement'
            try:
                if company and row['status']=='ready':
                    saved=_store_company(conn,draft['client_id'],payload,row,user)
                elif row['status']=='posted' and (company or row.get('id')):
                    saved={key:row[key] for key in ('id','voucher_id','voucher_ref','voucher_date','period','posted_lines','already_posted') if key in row}
                else:
                    saved=_store_match(conn,payload,draft,row,user)
            except ApiError as err:
                if 'voucher' in payload: raise
                raise ApiError(err.status, '第 %d 条流水保存失败：%s；整批未保存'%(ti+1,err.message),{'transaction_index':ti,'cause':err.details})
            if row['status']=='posted' and row.get('id'): _attach_sources(conn,payload,row,saved,user)
            if row['classification_evidence']:
                audit(conn,user,'reconcile_auto_classification','reconciliation_records',saved['id'],row['classification_evidence'])
            if saved.get('voucher_id'): saved['status']='posted'
            results.append({'transaction_index':ti,'invoice_index':ii,**saved})
        periods=sorted({x['period'] for x in results})
        return {'client_id':draft['client_id'],'client_name':draft['client_name'],'periods':periods,'results':results}


def _auto_post_company_pair(conn,payload,draft,row,user):
    invoice_index=row.get('paired_index')
    if invoice_index is None:
        return None
    invoice=next(item for item in draft['invoice_rows'] if item['invoice_index']==invoice_index)
    if not row.get('source_date') or not invoice.get('source_date'):
        raise ApiError(409,row['reason'] or invoice['reason'])
    bank_first=row['source_date']<=invoice['source_date']
    first,second=(row,invoice) if bank_first else (invoice,row)
    cid=draft['client_id']

    def post(item):
        if item['status']=='posted':
            return reconciliation._posting_result(conn,cid,item['voucher_id'],True,item.get('transaction'))
        if item['status']!='ready':
            raise ApiError(409,item['reason'] or '对应单据仍需核对')
        return _store_company(conn,cid,payload,item,user)

    first_result=post(first)
    if first['status']!='posted' and second['status']!='posted':
        updated=preview(conn,payload)
        second=next(item for item in updated['invoice_rows'] if item['invoice_index']==invoice_index) if bank_first else updated['rows'][row['transaction_index']]
    second_result=post(second)
    return (first_result,second_result) if bank_first else (second_result,first_result)


def auto_post(conn,payload,user,include_company=True):
    """Post unique invoice matches, rechecking under a separate lock per row."""
    initial=preview(conn,payload)
    results=[]; periods=set()
    for original in initial['rows']:
        index=original['transaction_index']
        try:
            with finance._write(conn):
                draft=preview(conn,payload)
                row=draft['rows'][index]
                result={key:row[key] for key in ('transaction_index','invoice_index','counterparty','amount_fen','basis','classification','classification_evidence')}
                if row.get('document')=='company_statement':
                    pair=_auto_post_company_pair(conn,payload,draft,row,user) if include_company else None
                    if pair:
                        payment,invoice=pair
                        result.update(payment,status='posted',invoice_index=row['paired_index'],invoice_posting=invoice,reason='')
                        periods.update((payment['period'],invoice['period']))
                    elif row['status']=='posted':
                        result.update({key:row[key] for key in ('voucher_id','voucher_ref','voucher_date','period','posted_lines','already_posted')},status='posted',reason='')
                        periods.add(row['period'])
                    else:
                        result.update(status='review',reason=row['reason'] or '请在核对结果中确认公司往来凭证')
                elif not row['matched']:
                    result.update(status='review',reason=row['reason'])
                elif row['status']=='posted' and row.get('id'):
                    result.update({key:row[key] for key in ('id','voucher_id','voucher_ref','voucher_date','period','posted_lines','already_posted')},status='posted',reason='')
                    _attach_sources(conn,payload,row,result,user)
                    periods.add(row['period'])
                else:
                    saved=_store_match(conn,payload,initial,row,user)
                    if row['status'] in ('ready','posted'):
                        result.update(saved,status='posted',reason='')
                        periods.add(saved['period'])
                        if row.get('classification_evidence'):
                            audit(conn,user,'reconcile_auto_classification','reconciliation_records',saved['id'],row['classification_evidence'])
                    else: result.update(saved,status='saved_pending',reason=row['reason'])
                if row.get('automatic_posting'): result['automatic_posting']=row['automatic_posting']
            results.append(result)
        except (ApiError,ValueError,TypeError,AttributeError) as error:
            results.append({key:original[key] for key in ('transaction_index','invoice_index','counterparty','amount_fen','basis','classification','classification_evidence')} |
                           {'status':'review','reason':getattr(error,'message','已保存匹配数据异常，请重新核对')})
    return {'client_id':initial['client_id'],'client_name':initial['client_name'],'rows':results,
            'unmatched_invoices':initial['unmatched_invoices'],'periods':sorted(periods),
            'invoice_verification':'not_checked','human_review':'not_performed'}


def retry_saved(conn,client_id,user):
    """Upgrade saved matched records to actual vouchers; active postings are skipped."""
    if isinstance(client_id,bool): raise ApiError(400,'客户无效')
    try: cid=int(client_id)
    except (TypeError,ValueError): raise ApiError(400,'请选择客户账套')
    client=finance._client(conn,cid)
    records=conn.execute("SELECT * FROM reconciliation_records WHERE client_id=? AND status IN ('matched','reviewed') ORDER BY id",(cid,)).fetchall()
    results=[]; periods=set()
    for stored in records:
        record=dict(stored)
        try:
            transaction=json.loads(record['transaction_json']); invoice=json.loads(record['invoice_json'])
            if _record_posting(conn,cid,record,transaction): continue
            meta=json.loads(record.get('batch_metadata_json') or '{}')
            evidence=meta.get('evidence') or {}
            payload={'client_id':cid,'account_name':record['account_name'],'transactions':[transaction],'invoices':[invoice],'evidence':[evidence]}
            context=meta.get('suggestion_context')
            index=0
            if isinstance(context,list) and 1<=len(context)<=100 and all(isinstance(item,dict) for item in context):
                matching=[i for i,item in enumerate(context) if all(reconciliation.normalize(item.get(key))==reconciliation.normalize(transaction.get(key)) for key in ('date','amount','counterparty','note'))]
                if len(matching)==1:
                    index=matching[0]; payload['transactions']=[dict(item) for item in context]; payload['transactions'][index]=transaction
                    payload['evidence']=[{} for _ in context]; payload['evidence'][index]=evidence
            outcome=auto_post(conn,payload,user)
            row=outcome['rows'][index]
            results.append({'id':record['id'],**row}); periods.update(outcome['periods'])
        except (ApiError,ValueError,TypeError,AttributeError) as error:
            results.append({'id':record['id'],'status':'review','reason':getattr(error,'message','已保存记录数据异常，请重新核对')})
    return {'client_id':cid,'client_name':client['name'],'rows':results,'periods':sorted(periods)}


def _receipt_account(conn,cid,code,sub,user):
    """Leaf for the suggested account, creating the named subaccount when the parent allows it."""
    parent=conn.execute('SELECT * FROM accounts WHERE client_id=? AND code=?',(cid,code)).fetchone()
    if not parent: raise ApiError(409,'账套中没有科目 '+code)
    if sub:
        named=[dict(x) for x in conn.execute('SELECT * FROM accounts WHERE client_id=? AND code LIKE ? AND code<>? AND name=?',(cid,code+'%',code,sub))]
        leaves=[x for x in named if _leaf(conn,cid,x['code'])]
        if len(leaves)==1: return leaves[0]
        if len(leaves)>1: raise ApiError(409,'科目 %s 下有多个名为“%s”的明细，请人工选择'%(code,sub))
        used=_leaf(conn,cid,code) and (conn.execute('SELECT 1 FROM voucher_lines WHERE client_id=? AND account_code=?',(cid,code)).fetchone()
            or conn.execute('SELECT 1 FROM opening_balances WHERE client_id=? AND account_code=? AND (debit_fen<>0 OR credit_fen<>0)',(cid,code)).fetchone())
        if not used: return finance._create_account(conn,cid,{'parent_code':code,'name':sub},user)
    leaf=_leaf(conn,cid,code)
    if not leaf: raise ApiError(409,'科目 %s 已有明细科目，请人工选择末级科目'%code)
    return leaf


def _confirmed_draft(payload,draft):
    voucher=payload.get('voucher')
    if not isinstance(voucher,dict): raise ApiError(400,'请确认完整的凭证摘要和分录')
    if voucher.get('date')!=draft['date']: raise ApiError(400,'凭证日期须与流水日期一致')
    return voucher


def post_receipts(conn,payload,user,preview_only=False):
    """Post every unique receipt match; overlapping invoices share one real voucher."""
    from contextlib import nullcontext
    if not isinstance(payload,dict): raise ApiError(400,'核对批次无效')
    if isinstance(payload.get('client_id'),bool): raise ApiError(400,'客户无效')
    try: cid=int(payload.get('client_id'))
    except (TypeError,ValueError): raise ApiError(400,'请选择客户账套')
    client=finance._client(conn,cid)
    import source_archive
    source_archive.validate(conn,cid,payload.get('archive_id'))
    txs=payload.get('transactions'); receipts=payload.get('receipts'); ivs=payload.get('invoices') or []
    if not isinstance(txs,list) or not txs or len(txs)>500 or not isinstance(receipts,list) or not receipts: raise ApiError(400,'需要流水和银行回单')
    account=reconciliation.normalize(payload.get('account_name'))
    full=client.get('enterprise_name','')
    if not account or account not in {reconciliation.normalize(client['name']),reconciliation.normalize(full)}: raise ApiError(400,'本方名称必须与客户名称或企业全称一致')
    evidence=payload.get('evidence') or []
    allowed={'treatment','bank_account_code','settlement_account_code','tax_treatment','tax_account_code','evidence_note','duplicate_review_note','business_account_code'}
    if not isinstance(evidence,list) or len(evidence)>len(txs) or any(not isinstance(item,dict) or any(key not in allowed or not isinstance(value,str) or len(value)>500 for key,value in item.items()) for item in evidence):
        raise ApiError(400,'业务依据格式无效')
    selected=payload.get('transaction_index')
    if selected is not None and (type(selected) is not int or not 0<=selected<len(txs)):
        raise ApiError(400,'流水位置无效，请重新核对')
    if not preview_only and 'voucher' in payload and selected is None:
        raise ApiError(400,'请选择一条待确认的回单核对记录')
    out=reconciliation.match(txs,ivs,account,False,conn,cid,receipts)
    pairs=out['receipt_matches']['matches']
    company_indices=set(out.get('company_receipts',[]))
    invoice_matches={item['transaction_index']:item for item in out['matches']
                     if item['status']=='matched' and item['transaction_index'] not in company_indices}
    company_payload=dict(payload,invoices=ivs)
    pairs_by_tx={item['transaction_index']:item for item in pairs}
    company_preview=_company_rows(conn,client,company_payload,pairs_by_tx)[0] if company_indices and preview_only else {}
    invoice_results={}
    if invoice_matches and not preview_only and selected is None and len(txs)<=100 and len(ivs)<=100:
        invoice_results={row['transaction_index']:row for row in auto_post(conn,payload,user,include_company=False)['rows']}
    rows=[]; periods=set()
    for pair in pairs:
        ti=pair['transaction_index']; t=txs[ti]; result={'transaction_index':ti,'receipt_index':pair.get('receipt_index')}
        if selected is not None and ti!=selected: continue
        if ti in invoice_matches and not preview_only and selected is None:
            invoice_result=invoice_results.get(ti)
            if invoice_result is None:
                match=invoice_matches[ti]
                matched_receipts=[receipts[pair['receipt_index']]] if pair['status']=='matched' else []
                one=dict(payload,transactions=[t],invoices=[ivs[match['invoice_index']]],receipts=matched_receipts,evidence=[evidence[ti] if ti<len(evidence) else {}])
                invoice_result=auto_post(conn,one,user)['rows'][0]
            if invoice_result['status']=='posted':
                result.update(invoice_result,transaction_index=ti,receipt_index=pair.get('receipt_index'))
                rows.append(result); periods.add(result['period']); continue
        if pair['status']!='matched':
            rows.append(dict(result,status='review',reason='；'.join(pair['reasons']))); continue
        receipt=receipts[pair['receipt_index']]
        try:
            with (nullcontext() if preview_only else finance._write(conn)):
                if ti in company_indices:
                    row=company_preview[ti] if preview_only else _company_rows(conn,client,company_payload,pairs_by_tx)[0][ti]
                    if row['status']=='posted':
                        if not preview_only:
                            source_archive.attach(conn,cid,payload.get('archive_id'),user,t,receipt=receipt,
                                                  voucher_id=row['voucher_id'],basis={'account_name':account})
                        row.update(reason='该回单已生成凭证',lines=row['posted_lines'])
                    elif row['status']=='ready' and not preview_only:
                        if selected is not None:
                            row['voucher']=_confirmed_draft(payload,row['voucher'])
                            row['automatic_posting']['operator_confirmed']=True
                        row.update(_store_company(conn,cid,company_payload,row,user))
                        business=next((line for line in row['posted_lines'] if line['report_key']!='cash'),{})
                        row.update(account_code=business.get('account_code'),account_name=business.get('account_name'),lines=row['posted_lines'])
                    elif row['status']=='review' and selected is not None and not preview_only:
                        raise ApiError(409,row['reason'])
                    if row['status']=='posted': periods.add(row['period'])
                    rows.append(row); continue
                own=reconciliation.normalize(receipt.get('own_name'))
                if own and own not in {account,reconciliation.normalize(client['name']),reconciliation.normalize(full)}:
                    raise ApiError(409,'回单本方名称与账套不一致')
                existing=_receipt_posting(conn,cid,t,receipt)
                record=conn.execute('SELECT * FROM reconciliation_records WHERE client_id=? AND transaction_key=?',(cid,reconciliation._transaction_key(t))).fetchone()
                if not existing and record: existing=_record_posting(conn,cid,dict(record),t)
                if not existing: existing=reconciliation._existing_statement_posting(conn,cid,t)
                if existing:
                    if not preview_only:
                        source_archive.attach(conn,cid,payload.get('archive_id'),user,t,receipt=receipt,
                                              voucher_id=existing['voucher_id'],basis={'matching_basis':pair.get('reasons') or ['流水与银行回单唯一匹配'],
                                              'evidence':evidence[ti] if ti<len(evidence) else {},'account_name':account})
                    result.update(existing,status='posted',reason='该回单已生成凭证')
                    result['lines']=result['posted_lines']; rows.append(result); periods.add(result['period']); continue
                classification=out['classification_transactions'][ti] or {}
                suggestion=classification.get('rule_suggestion') or {}
                if suggestion.get('account_code'):
                    classification=dict(classification,account_code=suggestion['account_code'],account_name=suggestion.get('account_name'),sub_account=suggestion.get('sub_account',''))
                ev=evidence[ti] if ti<len(evidence) else {}
                voucher,basis,provenance=_automatic_draft(conn,cid,t,None,classification,ev,'receipt',receipt)
                finance._open(conn,cid,voucher['date'][:7])
                if preview_only:
                    rows.append(dict(result,status='ready',voucher=voucher,basis=basis,classification=classification))
                    continue
                if selected is not None:
                    voucher=_confirmed_draft(payload,voucher)
                    provenance=dict(provenance,operator_confirmed=True)
                bank_code=provenance['funding_account_code']; value=reconciliation.fen(t.get('amount'))
                duplicates=conn.execute("SELECT DISTINCT v.id FROM vouchers v JOIN voucher_lines l ON l.voucher_id=v.id WHERE v.client_id=? AND v.status='active' AND v.date=? AND l.account_code=? AND l.debit_fen=? AND l.credit_fen=?",(cid,voucher['date'],bank_code,max(value,0),max(-value,0))).fetchall()
                if duplicates:
                    provenance['duplicate_policy']='automatic_distinct_matched_source'
                    provenance['same_amount_voucher_ids']=[item['id'] for item in duplicates]
                lines=finance._lines(conn,cid,reconciliation._resolve_voucher_lines(conn,cid,voucher.get('lines'),t,user))
                if finance._cash_net(lines)!=value:
                    raise ApiError(400,'货币资金分录净额必须与流水收付款金额及方向一致')
                vid=finance._insert_voucher(conn,cid,voucher['date'],voucher['summary'],lines,user,'bank_receipt',normalize_receipt_source(t,receipt),attachment_count=1,counterparty=reconciliation.normalize(t.get('counterparty')))
                source_archive.attach(conn,cid,payload.get('archive_id'),user,t,receipt=receipt,voucher_id=vid,
                                      basis={'matching_basis':basis,'evidence':ev,'classification':classification,
                                             'automatic_posting':provenance,'account_name':account})
                audit(conn,user,'reconcile_receipt_post','vouchers',vid,{'receipt':receipt.get('number'),'transaction_key':reconciliation._transaction_key(t),'automatic_posting':provenance,'rule_ids':suggestion.get('rule_ids',[]),
                      'transaction':{key:t.get(key,'') for key in ('date','amount','counterparty','note','source')},
                      'matched_receipt':{key:receipt.get(key,'') for key in ('number','date','amount','counterparty','own_name','note','purpose','source')}})
                posting=reconciliation._posting_result(conn,cid,vid,False,t)
                business=next((line for line in posting['posted_lines'] if line['report_key']!='cash'),{})
                result.update(posting,status='posted',reason='',account_code=business.get('account_code'),account_name=business.get('account_name'),basis=basis,automatic_posting=provenance,lines=posting['posted_lines'])
                periods.add(result['period']); rows.append(result)
        except ApiError as error:
            if not preview_only and selected is not None: raise
            rows.append(dict(result,status='review',reason=error.message))
    return {'client_id':cid,'client_name':client['name'],'rows':rows,'periods':sorted(periods)}
