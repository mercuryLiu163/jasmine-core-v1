#!/usr/bin/env python3
"""Owned loopback fixture server: static bytes only, never evaluates JavaScript."""
import argparse
import os
import stat
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args();root=args.root.resolve(strict=True)
    root_fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            name={'/':'index.html','/index.html':'index.html','/callback.js':'callback.js'}.get(urlsplit(self.path).path)
            if name is None:self.send_error(404);return
            try:
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root_fd)
                try:
                    metadata=os.fstat(fd)
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size>262144:raise ValueError('unsafe fixture file')
                    body=os.read(fd,262145)
                    if len(body)>262144:raise ValueError('fixture file cap')
                finally:os.close(fd)
            except (OSError,ValueError):self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','text/javascript; charset=utf-8' if name.endswith('.js') else 'text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=HTTPServer(('127.0.0.1',args.port),Handler)
    try:server.serve_forever()
    finally:server.server_close();os.close(root_fd)
if __name__=='__main__':main()
