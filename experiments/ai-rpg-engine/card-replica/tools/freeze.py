from pathlib import Path
import hashlib,json,shutil,sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from PIL import Image

BASE=Path(__file__).resolve().parents[1]
NS='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
def sha(data):return hashlib.sha256(data).hexdigest()
def extract(path):
    with ZipFile(path) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        # Preserve paragraph order, empty paragraphs, explicit tabs and line breaks.
        paragraphs=[];seen=[]
        for p in root.iter('{'+NS+'}p'):
            out=[]
            for n in p.iter():
                tag=n.tag.split('}')[-1]
                if tag=='t':out.append(n.text or '');seen.append(n.text or '')
                elif tag=='tab':out.append('\t')
                elif tag in ('br','cr'):out.append('\n')
            paragraphs.append(''.join(out))
        alltext=[n.text or '' for n in root.iter('{'+NS+'}t')]
        assert seen==alltext,'Nested paragraph text requires explicit extraction handling'
        unsupported=['altChunk','del','ins','txbxContent','numPr','sym']
        found={tag:len(list(root.iter('{'+NS+'}'+tag))) for tag in unsupported}
        assert not any(found.values()),f'Unsupported extraction structures: {found}'
        extra=[]
        for name in archive.namelist():
            if name.startswith(('word/header','word/footer','word/footnotes','word/endnotes')) and name.endswith('.xml'):
                if any(n.text for n in ET.fromstring(archive.read(name)).iter('{'+NS+'}t')):extra.append(name)
        assert not extra,f'Additional text parts: {extra}'
        text='\n'.join(paragraphs)
        return text,{'paragraphs':len(paragraphs),'textRuns':len(alltext),'textCharacters':len(text),'orderedTextRunsVerified':True,'unsupportedStructures':found}

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    source=Path(sys.argv[1]).resolve()
    target=BASE/'.local'/'originals'
    manifest={'version':1,'authority':'Author-provided; human-reviewed; no semantic audit or rewriting','extraction':'OOXML w:t in document order; paragraph LF, w:tab TAB, w:br LF; UTF-8 without BOM; no trimming','files':[],'prompts':{}}
    bundle={}
    aliases={'系统提示词.docx':'system','前置词.docx':'prefix','后置词.docx':'suffix','部分基于世界书可能触发并附加在后置词的规则.docx':'worldbookSource'}
    for f in sorted(source.rglob('*')):
        if not f.is_file():continue
        # Word owner/lock files are transient, not author resource documents.
        if f.name.startswith('~$') and f.suffix.lower()=='.docx':
            print('Ignored transient Word owner file: '+f.name, file=sys.stderr)
            continue
        rel=f.relative_to(source);data=f.read_bytes();dest=target/rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():assert dest.read_bytes()==data,'Frozen original changed'
        else:shutil.copy2(f,dest)
        row={'path':rel.as_posix(),'bytes':len(data),'sha256':sha(data)}
        if f.suffix.lower() in ('.png','.jpg','.jpeg'):
            with Image.open(f) as im:row['size']=list(im.size)
        if f.name in aliases:
            key=aliases[f.name];value,check=extract(f);bundle[key]=value
            manifest['prompts'][key]={'source':rel.as_posix(),'sha256':sha(value.encode()),**check}
        manifest['files'].append(row)
    assert len(manifest['files'])==177 and len(bundle)==4
    (BASE/'resources').mkdir(exist_ok=True)
    for name,obj in [('prompts.json',bundle),('MANIFEST.json',manifest)]:
        dest=BASE/'resources'/name;encoded=json.dumps(obj,ensure_ascii=False,indent=2)+'\n'
        if dest.exists():assert dest.read_text(encoding='utf-8')==encoded,'Frozen resource drift'
        else:dest.write_text(encoded,encoding='utf-8')
    print(json.dumps({'verifiedFiles':len(manifest['files']),'prompts':manifest['prompts']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
