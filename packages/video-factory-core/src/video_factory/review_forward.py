"""Bounded loopback-to-one-container forwarding; no generic proxy target."""
import ipaddress
import json
import select
import socket
import socketserver
import threading
import time

from .runtime_store import RuntimeFault


def container_address(stack, name, run):
    networks = json.loads(run(['docker', 'container', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', name], timeout=10))
    prefix = 'vf-'+stack.config['deployment']+'-'+stack.config['instance']
    if set(networks) != {prefix+'_private', prefix+'_worker_link'}:
        raise RuntimeFault('REVIEW_CONTAINER_NETWORK_MISMATCH')
    value = networks[prefix+'_private']['IPAddress']
    ip = ipaddress.ip_address(value)
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise RuntimeFault('REVIEW_CONTAINER_ADDRESS_INVALID')
    return value


class Forward(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, port, upstream, seconds):
        self.upstream = upstream
        self.deadline = time.monotonic()+seconds
        self.capacity = threading.BoundedSemaphore(32)
        super().__init__(('127.0.0.1', port), Pipe)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request); return
        try: super().process_request(request, client_address)
        except BaseException:
            self.capacity.release(); raise

    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request, client_address)
        finally: self.capacity.release()

    def close(self):
        self.deadline = 0
        self.shutdown(); self.server_close(); self.thread.join(2)


class Pipe(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(self.server.upstream, timeout=3) as upstream:
                self.request.settimeout(10); upstream.settimeout(10)
                peers = {self.request: upstream, upstream: self.request}
                while time.monotonic() < self.server.deadline:
                    ready, _, _ = select.select(list(peers), [], [], .5)
                    for stream in ready:
                        data = stream.recv(65536)
                        if not data: return
                        peers[stream].sendall(data)
        except OSError: pass
