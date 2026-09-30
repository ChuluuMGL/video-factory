"""Server-side administration. Secret values use hidden TTY or private files."""
import getpass
import json
import os
from pathlib import Path
import sys
import sqlite3
import signal

from cryptography.fernet import Fernet

from .runtime_http import RuntimeServer
from .postgres_store import PostgresStore, selected_store
from .runtime_ops import backup, restore
from .runtime_store import RuntimeFault, RuntimeStore, exclusive_write, private_directory, private_file


def secret_input(path, label):
    if path:
        path=Path(path)
        if not path.is_absolute() or path.resolve()!=path:
            raise RuntimeFault("PRIVATE_ABSOLUTE_SECRET_FILE_REQUIRED")
        private_file(path)
        if path.stat().st_size>65536:
            raise RuntimeFault("SECRET_TOO_LARGE")
        return path.read_text().rstrip("\n")
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RuntimeFault("HIDDEN_TERMINAL_OR_PRIVATE_FILE_REQUIRED")
    return getpass.getpass(label)


def register_runtime(commands):
    parser=commands.add_parser("runtime",help="customer-host runtime ledger alpha; no paid adapter")
    parser.add_argument("action",choices=("install","serve","doctor","login","recover-admin","keygen","secret-set","key-rotate","backup","restore","migrate-sqlite"))
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--deployment")
    parser.add_argument("--password-file",type=Path)
    parser.add_argument("--token-file",type=Path)
    parser.add_argument("--master-key-file",type=Path)
    parser.add_argument("--new-key-file",type=Path)
    parser.add_argument("--secret-file",type=Path)
    parser.add_argument("--alias")
    parser.add_argument("--backup-key-file",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--source",type=Path)
    parser.add_argument("--port",type=int,default=8787)
    parser.add_argument("--name",default="admin")
    parser.add_argument("--container-network", action="store_true", help="bind container interface; requires VF_CONTAINER_MODE=1 and host loopback-only port mapping")


def run_runtime(args):
    try:
        private_directory(args.root)
        store_type = selected_store()
        if store_type is PostgresStore and args.action in ("backup", "restore"):
            raise RuntimeFault("POSTGRES_BACKUP_REQUIRES_STACK_BACKUP")
        if args.action == "migrate-sqlite":
            if not args.source or store_type is not PostgresStore:
                raise RuntimeFault("SQLITE_SOURCE_AND_POSTGRES_TARGET_REQUIRED")
            result = PostgresStore.migrate_sqlite(args.source, args.root)
        elif args.action=="keygen":
            if not args.output:
                raise RuntimeFault("KEY_OUTPUT_REQUIRED")
            private_directory(args.output.parent)
            exclusive_write(args.output,Fernet.generate_key()+b"\n")
            result={"key_file":str(args.output),"store_off_host_recovery_copy":True}
        elif args.action=="install":
            store=store_type.install(args.root,args.deployment,secret_input(args.password_file,"设置管理员密码（至少14位）: "))
            result={"installed":True,"scope":"runtime_ledger_alpha","doctor":store.doctor()}
        elif args.action=="restore":
            if not args.source or not args.backup_key_file:
                raise RuntimeFault("BACKUP_SOURCE_AND_KEY_REQUIRED")
            result=restore(args.source,args.root,secret_input(args.backup_key_file,""))
        else:
            store=store_type(args.root)
            if args.action=="doctor":
                result=store.doctor()
            elif args.action=="serve":
                if not 1<=args.port<=65535:
                    raise RuntimeFault("PORT_INVALID")
                if args.container_network and os.environ.get("VF_CONTAINER_MODE") != "1":
                    raise RuntimeFault("CONTAINER_MODE_REQUIRED")
                bind = "0.0.0.0" if args.container_network else "127.0.0.1"
                signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
                with RuntimeServer((bind,args.port),store,container_network=args.container_network) as server:
                    print(json.dumps({"listening":bind,"port":server.server_port,"paid_adapter":"disabled"}),flush=True)
                    server.serve_forever()
                return 0
            elif args.action=="login":
                if not args.output:
                    raise RuntimeFault("PRIVATE_TOKEN_OUTPUT_REQUIRED")
                private_directory(args.output.parent)
                if args.output.exists() or args.output.is_symlink():
                    raise RuntimeFault("TOKEN_OUTPUT_EXISTS")
                receipt=store.login(args.name,secret_input(args.password_file,"管理员密码: "))
                exclusive_write(args.output,receipt["token"].encode())
                result={"token_file":str(args.output),"expires_at":receipt["expires_at"]}
            elif args.action=="recover-admin":
                result=store.recover_admin(secret_input(args.password_file,"新管理员密码: "))
            elif args.action=="backup":
                if not args.output or not args.backup_key_file:
                    raise RuntimeFault("BACKUP_OUTPUT_AND_KEY_REQUIRED")
                result=backup(store,args.output,secret_input(args.backup_key_file,""))
            elif args.action in ("secret-set","key-rotate"):
                if not args.token_file or not args.master_key_file:
                    raise RuntimeFault("TOKEN_AND_MASTER_KEY_REQUIRED")
                token=secret_input(args.token_file,"")
                key=secret_input(args.master_key_file,"")
                if args.action=="secret-set":
                    result=store.put_secret(token,args.alias,secret_input(args.secret_file,"凭据值（隐藏输入）: "),key)
                else:
                    if not args.new_key_file:
                        raise RuntimeFault("NEW_MASTER_KEY_REQUIRED")
                    result=store.rotate_key(token,key,secret_input(args.new_key_file,""))
            else:
                raise RuntimeFault("ACTION_UNSUPPORTED")
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0
    except RuntimeFault as error:
        print(json.dumps({"error":str(error)}))
        return 2
    except (OSError,ValueError,TypeError,sqlite3.Error):
        print(json.dumps({"error":"RUNTIME_OPERATION_FAILED"}))
        return 2
    except KeyboardInterrupt:
        return 130
