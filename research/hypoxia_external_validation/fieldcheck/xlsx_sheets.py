import sys,zipfile,re
import xml.etree.ElementTree as ET
M='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
z=zipfile.ZipFile(sys.argv[1]);maxr=int(sys.argv[2])
ss=[]
if 'xl/sharedStrings.xml' in z.namelist():
    ss=[''.join(t.text or '' for t in si.iter(M+'t')) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(M+'si')]
rels={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
for s in ET.fromstring(z.read('xl/workbook.xml')).find(M+'sheets'):
    t=rels[s.get(R+'id')];t=t.lstrip('/');t=t if t.startswith('xl/') else 'xl/'+t
    rows=list(ET.fromstring(z.read(t)).find(M+'sheetData'))
    print('## sheet',ascii(s.get('name')),'rows',len(rows))
    for r in rows[:maxr]:
        out=[]
        for c in r:
            v=c.find(M+'v')
            if v is None:
                isv=c.find(M+'is')
                out.append(''.join(x.text or '' for x in isv.iter(M+'t')) if isv is not None else '');continue
            out.append(ss[int(v.text)] if c.get('t')=='s' else v.text)
        print(ascii(' | '.join(out))[:420])
