"""Inspect browser-generated print output using the bundled pypdf runtime."""
from pathlib import Path
from pypdf import PdfReader

root=Path(__file__).resolve().parents[1]/'tmp'/'voucher-print-verification'
expected={'a4-two':1,'a4-odd':2,'a4-three':1,'a4-one-hidden':2,'long-voucher':2,'custom-digits':1,'all-vouchers':3}
for name,count in expected.items():
    pdf=PdfReader(root/(name+'.pdf'));text=''.join(''.join(p.extract_text() for p in pdf.pages).split())
    size=[(round(float(p.mediabox.width)*25.4/72,1),round(float(p.mediabox.height)*25.4/72,1)) for p in pdf.pages]
    assert len(pdf.pages)==count,(name,len(pdf.pages))
    target=(240,140) if name=='custom-digits' else (210,297)
    assert all(abs(w-target[0])<.3 and abs(h-target[1])<.3 for w,h in size),(name,size)
    assert '中企鸿昌代账台' not in text and '127.0.0.1' not in text and '2026/11/17' not in text
    if name=='a4-two':assert text.count('记账凭证')==2
    if name=='a4-one-hidden':assert '5602' not in text and '制单人' not in text
    if name=='long-voucher':assert all(f'管理费用第{i+1}行' in text for i in range(12)) and '银行存款最后一行' in text
    print(name,len(pdf.pages),size,text.count('记账凭证'))
print('PDF counts, dimensions, complete entries and removal of browser headers passed')
