"""Windows user-bound protection for the ephemeral local service token."""
import ctypes,json,sys
from ctypes import wintypes
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5]
class Blob(ctypes.Structure):
    _fields_=[("size",wintypes.DWORD),("data",ctypes.POINTER(ctypes.c_byte))]
def transform(raw,protect):
    buf=ctypes.create_string_buffer(raw);src=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte)));dst=Blob()
    api=ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok=api(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst))
    if not ok:raise RuntimeError("Task credential protection failed")
    try:return ctypes.string_at(dst.data,dst.size)
    finally:ctypes.windll.kernel32.LocalFree(dst.data)
def auth_path(run):
    p=Path(run).resolve();assert p.parent==Path("C:/tmp/mm-r7-live").resolve() and p.name.startswith(("mock-","real-"))
    return p/"local-auth.bin"
def save_auth(run,value):auth_path(run).write_bytes(transform(json.dumps(value).encode(),True))
def read_auth(run):return json.loads(transform(auth_path(run).read_bytes(),False))
if __name__=="__main__":
    assert len(sys.argv)==3 and sys.argv[1]=="--read"
    print(json.dumps(read_auth(sys.argv[2])))
