"""Reuse Windows user-bound DPAPI; R9 task paths only."""
import importlib.util, json, sys
from pathlib import Path
src=Path(__file__).parents[2]/'tasks/real/credential.py'
spec=importlib.util.spec_from_file_location('r9_dpapi',src); module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def auth_path(run):
    p=Path(run).resolve(); assert p.parent==Path('C:/tmp/mm-r9-live').resolve() and p.name.startswith(('mock-','real-'))
    return p/'local-auth.bin'
def save_auth(run,value): auth_path(run).write_bytes(module.transform(json.dumps(value).encode(),True))
def read_auth(run): return json.loads(module.transform(auth_path(run).read_bytes(),False))
if __name__=='__main__':
    assert len(sys.argv)==3 and sys.argv[1]=='--read'
    print(json.dumps(read_auth(sys.argv[2])))
