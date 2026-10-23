"""Minimal stdlib xlsx dumper (no openpyxl in the venv). usage: xl.py file [maxrows]"""
import sys,zipfile,re,xml.etree.ElementTree as ET
ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def read(path):
    z=zipfile.ZipFile(path)
    ss=[]
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns):
            ss.append(''.join(t.text or '' for t in si.iter('{%s}t'%ns['m'])))
    wb=ET.fromstring(z.read('xl/workbook.xml'))
    names=[s.get('name') for s in wb.find('m:sheets',ns)]
    out={}
    for i,n in enumerate(names,1):
        rows=[]
        for r in ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')).iter('{%s}row'%ns['m']):
            d={}
            for c in r.findall('m:c',ns):
                col=re.match(r'[A-Z]+',c.get('r')).group()
                v=c.find('m:v',ns)
                if v is None:
                    t=c.find('m:is',ns); val=''.join(x.text or '' for x in t.iter('{%s}t'%ns['m'])) if t is not None else None
                else: val=ss[int(v.text)] if c.get('t')=='s' else v.text
                d[col]=val
            rows.append(d)
        out[n]=rows
    return out
if __name__=='__main__':
    mx=int(sys.argv[2]) if len(sys.argv)>2 else 12
    for n,rows in read(sys.argv[1]).items():
        print('==',n,len(rows),'rows')
        for r in rows[:mx]: print(r)
