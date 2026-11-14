"""Task-local DNS adapter. Returns DNS answers; original egress policy still validates and pins."""
import json,subprocess
def resolve(host,port):
    if host != "openrouter.ai" or port != 443:
        raise OSError("Task resolver only supports the approved public endpoint")
    script="import socket,json; print(json.dumps(sorted({r[4][0] for r in socket.getaddrinfo('openrouter.ai',443,type=socket.SOCK_STREAM)})))"
    result=subprocess.run(["docker","exec","modelmirror-server","python","-c",script],capture_output=True,text=True,timeout=10,check=True)
    values=json.loads(result.stdout)
    if not isinstance(values,list) or not values or not all(isinstance(x,str) for x in values):
        raise OSError("Invalid DNS response")
    return values
