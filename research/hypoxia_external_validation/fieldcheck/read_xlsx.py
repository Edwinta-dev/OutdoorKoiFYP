import sys,zipfile,re,json
import xml.etree.ElementTree as ET
ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
z=zipfile.ZipFile(sys.argv[1])
ss=[ ''.join(t.text or '' for t in si.iter('{%s}t'%ns['m'])) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)]
rows=[]
for r in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).find('m:sheetData',ns):
    d={}
    for c in r:
        col=re.match(r'[A-Z]+',c.get('r')).group()
        v=c.find('m:v',ns)
        if v is None: continue
        d[col]=ss[int(v.text)] if c.get('t')=='s' else v.text
    rows.append(d)
json.dump(rows,open(sys.argv[2],'w'))
print(len(rows))
