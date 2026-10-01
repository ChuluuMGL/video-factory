"""Ephemeral HTTPS CONNECT relay: fixed destinations, public IPs, no TLS termination.

No configuration file, wildcard hosts, HTTP forwarding, credentials, access logs,
or persistent restart. Resolve once and connect to the checked numeric address.
"""
import ipaddress
import select
import socket
import socketserver
import threading
import time

from .h3_provider import MEDIA_HOSTS, ORIGINS
from urllib.parse import urlsplit

HOSTS = frozenset({'open.feishu.cn', 'accounts.feishu.cn', 'api.deepseek.com'} | MEDIA_HOSTS | {urlsplit(v).hostname for v in ORIGINS.values()})
MAX_TRANSFER = 400 * 1024 * 1024


def public_addresses(host):
    addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    parsed=[ipaddress.ip_address(row[4][0]) for row in addresses]
    if not parsed or any(not value.is_global or value.is_multicast or value.is_reserved for value in parsed):
        raise ValueError('NON_PUBLIC_DESTINATION')
    return addresses


def connect_public(host):
    if host not in HOSTS:raise ValueError('HOST_NOT_ALLOWED')
    for family,kind,protocol,_,address in public_addresses(host):
        stream=socket.socket(family,kind,protocol)
        try:
            stream.settimeout(10);stream.connect(address)
            return stream
        except OSError:stream.close()
    raise OSError('CONNECT_FAILED')


class Relay(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        upstream=None
        try:
            # Read one byte at a time through an unbuffered stream: do not consume
            # TLS bytes following the CONNECT request before tunnelling begins.
            request=self.rfile.readline(4097)
            if len(request)>4096:raise ValueError
            parts=request.decode('ascii').rstrip('\r\n').split(' ')
            if len(parts)!=3 or parts[0]!='CONNECT' or parts[2] not in ('HTTP/1.0','HTTP/1.1'):raise ValueError
            if parts[1] not in {h+':443' for h in HOSTS}:raise ValueError
            total=len(request)
            while True:
                line=self.rfile.readline(4097);total+=len(line)
                if not line or total>8192 or len(line)>4096:raise ValueError
                if line==b'\r\n':break
            upstream=connect_public(parts[1][:-4])
            self.connection.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            ends={self.connection:upstream,upstream:self.connection}
            total=0;deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                readable,_,_=select.select(list(ends),[],[],30)
                if not readable:return
                for source in readable:
                    data=source.recv(65536)
                    if not data:return
                    total+=len(data)
                    if total>MAX_TRANSFER:return
                    ends[source].sendall(data)
        except (OSError,ValueError,UnicodeError):
            if upstream is None:
                try:self.connection.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                except OSError:pass
        finally:
            if upstream:upstream.close()

    rbufsize=0


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(16)
        super().__init__(*args,**kwargs)

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request);return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise

    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

    def handle_error(self,request,address):
        pass  # Never emit client addresses, tunnel data, headers or credentials.


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--persistent', action='store_true')
    args=parser.parse_args()
    with RelayServer(('0.0.0.0',8443),Relay) as server:
        server.timeout=2
        deadline=float('inf') if args.persistent else time.monotonic()+600
        while time.monotonic()<deadline:server.handle_request()


if __name__=='__main__':main()
