"""Explicit-host SSH entry point; trust is pinned to a supplied known_hosts file."""
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

from . import host_bootstrap
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, private_file


def register_host(commands):
    p=commands.add_parser("host",help="preflight/install the runtime ledger alpha on an explicit Linux SSH host")
    p.add_argument("action",choices=("preflight","install"))
    p.add_argument("--host",required=True)
    p.add_argument("--user",required=True)
    p.add_argument("--identity",type=Path,required=True)
    p.add_argument("--known-hosts",type=Path,required=True)
    p.add_argument("--root",required=True)
    p.add_argument("--deployment",required=True)
    p.add_argument("--port",type=int,default=8787)
    p.add_argument("--ssh-port",type=int,default=22)
    p.add_argument("--wheelhouse",type=Path)
    p.add_argument("--password-file",type=Path)


def run_host(args):
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}",args.host) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}",args.user):
            raise RuntimeFault("SSH_TARGET_INVALID")
        for path in (args.identity,args.known_hosts):
            if not path.is_absolute() or path.resolve()!=path:
                raise RuntimeFault("SSH_FILES_ABSOLUTE_REQUIRED")
            private_file(path)
        if not 1<=args.ssh_port<=65535:
            raise RuntimeFault("SSH_PORT_INVALID")
        payload={"action":args.action,"root":args.root,"deployment":args.deployment,"port":args.port}
        if args.action=="install":
            if not args.wheelhouse or not args.wheelhouse.is_dir():
                raise RuntimeFault("RELEASE_WHEELHOUSE_REQUIRED")
            paths=sorted(args.wheelhouse.glob("*.whl"))
            if not 1<=len(paths)<=20 or sum(p.stat().st_size for p in paths)>64*1024*1024:
                raise RuntimeFault("RELEASE_WHEELHOUSE_SIZE_INVALID")
            payload["wheels"]=[]
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    raise RuntimeFault("WHEEL_NOT_REGULAR")
                raw=path.read_bytes()
                payload["wheels"].append({"name":path.name,"sha256":hashlib.sha256(raw).hexdigest(),"data":base64.b64encode(raw).decode()})
            payload["password"]=secret_input(args.password_file,"客户管理员密码: ")
        code=Path(host_bootstrap.__file__).read_text()
        command=["ssh","-T","-p",str(args.ssh_port),"-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","IdentitiesOnly=yes",
                 "-o","ConnectTimeout=15","-o","UserKnownHostsFile="+str(args.known_hosts),
                 "-i",str(args.identity),args.user+"@"+args.host,"python3 -c "+shlex.quote(code)]
        result=subprocess.run(command,input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600)
        try:
            response=json.loads(result.stdout)
        except ValueError:
            raise RuntimeFault("SSH_CONNECTION_OR_REMOTE_RESPONSE_FAILED") from None
        print(json.dumps(response,ensure_ascii=False))
        return 0 if result.returncode==0 else 2
    except RuntimeFault as error:
        print(json.dumps({"error":str(error)}));return 2
    except (OSError,ValueError,subprocess.TimeoutExpired):
        print(json.dumps({"error":"HOST_OPERATION_UNCERTAIN_READ_BACK_BEFORE_RETRY"}));return 2
