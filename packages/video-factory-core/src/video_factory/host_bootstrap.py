"""Standalone SSH bootstrap: Python stdlib only until the locked wheels install.

Input arrives through stdin, never the remote command line. This script is also
exercised on the isolated Linux CI runner using an ephemeral destination.
"""
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys


def require(condition, code):
    if not condition:
        raise ValueError(code)


def run(argv, **kwargs):
    result=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240,**kwargs)
    require(result.returncode==0,"HOST_COMMAND_FAILED")
    return result.stdout


def main(payload):
    require(platform.system()=="Linux" and platform.machine()=="x86_64","LINUX_X86_64_REQUIRED")
    require(sys.version_info >= (3,11),"PYTHON_311_REQUIRED")
    require(importlib.util.find_spec("ensurepip") is not None,"PYTHON_VENV_PACKAGE_REQUIRED")
    root=Path(payload["root"])
    require(root.is_absolute() and root.resolve()==root and re.fullmatch(r"/[A-Za-z0-9_./-]+",str(root)),"ROOT_UNSAFE")
    require(root.parent.is_dir(),"ROOT_PARENT_MISSING")
    parent=root.parent.stat()
    require(parent.st_uid==os.getuid() and not stat.S_IMODE(parent.st_mode)&0o022,"ROOT_PARENT_NOT_PRIVATE")
    require(shutil.disk_usage(root.parent).free >= 512*1024*1024,"DISK_SPACE_UNDER_512_MIB")
    require(type(payload["port"]) is int and 1024<=payload["port"]<=65535,"PORT_INVALID")
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1",payload["port"]))
        except OSError:
            raise ValueError("PORT_IN_USE") from None
    report={"platform":"linux_x86_64","python":platform.python_version(),"port_available":True,
            "scope":"runtime_ledger_alpha","n8n_installed":False,"paid_model_enabled":False}
    if payload["action"]=="preflight":
        return report
    require(payload["action"]=="install","ACTION_INVALID")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,96}",payload["deployment"]),"DEPLOYMENT_INVALID")
    require(isinstance(payload["password"],str) and 14<=len(payload["password"])<=256,"PASSWORD_INVALID")
    wheels=payload["wheels"]
    require(isinstance(wheels,list) and 1<=len(wheels)<=20,"WHEELS_INVALID")
    decoded={}
    for entry in wheels:
        name=entry["name"]
        require(re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl",name) and name not in decoded,"WHEEL_NAME_INVALID")
        raw=base64.b64decode(entry["data"],validate=True)
        require(hashlib.sha256(raw).hexdigest()==entry["sha256"],"WHEEL_HASH_MISMATCH")
        decoded[name]=raw
    require(sum(name.startswith("video_factory_core-") for name in decoded)==1,"ONE_PRODUCT_WHEEL_REQUIRED")
    manifest={"deployment":payload["deployment"],"port":payload["port"],
              "wheels":{entry["name"]:entry["sha256"] for entry in wheels}}
    root.mkdir(mode=0o700,exist_ok=True)
    require(root.stat().st_uid==os.getuid() and stat.S_IMODE(root.stat().st_mode)==0o700,"ROOT_NOT_PRIVATE")
    lockfd=os.open(root/"install.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(lockfd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid() and not stat.S_IMODE(info.st_mode)&0o077,"LOCK_UNSAFE")
        fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        manifest_path=root/"install-manifest.json"
        if manifest_path.exists():
            require(not manifest_path.is_symlink() and json.loads(manifest_path.read_text())==manifest,"INSTALL_TARGET_OR_VERSION_CHANGED")
        else:
            require(set(p.name for p in root.iterdir())=={"install.lock"},"INSTALL_DESTINATION_NOT_EMPTY")
            with manifest_path.open("x") as out:
                json.dump(manifest,out);out.flush();os.fsync(out.fileno())
            manifest_path.chmod(0o600)
        wheelhouse=root/"wheels"
        wheelhouse.mkdir(mode=0o700,exist_ok=True)
        require(wheelhouse.resolve()==wheelhouse,"WHEELHOUSE_UNSAFE")
        for name,raw in decoded.items():
            path=wheelhouse/name
            if path.exists():
                require(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==manifest["wheels"][name],"EXISTING_WHEEL_CHANGED")
            else:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,"wb") as out:out.write(raw)
        venv=root/"venv"
        require(not venv.is_symlink(),"VENV_UNSAFE")
        run([sys.executable,"-m","venv",str(venv)])
        product=next(name for name in decoded if name.startswith("video_factory_core-"))
        run([str(venv/"bin/python"),"-m","pip","install","--no-index","--find-links",str(wheelhouse),str(wheelhouse/product)])
        data=root/"data"
        data.mkdir(mode=0o700,exist_ok=True)
        # Import from the installed wheel in its own process. Password is stdin.
        code="""import json,sys
from video_factory.runtime_store import RuntimeStore
p=json.load(sys.stdin)
try:
 s=RuntimeStore(p['root'])
except Exception:
 if __import__('pathlib').Path(p['root'],'runtime.sqlite3').exists(): raise
 s=RuntimeStore.install(p['root'],p['deployment'],p['password'])
assert s.doctor()['deployment']==p['deployment']
print(json.dumps(s.doctor()))
"""
        doctor=json.loads(run([str(venv/"bin/python"),"-c",code],input=json.dumps({"root":str(data),"deployment":payload["deployment"],"password":payload["password"]}).encode()))
        unit=("[Unit]\nDescription=Video Factory runtime ledger alpha\nAfter=network.target\n\n[Service]\n"
              "Type=simple\nUMask=0077\nNoNewPrivileges=yes\nRestart=on-failure\nRestartSec=5\n"
              f"ExecStart={venv}/bin/vfctl runtime serve --root {data} --port {payload['port']}\n\n"
              "[Install]\nWantedBy=default.target\n")
        unitpath=root/"video-factory.service"
        require(not unitpath.is_symlink(),"SERVICE_PATH_UNSAFE")
        unitpath.write_text(unit);unitpath.chmod(0o600)
        return {**report,"installed":True,"doctor":doctor,"service_unit":str(unitpath),
                "service_started":False,"restart_setup":"install user unit and enable linger separately"}
    finally:
        os.close(lockfd)


if __name__=="__main__":
    os.umask(0o077)
    try:
        raw=sys.stdin.buffer.read(96*1024*1024+1)
        require(len(raw)<=96*1024*1024,"BUNDLE_TOO_LARGE")
        print(json.dumps(main(json.loads(raw))))
    except Exception as error:
        code=str(error) if type(error) is ValueError and re.fullmatch(r"[A-Z0-9_]+",str(error)) else "HOST_BOOTSTRAP_FAILED"
        print(json.dumps({"error":code}));sys.exit(2)
