"""Spike sidecar: run code for Obsidian. One Jupyter kernel per note, so state carries between blocks."""
import queue
from fastapi import Depends, FastAPI, Header, HTTPException
from jupyter_client.manager import KernelManager
from pydantic import BaseModel

TOKEN = 'spike-token'
app = FastAPI()
kernels: dict[str, tuple[KernelManager, object]] = {}


def auth(authorization: str = Header('')):
    if authorization != f'Bearer {TOKEN}':
        raise HTTPException(401)


def kernel_for(note: str):
    if note not in kernels:
        km = KernelManager(kernel_name='python3')
        km.start_kernel()
        kc = km.client(); kc.start_channels(); kc.wait_for_ready(timeout=30)
        kc.execute('%matplotlib inline', silent=True)
        kernels[note] = (km, kc)
    return kernels[note][1]


class Run(BaseModel):
    note: str
    code: str


@app.post('/execute', dependencies=[Depends(auth)])
def execute(run: Run):
    kc = kernel_for(run.note)
    msg_id = kc.execute(run.code)
    outputs = []
    while True:
        try:
            msg = kc.get_iopub_msg(timeout=60)
        except queue.Empty:
            outputs.append({'text': 'timed out', 'error': True}); break
        if msg['parent_header'].get('msg_id') != msg_id:
            continue
        t, c = msg['msg_type'], msg['content']
        if t == 'stream':
            outputs.append({'text': c['text']})
        elif t in ('execute_result', 'display_data'):
            d = c['data']
            outputs.append({'png': d['image/png']} if 'image/png' in d else {'text': d.get('text/plain', '')})
        elif t == 'error':
            outputs.append({'text': '\n'.join(c['traceback']), 'error': True})
        elif t == 'status' and c['execution_state'] == 'idle':
            break
    return {'outputs': outputs}


@app.on_event('shutdown')
def stop():
    for km, kc in kernels.values():
        kc.stop_channels(); km.shutdown_kernel(now=True)
