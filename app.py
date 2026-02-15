from __future__ import annotations

import datetime
import importlib
import logging
import os
import re
import shutil
import sqlite3
import sys
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

from core_config import load_config, path
from core_logging import boot_banner, setup_logging
from plugin_loader import load_plugins
from tabs import create_tabs
from ui_rtl import apply_rtl

log = logging.getLogger("APP")

# ----------------------------- Exceptions ----------------------------
def _excepthook(exc_type, exc, tb):
    msg = "".join(traceback.format_exception(exc_type, exc, tb))
    logging.getLogger("FATAL").error("Uncaught exception:\n%s", msg)
    try:
        messagebox.showerror("خطای غیرمنتظره", "مشکلی پیش آمد. جزئیات در فایل لاگ ثبت شد.")
    except Exception:
        pass


sys.excepthook = _excepthook

# --------------------------- Files & DB ------------------------------
def ensure_dirs() -> None:
    try:
        os.makedirs(os.path.dirname(path("db")), exist_ok=True)
        os.makedirs(os.path.dirname(path("logs")), exist_ok=True)
        for key in ("reports", "assets", "plugins"):
            os.makedirs(path(key), exist_ok=True)
    except Exception as e:
        log.error("ensure_dirs failed: %s", e)


def _backup_db_if_needed(db_file: str, enable: bool) -> None:
    if not enable or not os.path.isfile(db_file):
        return
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{os.path.splitext(db_file)[0]}_bak_{ts}.sqlite3"
        shutil.copy2(db_file, backup_name)
        log.info("Database backup created: %s", os.path.basename(backup_name))
    except Exception as e:
        log.error("DB backup failed: %s", e)


def init_database(cfg):
    db_file = path("db")
    ensure_dirs()
    _backup_db_if_needed(db_file, cfg["database"].get("backup_on_start", True))
    conn = sqlite3.connect(db_file)
    try:
        pragmas = cfg["database"].get("pragma", {})
        if "journal_mode" in pragmas:
            conn.execute(f"PRAGMA journal_mode={pragmas['journal_mode']}")
        if "synchronous" in pragmas:
            conn.execute(f"PRAGMA synchronous={pragmas['synchronous']}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations(
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        log.info("Database connected: %s", db_file)
    except Exception as e:
        log.exception("Database init failed: %s", e)
        raise
    return conn


def apply_pending_migrations(conn: sqlite3.Connection, cfg) -> None:
    desired = int(cfg["database"].get("schema_version", 1))
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
    current = int(cur.fetchone()[0] or 0)

    if current >= desired:
        log.info("DB schema up-to-date (current=%s, desired=%s)", current, desired)
        return

    for v in range(current + 1, desired + 1):
        cur.execute(
            "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (v, datetime.datetime.now().isoformat(timespec="seconds")),
        )
        log.info("Schema migration applied -> version %s", v)
    conn.commit()


# ----------------------------- UI Styles ----------------------------
_ZWNJ = "\u200c"
_NORMALIZE_RE = re.compile(
    rf"[ \t\-–—/_\\|{_ZWNJ}\u200f\u202a-\u202e\(\)\[\]{{}}،,:;؛.!؟?]"
)


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = s.replace("ي", "ی").replace("ك", "ک").replace("آ", "ا").replace("أ", "ا").replace("إ", "ا")
    s = _NORMALIZE_RE.sub("", s)
    return s.lower()


# پالت پیشنهادی
PAL = {
    "داشبورد": "#d4af37",
    "ثبتنام": "#0984e3",
    "پروندهکارآموز": "#6c5ce7",
    "حضوروغیاب": "#ff9f1a",
    "مربیان": "#a55eea",
    "یادداشت": "#00b894",
    "تخفیف": "#e84393",
    "مالی": "#2ecc71",
    "گزارش": "#636e72",
    "دوره": "#fdcb6e",
    "کلاس": "#74b9ff",
    "جلسه": "#ff7675",
    "crm": "#0fb9b1",
    "کمپین": "#e17055",
    "کارتکارآموز": "#1abc9c",
    "تنظیم": "#2d3436",
}
CYCLE = [
    "#0fb9b1",
    "#0984e3",
    "#6c5ce7",
    "#2ecc71",
    "#ff9f1a",
    "#e84393",
    "#1abc9c",
    "#d63031",
    "#636e72",
    "#fd9644",
]

_NORM_PAL = [(_norm(key), col) for key, col in PAL.items()]


def setup_style(cfg) -> None:
    style = ttk.Style()
    try:
        # روی ویندوز «vista» ظاهر خوبی دارد
        style.theme_use("vista")
    except Exception:
        try:
            style.theme_use("clam")
        except Exception:
            pass

    base_font = cfg["app"].get("default_font", "IRANSans")
    style.configure(".", font=(base_font, 11))

    bg = "#f9f9f9"
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground="#222")
    style.configure("TButton", padding=6)
    style.configure("TNotebook", background=bg, borderwidth=0)
    style.configure("TNotebook.Tab", padding=[12, 6])
    style.map("TNotebook.Tab", foreground=[("selected", "black"), ("!selected", "#444")])

    style.configure("Treeview", rowheight=26, background="#ffffff", fieldbackground="#ffffff")
    style.configure("Treeview.Heading", font=(base_font, 10, "bold"))
    style.map("Treeview", background=[("selected", "#ffe699")], foreground=[("selected", "black")])


