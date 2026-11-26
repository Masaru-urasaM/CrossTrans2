"""Private-fixture native position attempts through _tkinter, never production UI."""
import ctypes
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import tkinter as tk

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_suffix('.c')


class Position(ctypes.Structure):
    _fields_ = [('hwnd',W.HWND)] + [(name,ctypes.c_int) for name in ('x','y','width','height')] + [
        ('flags',W.UINT),('error',W.DWORD)]


def library():
    source_hash=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    directory=ROOT/'logs/tk_resize/native-geometry-runtime'/source_hash[:16]
    binary=directory/'native_geometry.dll'
    metadata=directory/'build.json'
    if binary.exists() and metadata.exists():
        saved=json.loads(metadata.read_text(encoding='utf-8'))
        if (saved['source_sha256']==source_hash and
                saved['dll_sha256']==hashlib.sha256(binary.read_bytes()).hexdigest()):
            return binary,saved
        raise RuntimeError('Private geometry probe build identity mismatch')
    compiler_record=json.loads((ROOT/'logs/tk_resize/native-trace-runtime/build.json').read_text(encoding='utf-8'))
    compiler=Path(compiler_record['command'][0])
    if hashlib.sha256(compiler.read_bytes()).hexdigest()!=compiler_record['compiler_sha256']:
        raise RuntimeError('Private geometry compiler identity mismatch')
    directory.mkdir(parents=True,exist_ok=True)
    command=[str(compiler),'-shared','-O2','-Wall','-Wextra','-Werror',str(SOURCE),'-o',str(binary),'-luser32']
    result=subprocess.run(command,capture_output=True,text=True,timeout=60)
    (directory/'build.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    result.check_returncode()
    saved=dict(abi=1,source_sha256=source_hash,dll_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        compiler_sha256=compiler_record['compiler_sha256'],command=command,
        purpose='Private own-window native geometry through a Tcl C command; no input injection')
    metadata.write_text(json.dumps(saved,indent=2)+'\n',encoding='utf-8')
    return binary,saved


class NativeGeometryProbe:
    def __init__(self,root):
        import quiet_session
        if not quiet_session.DESKTOP_NAME:
            raise RuntimeError('Native geometry probe requires the guarded private desktop')
        self.root,self.thread=root,threading.get_ident()
        self.closed=False
        binary,self.identity=library()
        self.api=ctypes.CDLL(str(binary))
        self.api.geometry_probe_size.restype=ctypes.c_uint
        if self.api.geometry_probe_size()!=ctypes.sizeof(Position):
            raise RuntimeError('Native geometry probe ABI mismatch')
        self.api.register_geometry_probe.argtypes=[ctypes.c_void_p,ctypes.c_void_p,
                                                  ctypes.c_char_p,ctypes.POINTER(Position)]
        self.api.register_geometry_probe.restype=ctypes.c_int
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetModuleHandleW.argtypes,kernel.GetModuleHandleW.restype=[W.LPCWSTR],W.HMODULE
        handle=kernel.GetModuleHandleW('tcl90.dll')
        if not handle:
            raise RuntimeError('Pinned Tcl 9 DLL is not already loaded')
        tcl=ctypes.CDLL('tcl90.dll',handle=handle)
        self.position_data=Position()
        self.command=f'CrossTransPrivateGeometry_{id(self)}'
        if not self.api.register_geometry_probe(root.tk.interpaddr(),
                ctypes.cast(tcl.Tcl_CreateObjCommand,ctypes.c_void_p),
                self.command.encode('ascii'),ctypes.byref(self.position_data)):
            raise RuntimeError('Could not register private geometry command')
        owners=getattr(root,'_ct_native_geometry_probes',None)
        if owners is None:
            root._ct_native_geometry_probes=owners=set()
        owners.add(self)
        self.binding=root.bind('<Destroy>',self._destroyed,add='+')

    def position(self,hwnd,rect):
        if self.closed or threading.get_ident()!=self.thread:
            raise RuntimeError('Native geometry probe belongs to the live GUI thread')
        self.position_data.hwnd=hwnd
        self.position_data.x,self.position_data.y,self.position_data.width,self.position_data.height=map(int,rect)
        self.position_data.flags=0x0004|0x0010
        try:
            self.root.tk.call(self.command)
        except tk.TclError:
            if self.position_data.error:
                raise ctypes.WinError(self.position_data.error)
            raise

    def _destroyed(self,event):
        if event.widget is self.root:
            self.close(destroying=True)

    def close(self,*,destroying=False):
        if self.closed:
            return
        self.closed=True
        try:
            self.root.tk.call('rename',self.command,'')
            if not destroying:
                self.root.unbind('<Destroy>',self.binding)
        except tk.TclError:
            pass
        self.root._ct_native_geometry_probes.discard(self)

    def __enter__(self):
        return self

    def __exit__(self,*_args):
        self.close()
