#!/usr/bin/env python3
"""
DS Stream Server GUI – Cross-Platform (Windows, macOS, Linux).
Automatically detects OS and uses:
- Win32 GDI on Windows for high performance & cursor capture.
- MSS / PIL + pynput on macOS & Linux for cross-platform screen/mouse capture.
"""

import socket
import struct
import time
import threading
import queue
import select
import os
import platform
import tkinter as tk
from tkinter import ttk, scrolledtext
from PIL import Image, ImageGrab, ImageTk, ImageDraw

# ── Constants ─────────────────────────────────────────────
PORT = 8888
STOP_BYTE = b"\x01"
RES_DIVS = [1, 2, 4, 8, 16, 32, 64]
DEBUG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_frame.png")
IS_WINDOWS = platform.system() == "Windows"

# ── Optional Cross-Platform Libraries ─────────────────────
try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from pynput import mouse
    pynput_mouse = mouse.Controller()
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False


# ── Windows-Specific WinAPI Imports & Definitions ──────────
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    HDC = wintypes.HDC
    HBITMAP = wintypes.HBITMAP
    HICON = wintypes.HICON
    HGDIOBJ = wintypes.HGDIOBJ
    BOOL = wintypes.BOOL
    INT = ctypes.c_int
    UINT = wintypes.UINT
    DWORD = wintypes.DWORD

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    user32.SetProcessDPIAware.restype = BOOL
    user32.GetSystemMetrics.argtypes = [INT]
    user32.GetSystemMetrics.restype = INT

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = BOOL

    user32.GetCursorInfo.argtypes = [ctypes.c_void_p]
    user32.GetCursorInfo.restype = BOOL

    user32.GetIconInfo.argtypes = [HICON, ctypes.c_void_p]
    user32.GetIconInfo.restype = BOOL

    user32.DrawIconEx.argtypes = [HDC, INT, INT, HICON, INT, INT, UINT, wintypes.HBRUSH, UINT]
    user32.DrawIconEx.restype = BOOL

    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = BOOL

    gdi32.CreateDCW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p]
    gdi32.CreateDCW.restype = HDC

    gdi32.GetDeviceCaps.argtypes = [HDC, INT]
    gdi32.GetDeviceCaps.restype = INT

    gdi32.CreateCompatibleDC.argtypes = [HDC]
    gdi32.CreateCompatibleDC.restype = HDC

    gdi32.CreateCompatibleBitmap.argtypes = [HDC, INT, INT]
    gdi32.CreateCompatibleBitmap.restype = HBITMAP

    gdi32.SelectObject.argtypes = [HDC, HGDIOBJ]
    gdi32.SelectObject.restype = HGDIOBJ

    gdi32.BitBlt.argtypes = [HDC, INT, INT, INT, INT, HDC, INT, INT, DWORD]
    gdi32.BitBlt.restype = BOOL

    gdi32.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, DWORD]
    gdi32.CreateDIBSection.restype = HBITMAP

    gdi32.GetDIBits.argtypes = [HDC, HBITMAP, UINT, UINT, ctypes.c_void_p, ctypes.c_void_p, UINT]
    gdi32.GetDIBits.restype = INT

    gdi32.DeleteObject.argtypes = [HGDIOBJ]
    gdi32.DeleteObject.restype = BOOL

    gdi32.DeleteDC.argtypes = [HDC]
    gdi32.DeleteDC.restype = BOOL

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    class _CURSORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_int), ("flags", ctypes.c_int),
            ("hCursor", HICON), ("ptScreenPos", _POINT),
        ]

    class _ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", ctypes.c_bool), ("xHotspot", ctypes.c_ulong),
            ("yHotspot", ctypes.c_ulong), ("hbmMask", HBITMAP),
            ("hbmColor", HBITMAP),
        ]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER)]

    class _MONITORINFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint32),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", ctypes.c_uint32),
            ("szDevice", ctypes.c_wchar * 32),
        ]