def _make_dot(color: str) -> tk.PhotoImage:
    """ساخت یک نقطهٔ رنگی کوچک برای نمایش داخل عنوان تب (پایدار روی ویندوز)."""
    img = tk.PhotoImage(width=12, height=12)
    img.put(color, to=(0, 0, 12, 12))
    # گرد کردن گوشه‌ها (با شفافیت ساده)
    trans = "#ffffff"
    for x in range(12):
        for y in range(12):
            if (x - 6) ** 2 + (y - 6) ** 2 > 36:  # شعاع تقریبی
                img.put(trans, (x, y))
    return img


def _accent_bar(parent: tk.Widget, color: str) -> None:
    try:
        bar = tk.Frame(parent, height=5, bg=color, highlightthickness=0, bd=0)
        bar.pack(fill="x", side="top")
    except Exception:
        pass


def _color_for_title(title: str, idx: int) -> str:
    n = _norm(title)
    for n_key, col in _NORM_PAL:
        if n_key in n:
            return col
    return CYCLE[idx % len(CYCLE)]


def apply_per_tab_accents(root: tk.Tk, notebook: ttk.Notebook, tabs_map: dict[str, ttk.Frame]) -> None:
    """به هر تب: ۱) یک دایرهٔ رنگی در عنوان ۲) یک نوار رنگی بالای محتوا اضافه می‌کند."""
    if not hasattr(root, "_tab_icons"):
        root._tab_icons = []

    tab_ids = notebook.tabs()
    for idx, (title, frame) in enumerate(tabs_map.items()):
        if idx >= len(tab_ids):
            log.warning("Tab index out of range while applying accents: idx=%s", idx)
            break

        color = _color_for_title(title, idx)
        dot = _make_dot(color)
        root._tab_icons.append(dot)

        # برای راست‌به‌چپ، آیکن را در سمت راست متن می‌گذاریم
        notebook.tab(tab_ids[idx], image=dot, compound="right", padding=[14, 6])
        _accent_bar(frame, color)


