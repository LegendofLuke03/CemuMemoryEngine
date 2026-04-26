import pymem
import struct
import os
import time
import psutil
import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
from typing import List, Dict, Optional, Any

# ─────────────────────────────────────────────
# PALETTE & STYLE
# ─────────────────────────────────────────────
BG = "#0d0e14"
SURFACE = "#14151f"
SURFACE2 = "#1c1d2e"
BORDER = "#252638"
ACCENT = "#4d8eff"
ACCENT2 = "#3AAA92"
DANGER = "#ff4d6a"
SUCCESS = "#00c97f"
WARN = "#f5a623"
TEXT = "#dde0f0"
TEXT_DIM = "#5a5c78"
TEXT_MONO = "#b0f0e0"
MV_BG = "#0a0f0d"
MV_HDR = "#006655"
MV_HDR_LT = "#00a882"
MV_ADDR = "#00dfb0"
MV_BYTE = "#c8ddd8"
MV_ASCII = "#6a9e94"
MV_CHANGED = "#ff3355"
MV_CHG_BG = "#2a0010"
MV_SURFACE = "#0f1a17"

# ─────────────────────────────────────────────
# WII U VIRTUAL MEMORY MAP
# ─────────────────────────────────────────────
WIIU_REGIONS = [
    ("MEM1", 0x00000000, 0x017FFFFF, "#3d5fc2", "Foreground / system heap (24 MB)"),
    (".syscall", 0x02000000, 0x0200001F, "#22aa44", "OS syscall stubs"),
    (".text", 0x02000020, 0x0FFFFFFF, "#22cc66", "Executable code (.text / .rodata)"),
    ("MEM2", 0x10000000, 0x4FFFFFFF, "#c47b20", "Main heap — .data, .bss, allocations (~1 GB)"),
    ("GPU/GX2", 0xE0000000, 0xE3FFFFFF, "#883355", "AMD Latte GX2 registers"),
    ("MMIO/AHB", 0xF4000000, 0xF5FFFFFF, "#773399", "AHB peripheral registers"),
    ("Loader", 0xFFE00000, 0xFFFFFFFF, "#446677", "Café OS loader / IPC"),
]

FONT_UI = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_MONO = ("Consolas", 9)
FONT_TINY = ("Segoe UI", 8)
FONT_BIG =  ("Segoe UI", 13)

FONT_MV = ("Consolas", 11)
DTYPES = ["int", "uint", "float", "double", "int64", "uint64", "string", "byte", "ubyte", "ushort", "halfword", "pointer", "aob"]

# Config file path
def get_config_path():
    appdata = os.getenv('APPDATA') or os.path.expanduser("~")
    config_dir = os.path.join(appdata, "CEMU_MemoryEngine")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "config.json")

CONFIG_FILE = get_config_path()

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def make_btn(parent, text, cmd, color=SURFACE2, fg=TEXT, width=None, **kw):
    b = tk.Button(parent, text=text, command=cmd,
                  bg=color, fg=fg, activebackground=color,
                  activeforeground=ACCENT, relief="flat", bd=0,
                  font=FONT_UI, cursor="hand2", padx=12, pady=5, **kw)
    if width:
        b.config(width=width)
    b.bind("<Enter>", lambda e: b.config(bg=BORDER))
    b.bind("<Leave>", lambda e: b.config(bg=color))
    return b

def fmt_addr(addr: int) -> str:
    if addr < 0:
        return f"-0x{-addr:08X}"
    return f"0x{addr:08X}"

