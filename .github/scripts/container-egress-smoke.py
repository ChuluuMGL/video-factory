"""Run in the real worker container; never contact a provider."""
import json
import socket

try:
    socket.create_connection(('1.1.1.1',443),2).close()
except OSError:pass
else:raise AssertionError('WORKER_HAS_DIRECT_INTERNET_ROUTE')
for target in ('example.com:443','169.254.169.254:443','postgres:5432','api.minimax.io:80','api.minimax.io.evil.example:443'):
    with socket.create_connection(('egress',8443),5) as stream:
        stream.sendall(('CONNECT '+target+' HTTP/1.1\r\nHost: '+target+'\r\n\r\n').encode())
        assert stream.recv(1024).startswith(b'HTTP/1.1 403'),target
print(json.dumps({'status':'PASS','direct_internet_blocked':True,'unlisted_hosts_and_ports_blocked':True,'provider_requests':0}))
