"""Start the sidecar. The plugin runs this and reads one line from stdout:

    ZK_READY {"port": 51234, "version": "0.1.0"}

Logs go to stderr. With --watch-stdin, the sidecar exits when stdin closes, so it never outlives Obsidian.
"""
import argparse
import asyncio
import json
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_app
from .config import Config

READY = 'ZK_READY'


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog='zk-sidecar')
    p.add_argument('--vault', type=Path, required=True, help='path to the Obsidian vault')
    p.add_argument('--port', type=int, default=0, help='0 picks a free port')
    p.add_argument('--watch-stdin', action='store_true', help='exit when stdin closes')
    return p.parse_args(argv)


async def serve(config: Config, port: int, watch_stdin: bool) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', port))
    port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(create_app(config), log_level='warning'))
    if watch_stdin:
        def wait_for_eof():
            sys.stdin.buffer.read()
            server.should_exit = True
        threading.Thread(target=wait_for_eof, daemon=True).start()

    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if task.done():
            await task
            return
        await asyncio.sleep(0.02)
    print(READY, json.dumps({'port': port, 'version': __version__}), flush=True)
    await task


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    vault = args.vault.expanduser().resolve()
    if not vault.is_dir():
        sys.exit(f'zk-sidecar: vault not found: {vault}')
    try:
        config = Config.from_env(vault)
    except ValueError as e:
        sys.exit(f'zk-sidecar: {e}')
    asyncio.run(serve(config, args.port, args.watch_stdin))


if __name__ == '__main__':
    main()
