"""Authorized capture of the selected encrypted route; never print snapshot."""
import ast,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT))
from bridge import serve
if __name__=="__main__":
    assert len(sys.argv)==4 and sys.argv[1] in ("--authorized-mock","--authorized-real")
    mode=sys.argv[1].removeprefix("--authorized-");run=Path(sys.argv[2]).resolve();port=int(sys.argv[3])
    assert run.parent==Path("C:/tmp/mm-r7-live").resolve() and run.name.startswith(mode+"-")
    assert port=={"mock":18896,"real":18897}[mode]
    assert not (run/"execution-claim.json").exists(),"Existing invocation must not be replayed"
    # Reuse the reviewed read-only selected-connection snapshot query verbatim.
    source=ast.parse((Path(__file__).parents[2]/"actions/real/launch.py").read_text(encoding="utf-8"))
    remote=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="REMOTE" for t in n.targets))
    result=subprocess.run(["docker","exec","-i","modelmirror-server","python","-"],input=remote,text=True,encoding="utf-8",capture_output=True)
    if result.returncode:raise SystemExit("Selected snapshot capture failed; no provider request sent")
    try:snapshot=json.loads(result.stdout)
    except ValueError:raise SystemExit("Selected snapshot invalid; no provider request sent")
    del result
    serve(run,mode,snapshot,port)