# ── Monitor Enumeration ────────────────────────────────────
def get_monitors():
    if IS_WINDOWS:
        monitors = []

        def _enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
            mi = _MONITORINFOEX()
            mi.cbSize = ctypes.sizeof(mi)
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                device = mi.szDevice
                r = mi.rcMonitor
                monitors.append({
                    "device": device,
                    "name": device,
                    "bbox": (r.left, r.top, r.right, r.bottom),
                    "width": r.right - r.left,
                    "height": r.bottom - r.top
                })
            return True

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HMONITOR, HDC,
            ctypes.POINTER(_RECT), wintypes.LPARAM
        )

        callback = MONITORENUMPROC(_enum_proc)
        user32.EnumDisplayMonitors(0, 0, callback, 0)
        if monitors:
            return monitors

    if HAS_MSS:
        with mss.mss() as sct:
            monitors = []
            for i, mon in enumerate(sct.monitors[1:], start=1):
                monitors.append({
                    "device": f"Screen {i}",
                    "name": f"Screen {i}",
                    "bbox": (mon["left"], mon["top"], mon["left"] + mon["width"], mon["top"] + mon["height"]),
                    "width": mon["width"],
                    "height": mon["height"],
                    "mss_dict": mon
                })
            if monitors:
                return monitors

    # Primary Display Fallback
    return [{
        "device": "DISPLAY",
        "name": "Primary Screen",
        "bbox": (0, 0, 1920, 1080),
        "width": 1920,
        "height": 1080
    }]


# ── Mouse Overlay Helpers ──────────────────────────────────
def get_os_cursor_pil_win():
    ci = _CURSORINFO()
    ci.cbSize = ctypes.sizeof(ci)

    if user32.GetCursorInfo(ctypes.byref(ci)):
        if not (ci.flags & 1):
            return None, 0, 0, True  # Hidden by application

        if ci.hCursor:
            hx, hy = 0, 0
            ii = _ICONINFO()
            if user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
                hx, hy = ii.xHotspot, ii.yHotspot
                if ii.hbmMask: gdi32.DeleteObject(ii.hbmMask)
                if ii.hbmColor: gdi32.DeleteObject(ii.hbmColor)

            try:
                hdc = gdi32.CreateCompatibleDC(None)
                bmi = _BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = 32
                bmi.bmiHeader.biHeight = -32
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0

                ptr = ctypes.c_void_p()
                dib = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(ptr), None, 0)
                old = gdi32.SelectObject(hdc, dib)

                ctypes.memset(ptr, 0, 32 * 32 * 4)
                user32.DrawIconEx(hdc, 0, 0, ci.hCursor, 32, 32, 0, None, 0x0003)

                buf = ctypes.create_string_buffer(32 * 32 * 4)
                gdi32.GetDIBits(hdc, dib, 0, 32, buf, ctypes.byref(bmi), 0)

                cursor_img = Image.frombytes("RGBA", (32, 32), bytes(buf), "raw", "BGRA")

                gdi32.SelectObject(hdc, old)
                gdi32.DeleteObject(dib)
                gdi32.DeleteDC(hdc)

                if cursor_img.getextrema()[3][1] > 0:
                    return cursor_img, hx, hy, False
            except Exception:
                pass

    return None, 0, 0, False


