# NSFW Photo Scanner - https://github.com/n-st-w/nsfw-photo-scanner
# Copyright (C) 2026 n-st-w
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. This program is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/>.
"""
Nudity Scanner - offline desktop app to scan a photo folder for nudity
and review the flagged images.

Uses NudeNet (ONNX) locally; nothing is uploaded.
"""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk

APP_NAME = "Nudity Scanner"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif", ".heic", ".heif"}
EXPOSED = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
           "BUTTOCKS_EXPOSED", "ANUS_EXPOSED"}
COVERED = {"FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED", "BUTTOCKS_COVERED", "ANUS_COVERED"}
DETECT_MAX_SIDE = 1280  # downscale big photos before detection (model runs at 320px anyway)

SESSION_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NudityScanner" / "sessions"

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

STATUS_LABEL = {"pending": "To review", "confirmed": "Confirmed", "ok": "Not nudity", "moved": "Moved",
                "deleted": "Deleted"}


def session_path(folder: Path) -> Path:
    h = hashlib.sha1(str(folder).lower().encode()).hexdigest()[:16]
    return SESSION_DIR / f"{h}.json"


def open_rgb(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.seek(0) if getattr(im, "is_animated", False) else None
        return ImageOps.exif_transpose(im).convert("RGB")


# --------------------------------------------------------------------------- scanning
class Scanner(threading.Thread):
    def __init__(self, folder, recursive, out_q, stop_evt):
        super().__init__(daemon=True)
        self.folder, self.recursive, self.q, self.stop_evt = folder, recursive, out_q, stop_evt

    def run(self):
        try:
            self.q.put(("status", "Loading model..."))
            from nudenet import NudeDetector
            det = NudeDetector()
            it = self.folder.rglob("*") if self.recursive else self.folder.glob("*")
            files = sorted(p for p in it if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
            self.q.put(("total", len(files)))
            for i, p in enumerate(files, 1):
                if self.stop_evt.is_set():
                    break
                try:
                    im = open_rgb(p)
                    w, h = im.size
                    scale = min(1.0, DETECT_MAX_SIDE / max(w, h))
                    if scale < 1.0:
                        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
                    bgr = np.ascontiguousarray(np.asarray(im)[:, :, ::-1])
                    dets = det.detect(bgr)
                    for d in dets:  # boxes back to original-image coordinates
                        d["box"] = [int(v / scale) for v in d["box"]]
                        d["score"] = float(d["score"])
                    self.q.put(("result", {"path": str(p), "dets": dets, "error": ""}))
                except Exception as e:
                    self.q.put(("result", {"path": str(p), "dets": [], "error": str(e)}))
                self.q.put(("progress", i))
            self.q.put(("done", self.stop_evt.is_set()))
        except Exception as e:
            self.q.put(("fatal", str(e)))


# --------------------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1280x820")
        self.minsize(900, 600)
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass

        self.folder = None
        self.results = {}       # path -> {"dets", "error", "status"}
        self.undo_stack = []    # [(paths, {path: previous_status})]
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.scanning = False
        self.current = None
        self._photo = None
        self._img_cache = (None, None)

        self.var_folder = tk.StringVar()
        self.var_thresh = tk.DoubleVar(value=0.45)
        self.var_covered = tk.BooleanVar(value=False)
        self.var_recursive = tk.BooleanVar(value=True)
        self.var_blur = tk.BooleanVar(value=True)
        self.var_boxes = tk.BooleanVar(value=True)
        self.var_filter = tk.StringVar(value="To review")
        self.var_status = tk.StringVar(value="Choose a folder and press Scan.")

        self._build_ui()
        self._bind_keys()
        self.after(100, self._poll)

    # ---------- layout
    def _build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Folder:").pack(side="left")
        ttk.Entry(top, textvariable=self.var_folder, width=60).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(top, text="Browse...", command=self.browse).pack(side="left")
        ttk.Checkbutton(top, text="Subfolders", variable=self.var_recursive).pack(side="left", padx=8)
        self.btn_scan = ttk.Button(top, text="Scan", command=self.start_scan)
        self.btn_scan.pack(side="left")
        self.btn_stop = ttk.Button(top, text="Stop", command=self.stop_scan, state="disabled")
        self.btn_stop.pack(side="left", padx=4)

        opts = ttk.Frame(self, padding=(8, 0, 8, 4))
        opts.pack(fill="x")
        ttk.Label(opts, text="Sensitivity threshold:").pack(side="left")
        ttk.Scale(opts, from_=0.15, to=0.9, variable=self.var_thresh, length=180,
                  command=lambda _=None: self._on_criteria()).pack(side="left", padx=4)
        self.lbl_thresh = ttk.Label(opts, text="0.45", width=5)
        self.lbl_thresh.pack(side="left")
        ttk.Checkbutton(opts, text="Include covered / suggestive", variable=self.var_covered,
                        command=self._on_criteria).pack(side="left", padx=10)
        ttk.Label(opts, text="Show:").pack(side="left", padx=(20, 2))
        cb = ttk.Combobox(opts, textvariable=self.var_filter, state="readonly", width=14,
                          values=["To review", "All flagged", "Confirmed", "Not nudity", "Errors", "All images"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())
        ttk.Button(opts, text="Export CSV...", command=self.export_csv).pack(side="right")

        prog = ttk.Frame(self, padding=(8, 0))
        prog.pack(fill="x")
        self.pbar = ttk.Progressbar(prog, mode="determinate")
        self.pbar.pack(fill="x")
        ttk.Label(prog, textvariable=self.var_status).pack(anchor="w")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(body)
        cols = ("score", "status")
        self.tree = ttk.Treeview(left, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="File")
        self.tree.heading("score", text="Score", command=lambda: self.refresh_list(sort="score"))
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=260)
        self.tree.column("score", width=60, anchor="center")
        self.tree.column("status", width=90, anchor="center")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_external())
        body.add(left, weight=1)

        right = ttk.Frame(body)
        self.canvas = tk.Canvas(right, bg="#202020", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.show_current())
        self.lbl_info = ttk.Label(right, text="", wraplength=700, justify="left")
        self.lbl_info.pack(fill="x", pady=4)

        bar = ttk.Frame(right)
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="Blur (B)", variable=self.var_blur, command=self.show_current).pack(side="left")
        ttk.Checkbutton(bar, text="Boxes", variable=self.var_boxes, command=self.show_current).pack(side="left", padx=6)
        ttk.Button(bar, text="Confirm nudity (F)", command=lambda: self.mark("confirmed")).pack(side="left", padx=6)
        ttk.Button(bar, text="Not nudity (K)", command=lambda: self.mark("ok")).pack(side="left")
        ttk.Button(bar, text="Undo (U)", command=self.undo).pack(side="left", padx=6)
        ttk.Button(bar, text="Reset selected", command=lambda: self.mark("pending")).pack(side="left")
        ttk.Button(bar, text="Open (Enter)", command=self.open_external).pack(side="left")
        ttk.Button(bar, text="Show in Explorer", command=self.reveal).pack(side="left", padx=6)

        bar2 = ttk.Frame(right)
        bar2.pack(fill="x", pady=(6, 0))
        ttk.Label(bar2, text="Confirmed images:").pack(side="left")
        ttk.Button(bar2, text="Move to folder...", command=self.move_confirmed).pack(side="left", padx=6)
        ttk.Button(bar2, text="Send to Recycle Bin", command=self.recycle_confirmed).pack(side="left")
        body.add(right, weight=3)

    def _bind_keys(self):
        for key, fn in {"f": lambda: self.mark("confirmed"), "k": lambda: self.mark("ok"),
                        "u": self.undo, "<Control-z>": self.undo, "b": self.toggle_blur,
                        "<Return>": self.open_external}.items():
            seq = key if key.startswith("<") else f"<KeyPress-{key}>"
            self.bind(seq, lambda e, fn=fn: None if isinstance(e.widget, (tk.Entry, ttk.Entry)) else fn())

    # ---------- flagging logic
    def labels(self):
        return EXPOSED | (COVERED if self.var_covered.get() else set())

    def hits(self, r):
        t, labs = self.var_thresh.get(), self.labels()
        return [d for d in r["dets"] if d["class"] in labs and d["score"] >= t]

    def is_flagged(self, r):
        return bool(self.hits(r))

    def _on_criteria(self):
        self.lbl_thresh.config(text=f"{self.var_thresh.get():.2f}")
        if not getattr(self, "_criteria_pending", False):
            self._criteria_pending = True
            self.after(250, self._apply_criteria)

    def _apply_criteria(self):
        self._criteria_pending = False
        self.refresh_list()
        self.save_session()

    # ---------- scanning
    def browse(self):
        d = filedialog.askdirectory(title="Choose photo folder")
        if d:
            self.var_folder.set(d)

    def start_scan(self):
        folder = Path(self.var_folder.get()).expanduser()
        if not folder.is_dir():
            messagebox.showerror(APP_NAME, "Please choose a valid folder.")
            return
        self.folder = folder.resolve()
        sp = session_path(self.folder)
        if sp.exists() and messagebox.askyesno(
                APP_NAME, "A previous scan of this folder exists.\n\nResume that review instead of rescanning?"):
            self.load_session(sp)
            return
        self.results = {}
        self.undo_stack = []
        self.refresh_list()
        self.stop_evt.clear()
        self.scanning = True
        self.btn_scan.config(state="disabled")
        self.btn_stop.config(state="normal")
        Scanner(self.folder, self.var_recursive.get(), self.q, self.stop_evt).start()

    def stop_scan(self):
        self.stop_evt.set()
        self.var_status.set("Stopping...")

    def _poll(self):
        added = False
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status":
                    self.var_status.set(val)
                elif kind == "total":
                    self.pbar.config(maximum=max(val, 1), value=0)
                    self.total = val
                    self.var_status.set(f"Scanning {val} images...")
                elif kind == "result":
                    self.results[val["path"]] = {"dets": val["dets"], "error": val["error"], "status": "pending"}
                    r = self.results[val["path"]]
                    if self._visible(r):
                        self._insert_row(val["path"], r)
                    added = True
                elif kind == "progress":
                    self.pbar.config(value=val)
                    nflag = sum(self.is_flagged(r) for r in self.results.values())
                    self.var_status.set(f"Scanned {val}/{self.total}  -  flagged {nflag}")
                elif kind == "done":
                    self._scan_finished(stopped=val)
                elif kind == "fatal":
                    self._scan_finished(stopped=True)
                    messagebox.showerror(APP_NAME, f"Scan failed:\n{val}")
        except queue.Empty:
            pass
        if added and self.current is None:
            kids = self.tree.get_children()
            if kids:
                self.tree.selection_set(kids[0])
        self.after(100, self._poll)

    def _scan_finished(self, stopped):
        self.scanning = False
        self.btn_scan.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.save_session()
        self.refresh_list()
        self.update_summary(prefix="Stopped. " if stopped else "Scan complete. ")

    def update_summary(self, prefix=""):
        rs = self.results.values()
        flagged = [r for r in rs if self.is_flagged(r)]
        pend = sum(r["status"] == "pending" for r in flagged)
        conf = sum(r["status"] == "confirmed" for r in self.results.values())
        err = sum(bool(r["error"]) for r in rs)
        self.var_status.set(f"{prefix}{len(self.results)} images, {len(flagged)} flagged "
                            f"({pend} to review, {conf} confirmed), {err} errors.")

    # ---------- list
    def _visible(self, r):
        f = self.var_filter.get()
        if r["status"] in ("moved", "deleted"):
            return f == "All images"
        if f == "All images":
            return True
        if f == "Errors":
            return bool(r["error"])
        if f == "Confirmed":
            return r["status"] == "confirmed"
        if f == "Not nudity":
            return r["status"] == "ok"
        flagged = self.is_flagged(r) or r["status"] == "confirmed"
        if f == "All flagged":
            return flagged
        return flagged and r["status"] == "pending"  # To review

    def _row_values(self, r):
        top = max((d["score"] for d in self.hits(r)), default=0.0)
        status = "Error" if r["error"] else STATUS_LABEL[r["status"]]
        return (f"{top:.2f}" if top else "", status)

    def _insert_row(self, path, r):
        try:
            name = str(Path(path).relative_to(self.folder))
        except ValueError:
            name = Path(path).name
        self.tree.insert("", "end", iid=path, text=name, values=self._row_values(r))

    def refresh_list(self, sort=None):
        sel = self.current
        self.tree.delete(*self.tree.get_children())
        items = [(p, r) for p, r in self.results.items() if self._visible(r)]
        if sort == "score":
            items.sort(key=lambda x: -max((d["score"] for d in self.hits(x[1])), default=0))
        for p, r in items:
            self._insert_row(p, r)
        if sel and self.tree.exists(sel):
            self.tree.selection_set(sel)
            self.tree.see(sel)
        else:
            self.current = None
            kids = self.tree.get_children()
            if kids:
                self.tree.selection_set(kids[0])
            else:
                self.canvas.delete("all")
                self.lbl_info.config(text="")
        if not self.scanning:
            self.update_summary()

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        self.current = sel[0] if sel else None
        self.show_current()

    # ---------- preview
    def show_current(self):
        self.canvas.delete("all")
        p = self.current
        if not p or p not in self.results:
            return
        r = self.results[p]
        cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        try:
            if self._img_cache[0] != p:
                self._img_cache = (p, open_rgb(Path(p)))
            im = self._img_cache[1]
        except Exception as e:
            self.canvas.create_text(cw // 2, ch // 2, text=f"Cannot open image:\n{e}", fill="white")
            self.lbl_info.config(text=p)
            return
        scale = min(cw / im.width, ch / im.height, 1.0)
        disp = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        if self.var_blur.get():
            disp = disp.filter(ImageFilter.GaussianBlur(radius=max(8, disp.width // 30)))
        hits = self.hits(r)
        if self.var_boxes.get() and hits:
            dr = ImageDraw.Draw(disp)
            for d in hits:
                x, y, w, h = [v * scale for v in d["box"]]
                dr.rectangle([x, y, x + w, y + h], outline=(255, 60, 60), width=3)
                dr.text((x + 4, y + 2), f"{d['class'].replace('_', ' ').lower()} {d['score']:.2f}",
                        fill=(255, 255, 0))
        self._photo = ImageTk.PhotoImage(disp)
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)
        labs = ", ".join(f"{d['class']} ({d['score']:.2f})" for d in sorted(hits, key=lambda d: -d["score"]))
        info = f"{p}\nStatus: {STATUS_LABEL[r['status']]}    Detections: {labs or 'none above threshold'}"
        if r["error"]:
            info += f"\nError: {r['error']}"
        self.lbl_info.config(text=info)

    def toggle_blur(self):
        self.var_blur.set(not self.var_blur.get())
        self.show_current()

    # ---------- review actions
    def mark(self, status):
        sel = list(self.tree.selection())
        if not sel:
            return
        sel = [p for p in sel if self.results[p]["status"] not in ("moved", "deleted")]
        if not sel:
            return
        prev = {p: self.results[p]["status"] for p in sel}
        if all(s == status for s in prev.values()):
            return  # nothing changes, don't move the selection
        self.undo_stack.append((sel, prev))
        del self.undo_stack[:-200]
        for p in sel:
            self.results[p]["status"] = status
        if status == "pending":  # explicit reset: stay on the same image(s)
            self.refresh_list()
            self.save_session()
            return
        kids = list(self.tree.get_children())
        idx = kids.index(sel[-1]) if sel[-1] in kids else 0
        nxt = next((k for k in kids[idx + 1:] if k not in sel), None) or \
            next((k for k in reversed(kids[:idx]) if k not in sel), None)
        self.current = nxt
        self.refresh_list()
        if nxt and self.tree.exists(nxt):
            self.tree.selection_set(nxt)
            self.tree.see(nxt)
        self.save_session()

    def undo(self):
        """Undo the last Confirm / Not nudity / Reset and bring those images back into view."""
        while self.undo_stack:
            paths, prev = self.undo_stack.pop()
            paths = [p for p in paths if self.results.get(p, {}).get("status") not in (None, "moved", "deleted")]
            if paths:
                break
        else:
            self.var_status.set("Nothing to undo.")
            return
        for p in paths:
            self.results[p]["status"] = prev[p]
        self.current = paths[0]
        self.refresh_list()
        if not any(self.tree.exists(p) for p in paths):  # restored items hidden by current filter
            self.var_filter.set("All flagged")
            self.refresh_list()
        shown = [p for p in paths if self.tree.exists(p)]
        if shown:
            self.tree.selection_set(shown)
            self.tree.see(shown[0])
        self.save_session()
        self.var_status.set(f"Undid last action on {len(paths)} image(s).")

    def open_external(self):
        if self.current and Path(self.current).exists():
            os.startfile(self.current)

    def reveal(self):
        if self.current and Path(self.current).exists():
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.current)])

    def _confirmed(self):
        return [p for p, r in self.results.items() if r["status"] == "confirmed" and Path(p).exists()]

    def move_confirmed(self):
        files = self._confirmed()
        if not files:
            messagebox.showinfo(APP_NAME, "No confirmed images to move.")
            return
        dest = filedialog.askdirectory(title="Move confirmed images to...")
        if not dest:
            return
        dest = Path(dest).resolve()
        moved = 0
        for p in files:
            src = Path(p)
            try:
                rel = src.relative_to(self.folder)
            except ValueError:
                rel = Path(src.name)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            n = 1
            while target.exists():
                target = target.with_name(f"{target.stem}_{n}{target.suffix}")
                n += 1
            try:
                shutil.move(str(src), str(target))
                self.results[p]["status"] = "moved"
                self.results[p]["moved_to"] = str(target)
                moved += 1
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Could not move {src}:\n{e}")
        self._img_cache = (None, None)
        self.save_session()
        self.refresh_list()
        messagebox.showinfo(APP_NAME, f"Moved {moved} image(s) to\n{dest}")

    def recycle_confirmed(self):
        files = self._confirmed()
        if not files:
            messagebox.showinfo(APP_NAME, "No confirmed images.")
            return
        if not messagebox.askyesno(APP_NAME, f"Send {len(files)} confirmed image(s) to the Recycle Bin?"):
            return
        from send2trash import send2trash
        self._img_cache = (None, None)
        done = 0
        for p in files:
            try:
                send2trash(os.path.normpath(p))
                self.results[p]["status"] = "deleted"
                done += 1
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Could not recycle {p}:\n{e}")
        self.save_session()
        self.refresh_list()
        messagebox.showinfo(APP_NAME, f"Sent {done} image(s) to the Recycle Bin.")

    def export_csv(self):
        if not self.results:
            return
        out = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                           initialfile="nudity_report.csv")
        if not out:
            return
        import csv
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["file", "flagged", "review_status", "max_score", "labels", "moved_to", "error"])
            for p, r in sorted(self.results.items()):
                h = self.hits(r)
                w.writerow([p, "yes" if h else "no", r["status"], f"{max((d['score'] for d in h), default=0):.3f}",
                            ";".join(sorted({d["class"] for d in h})), r.get("moved_to", ""), r["error"]])
        messagebox.showinfo(APP_NAME, f"Saved {out}")

    # ---------- sessions
    def save_session(self):
        if not self.folder or not self.results:
            return
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        data = {"folder": str(self.folder), "threshold": self.var_thresh.get(),
                "include_covered": self.var_covered.get(), "results": self.results}
        tmp = session_path(self.folder).with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, session_path(self.folder))

    def load_session(self, sp):
        data = json.loads(sp.read_text(encoding="utf-8"))
        self.results = data["results"]
        self.undo_stack = []
        self.var_thresh.set(data.get("threshold", 0.45))
        self.var_covered.set(data.get("include_covered", False))
        self.lbl_thresh.config(text=f"{self.var_thresh.get():.2f}")
        self.current = None
        self.refresh_list()
        self.update_summary(prefix="Resumed previous review. ")


def main():
    if "--selftest" in sys.argv:  # build check: load model, run one detection, write result to file
        from nudenet import NudeDetector
        out = sys.argv[sys.argv.index("--selftest") + 1]
        res = NudeDetector().detect(np.zeros((320, 320, 3), np.uint8))
        Path(out).write_text(f"ok {res}", encoding="utf-8")
        return
    App().mainloop()


if __name__ == "__main__":
    main()
