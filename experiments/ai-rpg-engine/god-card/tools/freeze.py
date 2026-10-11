from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import hashlib, json, sys

BASE = Path(__file__).resolve().parents[1]
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
def sha(data):
    return hashlib.sha256(data).hexdigest()

def extract(source):
    original = source.read_bytes()
    with ZipFile(source) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        body = root.find(W + 'body')
        assert body is not None, 'Missing OOXML body'
        unsupported = {tag: len(list(body.iter(W + tag))) for tag in
                       ('altChunk', 'del', 'ins', 'txbxContent', 'numPr', 'sym', 'drawing')}
        assert not any(unsupported.values()), f'Extraction structure requires handling: {unsupported}'
        paragraphs, seen = [], []
        for paragraph in body.iter(W + 'p'):
            fragments = []
            for node in paragraph.iter():
                if node.tag == W + 't':
                    fragments.append(node.text or '')
                    seen.append(node.text or '')
                elif node.tag == W + 'tab':
                    fragments.append('\t')
                elif node.tag in (W + 'br', W + 'cr'):
                    fragments.append('\n')
            paragraphs.append(''.join(fragments))
        all_runs = [node.text or '' for node in root.iter(W + 't')]
        assert seen == all_runs, 'Text outside body or nested paragraphs requires handling'
        for name in archive.namelist():
            if name.startswith(('word/header', 'word/footer', 'word/footnotes', 'word/endnotes')) and name.endswith('.xml'):
                assert not any(n.text for n in ET.fromstring(archive.read(name)).iter(W + 't')), 'Extra text part: ' + name
    text = '\n'.join(paragraphs)
    manifest = {'format': 1, 'cardId': 'god', 'authority': 'Author-provided and human-reviewed; no semantic edits',
                'extraction': 'OOXML body order; paragraph LF, explicit break LF, tab TAB; no trimming or normalization',
                'source': {'name': source.name, 'bytes': len(original), 'sha256': sha(original)},
                'system': {'sha256': sha(text.encode('utf-8')), 'unicodeCharacters': len(text),
                           'paragraphs': len(paragraphs), 'emptyParagraphs': paragraphs.count(''),
                           'textRuns': len(all_runs), 'orderedTextRunsVerified': True},
                'prefix': None, 'suffix': None, 'worldbook': None}
    return original, text, manifest

def main():
    source = Path(sys.argv[1]).resolve()
    assert source.suffix.lower() == '.docx' and not source.name.startswith('~$'), 'A formal DOCX is required'
    original, text, manifest = extract(source)
    outputs = {BASE / 'resources' / 'prompts.json': json.dumps({'system': text}, ensure_ascii=False, indent=2).encode('utf-8') + b'\n',
               BASE / 'resources' / 'MANIFEST.json': json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8') + b'\n',
               BASE.parent / '.rpg04-work' / 'god-card' / 'originals' / source.name: original}
    # Validate all existing files before writing any new artifact; drift never overwrites a freeze.
    for dest, data in outputs.items():
        assert not dest.exists() or dest.read_bytes() == data, 'Frozen resource drift: ' + str(dest)
    for dest, data in outputs.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            with dest.open('xb') as f:
                f.write(data)
    print(json.dumps(manifest, ensure_ascii=True))

if __name__ == '__main__':
    main()
