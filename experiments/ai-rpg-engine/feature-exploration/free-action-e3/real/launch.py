"""Capture only the previously selected connection. No plaintext credentials on disk."""
import ast,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5]
sys.path.insert(0,str(ROOT))
from bridge import serve
if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in ("--authorized-mock","--authorized-real"):raise SystemExit("Explicit authorization mode required")
    mode=sys.argv[1].removeprefix("--authorized-")
    run=ROOT/"experiments/ai-rpg-engine/.rpg03-work"/("e3-clarification-"+mode+("-v2" if mode=="mock" else "-v1"))
    # Reuse the audited read-only R4 snapshot capture text, not its launcher or dispatch.
    source=ROOT/"experiments/ai-rpg-engine/agent-core/context/real/launch.py"
    tree=ast.parse(source.read_text(encoding="utf-8-sig"))
    remote=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="REMOTE" for t in n.targets))
    result=subprocess.run(["docker","exec","-i","modelmirror-server","python","-"],input=remote,text=True,encoding="utf8",capture_output=True)
    if result.returncode:raise SystemExit("Selected snapshot capture failed; no provider request sent")
    try:snapshot=json.loads(result.stdout)
    except ValueError:raise SystemExit("Selected snapshot invalid; no provider request sent")
    del result
    serve(run,mode,snapshot,18882 if mode=="mock" else 18883)