# ------------------ Mounting ------------------
MOUNT_RULES = [
    (lambda n: ("پروندهکارآموز" in n) or ("پروندهکاراموز" in n), ("tab_students", "mount_students")),
    (lambda n: ("ثبتنام" in n) or ("کارآموزانثبتنام" in n), ("tab_enroll", "mount_enroll")),
    (lambda n: ("دوره" in n) or ("course" in n), ("tab_courses", "mount_courses")),
    (lambda n: ("کلاس" in n) or ("class" in n), ("tab_classes", "mount_classes")),
    (lambda n: ("جلسه" in n) or ("جلسات" in n) or ("session" in n) or ("sessions" in n), ("tab_sessions", "mount_sessions")),
    (lambda n: ("حضوروغیاب" in n) or ("غیاب" in n) or ("غياب" in n), ("tab_attendance", "mount_attendance")),
    (lambda n: ("مربیان" in n) or ("مربيان" in n), ("tab_instructors", "mount_instructors")),
    (lambda n: ("یادداشت" in n) or ("يادداشت" in n), ("tab_instructor_notes", "mount_instructor_notes")),
    (lambda n: ("تخفیف" in n) or ("تخفيف" in n), ("tab_discounts", "mount_discounts")),
    (lambda n: ("مالی" in n) or ("حساب" in n), ("tab_finance", "mount_finance")),
    (lambda n: ("گزارش" in n) or ("report" in n), ("tab_report_builder", "mount_report_builder")),
    (lambda n: ("داشبورد" in n) or ("داشبرد" in n) or ("dashboard" in n), ("tab_dashboard", "mount_dashboard")),
    (lambda n: ("crm" in n) or ("سیآرام" in n) or ("مدیریتارتباطمشتری" in n), ("tab_crm", "mount_crm")),
    (lambda n: ("کمپین" in n) or ("زمانبندی" in n) or ("campaign" in n), ("tab_campaign", "mount_campaign")),
    (lambda n: ("کارت" in n) and (("آموز" in n) or ("اموز" in n) or ("studentcard" in n)), ("tab_student_card", "mount_student_card")),
    (lambda n: ("تنظیم" in n) or ("تنظيم" in n) or ("setting" in n) or ("settings" in n), ("tab_settings", "mount_settings")),
]


def _clear_frame(frame: ttk.Frame) -> None:
    try:
        for widget in frame.winfo_children():
            widget.destroy()
    except Exception:
        pass


def _mount(tab_frame: ttk.Frame, module_name: str, func_name: str) -> None:
    _clear_frame(tab_frame)
    try:
        mod = importlib.import_module(module_name)
        fn = getattr(mod, func_name)
        fn(tab_frame)
        log.info("Mounted %s.%s", module_name, func_name)
    except Exception as e:
        log.error("Mount failed for %s.%s: %s", module_name, func_name, e, exc_info=True)
        wrap = ttk.Frame(tab_frame, padding=16)
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="❗ خطا در بارگذاری این تب", foreground="red").pack(anchor="e")
        ttk.Label(
            wrap,
            text=f"ماژول: {module_name}\nتابع: {func_name}\nپیغام: {e}",
            justify="right",
        ).pack(anchor="e")


def _diagnostic_placeholder(frame: ttk.Frame, title: str, norm_title: str) -> None:
    _clear_frame(frame)
    box = ttk.Frame(frame, padding=18)
    box.pack(fill="both", expand=True)
    ttk.Label(box, text="ℹ️ این تب هنوز به ماژول خاصی متصل نشد.", foreground="#444").pack(anchor="e", pady=(0, 8))
    ttk.Label(box, text=f"عنوان تب: {title}", justify="right").pack(anchor="e")
    ttk.Label(box, text=f"عنوان نرمال‌شده: {norm_title}", justify="right", foreground="#666").pack(anchor="e", pady=(0, 8))
    ttk.Label(box, text="عنوان دقیق را بفرستید تا به قوانین مچینگ اضافه کنم.", foreground="#666").pack(anchor="e")


def mount_optional_tabs(cfg, tabs_map: dict[str, ttk.Frame]) -> None:  # cfg kept for future extensibility
    for title, frame in tabs_map.items():
        n = _norm(title)
        mounted = False

        for cond, target in MOUNT_RULES:
            try:
                if cond(n):
                    _mount(frame, *target)
                    mounted = True
                    break
            except Exception as e:
                log.error("Rule error for '%s': %s", title, e)

        if not mounted:
            _diagnostic_placeholder(frame, title, n)


