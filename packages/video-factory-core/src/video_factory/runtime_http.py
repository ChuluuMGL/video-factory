"""Customer-host alpha management API, deliberately bound to loopback.

Use an authenticated SSH tunnel. Tokens are separate from n8n/Feishu identities.
No endpoint returns decrypted credentials or resets the OS-owner administrator.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .onboarding import read_json
from . import automation
from .runtime_store import RuntimeFault, canonical
import io


class RuntimeServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, store, *, container_network=False):
        if address[0] != "127.0.0.1" and not (container_network and address[0] == "0.0.0.0"):
            raise RuntimeFault("LOOPBACK_BIND_REQUIRED")
        self.store=store
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    server_version="VideoFactoryAlpha"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self,*args):
        pass  # No request body, token, query or provider error in access logs.

    def reply(self,status,value):
        data=canonical(value).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path=="/healthz":
            self.reply(200,{"status":"up","scope":"runtime_ledger_alpha"})
        else:
            self.reply(404,{"error":"ROUTE_NOT_FOUND"})

    def do_POST(self):
        try:
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type()!="application/json":
                raise RuntimeFault("JSON_CONTENT_REQUIRED")
            lengths=self.headers.get_all("Content-Length",[])
            if len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=65536:
                raise RuntimeFault("BODY_LENGTH_INVALID")
            raw=self.rfile.read(int(lengths[0]))
            if len(raw)!=int(lengths[0]):
                raise RuntimeFault("BODY_INCOMPLETE")
            body=read_json(io.StringIO(raw.decode()))
            if not isinstance(body,dict):
                raise RuntimeFault("BODY_OBJECT_REQUIRED")
            store=self.server.store
            if self.path=="/v1/login":
                if set(body)!={"name","password"}:
                    raise RuntimeFault("FIELDS_INVALID")
                result=store.login(**body)
            else:
                token=self.headers.get("Authorization","")
                if not token.startswith("Bearer "):
                    raise RuntimeFault("AUTH_REQUIRED")
                token=token[7:]
                routes={
                    "/v1/automation/keys":(lambda token,**kw:automation.issue(store,token,**kw),{"project","ttl_hours"}),
                    "/v1/automation/revoke":(lambda token,**kw:automation.revoke(store,token,**kw),{"key_id"}),
                    "/v1/automation/queue":(lambda token,**kw:automation.queue(store,token,**kw),{"project","after"}),
                    "/v1/projects":(store.put_project,{"project","configuration","expected_digest"}),
                    "/v1/tasks":(store.create_task,{"project","task","payload","expected_revision"}),
                    "/v1/tasks/read":(store.inspect_task,{"project","task"}),
                    "/v1/reviews":(store.review,{"event","project","task","revision","stage","decision","feedback"}),
                    "/v1/users":(store.set_user,{"name","password","project"}),
                    "/v1/users/revoke":(store.revoke_user,{"name"}),
                }
                if self.path=="/v1/doctor":
                    if body:
                        raise RuntimeFault("FIELDS_INVALID")
                    with store.connect() as db:
                        store.authorize(db,token,"admin")
                    result=store.doctor()
                elif self.path in routes:
                    fn,allowed=routes[self.path]
                    if set(body)-allowed:
                        raise RuntimeFault("FIELDS_INVALID")
                    result=fn(token,**body)
                else:
                    self.reply(404,{"error":"ROUTE_NOT_FOUND"}); return
            self.reply(200,result)
        except RuntimeFault as error:
            status=401 if str(error).startswith("AUTH_") else 409
            self.reply(status,{"error":str(error)})
        except (ValueError,TypeError,UnicodeError):
            self.reply(400,{"error":"REQUEST_INVALID"})
        except Exception:
            self.reply(500,{"error":"INTERNAL_FAILURE"})