def overlay_mouse_cursor(img, monitor_info):
    if not monitor_info:
        return img

    bbox = monitor_info.get("bbox", (0, 0, 1920, 1080))
    left, top, right, bottom = bbox
    mon_w = max(1, right - left)
    mon_h = max(1, bottom - top)

    mx, my = 0, 0
    cursor_img = None
    hx, hy = 0, 0

    if IS_WINDOWS:
        c_img, c_hx, c_hy, is_hidden = get_os_cursor_pil_win()
        if is_hidden:
            return img
        cursor_img, hx, hy = c_img, c_hx, c_hy
        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            mx, my = pt.x, pt.y
    elif HAS_PYNPUT:
        mx, my = pynput_mouse.position
    else:
        return img

    if not (left <= mx < right and top <= my < bottom):
        return img

    cx = int((mx - left) * img.width / mon_w)
    cy = int((my - top) * img.height / mon_h)

    if cursor_img:
        hx_s = int(hx * img.width / mon_w)
        hy_s = int(hy * img.height / mon_h)
        try:
            img.paste(cursor_img, (cx - hx_s, cy - hy_s), cursor_img)
            return img
        except Exception:
            pass

    # Generic Cross-Platform Pointer Fallback
    draw = ImageDraw.Draw(img)
    sz = max(6, min(img.width, img.height) // 20)
    poly = [
        (cx, cy),
        (cx, cy + sz),
        (cx + int(sz * 0.4), cy + int(sz * 0.7)),
        (cx + int(sz * 0.7), cy + int(sz * 0.7)),
    ]
    draw.polygon(poly, fill=(255, 255, 255), outline=(0, 0, 0))
    return img


# ── Screen Capture Engine ─────────────────────────────────
def capture_screen(monitor_info=None, draw_mouse=True, log_callback=None):
    img = None
    bbox = monitor_info.get("bbox") if monitor_info else None

    # 1. Windows GDI Capture
    if IS_WINDOWS and monitor_info and monitor_info.get("device"):
        try:
            device_name = monitor_info["device"]
            hdc_screen = gdi32.CreateDCW(device_name, None, None, None)
            if hdc_screen:
                width = gdi32.GetDeviceCaps(hdc_screen, 8)
                height = gdi32.GetDeviceCaps(hdc_screen, 10)

                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                bmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
                old = gdi32.SelectObject(hdc_mem, bmp)

                gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, 0, 0, 0x00CC0020)

                bmi = _BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = width
                bmi.bmiHeader.biHeight = -height
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0

                buf = ctypes.create_string_buffer(width * height * 4)
                gdi32.GetDIBits(hdc_mem, bmp, 0, height, buf, ctypes.byref(bmi), 0)

                img = Image.frombytes("RGBA", (width, height), bytes(buf), "raw", "BGRA").convert("RGB")

                gdi32.SelectObject(hdc_mem, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(hdc_mem)
                gdi32.DeleteDC(hdc_screen)
        except Exception as e:
            if log_callback:
                log_callback(f"GDI error: {e}, falling back")

    # 2. MSS Cross-Platform Capture
    if img is None and HAS_MSS and monitor_info and "mss_dict" in monitor_info:
        try:
            with mss.mss() as sct:
                sct_img = sct.grab(monitor_info["mss_dict"])
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception:
            pass

    # 3. Universal PIL Fallback
    if img is None:
        img = ImageGrab.grab(bbox=bbox).convert("RGB")

    # Overlay mouse pointer
    if draw_mouse and monitor_info:
        img = overlay_mouse_cursor(img, monitor_info)

    return img


# ── Color / Encoding Helpers ──────────────────────────────
def idx_to_rgb(i):
    r = (i >> 5) & 7
    g = (i >> 2) & 7
    b = i & 3
    return (r * 255 // 7, g * 255 // 7, b * 255 // 3)


def rgb_to_idx(r, g, b, cluster_size):
    r = max(0, min(255, round(r / cluster_size) * cluster_size))
    g = max(0, min(255, round(g / cluster_size) * cluster_size))
    b = max(0, min(255, round(b / cluster_size) * cluster_size))
    r3 = min(7, round(r / 255 * 7))
    g3 = min(7, round(g / 255 * 7))
    b2 = min(3, round(b / 255 * 3))
    return (r3 << 5) | (g3 << 2) | b2


def color_distance(idx1, idx2):
    r1, g1, b1 = idx_to_rgb(idx1)
    r2, g2, b2 = idx_to_rgb(idx2)
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def quantize_rgb(r, g, b, cluster_size):
    return (
        max(0, min(255, round(r / cluster_size) * cluster_size)),
        max(0, min(255, round(g / cluster_size) * cluster_size)),
        max(0, min(255, round(b / cluster_size) * cluster_size)),
    )


def encode_image(img, res_div, cluster_size, threshold, max_dist):
    logic_w = 256 // res_div
    logic_h = 192 // res_div
    img = img.resize((logic_w, logic_h), Image.Resampling.NEAREST)
    pixels = list(img.getdata())

    runs = []
    i = 0
    while i < len(pixels):
        r, g, b = pixels[i]
        r, g, b = quantize_rgb(r, g, b, cluster_size)
        idx = rgb_to_idx(r, g, b, cluster_size)
        amount = res_div
        i += 1
        while i < len(pixels):
            r2, g2, b2 = pixels[i]
            r2, g2, b2 = quantize_rgb(r2, g2, b2, cluster_size)
            if rgb_to_idx(r2, g2, b2, cluster_size) != idx:
                break
            i += 1
            amount += res_div
        runs.append([idx, amount])

    pos = 0
    while pos < len(runs) - 1:
        if runs[pos][0] == runs[pos + 1][0]:
            runs[pos][1] += runs[pos + 1][1]
            del runs[pos + 1]
        else:
            pos += 1

    pos = 0
    while pos < len(runs) - 1:
        if runs[pos][1] < threshold:
            dist = color_distance(runs[pos][0], runs[pos + 1][0])
            if dist <= max_dist:
                runs[pos + 1][1] += runs[pos][1]
                del runs[pos]
            else:
                pos += 1
        else:
            pos += 1

    colors = [r[0] for r in runs]
    amounts = [r[1] for r in runs]
    return len(colors), colors, amounts, img


def build_packet(num_items, indices, amounts):
    data = struct.pack("<H", num_items)
    for idx, amt in zip(indices, amounts):
        data += struct.pack("<B", idx)
        data += struct.pack("<H", amt)
    return data


# ── Streaming Thread ───────────────────────────────────────
class StreamThread(threading.Thread):
    def __init__(self, params, msg_queue):
        super().__init__(daemon=True)
        self.params = params
        self.msg_queue = msg_queue
        self._stop = threading.Event()
        self.server_sock = None
        self.conn = None
        self.last_sent_res = None

    def emit(self, kind, data=None):
        self.msg_queue.put((kind, data))

    def send_resolution(self, res_div):
        if not self.conn:
            return
        try:
            mode = RES_DIVS.index(res_div)
            packet = struct.pack("<H", 0xFFFF) + struct.pack("<B", mode & 0x07)
            self.conn.sendall(packet)
            self.last_sent_res = res_div
            self.emit("log", f"Sent resolution mode {mode} (÷{res_div})")
        except Exception as e:
            self.emit("log", f"Failed to send resolution: {e}")

    def send_stop_cmd(self):
        if self.conn:
            try:
                self.conn.sendall(STOP_BYTE)
                time.sleep(0.05)
            except Exception:
                pass

    def run(self):
        try:
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(("0.0.0.0", PORT))
            self.server_sock.listen(1)

            self.server_sock.settimeout(0.5)
            self.emit("status", "Waiting for DS…")
            conn = None
            addr = None
            wait_start = time.time()

            while not self._stop.is_set():
                try:
                    conn, addr = self.server_sock.accept()
                    break
                except socket.timeout:
                    if time.time() - wait_start > 10:
                        self.emit("log", "ERROR: No DS connected within 10 s")
                        self.emit("stopped")
                        return
                    continue

            if not conn:
                self.emit("stopped")
                return

            self.conn = conn
            self.conn.settimeout(10.0)
            self.emit("log", f"DS connected from {addr}")
            self.emit("status", "Streaming")

            self.send_resolution(self.params["res_div"])

            frame_count = 0
            bytes_count = 0
            start_time = time.time()
            last_stats = start_time
            last_send = start_time

            while not self._stop.is_set():
                if self.conn:
                    try:
                        ready, _, _ = select.select([self.conn], [], [], 0)
                        if ready:
                            data = self.conn.recv(16)
                            if data:
                                self.emit("log", "DS requested stop")
                                break
                    except (OSError, ValueError):
                        break

                if time.time() - last_send > 10:
                    self.emit("log", "No data sent for 10 s, stopping")
                    break

                current_res = self.params["res_div"]
                if current_res != self.last_sent_res:
                    self.send_resolution(current_res)

                target = self.params["fps"]
                if 0 < target < 60:
                    min_interval = 1.0 / target
                    elapsed = time.time() - last_send
                    if elapsed < min_interval:
                        time.sleep(min_interval - elapsed)

                monitor_info = self.params.get("monitor_info")
                draw_mouse = self.params.get("draw_mouse", True)

                def log_callback(msg):
                    self.emit("log", f"Capture: {msg}")

                try:
                    screenshot = capture_screen(
                        monitor_info=monitor_info,
                        draw_mouse=draw_mouse,
                        log_callback=log_callback
                    )
                except Exception as e:
                    self.emit("log", f"Capture error: {e}")
                    continue

                n, indices, amounts, resized_img = encode_image(
                    screenshot,
                    self.params["res_div"],
                    self.params["cluster"],
                    self.params["threshold"],
                    self.params["max_dist"],
                )

                if self.params.get("debug_save", False):
                    try:
                        resized_img.save(DEBUG_PATH)
                        self.emit("log", f"DEBUG saved: {resized_img.size[0]}×{resized_img.size[1]}")
                    except Exception as e:
                        self.emit("log", f"DEBUG save failed: {e}")

                packet = build_packet(n, indices, amounts)

                try:
                    self.conn.sendall(packet)
                    last_send = time.time()
                except socket.timeout:
                    self.emit("log", "DS not responding for 10 s (timed out)")
                    break
                except (ConnectionResetError, BrokenPipeError, OSError) as e:
                    self.emit("log", f"DS disconnected: {e}")
                    break

                frame_count += 1
                bytes_count += len(packet)

                now = time.time()
                if now - last_stats >= 1.0:
                    elapsed = now - start_time
                    self.emit("stats", {
                        "fps": frame_count / elapsed,
                        "bytes": bytes_count,
                        "runs": n,
                        "packet": len(packet),
                        "bw": bytes_count / elapsed,
                        "res": self.params["res_div"],
                    })
                    last_stats = now

        except Exception as e:
            self.emit("log", f"Server error: {e}")
        finally:
            self.emit("status", "Stopped")
            self.emit("stopped")
            for s in (self.conn, self.server_sock):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass

    def stop(self):
        self.send_stop_cmd()
        self._stop.set()
        for s in (self.conn, self.server_sock):
            try:
                if s:
                    s.close()
            except Exception:
                pass


# ── GUI Application ───────────────────────────────────────
class DSStreamApp(tk.Tk):
    PRESETS = {
        "quality":  (10, 1,  15, 80),
        "balanced": (15, 15, 20, 120),
        "speed":    (30, 30, 25, 160),
    }

    def __init__(self):
        super().__init__()
        self.title("DS Stream Server")
        self.geometry("540x860")
        self.resizable(False, False)
        self.msg_queue = queue.Queue()
        self.stream_thread = None

        self.monitors = get_monitors()
        self.params = {
            "fps": 30,
            "cluster": 15,
            "threshold": 20,
            "max_dist": 120,
            "res_div": 4,
            "draw_mouse": True,
            "debug_save": False,
            "screen": 1,
            "bbox": self.monitors[0]["bbox"] if self.monitors else None,
            "monitor_info": self.monitors[0] if self.monitors else None,
        }

        self.preview_window = None
        self.preview_label = None
        self.preview_photo = None
        self.preview_info = None

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_queue()

    def build_ui(self):
        pad = {"padx": 10, "pady": 5}

        ttk.Label(self, text="DS Stream Server", font=("Segoe UI", 18, "bold")).pack(pady=(10, 5))
        self.status_var = tk.StringVar(value="Stopped")
        self.status_lbl = ttk.Label(self, textvariable=self.status_var, font=("Segoe UI", 11), foreground="red")
        self.status_lbl.pack()

        preset_frm = ttk.Frame(self)
        preset_frm.pack(**pad)
        ttk.Label(preset_frm, text="Presets:").pack(side=tk.LEFT)
        for name in ("quality", "balanced", "speed"):
            ttk.Button(preset_frm, text=name.capitalize(),
                       command=lambda n=name: self.apply_preset(n)).pack(side=tk.LEFT, padx=3)

        sliders = ttk.LabelFrame(self, text="Settings", padding=10)
        sliders.pack(fill=tk.X, **pad)

        self.fps_var, self.fps_lbl = self._slider_row(sliders, "Max FPS", 1, 60, 30, 0)
        self.cluster_var, self.cluster_lbl = self._slider_row(sliders, "Cluster", 1, 64, 15, 1)
        self.thresh_var, self.thresh_lbl = self._slider_row(sliders, "Threshold", 0, 100, 20, 2)
        self.dist_var, self.dist_lbl = self._slider_row(sliders, "Max Dist", 0, 255, 120, 3)
        self.res_var, self.res_lbl = self._res_slider(sliders)

        num_monitors = len(self.monitors)
        if num_monitors > 1:
            self.screen_var, self.screen_lbl = self._screen_slider(sliders, num_monitors)
        else:
            frm = ttk.Frame(sliders)
            frm.pack(fill=tk.X, pady=4)
            ttk.Label(frm, text="Screen", width=10).pack(side=tk.LEFT)
            ttk.Label(frm, text="1 (only monitor)").pack(side=tk.LEFT, padx=5)

        self.mouse_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(sliders, text="Draw real mouse cursor", variable=self.mouse_var,
                        command=lambda: self.params.update({"draw_mouse": self.mouse_var.get()})).pack(anchor=tk.W, pady=(5, 0))

        self.preview_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sliders, text="Show DS preview window", variable=self.preview_var,
                        command=self.toggle_preview).pack(anchor=tk.W, pady=(2, 0))

        self.debug_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sliders, text="DEBUG: save resized frame to disk", variable=self.debug_var,
                        command=lambda: self.params.update({"debug_save": self.debug_var.get()})).pack(anchor=tk.W, pady=(2, 0))

        self.btn_text = tk.StringVar(value="Start Stream")
        self.start_btn = ttk.Button(self, textvariable=self.btn_text, command=self.toggle_stream)
        self.start_btn.pack(fill=tk.X, **pad)

        stats = ttk.LabelFrame(self, text="Live Stats", padding=10)
        stats.pack(fill=tk.X, **pad)
        self.fps_stat = tk.StringVar(value="FPS: 0.0")
        self.bytes_stat = tk.StringVar(value="Bytes: 0")
        self.runs_stat = tk.StringVar(value="Runs: 0")
        self.pkt_stat = tk.StringVar(value="Packet: 0 B")
        self.bw_stat = tk.StringVar(value="BW: 0 B/s")
        self.res_stat = tk.StringVar(value="Res: 4 (64x48)")

        stat_vars = [self.fps_stat, self.bytes_stat, self.runs_stat,
                     self.pkt_stat, self.bw_stat, self.res_stat]
        for i, var in enumerate(stat_vars):
            ttk.Label(stats, textvariable=var).grid(row=i // 2, column=i % 2, sticky=tk.W, padx=5, pady=2)

        log_frm = ttk.LabelFrame(self, text="Log", padding=5)
        log_frm.pack(fill=tk.BOTH, expand=True, **pad)
        self.log_box = scrolledtext.ScrolledText(log_frm, height=10, state=tk.DISABLED, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True)

    def _slider_row(self, parent, label, min_v, max_v, default, row_idx):
        frm = ttk.Frame(parent)
        frm.pack(fill=tk.X, pady=4)

        ttk.Label(frm, text=label, width=10).pack(side=tk.LEFT)
        scale = ttk.Scale(frm, from_=min_v, to=max_v, orient=tk.HORIZONTAL)
        scale.set(default)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        val_lbl = ttk.Label(frm, text=str(default), width=4)
        val_lbl.pack(side=tk.LEFT)

        key_map = {
            "Max FPS": "fps",
            "Cluster": "cluster",
            "Threshold": "threshold",
            "Max Dist": "max_dist",
        }
        key = key_map.get(label, label.lower().replace(" ", "_"))

        def on_change(v, lbl=val_lbl, k=key):
            iv = int(float(v))
            lbl.config(text=str(iv))
            self.params[k] = iv

        scale.config(command=on_change)
        return scale, val_lbl

    def _res_slider(self, parent):
        frm = ttk.Frame(parent)
        frm.pack(fill=tk.X, pady=4)

        ttk.Label(frm, text="Res Div", width=10).pack(side=tk.LEFT)
        scale = ttk.Scale(frm, from_=0, to=6, orient=tk.HORIZONTAL)
        scale.set(2)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        val_lbl = ttk.Label(frm, text="4 (64x48)", width=12)
        val_lbl.pack(side=tk.LEFT)

        def on_change(v, lbl=val_lbl):
            exp = int(round(float(v)))
            exp = max(0, min(6, exp))
            res = 1 << exp
            lbl.config(text=f"{res} ({256//res}x{192//res})")
            self.params["res_div"] = res

        scale.config(command=on_change)
        return scale, val_lbl

    def _screen_slider(self, parent, num_screens):
        frm = ttk.Frame(parent)
        frm.pack(fill=tk.X, pady=4)

        ttk.Label(frm, text="Screen", width=10).pack(side=tk.LEFT)
        scale = ttk.Scale(frm, from_=1, to=num_screens, orient=tk.HORIZONTAL)
        scale.set(1)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        val_lbl = ttk.Label(frm, text="1", width=4)
        val_lbl.pack(side=tk.LEFT)

        def on_change(v, lbl=val_lbl):
            iv = int(round(float(v)))
            iv = max(1, min(num_screens, iv))
            lbl.config(text=str(iv))
            self.params["screen"] = iv
            mon = self.monitors[iv - 1]
            self.params["bbox"] = mon["bbox"]
            self.params["monitor_info"] = mon
            self.log(f"Selected screen {iv}: {mon['device']} bbox {mon['bbox']}")

        scale.config(command=on_change)
        return scale, val_lbl

    def apply_preset(self, name):
        fps, cl, th, dist = self.PRESETS[name]
        self.fps_var.set(fps)
        self.cluster_var.set(cl)
        self.thresh_var.set(th)
        self.dist_var.set(dist)

        self.fps_lbl.config(text=str(fps))
        self.cluster_lbl.config(text=str(cl))
        self.thresh_lbl.config(text=str(th))
        self.dist_lbl.config(text=str(dist))

        self.params["fps"] = fps
        self.params["cluster"] = cl
        self.params["threshold"] = th
        self.params["max_dist"] = dist
        self.log(f"Preset loaded: {name}")

    def toggle_stream(self):
        if self.stream_thread and self.stream_thread.is_alive():
            self.stop_stream()
        else:
            self.start_stream()

    def start_stream(self):
        if self.stream_thread:
            self.stream_thread.join(timeout=0.5)

        self.start_btn.config(state=tk.DISABLED)
        self.btn_text.set("Stop Stream")
        self.status_var.set("Waiting for DS…")
        self.status_lbl.config(foreground="orange")

        self.stream_thread = StreamThread(self.params, self.msg_queue)
        self.stream_thread.start()

    def stop_stream(self):
        self.btn_text.set("Stopping…")
        self.start_btn.config(state=tk.DISABLED)
        if self.stream_thread:
            self.stream_thread.stop()

    def on_close(self):
        self.close_preview()
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.stop()
            self.stream_thread.join(timeout=1)
        self.destroy()

    def toggle_preview(self):
        if self.preview_var.get():
            self.open_preview()
        else:
            self.close_preview()

    def open_preview(self):
        if self.preview_window and self.preview_window.winfo_exists():
            return
        self.preview_window = tk.Toplevel(self)
        self.preview_window.title("DS Preview")
        self.preview_window.geometry("520x420")
        self.preview_window.protocol("WM_DELETE_WINDOW", self.on_preview_close)
        self.preview_info = ttk.Label(self.preview_window, text="")
        self.preview_info.pack(pady=(5, 0))
        self.preview_label = ttk.Label(self.preview_window)
        self.preview_label.pack(pady=5)
        self.update_preview()

    def close_preview(self):
        if self.preview_window:
            self.preview_window.destroy()
            self.preview_window = None

    def on_preview_close(self):
        self.preview_var.set(False)
        self.close_preview()

    def update_preview(self):
        if not self.preview_var.get() or not self.preview_window or not self.preview_window.winfo_exists():
            return

        try:
            monitor_info = self.params.get("monitor_info")
            draw_mouse = self.params.get("draw_mouse", True)

            def log_callback(msg):
                self.log(f"Preview: {msg}")

            img = capture_screen(
                monitor_info=monitor_info,
                draw_mouse=draw_mouse,
                log_callback=log_callback
            )

            res_div = self.params["res_div"]
            cluster = self.params["cluster"]
            w, h = 256 // res_div, 192 // res_div

            img = img.resize((w, h), Image.Resampling.NEAREST)

            pixels = list(img.getdata())
            out = []
            for r, g, b in pixels:
                r, g, b = quantize_rgb(r, g, b, cluster)
                out.append((r, g, b))
            img.putdata(out)

            scale = max(1, 384 // max(w, h))
            display = img.resize((w * scale, h * scale), Image.Resampling.NEAREST)

            self.preview_photo = ImageTk.PhotoImage(display)
            self.preview_label.config(image=self.preview_photo)
            self.preview_info.config(text=f"DS view: {w}×{h} (÷{res_div})  |  scale {scale}×")

        except Exception as e:
            self.log(f"Preview update error: {e}")

        delay = max(16, int(1000 / max(1, self.params["fps"])))
        self.after(delay, self.update_preview)

    def poll_queue(self):
        try:
            while True:
                kind, data = self.msg_queue.get_nowait()

                if kind == "log":
                    self.log(data)

                elif kind == "status":
                    self.status_var.set(data)
                    if data == "Streaming":
                        self.status_lbl.config(foreground="green")
                    elif data == "Stopped":
                        self.status_lbl.config(foreground="red")
                    else:
                        self.status_lbl.config(foreground="orange")

                elif kind == "stats":
                    self.fps_stat.set(f"FPS: {data['fps']:.1f}")
                    self.bytes_stat.set(f"Bytes: {data['bytes']}")
                    self.runs_stat.set(f"Runs: {data['runs']}")
                    self.pkt_stat.set(f"Packet: {data['packet']} B")
                    self.bw_stat.set(f"BW: {data['bw']:.0f} B/s")
                    self.res_stat.set(f"Res: {data['res']} ({256//data['res']}x{192//data['res']})")

                elif kind == "stopped":
                    self.btn_text.set("Start Stream")
                    self.start_btn.config(state=tk.NORMAL)
                    self.status_var.set("Stopped")
                    self.status_lbl.config(foreground="red")
                    if self.stream_thread:
                        self.stream_thread.join(timeout=0.1)

        except queue.Empty:
            pass
        self.after(100, self.poll_queue)

    def log(self, text):
        self.log_box.config(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.log_box.see(tk.END)
        self.log_box.config(state=tk.DISABLED)


if __name__ == "__main__":
    app = DSStreamApp()
    app.mainloop()