def build_menu(root: tk.Tk, cfg) -> None:
    menubar = tk.Menu(root, tearoff=0)
    # تلاش برای رنگ‌دهی (ممکن است ویندوز نادیده بگیرد)
    try:
        menubar.configure(background="#ffffff", foreground="#222", activebackground="#ffe8a6", activeforeground="#000")
    except Exception:
        pass

    file_menu = tk.Menu(menubar, tearoff=0, bg="#ffffff", fg="#222", activebackground="#ffe8a6", activeforeground="#000")
    file_menu.add_command(label="خروج", command=lambda: root.event_generate("<<AppExit>>"))
    menubar.add_cascade(label="پرونده", menu=file_menu)

    help_menu = tk.Menu(menubar, tearoff=0, bg="#ffffff", fg="#222", activebackground="#ffe8a6", activeforeground="#000")

    def _about():
        messagebox.showinfo(
            "درباره برنامه",
            f"{cfg['app']['name']} v{cfg['app']['version']}\n"
            "آموزشگاه فنی و حرفه‌ای بهان رایانه\n"
            "تمامی حقوق محفوظ است.",
        )

    help_menu.add_command(label="درباره", command=_about)
    menubar.add_cascade(label="راهنما", menu=help_menu)

    try:
        root.option_add("*Menu.activeBackground", "#ffe8a6")
        root.option_add("*Menu.activeForeground", "#000000")
    except Exception:
        pass

    root.config(menu=menubar)


# ----------------------------- Main Window ---------------------------
def create_main_window(cfg):
    root = tk.Tk()
    root.title(cfg["app"]["name"])

    w = int(cfg["app"]["window"].get("width", 1280))
    h = int(cfg["app"]["window"].get("height", 800))
    root.geometry(f"{w}x{h}+80+60")

    if cfg["app"]["window"].get("maximize", True):
        try:
            root.state("zoomed")
        except Exception:
            root.attributes("-zoomed", True)

    try:
        root.tk.call("tk", "scaling", 1.2)
    except Exception:
        pass

    setup_style(cfg)
    try:
        root.configure(bg="#f9f9f9")
    except Exception:
        pass

    build_menu(root, cfg)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    tabs_map = create_tabs(notebook)
    apply_rtl(root)

    # تزریق رنگ برای هر تب (آیکن + نوار بالایی)
    apply_per_tab_accents(root, notebook, tabs_map)

    status = ttk.Label(root, anchor="e")
    status.pack(fill="x", side="bottom")
    status["text"] = "آماده"

    try:
        for i in range(len(notebook.tabs())):
            notebook.tab(i, state="normal")
        notebook.enable_traversal()

        def _on_tab_changed(event=None):
            try:
                current = notebook.select()
                text = notebook.tab(current, "text")
                status["text"] = f"تب فعال: {text}"
            except Exception:
                pass

        notebook.bind("<<NotebookTabChanged>>", _on_tab_changed)
    except Exception as e:
        log.error("Notebook activation failed: %s", e)

    def _on_exit(evt=None):
        root.quit()

    root.bind("<<AppExit>>", _on_exit)
    return root, notebook, tabs_map, status


# ------------------------------ Bootstrap ----------------------------
def main() -> None:
    setup_logging()
    boot_banner()

    cfg = load_config()
    log.info("Config loaded. App=%s v%s", cfg["app"]["name"], cfg["app"]["version"])

    conn = init_database(cfg)
    apply_pending_migrations(conn, cfg)

    root, notebook, tabs_map, status = create_main_window(cfg)

    try:
        loaded = load_plugins()
        status["text"] = f"افزونه‌ها بارگذاری شدند: {len(loaded)}"
    except Exception as e:
        log.error("Plugin loading failed: %s", e)

    mount_optional_tabs(cfg, tabs_map)

    log.info("UI ready. Entering mainloop.")
    root.mainloop()

    try:
        conn.close()
        log.info("Database closed.")
    except Exception:
        pass


if __name__ == "__main__":
    main()