def load_config() -> Dict:
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_config(config: Dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass

# ─────────────────────────────────────────────
# MEMORY VIEWER
# ─────────────────────────────────────────────
class MemoryViewer(tk.Toplevel):
    COLS = 16
    MIN_ROWS = 12
    MAX_ROWS = 60
    REFRESH_MS = 150
    FLASH_MS = 900
    DEFAULT_ADDR = 0x02000000

    def __init__(self, parent, pm_getter, base_getter):
        super().__init__(parent)
        self.pm_getter = pm_getter
        self.base_getter = base_getter
        self.current_addr = self.DEFAULT_ADDR
        self.prev_snapshot: Dict[int, int] = {}
        self.changed_times: Dict[int, float] = {}
        self._running = True
        self.rows = self.MIN_ROWS
        self._sel_addr: Optional[int] = None
        self._nibble = ""
        self._edit_mode = "hex"
        self._showing_copied = False          
        self.title("Memory Viewer — Wii U Virtual Address Space")
        self.geometry("810x680")
        self.configure(bg=MV_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.bind("<Configure>", self._on_configure)
        self._schedule_refresh()

    @staticmethod
    def _region_for(addr: int):
        for label, start, end, color, _ in WIIU_REGIONS:
            if start <= addr <= end:
                return label, color
        return None, "#555577"

    def _build(self):
        hdr = tk.Frame(self, bg=MV_HDR, height=34)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        self._conn_dot = tk.Label(hdr, text="●", fg=DANGER, bg=MV_HDR, font=("Consolas", 11))
        self._conn_dot.pack(side="left", padx=(0, 4), pady=10)
        self._conn_lbl = tk.Label(hdr, text="not attached", fg="#80c0b0", bg=MV_HDR, font=FONT_BIG)
        self._conn_lbl.pack(side="left", padx=(0, 4), pady=10)

        addr_bar = tk.Frame(self, bg=SURFACE2, pady=6)
        addr_bar.pack(fill="x")
        
        bar_label = tk.Label(addr_bar, text="Address space (32-bit)", fg=TEXT_DIM, bg=SURFACE, font=FONT_TINY)
        bar_label.pack(pady=(8, 2))
        bar_canvas = tk.Canvas(addr_bar, bg="#111", height=22, bd=0, highlightthickness=0)
        bar_canvas.pack(padx=6, pady=(0, 12))
        bar_canvas.bind("<Configure>", lambda e: self._draw_bar(bar_canvas))
        self._bar_canvas = bar_canvas
        self._bar_drawn = False
        self._pos_canvas = tk.Canvas(addr_bar, bg="#111", height=4, bd=0, highlightthickness=0)
        self._pos_canvas.pack( padx=6, pady=(0, 8))

        tk.Label(addr_bar, text="Jump to an address:", fg=TEXT_DIM, bg=SURFACE2, font=FONT_UI).pack(side="left", padx=3)
        self.addr_var = tk.StringVar(value=fmt_addr(self.current_addr))
        addr_ent = tk.Entry(addr_bar, textvariable=self.addr_var, bg=MV_SURFACE, fg=MV_ADDR,
                            insertbackground=ACCENT2, relief="flat", bd=0, font=FONT_MV, width=20)
        addr_ent.pack(side="left", padx=4, ipady=5)
        addr_ent.bind("<Return>", self._jump)
        self._region_pill = tk.Label(addr_bar, text="", bg=SURFACE2, fg="#d0fff6", font=FONT_TINY, padx=8, pady=3)
        self._region_pill.pack(side="left", padx=6)

        def qbtn(parent, text, addr, color):
            b = tk.Button(parent, text=text, command=lambda: self._jump_to(addr),
                          bg=color, fg="#ffffff", activebackground=color, activeforeground="#fff",
                          relief="flat", bd=0, font=FONT_TINY, cursor="hand2", padx=10, pady=4)
            b.bind("<Enter>", lambda e, c=color: b.config(bg=self._lighten(c)))
            b.bind("<Leave>", lambda e, c=color: b.config(bg=c))
            return b
        btn_frame = tk.Frame(addr_bar, bg=SURFACE2)
        btn_frame.pack(side="left", padx=4)

        for label, start, _, color, _ in WIIU_REGIONS:
            qbtn(btn_frame, label, start, color).pack(side="left", padx=2)

        body = tk.Frame(self, bg=MV_BG)
        body.pack(fill="both", expand=True, padx=0, pady=0)
        
        hdr_wrap = tk.Frame(body, bg=BORDER, padx=1, pady=1)
        hdr_wrap.pack(side="top", fill="x", padx=(6,2), pady=(2,0))
        self.hdr_txt = tk.Text(hdr_wrap, bg=MV_BG, fg=MV_BYTE, font=("Consolas", 14), 
                               state="disabled", relief="flat", bd=0, wrap="none", 
                               height=2, takefocus=0)
        self.hdr_txt.pack(fill="x")
        
        # Configure header tags
        for tag, fg, bg in [
            ("hdr", MV_HDR_LT, SURFACE2), ("sep", TEXT_DIM, None)
        ]:
            self.hdr_txt.tag_configure(tag, foreground=fg, background=bg)
        
        # ── SCROLLABLE DATA AREA ──
        dump_wrap = tk.Frame(body, bg=BORDER, padx=1, pady=1)
        dump_wrap.pack(side="left", fill="both", expand=True, padx=(6,2), pady=(0,2))
        self.txt = tk.Text(dump_wrap, bg=MV_BG, fg=MV_BYTE, font=("Consolas", 14), state="disabled",
                           relief="flat", bd=0, cursor="crosshair", selectbackground=BORDER,
                           wrap="none", spacing1=0, spacing3=0, takefocus=1)
        vsb = ttk.Scrollbar(dump_wrap, orient="vertical", command=self._scroll_rows, style="Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=lambda *a: None)
        self.txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for tag, fg, bg in [
            ("addr", MV_ADDR, None), ("byte", MV_BYTE, None),
            ("changed", MV_CHANGED, MV_CHG_BG), ("ascii", MV_ASCII, None),
            ("zero", "#2a3040", None), ("sel_hex", "#000000", "#ffffff"),
            ("sel_nibble", "#000000", "#ffcc00"), ("sel_ascii", "#000000", ACCENT2)
        ]:
            self.txt.tag_configure(tag, foreground=fg, background=bg)
        for label, _, _, color, _ in WIIU_REGIONS:
            self.txt.tag_configure(f"rgn_{label}", foreground=color)

        self.txt.bind("<MouseWheel>", self._on_mousewheel)
        self.txt.bind("<Button-4>", self._on_mousewheel)
        self.txt.bind("<Button-5>", self._on_mousewheel)
        self.txt.bind("<Button-1>", self._on_byte_click)
        self.txt.bind("<Button-3>", self._on_right_click)
        self.txt.bind("<Key>", self._on_key)

        bot = tk.Frame(self, bg=SURFACE2, height=28)
        bot.pack(fill="x", side="bottom")
        bot.pack_propagate(False)
        self._range_lbl = tk.Label(bot, text="", fg=MV_ADDR, bg=SURFACE2, font=FONT_TINY)
        self._range_lbl.pack(side="right", padx=14, pady=6)
        self._edit_lbl = tk.Label(bot, text="Click a byte to select · Type hex 00–FF · ← → ↑ ↓ navigate · ESC deselect",
                                  fg=TEXT_DIM, bg=SURFACE2, font=FONT_TINY)
        self._edit_lbl.pack(side="left", padx=14, pady=6)

    def _on_configure(self, event=None):
        """Debounced resize handler - grows the dump when window gets taller"""
        if hasattr(self, '_resize_timer'):
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(120, self._recompute_rows)

    def _recompute_rows(self):
        self.update_idletasks()
        text_h = self.txt.winfo_height()
        if text_h < 100:
            return

        line_height = 15
        header_height = 55
        usable = text_h - header_height

        new_rows = max(self.MIN_ROWS, min(self.MAX_ROWS, usable // line_height))

        if new_rows != self.rows:
            self.rows = new_rows
            self._do_refresh()

    def _draw_bar(self, canvas):
        canvas.delete("all")
        w = canvas.winfo_width()
        if w < 4: return
        h = canvas.winfo_height()
        ADDR_MAX = 0xFFFFFFFF
        for _, start, end, color, _ in WIIU_REGIONS:
            x1 = int(start / ADDR_MAX * w)
            x2 = max(x1 + 2, int(end / ADDR_MAX * w))
            canvas.create_rectangle(x1, 0, x2, h, fill=color, outline="")
        self._bar_drawn = True

    def _update_pos_bar(self):
        c = self._pos_canvas
        w = c.winfo_width()
        if w < 4: return
        c.delete("all")
        ADDR_MAX = 0xFFFFFFFF
        x = int(self.current_addr / ADDR_MAX * w)
        c.create_rectangle(max(0, x-2), 0, min(w, x+2), 4, fill="#ffffff", outline="")

    @staticmethod
    def _lighten(hex_color: str) -> str:
        try:
            r = min(255, int(hex_color[1:3], 16) + 40)
            g = min(255, int(hex_color[3:5], 16) + 40)
            b = min(255, int(hex_color[5:7], 16) + 40)
            return f"#{r:02X}{g:02X}{b:02X}"
        except Exception:
            return hex_color

    def _jump(self, event=None):
        try:
            raw = self.addr_var.get().strip()
            addr = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 16)
            self._jump_to(addr)
        except ValueError:
            pass

    def _jump_to(self, addr: int):
        self.current_addr = max(0, addr)
        self.addr_var.set(fmt_addr(self.current_addr))
        label, color = self._region_for(self.current_addr)
        if label:
            self._region_pill.config(text=f" {label} ", bg=color)
        else:
            self._region_pill.config(text=" unknown ", bg="#333344")
        self._do_refresh()

    def _scroll_rows(self, *args):
        if args[0] == "scroll":
            delta = int(args[1])
            self.current_addr = max(0, self.current_addr + delta * self.COLS)
            self.addr_var.set(fmt_addr(self.current_addr))
            self._do_refresh()

    def _on_mousewheel(self, event):
        step = -self.COLS * 4 if (event.num == 4 or getattr(event, "delta", 0) > 0) else self.COLS * 4
        self.current_addr = max(0, self.current_addr + step)
        self.addr_var.set(fmt_addr(self.current_addr))

    def _do_refresh(self):
        pm = self.pm_getter()
        base = self.base_getter()
        if pm and base:
            self._conn_dot.config(fg=SUCCESS)
            if not self._showing_copied:
                self._conn_lbl.config(text=f"BASE {fmt_addr(base)}", fg="#80c0b0")
        else:
            self._conn_dot.config(fg=DANGER)
            if not self._showing_copied:
                self._conn_lbl.config(text="not attached", fg="#80c0b0")
        if not self._bar_drawn:
            self._draw_bar(self._bar_canvas)
        self._update_pos_bar()
        if not pm or not base:
            return
        now = time.time()
        start = self.current_addr
        total = self.rows * self.COLS
        try:
            raw = pm.read_bytes(base + start, total)
        except Exception:
            return
        for i, b in enumerate(raw):
            a = start + i
            if a in self.prev_snapshot and self.prev_snapshot[a] != b:
                self.changed_times[a] = now
            self.prev_snapshot[a] = b
        cutoff = now - self.FLASH_MS / 1000.0
        self.changed_times = {a: t for a, t in self.changed_times.items() if t >= cutoff}
        
        # ── Update header ──
        start_nibble = self.current_addr & 0xF
        hdr_line = (" Address " +
                    " ".join(f".{(start_nibble + i) & 0xF:X}" for i in range(self.COLS)) +
                    " Text (ASCII)\n")
        self.hdr_txt.config(state="normal")
        self.hdr_txt.delete("1.0", "end")
        self.hdr_txt.insert("end", hdr_line, "hdr")
        self.hdr_txt.insert("end", " " + "─" * (10 + 3 * self.COLS + 4 + self.COLS) + "\n", "sep")
        self.hdr_txt.config(state="disabled")
        
        # ── Update scrollable data area ──
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        for row in range(self.rows):
            row_addr = start + row * self.COLS
            row_bytes = raw[row * self.COLS : (row + 1) * self.COLS]
            region_label, region_color = self._region_for(row_addr)
            addr_tag = f"rgn_{region_label}" if region_label else "addr"
            self.txt.insert("end", f" {row_addr:08X} ", addr_tag)
            for i, b in enumerate(row_bytes):
                a = row_addr + i
                if a == self._sel_addr and self._nibble:
                    self.txt.insert("end", self._nibble + "_ ", "sel_nibble")
                elif a == self._sel_addr:
                    self.txt.insert("end", f"{b:02X} ", "sel_hex")
                elif a in self.changed_times:
                    self.txt.insert("end", f"{b:02X} ", "changed")
                elif b == 0x00:
                    self.txt.insert("end", f"{b:02X} ", "zero")
                else:
                    self.txt.insert("end", f"{b:02X} ", "byte")
            self.txt.insert("end", " ", "byte")
            for j, b in enumerate(row_bytes):
                a = row_addr + j
                ch = chr(b) if 0x20 <= b < 0x7F else "."
                tag = "sel_ascii" if a == self._sel_addr else ("ascii" if 0x20 <= b < 0x7F else "zero")
                self.txt.insert("end", ch, tag)
            self.txt.insert("end", "\n")
        self.txt.config(state="disabled")
        end_addr = start + total - 1
        region_label, _ = self._region_for(start)
        region_str = f" [{region_label}]" if region_label else ""
        self._range_lbl.config(text=f"{fmt_addr(start)} – {fmt_addr(end_addr)} ({total} bytes){region_str}")
        self._update_edit_label()

    def _click_to_addr(self, event) -> Optional[tuple]:
        pos = self.txt.index(f"@{event.x},{event.y}")
        line, col = map(int, pos.split("."))
        data_row = line - 1
        if data_row < 0 or data_row >= self.rows:
            return None
        if 11 <= col < 11 + self.COLS * 3:
            byte_idx = (col - 11) // 3
            return (self.current_addr + data_row * self.COLS + byte_idx, "hex")
        if 60 <= col < 60 + self.COLS:
            byte_idx = col - 60
            return (self.current_addr + data_row * self.COLS + byte_idx, "ascii")
        return None

    def _on_byte_click(self, event):
        self.txt.focus_set()
        result = self._click_to_addr(event)
        if result is None:
            self._sel_addr = None
            self._nibble = ""
            self._update_edit_label()
            return "break"
        addr, mode = result
        self._sel_addr = addr
        self._nibble = ""
        self._edit_mode = mode
        self._update_edit_label()
        return "break"

    def _on_right_click(self, event):
        result = self._click_to_addr(event)
        if result is None:
            return "break"
        addr, _ = result
        addr_str = fmt_addr(addr)
        self.clipboard_clear()
        self.clipboard_append(addr_str)

        self._showing_copied = True
        self._conn_lbl.config(text=f"Copied {addr_str}", fg=SUCCESS)
        self.after(1500, self._restore_conn_label)

        return "break"
    
    def _restore_conn_label(self):
        """Restore connection label and clear the copied flag"""
        self._showing_copied = False
        pm = self.pm_getter()
        base = self.base_getter()
        if pm and base:
            self._conn_lbl.config(text=f"BASE {fmt_addr(base)}", fg="#80c0b0")
        else:
            self._conn_lbl.config(text="not attached", fg="#80c0b0")

    def _on_key(self, event):
        if self._sel_addr is None:
            return
        key = event.keysym
        if key == "Escape":
            self._sel_addr = None
            self._nibble = ""
            self._update_edit_label()
            return "break"
        moves = {"Right": 1, "Tab": 1, "Left": -1, "Down": self.COLS, "Up": -self.COLS}
        if key in moves:
            self._nibble = ""
            self._advance_selection(moves[key])
            return "break"
        if self._edit_mode == "hex":
            ch = event.char.upper()
            if ch in "0123456789ABCDEF":
                self._nibble += ch
                if len(self._nibble) == 2:
                    self._write_byte(self._sel_addr, int(self._nibble, 16))
                    self._nibble = ""
                    self._advance_selection(1)
                else:
                    self._update_edit_label()
            return "break"
        if self._edit_mode == "ascii":
            ch = event.char
            if ch and len(ch) == 1 and 0x20 <= ord(ch) <= 0x7E:
                self._write_byte(self._sel_addr, ord(ch))
                self._advance_selection(1)
            return "break"
        return "break"

    def _write_byte(self, wiiu_addr: int, value: int):
        pm = self.pm_getter()
        base = self.base_getter()
        if not pm or not base:
            return
        try:
            pm.write_bytes(base + wiiu_addr, bytes([value & 0xFF]), 1)
            self.changed_times[wiiu_addr] = time.time()
        except Exception:
            pass

    def _advance_selection(self, delta: int):
        if self._sel_addr is None:
            return
        new_addr = max(0, min(0xFFFFFFFF, self._sel_addr + delta))
        self._sel_addr = new_addr
        view_start = self.current_addr
        view_end = self.current_addr + self.rows * self.COLS - 1
        if new_addr < view_start:
            rows_up = (view_start - new_addr + self.COLS - 1) // self.COLS
            self.current_addr = max(0, self.current_addr - rows_up * self.COLS) & ~0xF
            self.addr_var.set(fmt_addr(self.current_addr))
        elif new_addr > view_end:
            rows_down = (new_addr - view_end + self.COLS - 1) // self.COLS
            self.current_addr = self.current_addr + rows_down * self.COLS & ~0xF
            self.addr_var.set(fmt_addr(self.current_addr))
        self._update_edit_label()

    def _update_edit_label(self):
        if self._sel_addr is None:
            self._edit_lbl.config(text="Click a byte to select · Type hex 00–FF · ← → ↑ ↓ navigate · ESC deselect", fg=TEXT_DIM)
            return
        pm = self.pm_getter()
        base = self.base_getter()
        cur_val = "??"
        if pm and base:
            try:
                cur_val = f"{pm.read_bytes(base + self._sel_addr, 1)[0]:02X}"
            except Exception:
                pass
        mode_str = "HEX" if self._edit_mode == "hex" else "ASCII"
        pending = f" input: {self._nibble}_" if self._nibble else ""
        self._edit_lbl.config(text=f"✎ {fmt_addr(self._sel_addr)} = {cur_val} [{mode_str}]{pending} ← → ↑ ↓ navigate · ESC deselect", fg=ACCENT2)

    def _schedule_refresh(self):
        if not self._running:
            return
        self._do_refresh()
        self.after(self.REFRESH_MS, self._schedule_refresh)

    def _on_close(self):
        self._running = False
        self.destroy()

# ─────────────────────────────────────────────
# FIELD DIALOG
# ─────────────────────────────────────────────
class FieldDialog(tk.Toplevel):
    def __init__(self, parent, on_save, existing: Optional[Dict] = None):
        super().__init__(parent)
        self.on_save = on_save
        self.title("Edit Field" if existing else "Add Field")
        self.geometry("520x580")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        d = existing or {}
        self.desc_var = tk.StringVar(value=d.get("description", "My Field"))
        self.offset_var = tk.StringVar(value=fmt_addr(d.get('offset', 0)))
        self.value_var = tk.StringVar()
        self.type_var = tk.StringVar(value=d.get("dtype", "int"))
        self.hex_var = tk.BooleanVar(value=d.get("use_hex", False))
        self.sub_type_var = tk.StringVar(value=d.get("ptr_sub_type", "float"))
        self.ptr_off_var = tk.StringVar(value=fmt_addr(d.get('ptr_extra_offset', 0)))
        dtype = d.get("dtype", "int")
        sv = d.get("saved_value", 0)
        if dtype == "float":
            self.value_var.set(f"{float(sv):.6f}" if sv else "0.000000")
        elif dtype in ("string", "aob"):
            self.value_var.set(str(sv) if sv else "")
        else:
            self.value_var.set(str(int(sv)) if sv is not None else "0")
        self._build()
        self.type_var.trace_add("write", self._on_type_change)
        self.sub_type_var.trace_add("write", lambda *_: self._highlight_sub_btns())
        self._on_type_change()

    def _build(self):
        hdr = tk.Frame(self, bg=SURFACE2, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Configure Field", font=FONT_BOLD, fg=ACCENT, bg=SURFACE2).pack(side="left", padx=20, pady=12)
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=30, pady=20)
        def row(label_text, widget_fn):
            tk.Label(body, text=label_text, fg=TEXT_DIM, bg=BG, font=FONT_TINY, anchor="w").pack(fill="x", pady=(10,1))
            return widget_fn()
        def entry(var, **kw):
            e = tk.Entry(body, textvariable=var, bg=SURFACE2, fg=TEXT_MONO, insertbackground=ACCENT, relief="flat", bd=0, font=FONT_MONO, **kw)
            e.pack(fill="x", ipady=6)
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x")
            return e
        row("DESCRIPTION", lambda: entry(self.desc_var))
        row("ADDRESS", lambda: entry(self.offset_var))
        row("SAVED VALUE", lambda: entry(self.value_var))
        tk.Label(body, text="TYPE", fg=TEXT_DIM, bg=BG, font=FONT_TINY, anchor="w").pack(fill="x", pady=(10,1))
        type_frame = tk.Frame(body, bg=BG)
        type_frame.pack(fill="x")
        self._type_btns = {}
        COLS = 7
        for i, dt in enumerate(DTYPES):
            color = ACCENT if dt == "pointer" else SURFACE2
            txt = dt.upper() if dt == "aob" else dt
            b = tk.Button(type_frame, text=txt, bg=color, fg=TEXT, relief="flat", bd=0,
                          font=FONT_TINY, padx=10, pady=5, cursor="hand2",
                          command=lambda d=dt: self.type_var.set(d))
            row = i // COLS
            col = i % COLS
            b.grid(row=row, column=col, padx=3, pady=2, sticky="w")
            self._type_btns[dt] = b
        self.ptr_frame = tk.Frame(body, bg=BG)
        tk.Label(self.ptr_frame, text="POINTER → SUB-TYPE", fg=TEXT_DIM, bg=BG, font=FONT_TINY, anchor="w").pack(fill="x", pady=(10,1))
        sub_frame = tk.Frame(self.ptr_frame, bg=BG)
        sub_frame.pack(fill="x")
        self._sub_btns = {}
        for i, dt in enumerate([t for t in DTYPES if t != "pointer"]):
            b = tk.Button(sub_frame, text=dt, bg=SURFACE2, fg=TEXT, relief="flat", bd=0,
                          font=FONT_TINY, padx=10, pady=5, cursor="hand2",
                          command=lambda d=dt: self.sub_type_var.set(d))
            row = i // COLS
            col = i % COLS
            b.grid(row=row, column=col, padx=3, pady=2, sticky="w")
            self._sub_btns[dt] = b
        tk.Label(self.ptr_frame, text="EXTRA OFFSET (hex)", fg=TEXT_DIM, bg=BG, font=FONT_TINY, anchor="w").pack(fill="x", pady=(8,1))
        e2 = tk.Entry(self.ptr_frame, textvariable=self.ptr_off_var, bg=SURFACE2, fg=TEXT_MONO, insertbackground=ACCENT, relief="flat", bd=0, font=FONT_MONO)
        e2.pack(fill="x", ipady=6)
        tk.Frame(self.ptr_frame, bg=BORDER, height=1).pack(fill="x")
        hx_frame = tk.Frame(body, bg=BG)
        hx_frame.pack(fill="x", pady=(14,0))
        tk.Checkbutton(hx_frame, text="Display values in hex", variable=self.hex_var, bg=BG, fg=TEXT_DIM,
                       selectcolor=SURFACE2, activebackground=BG, font=FONT_TINY).pack(side="left")
        foot = tk.Frame(self, bg=SURFACE2, height=56)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        make_btn(foot, "Cancel", self.destroy, color=SURFACE2).pack(side="right", padx=10, pady=10)
        make_btn(foot, "Save Field", self._save, color=SUCCESS, fg="#000").pack(side="right", padx=4, pady=10)

    def _on_type_change(self, *_):
        t = self.type_var.get()
        for dt, b in self._type_btns.items():
            active = dt == t
            c = ACCENT2 if active and dt != "pointer" else (ACCENT if active else SURFACE2)
            b.config(bg=c)
        if t == "pointer":
            self.ptr_frame.pack(fill="x")
            self._highlight_sub_btns()
        else:
            self.ptr_frame.pack_forget()
        if t == "float" and not self.value_var.get():
            self.value_var.set("0.000000")
        elif t in ("pointer", "aob") and not self.value_var.get():
            self.value_var.set("0" if t == "pointer" else "")

    def _highlight_sub_btns(self):
        st = self.sub_type_var.get()
        for dt, b in self._sub_btns.items():
            b.config(bg=ACCENT2 if dt == st else SURFACE2)

    def _save(self):
        try:
            offset_str = self.offset_var.get().strip()
            offset = int(offset_str, 16) if offset_str.lower().startswith(("0x", "-0x")) else int(offset_str)
            dtype = self.type_var.get()
            desc = self.desc_var.get().strip() or "Unnamed"
            use_hex = self.hex_var.get()
            val_str = self.value_var.get().strip()
            if dtype in ("string", "aob"):
                value = val_str
            elif dtype == "float":
                value = float(val_str)
            elif dtype == "pointer":
                value = int(val_str, 16) if val_str.lower().startswith("0x") else int(val_str)
            else:
                value = int(val_str, 16) if val_str.lower().startswith("0x") else int(val_str)
            ptr_off_str = self.ptr_off_var.get().strip().lower()
            if ptr_off_str.startswith("-0x"):
                ptr_extra = -int(ptr_off_str[3:], 16)
            elif ptr_off_str.startswith("0x-") or ptr_off_str.startswith("-"):
                ptr_extra = int(ptr_off_str.replace("0x", ""), 16) if "0x" in ptr_off_str else int(ptr_off_str)
            else:
                ptr_extra = int(ptr_off_str, 16) if ptr_off_str else 0
            field = {
                "offset": offset,
                "saved_value": value,
                "dtype": dtype,
                "description": desc,
                "use_hex": use_hex,
                "ptr_sub_type": self.sub_type_var.get(),
                "ptr_extra_offset": ptr_extra,
            }
            self.on_save(field)
            self.destroy()
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Could not parse number:\n{str(e)}", parent=self)

# ─────────────────────────────────────────────
# FOLDER DIALOG
# ─────────────────────────────────────────────
class FolderDialog(tk.Toplevel):
    def __init__(self, parent, on_save, existing_name: str = ""):
        super().__init__(parent)
        self.on_save = on_save
        self.title("Edit Folder" if existing_name else "New Folder")
        self.geometry("400x180")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.name_var = tk.StringVar(value=existing_name)
        hdr = tk.Frame(self, bg=SURFACE2, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Folder Name", font=FONT_BOLD, fg=ACCENT, bg=SURFACE2).pack(side="left", padx=20, pady=12)
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=30, pady=20)
        tk.Label(body, text="NAME", fg=TEXT_DIM, bg=BG, font=FONT_TINY, anchor="w").pack(fill="x", pady=(10,1))
        e = tk.Entry(body, textvariable=self.name_var, bg=SURFACE2, fg=TEXT_MONO, insertbackground=ACCENT,
                     relief="flat", bd=0, font=FONT_MONO)
        e.pack(fill="x", ipady=6)
        e.focus_set()
        e.select_range(0, tk.END)
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x")
        foot = tk.Frame(self, bg=SURFACE2, height=56)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        make_btn(foot, "Cancel", self.destroy, color=SURFACE2).pack(side="right", padx=10, pady=10)
        make_btn(foot, "Save", self._save, color=SUCCESS, fg="#000").pack(side="right", padx=4, pady=10)
        e.bind("<Return>", lambda e: self._save())

    def _save(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Invalid Name", "Folder name cannot be empty", parent=self)
            return
        self.on_save(name)
        self.destroy()

# ─────────────────────────────────────────────
# MEMORY SEARCH (with * wildcard)
# ─────────────────────────────────────────────
class MemorySearch(tk.Toplevel):
    CHUNK_SIZE = 0x200000
    MAX_RESULTS = 3000000
    UI_MAX_DISPLAY = 50
    first_scan_types = ["Exact Value", "Bigger Than", "Smaller Than", "Value Between", "Unknown Initial Value"]
    next_scan_types = ["Exact Value", "Bigger Than", "Smaller Than", "Value Between",
                       "Increased By", "Decreased By", "Increased", "Decreased", "Changed", "Unchanged"]

    def __init__(self, parent, pm_getter, base_getter, add_callback):
        super().__init__(parent)
        self.pm_getter = pm_getter
        self.base_getter = base_getter
        self.add_callback = add_callback
        self.results: List[Dict[str, Any]] = []
        self.stop_scan = False
        self.has_first_scan = False
        self.hex_input_var = tk.BooleanVar(value=False)
        self.title("🔍 Memory Search — Wii U Big Endian (Cheat Engine Style)")
        self.geometry("1080x720")
        self.configure(bg=BG)
        self._build_ui()

    def _build_ui(self):
        ctrl = tk.Frame(self, bg=SURFACE2, height=110)
        ctrl.pack(fill="x", padx=10, pady=8)
        ctrl.pack_propagate(False)
        tk.Label(ctrl, text="Type:", bg=SURFACE2, fg=TEXT_DIM, font=FONT_UI).pack(side="left", padx=(10,5))
        self.type_var = tk.StringVar(value="byte")
        ttk.Combobox(ctrl, textvariable=self.type_var, values=DTYPES, state="readonly", width=12, font=FONT_UI).pack(side="left", padx=5)
        tk.Label(ctrl, text="Scan Type:", bg=SURFACE2, fg=TEXT_DIM, font=FONT_UI).pack(side="left", padx=(10,5))
        self.scan_type_var = tk.StringVar(value="Exact Value")
        self.scan_combo = ttk.Combobox(ctrl, textvariable=self.scan_type_var, width=28, font=FONT_UI)
        self.scan_combo.pack(side="left", padx=5)
        self.value_frame = tk.Frame(ctrl, bg=SURFACE2)
        self.value_frame.pack(side="left", padx=10)
        self.value1_var = tk.StringVar()
        self.value1_entry = tk.Entry(self.value_frame, textvariable=self.value1_var, font=FONT_MONO, width=18, bg=MV_SURFACE, fg=TEXT_MONO)
        self.value1_entry.pack(side="left")
        self.value2_var = tk.StringVar()
        self.value2_entry = tk.Entry(self.value_frame, textvariable=self.value2_var, font=FONT_MONO, width=18, bg=MV_SURFACE, fg=TEXT_MONO)
        self.hex_check = tk.Checkbutton(ctrl, text="Hex", variable=self.hex_input_var,
                                        bg=SURFACE2, fg=TEXT_DIM, selectcolor=SURFACE2,
                                        activebackground=SURFACE2, font=FONT_TINY)
        self.hex_check.pack(side="left", padx=6)
        tk.Label(ctrl, text="Align:", bg=SURFACE2, fg=TEXT_DIM, font=FONT_UI).pack(side="left", padx=(5,5))
        self.align_var = tk.IntVar(value=4)
        ttk.Combobox(ctrl, textvariable=self.align_var, width=5, values=[1, 2, 4, 8, 16], state="readonly", font=FONT_UI).pack(side="left", padx=5)
        tk.Label(ctrl, text="Start:", bg=SURFACE2, fg=TEXT_DIM, font=FONT_UI).pack(side="left", padx=(10,5))
        self.start_var = tk.StringVar(value="0x10000000")
        tk.Entry(ctrl, textvariable=self.start_var, font=FONT_MONO, width=14).pack(side="left", padx=5)
        tk.Label(ctrl, text="End:", bg=SURFACE2, fg=TEXT_DIM, font=FONT_UI).pack(side="left", padx=(5,5))
        self.end_var = tk.StringVar(value="0x4FFFFFFF")
        tk.Entry(ctrl, textvariable=self.end_var, font=FONT_MONO, width=14).pack(side="left", padx=5)
        btnf = tk.Frame(self, bg=BG)
        btnf.pack(fill="x", padx=10, pady=4)
        scanf = tk.Frame(self, bg=BG)
        scanf.pack(fill="x", padx=10, pady=6)
        make_btn(scanf, "First Scan", self._first_scan, color=ACCENT, fg="#000").pack(side="left", padx=4)
        make_btn(scanf, "Next Scan", self._next_scan, color=ACCENT2, fg="#000").pack(side="left", padx=4)
        self.stop_btn = make_btn(scanf, "⏹ Stop Scan", self._stop_scan, color=MV_CHG_BG)
        self.stop_btn.pack(side="left", padx=4)
        self.stop_btn.config(state="disabled")
        make_btn(scanf, "Add Selected to List", self._add_selected_results, color=SUCCESS, fg="#000").pack(side="left", padx=8)
        make_btn(scanf, "Add All", lambda: self._add_selected_results(add_all=True), color=SUCCESS, fg="#000").pack(side="left", padx=8)
        make_btn(scanf, "Clear", self._clear_results, color=DANGER, fg="#000").pack(side="left", padx=4)
        res_frame = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        res_frame.pack(fill="both", expand=True, padx=10, pady=4)
        cols = ("addr", "value", "prev_value", "region")
        self.res_tree = ttk.Treeview(res_frame, columns=cols, show="headings")
        self.res_tree.heading("addr", text="Virtual Address")
        self.res_tree.heading("value", text="Current Value")
        self.res_tree.heading("prev_value", text="Previous Value")
        self.res_tree.heading("region", text="Region")
        self.res_tree.column("addr", width=160)
        self.res_tree.column("value", width=180)
        self.res_tree.column("prev_value", width=180)
        self.res_tree.column("region", width=100)
        vsb = ttk.Scrollbar(res_frame, orient="vertical", command=self.res_tree.yview)
        self.res_tree.configure(yscrollcommand=vsb.set)
        self.res_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.status = tk.Label(self, text="Ready — select scan type and press First Scan", bg=BG, fg=TEXT_DIM, font=FONT_TINY)
        self.status.pack(pady=8)
        self.scan_combo.bind("<<ComboboxSelected>>", self._on_scan_type_change)
        self._update_scan_dropdown()

    def _parse_search_value(self, val_str: str, dtype: str):
        if not val_str or not val_str.strip():
            return None
        val_str = val_str.strip()
        use_hex = self.hex_input_var.get() and dtype in ("int", "uint", "byte", "halfword", "pointer")
        base = 16 if use_hex else 0
        try:
            if dtype == "float":
                return float(val_str)
            elif dtype in ("string", "aob"):
                return val_str
            else:
                return int(val_str, base)
        except Exception:
            return val_str

    def _update_scan_dropdown(self):
        if not self.has_first_scan:
            self.scan_combo['values'] = self.first_scan_types
            self.scan_type_var.set("Exact Value")
        else:
            self.scan_combo['values'] = self.next_scan_types
            if self.scan_type_var.get() not in self.next_scan_types:
                self.scan_type_var.set("Exact Value")
        self._on_scan_type_change()

    def _on_scan_type_change(self, *args):
        st = self.scan_type_var.get()
        for widget in self.value_frame.winfo_children():
            widget.pack_forget()
        self.value1_entry.pack(side="left")
        if st == "Value Between":
            self.value2_entry.pack(side="left", padx=(8, 0))

    def _set_range(self, start):
        self.start_var.set(fmt_addr(start))
        for _, s, e, _, _ in WIIU_REGIONS:
            if s == start:
                self.end_var.set(fmt_addr(e))
                break

    def _get_range(self):
        try:
            return int(self.start_var.get().strip(), 16), int(self.end_var.get().strip(), 16)
        except Exception:
            messagebox.showerror("Invalid Range", "Start / End must be valid hex addresses", parent=self)
            return None, None

    def _dtype_size(self, dtype: str) -> int:
        return {"byte":1,"ubyte":1,"halfword":2,"ushort":2,"int":4,"uint":4,"float":4,"pointer":4,
                "double":8,"int64":8,"uint64":8,"aob":16,"string":1}.get(dtype, 4)

    def _pack_pattern(self, dtype: str, val_str: str):
        try:
            if dtype == "aob":
                clean = val_str.replace(" ", "").replace(",", "")
                if len(clean) % 2 != 0: clean = "0" + clean
                return bytes.fromhex(clean)
            if dtype == "string":
                return val_str.encode("utf-8")
            value = self._parse_search_value(val_str, dtype)
            if value is None: return None
            if dtype == "float": return struct.pack(">f", value)
            if dtype == "double": return struct.pack(">d", value)
            if dtype == "int": return struct.pack(">i", value)
            if dtype == "int64": return struct.pack(">q", value)
            if dtype == "uint": return struct.pack(">I", value)
            if dtype == "uint64": return struct.pack(">Q", value)
            if dtype == "byte": return bytes([value & 0xFF])
            if dtype == "ubyte": return bytes([value & 0xFF])
            if dtype == "halfword": return struct.pack(">h", value)
            if dtype == "ushort": return struct.pack(">H", value)
            if dtype == "pointer": return struct.pack(">I", value)
            return None
        except Exception:
            return None

    def _read_value_at(self, pm, base, vaddr: int, dtype: str):
        try:
            host_addr = base + vaddr
            if dtype == "aob":
                data = pm.read_bytes(host_addr, 32)
                return " ".join(f"{b:02X}" for b in data)
            if dtype == "string":
                data = pm.read_bytes(host_addr, 128)
                null = data.find(b'\x00')
                return data[:null].decode("utf-8", errors="ignore") if null != -1 else data.decode("utf-8", errors="ignore")
            if dtype == "float": return struct.unpack(">f", pm.read_bytes(host_addr, 4))[0]
            if dtype == "double": return struct.unpack(">d", pm.read_bytes(host_addr, 8))[0]
            if dtype == "int": return struct.unpack(">i", pm.read_bytes(host_addr, 4))[0]
            if dtype == "int64": return struct.unpack(">q", pm.read_bytes(host_addr, 8))[0]
            if dtype == "uint": return struct.unpack(">I", pm.read_bytes(host_addr, 4))[0]
            if dtype == "uint64": return struct.unpack(">Q", pm.read_bytes(host_addr, 8))[0]
            if dtype == "byte": return struct.unpack(">b", pm.read_bytes(host_addr, 1))[0]
            if dtype == "ubyte": return pm.read_bytes(host_addr, 1)[0]
            if dtype == "halfword": return struct.unpack(">h", pm.read_bytes(host_addr, 2))[0]
            if dtype == "ushort": return struct.unpack(">H", pm.read_bytes(host_addr, 2))[0]
            if dtype == "pointer": return struct.unpack(">I", pm.read_bytes(host_addr, 4))[0]
            return 0
        except Exception:
            return None

    def _val_to_float(self, v) -> float:
        return float(v) if v is not None else 0.0

    def _is_text_dtype(self, dtype: str) -> bool:
        return dtype in ("string", "aob")

    def _matches_condition(self, current_val, scan_type: str, val1_str: str, val2_str: str, dtype: str) -> bool:
        if current_val is None: return False
        if self._is_text_dtype(dtype):
            if scan_type != "Exact Value": return False
            s_current = str(current_val)
            if dtype == "string" and "*" in val1_str:
                regex_pat = re.escape(val1_str).replace(r'\*', r'.*')
                return bool(re.search(regex_pat, s_current))
            return s_current == val1_str
        user_val1 = self._parse_search_value(val1_str, dtype)
        if user_val1 is None: return False
        nv = self._val_to_float(current_val)
        if scan_type == "Exact Value": return nv == self._val_to_float(user_val1)
        if scan_type == "Bigger Than": return nv > self._val_to_float(user_val1)
        if scan_type == "Smaller Than": return nv < self._val_to_float(user_val1)
        if scan_type == "Value Between":
            user_val2 = self._parse_search_value(val2_str, dtype)
            if user_val2 is None: return False
            return self._val_to_float(user_val1) <= nv <= self._val_to_float(user_val2)
        return True

    def _matches_next_condition(self, new_val, old_val, scan_type: str, val1_str: str, val2_str: str, dtype: str) -> bool:
        if new_val is None: return False
        if self._is_text_dtype(dtype):
            s_new = str(new_val)
            s_old = str(old_val) if old_val is not None else ""
            if scan_type == "Exact Value":
                if dtype == "string" and "*" in val1_str:
                    regex_pat = re.escape(val1_str).replace(r'\*', r'.*')
                    return bool(re.search(regex_pat, s_new))
                return s_new == val1_str
            if scan_type == "Changed": return s_new != s_old
            if scan_type == "Unchanged": return s_new == s_old
            return True
        nv = self._val_to_float(new_val)
        ov = self._val_to_float(old_val)
        user_val1 = self._parse_search_value(val1_str, dtype)
        if user_val1 is None and scan_type in ("Exact Value", "Increased By", "Decreased By", "Value Between"):
            return False
        if scan_type == "Exact Value": return nv == self._val_to_float(user_val1)
        if scan_type == "Bigger Than": return nv > self._val_to_float(user_val1)
        if scan_type == "Smaller Than": return nv < self._val_to_float(user_val1)
        if scan_type == "Value Between":
            user_val2 = self._parse_search_value(val2_str, dtype)
            if user_val2 is None: return False
            return self._val_to_float(user_val1) <= nv <= self._val_to_float(user_val2)
        if scan_type == "Increased By": return nv == ov + self._val_to_float(user_val1)
        if scan_type == "Decreased By": return nv == ov - self._val_to_float(user_val1)
        if scan_type == "Increased": return nv > ov
        if scan_type == "Decreased": return nv < ov
        if scan_type == "Changed": return nv != ov
        if scan_type == "Unchanged": return nv == ov
        return True

    def _stop_scan(self):
        self.stop_scan = True

    def _first_scan(self):
        self.results.clear()
        self.stop_scan = False
        self.stop_btn.config(state="normal")
        pm = self.pm_getter()
        base = self.base_getter()
        if not pm or not base:
            self.status.config(text="❌ Not attached to Cemu")
            self.stop_btn.config(state="disabled")
            return
        start, end = self._get_range()
        if start is None:
            self.stop_btn.config(state="disabled")
            return
        dtype = self.type_var.get()
        scan_type = self.scan_type_var.get()
        if dtype == "aob":
            align = 1
        else:
            align = self.align_var.get()

        if scan_type == "Unknown Initial Value":
            align = self.align_var.get()
            addr = start
            found = 0
            while addr < end and not self.stop_scan and len(self.results) < self.MAX_RESULTS:
                rsize = min(self.CHUNK_SIZE, end - addr + 1)
                try:
                    data = pm.read_bytes(base + addr, rsize)
                    sz = self._dtype_size(dtype)
                    for i in range(0, len(data) - sz + 1, align):
                        match_addr = addr + i
                        if match_addr > end: break
                        val = self._read_value_at(pm, base, match_addr, dtype)
                        if val is not None:
                            self.results.append({"addr": match_addr, "value": val, "old_value": val, "dtype": dtype})
                            found += 1
                            if found >= self.MAX_RESULTS: break
                except Exception:
                    pass
                addr += self.CHUNK_SIZE
                if found % 5000 == 0 and found > 0:
                    self.status.config(text=f"Unknown Initial Value scan… ({found} so far)")
                    self.update_idletasks()
            self.has_first_scan = True
            self._update_scan_dropdown()
            self._update_tree()
            self.status.config(text=f"✅ Snapshot complete — {found} addresses stored (limit {self.MAX_RESULTS:,})")
            self.stop_btn.config(state="disabled")
            return

        val_str = self.value1_var.get().strip()
        if not val_str:
            self.status.config(text="❌ Enter a value to search for")
            self.stop_btn.config(state="disabled")
            return

        val2_str = self.value2_var.get().strip()
        is_string_wildcard = (dtype == "string" and scan_type == "Exact Value" and "*" in val_str)

        if is_string_wildcard:
            regex_pat = re.escape(val_str).replace(r'\*', r'.*')
            search_re = re.compile(regex_pat.encode('utf-8', errors='ignore'), re.DOTALL)
            align = self.align_var.get()
            addr = start
            found = 0
            chunk_count = 0
            self.status.config(text=f"Scanning string wildcard '{val_str}' … (0 found)")
            self.update()
            while addr < end and not self.stop_scan and len(self.results) < self.MAX_RESULTS:
                rsize = min(self.CHUNK_SIZE, end - addr + 1)
                try:
                    data = pm.read_bytes(base + addr, rsize)
                    for match in search_re.finditer(data):
                        offset = match.start()
                        match_addr = addr + offset
                        if match_addr > end: break
                        if match_addr % align == 0:
                            val = self._read_value_at(pm, base, match_addr, dtype)
                            if val is not None and self._matches_condition(val, scan_type, val_str, val2_str, dtype):
                                self.results.append({"addr": match_addr, "value": val, "old_value": val, "dtype": dtype})
                                found += 1
                                if found >= self.MAX_RESULTS: break
                except Exception:
                    pass
                addr += self.CHUNK_SIZE
                chunk_count += 1
                if chunk_count % 4 == 0:
                    self.status.config(text=f"Scanning string wildcard … ({found} found so far)")
                    self.update_idletasks()
            self.has_first_scan = True
            self._update_scan_dropdown()
            self._update_tree()
            self.status.config(text=f"✅ First scan complete — {found} match(es) (limit {self.MAX_RESULTS:,})")
            self.stop_btn.config(state="disabled")
            return

        # Normal scan
        pattern = self._pack_pattern(dtype, val_str)
        if pattern is None:
            self.status.config(text="❌ Invalid value / could not pack pattern")
            self.stop_btn.config(state="disabled")
            return
        align = self.align_var.get()
        plen = len(pattern)
        addr = start
        found = 0
        chunk_count = 0
        self.status.config(text=f"Scanning {dtype} … (0 found)")
        self.update()
        while addr < end and not self.stop_scan and len(self.results) < self.MAX_RESULTS:
            rsize = min(self.CHUNK_SIZE, end - addr + 1)
            try:
                data = pm.read_bytes(base + addr, rsize)
                offset = 0
                while True:
                    offset = data.find(pattern, offset)
                    if offset == -1: break
                    match_addr = addr + offset
                    if match_addr > end: break
                    if match_addr % align == 0:
                        val = self._read_value_at(pm, base, match_addr, dtype)
                        if val is not None and self._matches_condition(val, scan_type, val_str, val2_str, dtype):
                            self.results.append({"addr": match_addr, "value": val, "old_value": val, "dtype": dtype})
                            found += 1
                    offset += plen
            except Exception:
                pass
            addr += self.CHUNK_SIZE - max(0, plen - 1)
            chunk_count += 1
            if chunk_count % 4 == 0:
                self.status.config(text=f"Scanning {dtype} … ({found} found so far)")
                self.update_idletasks()
        self.has_first_scan = True
        self._update_scan_dropdown()
        self._update_tree()
        self.status.config(text=f"✅ First scan complete — {found} match(es) (limit {self.MAX_RESULTS:,})")
        self.stop_btn.config(state="disabled")

    def _next_scan(self):
        if not self.results:
            self.status.config(text="⚠ Run First Scan first")
            return
        pm = self.pm_getter()
        base = self.base_getter()
        if not pm or not base:
            self.status.config(text="❌ Not attached to Cemu")
            return
        scan_type = self.scan_type_var.get()
        val1_str = self.value1_var.get().strip()
        val2_str = self.value2_var.get().strip()
        new_res = []
        checked = kept = 0
        self.status.config(text=f"Next scan ({scan_type}) — filtering {len(self.results):,} addresses…")
        self.update_idletasks()
        for r in self.results:
            dtype = r.get("dtype", self.type_var.get())
            old_val = r.get("old_value")
            new_val = self._read_value_at(pm, base, r["addr"], dtype)
            if self._matches_next_condition(new_val, old_val, scan_type, val1_str, val2_str, dtype):
                r["old_value"] = new_val
                r["value"] = new_val
                new_res.append(r)
                kept += 1
            checked += 1
            if checked % 5000 == 0:
                self.status.config(text=f"Filtering… {checked:,}/{len(self.results):,} ({kept} kept so far)")
                self.update_idletasks()
        self.results = new_res
        self._update_tree()
        self.status.config(text=f"✅ Next scan complete — {kept:,} match(es) remain")

    def _update_tree(self):
        for i in self.res_tree.get_children():
            self.res_tree.delete(i)
        display_list = self.results[:self.UI_MAX_DISPLAY]
        for r in display_list:
            label, _ = MemoryViewer._region_for(r["addr"])
            cur = str(r["value"]) if r["value"] is not None else "??"
            prev = str(r["old_value"]) if r.get("old_value") is not None else "—"
            self.res_tree.insert("", "end", values=(fmt_addr(r['addr']), cur, prev, label or "unknown"))
        if len(self.results) > self.UI_MAX_DISPLAY:
            self.status.config(text=f"Showing first {self.UI_MAX_DISPLAY} of {len(self.results):,} matches — use Next Scan to narrow down")

    def _add_selected_results(self, add_all=False):
        if add_all:
            to_add = self.results[:]
        else:
            sel = self.res_tree.selection()
            to_add = [self.results[self.res_tree.index(iid)] for iid in sel if self.res_tree.index(iid) < len(self.results)]
        if not to_add:
            messagebox.showinfo("Nothing selected", "Select rows or use 'Add All'", parent=self)
            return
        fields = []
        for r in to_add:
            dtype = r["dtype"]
            sv = r["value"] if r["value"] is not None else 0
            if dtype in ("string", "aob"):
                sv = str(sv)
            field = {
                "offset": r["addr"],
                "saved_value": sv,
                "dtype": dtype,
                "description": f"Search {dtype} @ {fmt_addr(r['addr'])}",
                "use_hex": dtype in ("uint", "int", "byte", "halfword", "aob"),
                "ptr_sub_type": "uint",
                "ptr_extra_offset": 0,
            }
            fields.append(field)
        self.add_callback(fields)
        messagebox.showinfo("Added", f"✅ Added {len(fields)} field(s) to the main list", parent=self)

    def _clear_results(self):
        self.results.clear()
        self.has_first_scan = False
        self._update_scan_dropdown()
        self._update_tree()
        self.status.config(text="Results cleared — ready for a new First Scan")

# ─────────────────────────────────────────────
# MAIN APPLICATION
# ─────────────────────────────────────────────
class CemuMemoryGUI:
    def __init__(self):
        self.pm: Optional[pymem.Pymem] = None
        self.base: int = 0
        self.log_path: str = ""
        self.tree_data: Dict[str, Any] = {}
        self.update_running = False
        self._mem_viewer: Optional[MemoryViewer] = None
        self._search_win: Optional[MemorySearch] = None
        self.root = tk.Tk()
        self.root.title("CEMU · Memory Engine")
        self.root.geometry("800x820")
        self.root.minsize(800, 120)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_hex = tk.BooleanVar(value=False)
        self.read_ms = tk.IntVar(value=100)
        self.write_ms = tk.IntVar(value=100)
        self.last_file_path = ""
        self._dragging = False
        self._drag_start_item = None
        self._setup_styles()
        self._build_ui()
        self._refresh_connection()
        self._auto_load_last_file()

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Treeview", background=SURFACE, foreground=TEXT, fieldbackground=SURFACE,
                    borderwidth=0, font=FONT_MONO, rowheight=24)
        s.configure("Treeview.Heading", background=SURFACE2, foreground=TEXT_DIM, borderwidth=0,
                    relief="flat", font=FONT_BOLD)
        s.map("Treeview", background=[("selected", BORDER)], foreground=[("selected", ACCENT2)])
        s.map("Treeview.Heading", background=[("active", BORDER)])
        s.configure("Vertical.TScrollbar", background=SURFACE2, troughcolor=SURFACE,
                    borderwidth=0, arrowcolor=TEXT_DIM)

    def _build_ui(self):
        topbar = tk.Frame(self.root, bg=SURFACE2, height=24)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        self.status_frame = tk.Frame(topbar, bg=SURFACE2)
        self.status_frame.pack(side="right", padx=16)
        self.status_dot = tk.Label(self.status_frame, text="●", fg=DANGER, bg=SURFACE2, font=("Consolas", 11))
        self.status_dot.pack(side="left", padx=(0,4))
        self.status_label = tk.Label(self.status_frame, text="Disconnected", fg=TEXT_DIM, bg=SURFACE2, font=FONT_BIG)
        self.status_label.pack(side="left")
        self.base_label = tk.Label(topbar, text="BASE ——", fg=TEXT_DIM, bg=SURFACE2, font=FONT_BIG)
        self.base_label.pack(side="left", padx=20)

        toolbar = tk.Frame(self.root, bg=BG, pady=8)
        toolbar.pack(fill="x", padx=12)
        left_tools = tk.Frame(toolbar, bg=BG)
        left_tools.pack(side="left")
        make_btn(left_tools, "📁 New Folder", self._add_folder_dialog, color=WARN, fg="#000", width=8).pack(side="left", padx=2)
        make_btn(left_tools, "+ Add Field", self._add_field_dialog, color=SUCCESS, fg="#001", width= 6).pack(side="left", padx=2)
        make_btn(left_tools, "✕ Remove", self._remove_selected, color=DANGER, fg="#fff", width=6).pack(side="left", padx=2)
        make_btn(left_tools, "Clear All", self._remove_all_fields, color=DANGER, fg="#fff", width=6).pack(side="left", padx=2)
        tk.Frame(left_tools, bg=BORDER, width=1, height=26).pack(side="left", padx=4, pady=2)
        make_btn(left_tools, "▶ Write Selected", self._write_selected, color=ACCENT, fg="#fff", width=10).pack(side="left", padx=2)
        self.toggle_btn = make_btn(left_tools, "⏩ Continuous OFF", self._toggle_update, color=SURFACE2, width=12)
        self.toggle_btn.pack(side="left", padx=2)
        tk.Frame(left_tools, bg=BORDER, width=1, height=26).pack(side="left", padx=4, pady=2)
        mv_btn = tk.Button(left_tools, text="🔬 MemView", command=self._open_memory_viewer,
                           bg=MV_HDR, fg="#d0fff6", activebackground=MV_HDR_LT, activeforeground="#fff",
                           relief="flat", bd=0, font=FONT_UI, cursor="hand2", padx=12, pady=5, width=12)
        mv_btn.bind("<Enter>", lambda e: mv_btn.config(bg=MV_HDR_LT))
        mv_btn.bind("<Leave>", lambda e: mv_btn.config(bg=MV_HDR))
        mv_btn.pack(side="left", padx=2)
        search_btn = tk.Button(left_tools, text="🔍 MemSearch", command=self._open_memory_search,
                               bg="#ff8c00", fg="#ffffff", activebackground="#ffaa33", activeforeground="#fff",
                               relief="flat", bd=0, font=FONT_UI, cursor="hand2", padx=12, pady=5, width=12)
        search_btn.bind("<Enter>", lambda e: search_btn.config(bg="#ffaa33"))
        search_btn.bind("<Leave>", lambda e: search_btn.config(bg="#ff8c00"))
        search_btn.pack(side="left", padx=2)

        tree_frame = tk.Frame(self.root, bg=BORDER, padx=1, pady=1)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=4)
        cols = ("Address", "Live Value", "Saved Value", "Type", "Sub/Extra", "Points To")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="Name")
        self.tree.column("#0", width=180, minwidth=150)
        widths = {"Address": 90, "Live Value": 110, "Saved Value": 110,
                  "Type": 65, "Sub/Extra": 120, "Points To": 100}
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths.get(col, 100), minwidth=60)
        self.tree.tag_configure("folder", foreground=WARN, font=FONT_BOLD)
        self.tree.tag_configure("pointer_row", foreground=ACCENT)
        self.tree.tag_configure("ok", foreground=TEXT)
        self.tree.tag_configure("err", foreground=DANGER)
        self.tree.tag_configure("live", foreground=ACCENT2)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Control-c>", self._copy_selected)
        self.tree.bind("<Control-C>", self._copy_selected)
        self.tree.bind("<Control-v>", self._paste_fields)
        self.tree.bind("<Control-V>", self._paste_fields)
        self.tree.bind("<ButtonPress-1>", self._on_drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._on_drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._on_drag_release)
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bot = tk.Frame(self.root, bg=SURFACE2, height=36)
        bot.pack(fill="x", side="bottom")
        bot.pack_propagate(False)
        tk.Checkbutton(bot, text="Hex display", variable=self.show_hex, bg=SURFACE2, fg=TEXT_DIM,
                       selectcolor=BORDER, activebackground=SURFACE2, font=FONT_TINY,
                       command=self._refresh_tree).pack(side="left", padx=16, pady=8)
        def spin_group(parent, label, var):
            tk.Label(parent, text=label, bg=SURFACE2, fg=TEXT_DIM, font=FONT_TINY).pack(side="left", padx=(12,2))
            e = tk.Entry(parent, textvariable=var, width=6, justify="center", bg=BORDER, fg=TEXT_MONO,
                         relief="flat", bd=0, font=FONT_MONO, insertbackground=ACCENT)
            e.pack(side="left", padx=(0,4), pady=6, ipady=2)
        spin_group(bot, "Read ms", self.read_ms)
        spin_group(bot, "Write ms", self.write_ms)
        self.connect_btn = make_btn(bot, "🔌 Connect", self._manual_connect, color=ACCENT, fg="#fff", width=6)
        self.connect_btn.pack(side="right", padx=6, pady=2)
        tk.Frame(bot, bg=BORDER, width=1, height=26).pack(side="right", padx=4, pady=2)
        make_btn(bot, "⬆ Save", self._save_fields, color=ACCENT).pack(side="right", padx=2)
        make_btn(bot, "⬇ Load", self._load_fields, color=ACCENT).pack(side="right", padx=2)
        self.file_indicator = tk.Label(bot, text="No file loaded", fg=TEXT_DIM, bg=SURFACE2, font=FONT_TINY)
        self.file_indicator.pack(side="left", padx=16)

    def _on_drag_start(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self._dragging = True
            self._drag_start_item = item
            self.tree.configure(cursor="fleur")

    def _on_drag_motion(self, event):
        pass

    def _on_drag_release(self, event):
        if not getattr(self, '_dragging', False) or not getattr(self, '_drag_start_item', None):
            self._reset_drag()
            return
        target = self.tree.identify_row(event.y)
        start_item = self._drag_start_item
        self._reset_drag()
        if not target or target == start_item:
            return
        if self._is_folder(start_item) and self._would_create_cycle(start_item, target):
            return
        bbox = self.tree.bbox(target)
        if not bbox:
            return
        y_center = bbox[1] + bbox[3] // 2
        if self._is_folder(target):
            self.tree.move(start_item, target, "end")
        else:
            parent = self.tree.parent(target)
            index = self.tree.index(target)
            new_index = index if event.y < y_center else index + 1
            self.tree.move(start_item, parent, new_index)

    def _would_create_cycle(self, moving_item: str, potential_parent: str) -> bool:
        current = potential_parent
        while current:
            if current == moving_item:
                return True
            current = self.tree.parent(current)
        return False

    def _reset_drag(self):
        self._dragging = False
        self._drag_start_item = None
        self.tree.configure(cursor="")

    def _on_right_click(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return

        if self._is_folder(item):
            menu = tk.Menu(self.root, tearoff=0, bg=SURFACE2, fg=TEXT,
                           activebackground=ACCENT, activeforeground="#fff")
            menu.add_command(label="Rename Folder", command=lambda: self._rename_folder(item))
            menu.add_separator()
            menu.add_command(label="Add Field Here", command=lambda: self._add_field_to_folder(item))
            menu.add_command(label="Add Subfolder", command=lambda: self._add_subfolder(item))
            menu.add_separator()
            menu.add_command(label="Delete Folder", command=lambda: self._remove_folder(item))
            menu.post(event.x_root, event.y_root)
            return

        col_idx = int(col[1:]) if col.startswith("#") else 0

        if col_idx == 1:
            if item in self.tree_data and not self._is_folder(item):
                field = self.tree_data[item]
                addr_str = fmt_addr(field["offset"])
                self.root.clipboard_clear()
                self.root.clipboard_append(addr_str)

                self.status_label.config(text=f"Copied {addr_str}", fg=SUCCESS)
                self.root.after(1200, self._restore_status_label)
                return

        if col_idx == 6:
            field = self.tree_data.get(item)
            if field and field.get("dtype") == "pointer":
                resolved = self._get_resolved_addr(field)
                if resolved is not None:
                    addr_str = fmt_addr(resolved)
                    self.root.clipboard_clear()
                    self.root.clipboard_append(addr_str)

                    self.status_label.config(text=f"Copied {addr_str}", fg=SUCCESS)
                    self.root.after(1200, self._restore_status_label)
            return

        self._edit_field(item)

    def _add_folder_dialog(self):
        selected = self.tree.selection()
        parent_id = selected[0] if selected and self._is_folder(selected[0]) else ""
        def on_save(name):
            self._add_folder(name, parent_id)
        FolderDialog(self.root, on_save)

    def _add_folder(self, name: str, parent_id: str = ""):
        folder_id = self.tree.insert(parent_id, "end", text=name, values=("", "", "", "FOLDER", "", ""), tags=("folder",))
        self.tree_data[folder_id] = {"type": "folder", "name": name}
        return folder_id

    def _is_folder(self, item_id: str) -> bool:
        return item_id in self.tree_data and self.tree_data[item_id].get("type") == "folder"

    def _add_field_dialog(self):
        selected = self.tree.selection()
        parent_id = selected[0] if selected and self._is_folder(selected[0]) else ""
        def on_save(field):
            self._add_field(field, parent_id)
        FieldDialog(self.root, on_save)

    def _add_field(self, field: Dict, parent_id: str = ""):
        item_id = self._insert_field_row(field, parent_id)
        self.tree_data[item_id] = {**field, "type": "field"}
        return item_id

    def _insert_field_row(self, field: Dict, parent_id: str = "") -> str:
        offset = field["offset"]
        sv = field["saved_value"]
        dtype = field["dtype"]
        desc = field["description"]
        use_hex = field.get("use_hex", False)
        live_display = ""
        tag = "ok"
        if self.pm and self.base:
            live = self._read_value(self.base + offset, dtype, field)
            live_display = self._fmt(live, dtype, use_hex, field)
            tag = "err" if live is None else ("pointer_row" if dtype == "pointer" else "live")
        saved_display = self._fmt(sv, dtype, use_hex, field)
        sub_label = self._sub_extra_label(field)
        resolved = self._get_resolved_addr(field)
        resolved_display = fmt_addr(resolved) if resolved is not None else "—"
        return self.tree.insert(parent_id, "end", text=desc, values=(
            fmt_addr(offset), live_display or "—", saved_display,
            dtype, sub_label, resolved_display
        ), tags=(tag,))

    def _remove_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        for item in selected:
            if item in self.tree_data:
                del self.tree_data[item]
            self._remove_tree_data_recursive(item)
            self.tree.delete(item)

    def _remove_all_fields(self):
        if not self.tree_data:
            messagebox.showinfo("Empty", "No fields to remove.", parent=self.root)
            return
        if messagebox.askyesno("Clear All", "Remove ALL fields and folders?\n\nThis cannot be undone.", parent=self.root):
            for item in self.tree.get_children(""):
                self.tree.delete(item)
            self.tree_data.clear()
            self.status_label.config(text="All fields cleared", fg=WARN)
            self.root.after(2000, self._restore_status_label)

    def _remove_tree_data_recursive(self, parent_id: str):
        for child in self.tree.get_children(parent_id):
            if child in self.tree_data:
                del self.tree_data[child]
            self._remove_tree_data_recursive(child)

    def _refresh_tree(self):
        for item_id in self.tree.get_children(""):
            self._refresh_tree_recursive(item_id)

    def _refresh_tree_recursive(self, item_id: str):
        if self._is_folder(item_id):
            for child in self.tree.get_children(item_id):
                self._refresh_tree_recursive(child)
        else:
            if item_id in self.tree_data:
                field = self.tree_data[item_id]
                offset = field["offset"]
                dtype = field["dtype"]
                use_hex = field.get("use_hex", False)
                live_display = ""
                tag = "ok"
                if self.pm and self.base:
                    live = self._read_value(self.base + offset, dtype, field)
                    live_display = self._fmt(live, dtype, use_hex, field)
                    tag = "err" if live is None else ("pointer_row" if dtype == "pointer" else "live")
                saved_display = self._fmt(field["saved_value"], dtype, use_hex, field)
                sub_label = self._sub_extra_label(field)
                resolved = self._get_resolved_addr(field)
                resolved_display = fmt_addr(resolved) if resolved is not None else "—"
                new_name = field.get("description", "Unnamed Field")
                self.tree.item(item_id,
                               text=new_name,
                               values=(fmt_addr(offset), live_display or "—", saved_display,
                                       dtype, sub_label, resolved_display),
                               tags=(tag,))

    def _get_resolved_addr(self, field: Dict) -> Optional[int]:
        if field.get("dtype") != "pointer":
            return None
        if not self.pm or not self.base:
            return None
        try:
            addr = self.base + field["offset"]
            while True:
                data = self.pm.read_bytes(addr, 4)
                ptr = struct.unpack(">I", data)[0]
                if ptr == 0:
                    return None
                extra = field.get("ptr_extra_offset", 0)
                next_addr = self.base + ptr + extra
                if field.get("ptr_sub_type") != "pointer":
                    return ptr + extra
                addr = next_addr
        except Exception:
            return None

    def _read_value(self, address: int, dtype: str, field: Optional[Dict] = None) -> Any:
        try:
            if dtype != "pointer":
                return self._raw_read(address, dtype)
            ptr_addr = address
            while dtype == "pointer":
                data = self.pm.read_bytes(ptr_addr, 4)
                wiiu_ptr = struct.unpack(">I", data)[0]
                if wiiu_ptr == 0:
                    return None
                sub_type = (field or {}).get("ptr_sub_type", "uint")
                extra_off = (field or {}).get("ptr_extra_offset", 0)
                ptr_addr = self.base + wiiu_ptr + extra_off
                dtype = sub_type
            return self._raw_read(ptr_addr, dtype)
        except Exception:
            return None

    def _write_value(self, address: int, value: Any, dtype: str, field: Optional[Dict] = None) -> bool:
        try:
            if dtype != "pointer":
                return self._raw_write(address, value, dtype)
            ptr_addr = address
            while dtype == "pointer":
                data = self.pm.read_bytes(ptr_addr, 4)
                wiiu_ptr = struct.unpack(">I", data)[0]
                if wiiu_ptr == 0:
                    return False
                sub_type = (field or {}).get("ptr_sub_type", "uint")
                extra_off = (field or {}).get("ptr_extra_offset", 0)
                ptr_addr = self.base + wiiu_ptr + extra_off
                dtype = sub_type
            return self._raw_write(ptr_addr, value, dtype)
        except Exception:
            return False

    def _raw_read(self, address: int, dtype: str):
        if dtype == "aob":
            data = self.pm.read_bytes(address, 16)
            return " ".join(f"{b:02X}" for b in data)
        if dtype == "string":
            data = self.pm.read_bytes(address, 256)
            null_pos = data.find(b'\x00')
            if null_pos != -1:
                data = data[:null_pos]
            return data.decode('utf-8', errors='ignore')
        if dtype == "float": return struct.unpack(">f", self.pm.read_bytes(address, 4))[0]
        if dtype == "double": return struct.unpack(">d", self.pm.read_bytes(address, 8))[0]
        if dtype == "int": return struct.unpack(">i", self.pm.read_bytes(address, 4))[0]
        if dtype == "int64": return struct.unpack(">q", self.pm.read_bytes(address, 8))[0]
        if dtype == "uint": return struct.unpack(">I", self.pm.read_bytes(address, 4))[0]
        if dtype == "uint64": return struct.unpack(">Q", self.pm.read_bytes(address, 8))[0]
        if dtype == "byte": return struct.unpack(">b", self.pm.read_bytes(address, 1))[0]
        if dtype == "ubyte": return self.pm.read_bytes(address, 1)[0]
        if dtype == "halfword": return struct.unpack(">h", self.pm.read_bytes(address, 2))[0]
        if dtype == "ushort": return struct.unpack(">H", self.pm.read_bytes(address, 2))[0]
        return None

    def _raw_write(self, address: int, value: Any, dtype: str) -> bool:
        if dtype == "aob":
            clean = str(value).replace(" ", "").replace(",", "")
            if len(clean) % 2 != 0:
                clean = "0" + clean
            data = bytes.fromhex(clean)
            self.pm.write_bytes(address, data, len(data))
        elif dtype == "string":
            data = (str(value).encode('utf-8') + b'\x00')[:256]
            self.pm.write_bytes(address, data, len(data))
        elif dtype == "float":
            self.pm.write_bytes(address, struct.pack(">f", float(value)), 4)
        elif dtype == "double":
            self.pm.write_bytes(address, struct.pack(">d", float(value)), 8)
        elif dtype == "int":
            self.pm.write_bytes(address, struct.pack(">i", int(value)), 4)
        elif dtype == "int64":
            self.pm.write_bytes(address, struct.pack(">q", int(value)), 8)
        elif dtype == "uint":
            self.pm.write_bytes(address, struct.pack(">I", int(value)), 4)
        elif dtype == "uint64":
            self.pm.write_bytes(address, struct.pack(">Q", int(value)), 8)
        elif dtype == "byte":
            self.pm.write_bytes(address, struct.pack(">b", int(value) & 0xFF), 1)
        elif dtype == "ubyte":
            self.pm.write_bytes(address, bytes([int(value) & 0xFF]), 1)
        elif dtype == "halfword":
            self.pm.write_bytes(address, struct.pack(">h", int(value) & 0xFFFF), 2)
        elif dtype == "ushort":
            self.pm.write_bytes(address, struct.pack(">H", int(value) & 0xFFFF), 2)
        else:
            return False
        return True

    def _fmt(self, value: Any, dtype: str, use_hex: bool, field: Optional[Dict] = None) -> str:
        if value is None:
            return "✕ read error"
        if dtype == "aob":
            return str(value)[:60]
        effective = (field or {}).get("ptr_sub_type", "uint") if dtype == "pointer" else dtype
        display_hex = use_hex or self.show_hex.get()
        if effective == "float":
            return f"{float(value):.6f}"
        if effective == "double":
            return f"{float(value):.8f}"
        if effective == "string":
            return str(value)[:60]
        if effective in ("int", "uint", "byte", "halfword") and display_hex:
            bits = {"byte": 2, "halfword": 4}.get(effective, 8)
            return f"0x{int(value):0{bits}X}"
        if effective == "pointer":
            return f"→ {fmt_addr(int(value))}"
        return str(value)

    def _sub_extra_label(self, field: Dict) -> str:
        if field.get("dtype") != "pointer":
            return ""
        sub = field.get("ptr_sub_type", "?")
        extra = field.get("ptr_extra_offset", 0)
        return f"→{sub} +0x{extra:X}" if extra else f"→{sub}"

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return
        if self._is_folder(item):
            self._rename_folder(item)
            return
        col_idx = int(col[1:]) if col.startswith("#") else 0
        if col_idx == 2:
            self._inline_edit_live(item)
        elif col_idx == 3:
            self._inline_edit_saved(item)
        elif col_idx == 0:
            self._edit_field(item)

    def _rename_folder(self, folder_id: str):
        current_name = self.tree_data[folder_id].get("name", "")
        def on_save(new_name):
            self.tree_data[folder_id]["name"] = new_name
            self.tree.item(folder_id, text=new_name)
        FolderDialog(self.root, on_save, existing_name=current_name)

    def _add_field_to_folder(self, folder_id: str):
        def on_save(field):
            self._add_field(field, folder_id)
        FieldDialog(self.root, on_save)

    def _add_subfolder(self, parent_folder_id: str):
        def on_save(name):
            self._add_folder(name, parent_folder_id)
        FolderDialog(self.root, on_save)

    def _remove_folder(self, folder_id: str):
        if messagebox.askyesno("Delete Folder", "Delete this folder and all its contents?", parent=self.root):
            self._remove_tree_data_recursive(folder_id)
            if folder_id in self.tree_data:
                del self.tree_data[folder_id]
            self.tree.delete(folder_id)

    def _inline_entry(self, item, col_idx: int, initial: str):
        bbox = self.tree.bbox(item, f"#{col_idx}")
        if not bbox:
            return None
        x, y, w, h = bbox
        e = tk.Entry(self.tree, bg=SURFACE2, fg=ACCENT2, insertbackground=ACCENT,
                     relief="flat", bd=0, font=FONT_MONO)
        e.insert(0, initial)
        e.place(x=x, y=y, width=w, height=h)
        e.focus_set()
        e.select_range(0, tk.END)
        return e

    def _parse_typed(self, val_str: str, dtype: str, field: Dict) -> Any:
        effective = field.get("ptr_sub_type", "uint") if dtype == "pointer" else dtype
        if effective in ("string", "aob"):
            return val_str[:120]
        if effective == "float":
            return float(val_str)
        return int(val_str, 16) if val_str.strip().lower().startswith("0x") else int(val_str)

    def _inline_edit_live(self, item):
        if item not in self.tree_data or self._is_folder(item):
            return
        field = self.tree_data[item]
        dtype = field["dtype"]
        addr = self.base + field["offset"]
        current = self._read_value(addr, dtype, field)
        if current is None:
            return
        initial = self._fmt(current, dtype, field.get("use_hex", False), field)
        e = self._inline_entry(item, 2, initial)
        if not e: return
        def commit(event=None):
            try:
                new_val = self._parse_typed(e.get(), dtype, field)
                self._write_value(addr, new_val, dtype, field)
                self._refresh_tree()
            except ValueError:
                pass
            e.destroy()
        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)

    def _inline_edit_saved(self, item):
        if item not in self.tree_data or self._is_folder(item):
            return
        field = self.tree_data[item]
        dtype = field["dtype"]
        initial = self._fmt(field["saved_value"], dtype, field.get("use_hex", False), field)
        e = self._inline_entry(item, 3, initial)
        if not e: return
        def commit(event=None):
            try:
                new_val = self._parse_typed(e.get(), dtype, field)
                self.tree_data[item]["saved_value"] = new_val
                self._refresh_tree()
            except ValueError:
                pass
            e.destroy()
        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)

    def _edit_field(self, item):
        if item not in self.tree_data or self._is_folder(item):
            return
        def on_save(field):
            self.tree_data[item] = {**field, "type": "field"}
            self._refresh_tree()
        FieldDialog(self.root, on_save, existing=self.tree_data[item])

    def _write_selected(self):
        if not self.pm or not self.base:
            messagebox.showwarning("Not Connected", "Attach to Cemu first.", parent=self.root)
            return
        selected = self.tree.selection()
        items_to_write = []
        if not selected:
            if messagebox.askyesno("Write All?", "No selection — write ALL fields?", parent=self.root):
                items_to_write = self._get_all_fields()
        else:
            for item in selected:
                if self._is_folder(item):
                    items_to_write.extend(self._get_folder_fields(item))
                else:
                    items_to_write.append(item)
        if not items_to_write:
            return
        ok = sum(1 for item in items_to_write if item in self.tree_data and
                 self._write_value(self.base + self.tree_data[item]["offset"],
                                  self.tree_data[item]["saved_value"],
                                  self.tree_data[item]["dtype"],
                                  self.tree_data[item]))

    def _get_all_fields(self) -> List[str]:
        result = []
        for item in self.tree.get_children(""):
            if self._is_folder(item):
                result.extend(self._get_folder_fields(item))
            else:
                result.append(item)
        return result

    def _get_folder_fields(self, folder_id: str) -> List[str]:
        result = []
        for child in self.tree.get_children(folder_id):
            if self._is_folder(child):
                result.extend(self._get_folder_fields(child))
            else:
                result.append(child)
        return result

    def _toggle_update(self):
        if self.update_running:
            self.update_running = False
            self.toggle_btn.config(text="⏩ Continuous OFF", bg=SURFACE2)
        else:
            if not self.pm or not self.base:
                messagebox.showwarning("Not Connected", "Connect to Cemu first.", parent=self.root)
                return
            selected = self.tree.selection()
            items_to_update = []
            if not selected:
                messagebox.showwarning("No Selection", "Select at least one field or folder.", parent=self.root)
                return
            for item in selected:
                if self._is_folder(item):
                    items_to_update.extend(self._get_folder_fields(item))
                else:
                    items_to_update.append(item)
            if not items_to_update:
                messagebox.showwarning("No Fields", "No fields in selection.", parent=self.root)
                return
            self.update_running = True
            self.toggle_btn.config(text="⏹ Continuous ON", bg=DANGER)
            self.root.after(50, lambda: self._update_loop(items_to_update))

    def _update_loop(self, items):
        if not self.update_running or not self.pm or not self.base:
            return
        for item in items:
            if item in self.tree_data:
                f = self.tree_data[item]
                self._write_value(self.base + f["offset"], f["saved_value"], f["dtype"], f)
        self.root.after(max(50, self.write_ms.get()), lambda: self._update_loop(items))

    def _auto_read_loop(self):
        if self.pm and self.base:
            self._refresh_tree()
        self.root.after(max(50, self.read_ms.get()), self._auto_read_loop)

    def _copy_selected(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return "break"
        data_to_copy = []
        for item in selected:
            data_to_copy.append(self._serialize_item(item))
        try:
            data = json.dumps(data_to_copy)
            self.root.clipboard_clear()
            self.root.clipboard_append(data)
            count = len(data_to_copy)
            self.status_label.config(text=f"✅ Copied {count} item{'s' if count > 1 else ''}", fg=SUCCESS)
            self.root.after(1400, self._restore_status_label)
        except Exception as e:
            messagebox.showerror("Copy Failed", str(e), parent=self.root)
        return "break"

    def _serialize_item(self, item_id: str) -> Dict:
        if self._is_folder(item_id):
            children = [self._serialize_item(child) for child in self.tree.get_children(item_id)]
            is_open = bool(self.tree.item(item_id, "open"))
            return {
                "type": "folder",
                "name": self.tree_data[item_id]["name"],
                "open": is_open,
                "children": children
            }
        else:
            field = self.tree_data[item_id].copy()
            field.pop("type", None)
            return field

    def _paste_fields(self, event=None):
        try:
            text = self.root.clipboard_get().strip()
            if not text:
                return "break"
            imported = json.loads(text)
            if isinstance(imported, dict):
                imported = [imported]
            if not isinstance(imported, list):
                return "break"
            selected = self.tree.selection()
            parent_id = selected[0] if selected and self._is_folder(selected[0]) else ""
            added = 0
            for item_data in imported:
                if not isinstance(item_data, dict):
                    continue
                added += self._deserialize_item(item_data, parent_id)
            if added > 0:
                self._refresh_tree()
                self.status_label.config(text=f"✅ Pasted {added} item{'s' if added > 1 else ''}", fg=SUCCESS)
                self.root.after(1400, self._restore_status_label)
        except (json.JSONDecodeError, TypeError):
            messagebox.showwarning("Paste Failed",
                                  "Clipboard does not contain valid data.\n\n"
                                  "Copy items first using Ctrl+C in this program.", parent=self.root)
        except Exception:
            pass
        return "break"

    def _deserialize_item(self, item_data: Dict, parent_id: str = "") -> int:
        if item_data.get("type") == "folder":
            folder_id = self._add_folder(item_data.get("name", "Pasted Folder"), parent_id)
            open_state = item_data.get("open", True)
            self.tree.item(folder_id, open=open_state)
            count = 1
            for child_data in item_data.get("children", []):
                count += self._deserialize_item(child_data, folder_id)
            return count
        else:
            try:
                field = {
                    "offset": int(item_data.get("offset", 0)),
                    "saved_value": item_data.get("saved_value", 0),
                    "dtype": item_data.get("dtype", "int"),
                    "description": item_data.get("description", "Pasted Field"),
                    "use_hex": bool(item_data.get("use_hex", False)),
                    "ptr_sub_type": item_data.get("ptr_sub_type", "uint"),
                    "ptr_extra_offset": int(item_data.get("ptr_extra_offset", 0)),
                }
                dtype = field["dtype"]
                sv = field["saved_value"]
                if dtype == "float":
                    field["saved_value"] = float(sv)
                elif dtype in ("string", "aob"):
                    field["saved_value"] = str(sv) if sv is not None else ""
                else:
                    field["saved_value"] = int(sv) if sv is not None else 0
                self._add_field(field, parent_id)
                return 1
            except Exception:
                return 0

    def _restore_status_label(self):
        if self.pm and self.base:
            self.status_label.config(text=f"Connected ", fg=TEXT)
        else:
            self.status_label.config(text="Disconnected — launch Cemu first", fg=TEXT_DIM)

    def _save_fields(self):
        if not self.tree_data:
            messagebox.showinfo("Empty", "No data to save.", parent=self.root)
            return
        initial_file = self.last_file_path if self.last_file_path else ""
        fp = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialfile=os.path.basename(initial_file) if initial_file else "",
            initialdir=os.path.dirname(initial_file) if initial_file else "",
            parent=self.root
        )
        if not fp:
            return
        try:
            root_items = []
            for item in self.tree.get_children(""):
                root_items.append(self._serialize_item(item))
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(root_items, f, indent=2)
            self.last_file_path = fp
            config = load_config()
            config["last_file_path"] = fp
            save_config(config)
            self._update_file_indicator()
            messagebox.showinfo("Saved", f"Saved successfully.", parent=self.root)
        except Exception as e:
            messagebox.showerror("Save Failed", str(e), parent=self.root)

    def _load_fields(self):
        initial_dir = os.path.dirname(self.last_file_path) if self.last_file_path else ""
        fp = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialdir=initial_dir,
            parent=self.root
        )
        if not fp:
            return
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for item in self.tree.get_children(""):
                self.tree.delete(item)
            self.tree_data.clear()
            if isinstance(raw, dict):
                raw = [raw]
            count = 0
            for item_data in raw:
                count += self._deserialize_item(item_data, "")
            self.last_file_path = fp
            config = load_config()
            config["last_file_path"] = fp
            save_config(config)
            self._update_file_indicator()
            self._refresh_tree()
            messagebox.showinfo("Loaded", f"Loaded {count} item(s).", parent=self.root)
        except Exception as e:
            messagebox.showerror("Load Failed", str(e), parent=self.root)

    def _auto_load_last_file(self):
        config = load_config()
        last_path = config.get("last_file_path", "")
        if last_path and os.path.exists(last_path):
            try:
                with open(last_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    raw = [raw]
                for item_data in raw:
                    self._deserialize_item(item_data, "")
                self.last_file_path = last_path
                self._update_file_indicator()
                self._refresh_tree()
            except Exception:
                pass

    def _update_file_indicator(self):
        if self.last_file_path:
            filename = os.path.basename(self.last_file_path)
            self.file_indicator.config(text=f"📄 {filename}", fg=SUCCESS)
        else:
            self.file_indicator.config(text="No file loaded", fg=TEXT_DIM)

    def _find_cemu(self) -> bool:
        try:
            self.pm = pymem.Pymem("Cemu.exe")
            return True
        except Exception:
            self.pm = None
            return False

    def _find_log(self) -> bool:
        if not self.pm:
            return False
        try:
            exe = psutil.Process(self.pm.process_id).exe()
            log = os.path.join(os.path.dirname(exe), "log.txt")
            if os.path.exists(log):
                self.log_path = log
                return True
        except Exception:
            pass
        return False

    def _parse_base(self) -> bool:
        if not self.log_path or not os.path.exists(self.log_path):
            return False
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            m = re.search(r"base\s*:\s*0x([0-9a-fA-F]+)", content, re.IGNORECASE)
            if m:
                self.base = int(m.group(1), 16)
                return True
        except Exception:
            pass
        return False

    def _refresh_connection(self):
        if self._find_cemu() and self._find_log() and self._parse_base():
            self.status_dot.config(fg=SUCCESS)
            self.status_label.config(text=f"Connected ", fg=TEXT)
            self.base_label.config(text=f"BASE {fmt_addr(self.base)}", fg=ACCENT)
            self.root.after(200, self._auto_read_loop)
        else:
            self.status_dot.config(fg=DANGER)
            self.status_label.config(text="Disconnected — launch Cemu first", fg=TEXT_DIM)
            self.base_label.config(text="BASE ——", fg=TEXT_DIM)
            self.base = 0

    def _manual_connect(self):
        self._refresh_connection()
        self._refresh_tree()

    def _open_memory_viewer(self):
        if self._mem_viewer and self._mem_viewer.winfo_exists():
            self._mem_viewer.lift()
            return
        self._mem_viewer = MemoryViewer(self.root, lambda: self.pm, lambda: self.base)

    def _open_memory_search(self):
        if not self.pm or not self.base:
            messagebox.showwarning("Not Connected", "Attach to Cemu first.", parent=self.root)
            return
        if self._search_win and self._search_win.winfo_exists():
            self._search_win.lift()
            return
        def add_cb(fields_list: List[Dict]):
            selected = self.tree.selection()
            parent_id = selected[0] if selected and self._is_folder(selected[0]) else ""
            for field in fields_list:
                self._add_field(field, parent_id)
            self._refresh_tree()
        self._search_win = MemorySearch(self.root, lambda: self.pm, lambda: self.base, add_cb)

    def _on_close(self):
        self.update_running = False
        if self._mem_viewer and self._mem_viewer.winfo_exists():
            self._mem_viewer.destroy()
        if self._search_win and self._search_win.winfo_exists():
            self._search_win.destroy()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = CemuMemoryGUI()
    app.run()