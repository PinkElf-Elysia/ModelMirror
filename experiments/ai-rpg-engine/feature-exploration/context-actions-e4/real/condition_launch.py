"""Capture only the previously selected connection. No plaintext credentials on disk."""
import ast,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5]
sys.path.insert(0,str(ROOT))
from condition_bridge import serve
if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in ("--authorized-mock","--authorized-real"):raise SystemExit("Explicit authorization mode required")
    mode=sys.argv[1].removeprefix("--authorized-")
    run=ROOT/"experiments/ai-rpg-engine/.rpg03-work"/("e4-condition-check-"+mode+"-v1")
    if mode=="real":
        mock=ROOT/"experiments/ai-rpg-engine/.rpg03-work/e4-condition-check-mock-v1"
        report=json.loads((mock/"review.json").read_text(encoding="utf8"))
        cleanup=json.loads((mock/"cleanup.json").read_text(encoding="utf8"))
        assert report["mode"]=="mock" and report["failure"] is None and len(report["outputs"])==1
        assert all(o["passed"] for o in report["outputs"]) and report["sourceUnchanged"] and cleanup["credentialDirectoryRemoved"]
    # Reuse the audited read-only R4 snapshot capture text, not its launcher or dispatch.
    source=ROOT/"experiments/ai-rpg-engine/agent-core/context/real/launch.py"
    tree=ast.parse(source.read_text(encoding="utf-8-sig"))
    remote=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="REMOTE" for t in n.targets))
    result=subprocess.run(["docker","exec","-i","modelmirror-server","python","-"],input=remote,text=True,encoding="utf8",capture_output=True)
    if result.returncode:raise SystemExit("Selected snapshot capture failed; no provider request sent")
    try:snapshot=json.loads(result.stdout)
    except ValueError:raise SystemExit("Selected snapshot invalid; no provider request sent")
    del result
    serve(run,mode,snapshot,18911 if mode=="mock" else 18912)
