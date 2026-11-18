#!/usr/bin/env python3
"""
KSeF Rechnungsübermittlung – Desktop-App
Liest CSV-Rechnungsdaten ein und übermittelt diese an KSeF API 2.0 (FA(3)).
"""

import json
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ksef_utils import (
    BASE_DIR, DIR_RECHNUNGEN, DIR_ARCHIV, DIR_ERGEBNISSE, DIR_UPO, DIR_PROTOKOLLE,
    SETTINGS_FILE,
    _csv_reader, _normalize_nip, group_rows, build_invoice_xml,
    generate_pdf, write_protocol, setup_dirs, _calc_netto,
    extract_response, download_upo, invoice_warnings,
    build_qr_url, write_feedback_csv, is_correction, schema_check_status,
)

try:
    from ksef2 import Client, Environment, FormSchema
    KSEF_AVAILABLE = True
except ImportError:
    KSEF_AVAILABLE = False

# ── Farben & Schriften ────────────────────────────────────────────────────────
C_BG      = "#1a1f2e"
C_PANEL   = "#222840"
C_SURFACE = "#2a3154"
C_CARD    = "#1e2438"
C_ACCENT  = "#4d8ef0"
C_GREEN   = "#2ed68a"
C_WARN    = "#f0a832"
C_ERROR   = "#e85555"
C_TEXT    = "#dde3f0"
C_MUTED   = "#6b7799"
C_BORDER  = "#333d5c"
C_BTN_HVR = "#3a70cc"

F_BODY    = ("Segoe UI", 10)
F_MONO    = ("Consolas", 9)
F_TITLE   = ("Segoe UI", 13, "bold")
F_SECTION = ("Segoe UI", 9, "bold")
F_SMALL   = ("Segoe UI", 8)

# ── Einstellungen laden/speichern ─────────────────────────────────────────────

def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"token": "", "nip": "", "env": "TEST", "cert_path": ""}

def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ── Hauptfenster ──────────────────────────────────────────────────────────────

class KSeFApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KSeF Rechnungsübermittlung")
        self.geometry("980x700")
        self.minsize(820, 580)
        self.configure(bg=C_BG)

        self._settings = load_settings()
        self._csv_path: str | None = None
        self._rows: list[dict] = []
        self._results: list[dict] = []
        self._tx_log: list[str] = []
        self._send_start: datetime = datetime.now()
        self._active_tab = tk.StringVar(value="send")
        setup_dirs()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not KSEF_AVAILABLE:
            self._log("⚠  ksef2-Paket nicht gefunden — pip install ksef2", "warn")

    # ── UI-Aufbau ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Seitenleiste ──
        sidebar = tk.Frame(self, bg=C_PANEL, width=200)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar, text="KSeF", font=("Segoe UI", 18, "bold"),
            bg=C_PANEL, fg=C_ACCENT, pady=4,
        ).pack(pady=(20, 2))
        tk.Label(
            sidebar, text="Rechnungsübermittlung",
            font=F_SMALL, bg=C_PANEL, fg=C_MUTED,
        ).pack()
        tk.Frame(sidebar, bg=C_BORDER, height=1).pack(fill="x", padx=16, pady=18)

        self._nav_btns: dict[str, tk.Label] = {}
        nav_items = [
            ("send",     "✈  Senden"),
            ("settings", "⚙  Einstellungen"),
            ("log",      "📋  Protokoll"),
        ]
        for key, label in nav_items:
            btn = tk.Label(
                sidebar, text=label, font=F_BODY,
                bg=C_PANEL, fg=C_TEXT,
                anchor="w", padx=20, pady=10, cursor="hand2",
            )
            btn.pack(fill="x")
            btn.bind("<Button-1>", lambda e, k=key: self._switch_tab(k))
            btn.bind("<Enter>",    lambda e, b=btn: b.config(bg=C_SURFACE))
            btn.bind("<Leave>",    lambda e, b=btn, k=key: b.config(
                bg=C_ACCENT if self._active_tab.get() == k else C_PANEL))
            self._nav_btns[key] = btn

        # Versionslabel unten
        tk.Label(
            sidebar, text="v1.0  •  API 2.0",
            font=F_SMALL, bg=C_PANEL, fg=C_MUTED,
        ).pack(side="bottom", pady=12)

        # Trennlinie
        tk.Frame(self, bg=C_BORDER, width=1).pack(side="left", fill="y")

        # ── Inhaltsbereich ──
        self._content = tk.Frame(self, bg=C_BG)
        self._content.pack(side="left", fill="both", expand=True)

        # Statusleiste
        self._statusbar = tk.Frame(self, bg=C_PANEL, height=28)
        self._statusbar.pack(side="bottom", fill="x")
        self._status_var = tk.StringVar(value="Bereit")
        tk.Label(self._statusbar, textvariable=self._status_var,
                 font=F_SMALL, bg=C_PANEL, fg=C_MUTED,
                 anchor="w", padx=14).pack(side="left")
        self._progress = ttk.Progressbar(self._statusbar, length=120,
                                          mode="indeterminate")
        self._progress.pack(side="right", padx=10, pady=4)

        # Tabs aufbauen
        self._frames: dict[str, tk.Frame] = {}
        self._frames["send"]     = self._build_send_tab()
        self._frames["settings"] = self._build_settings_tab()
        self._frames["log"]      = self._build_log_tab()

        self._switch_tab("send")

        # ttk-Styles
        self._apply_styles()

    def _switch_tab(self, key: str):
        for k, f in self._frames.items():
            f.pack_forget()
        self._frames[key].pack(fill="both", expand=True)
        self._active_tab.set(key)
        for k, btn in self._nav_btns.items():
            btn.config(bg=C_ACCENT if k == key else C_PANEL,
                       fg="#fff"   if k == key else C_TEXT)

    # ── Tab: Senden ──────────────────────────────────────────────────────────

    def _build_send_tab(self) -> tk.Frame:
        frame = tk.Frame(self._content, bg=C_BG)

        # Kopfzeile
        head = tk.Frame(frame, bg=C_BG, pady=14)
        head.pack(fill="x", padx=24)
        tk.Label(head, text="✈  Rechnungen übermitteln",
                 font=F_TITLE, bg=C_BG, fg=C_TEXT).pack(side="left")

        body = tk.Frame(frame, bg=C_BG)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 12))

        # Linke Spalte
        left = tk.Frame(body, bg=C_BG, width=340)
        left.pack(side="left", fill="y", padx=(0, 14))
        left.pack_propagate(False)

        # ── Karte: Aktuelle Konfiguration ──
        self._build_config_card(left)

        # ── Karte: CSV-Datei ──
        self._section_card(left, "📄  CSV-Datei", top=12)

        csv_row = tk.Frame(left, bg=C_BG)
        csv_row.pack(fill="x", pady=(4, 0))

        self._csv_label_var = tk.StringVar(value="Keine Datei gewählt")
        tk.Label(csv_row, textvariable=self._csv_label_var,
                 font=F_MONO, bg=C_SURFACE, fg=C_MUTED,
                 anchor="w", padx=8, pady=5, relief="flat"
                 ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._make_btn(csv_row, "Öffnen", self._pick_csv).pack(side="left")

        tk.Label(left, text="Vorlage: rechnungen_vorlage.csv",
                 font=F_SMALL, bg=C_BG, fg=C_MUTED).pack(anchor="w", pady=(3, 0))

        # ── Karte: Vorschau-Tabelle ──
        self._section_card(left, "🔍  Vorschau", top=14)

        tree_wrap = tk.Frame(left, bg=C_BORDER, bd=0)
        tree_wrap.pack(fill="both", expand=True, pady=(4, 0))

        cols = ("Nr.", "Datum", "Empfänger", "Netto")
        self._tree = ttk.Treeview(tree_wrap, columns=cols,
                                   show="headings", height=7)
        for c, w in zip(cols, (90, 82, 110, 80)):
            self._tree.heading(c, text=c)
            self._tree.column(c, width=w, anchor="w")
        self._tree.pack(fill="both", expand=True)

        # ── Aktions-Buttons ──
        tk.Frame(left, bg=C_BG, height=12).pack()
        self._send_btn = self._make_btn(
            left, "✈  Alle Rechnungen senden", self._on_send, primary=True)
        self._send_btn.pack(fill="x")

        tk.Frame(left, bg=C_BG, height=6).pack()
        self._export_btn = self._make_btn(
            left, "📥  Ergebnisse als JSON speichern", self._export_results)
        self._export_btn.pack(fill="x")
        self._export_btn.config(state="disabled")

        # Rechte Spalte: Mini-Log im Send-Tab
        right = tk.Frame(body, bg=C_BG)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text="Verlauf", font=F_SECTION,
                 bg=C_BG, fg=C_MUTED).pack(anchor="w")
        self._send_log = scrolledtext.ScrolledText(
            right, font=F_MONO, bg=C_CARD, fg=C_TEXT,
            relief="flat", state="disabled",
            wrap="word", padx=10, pady=8, bd=0,
        )
        self._send_log.pack(fill="both", expand=True)
        self._send_log.tag_config("ok",   foreground=C_GREEN)
        self._send_log.tag_config("warn", foreground=C_WARN)
        self._send_log.tag_config("err",  foreground=C_ERROR)
        self._send_log.tag_config("info", foreground=C_TEXT)
        self._send_log.tag_config("head", foreground=C_ACCENT)

        self._slog("KSeF-App bereit.", "ok")
        self._slog("Einstellungen unter ⚙ Einstellungen konfigurieren.", "info")

        return frame

    def _build_config_card(self, parent):
        """Zeigt die aktuelle Konfiguration (Token, NIP, Umgebung) read-only an."""
        self._section_card(parent, "🔐  Aktuelle Konfiguration")

        self._cfg_labels: dict[str, tk.Label] = {}
        for label in ("Token", "NIP", "Umgebung"):
            row = tk.Frame(parent, bg=C_BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=F_SMALL,
                     bg=C_BG, fg=C_MUTED, width=10, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="–", font=F_MONO, bg=C_BG,
                           fg=C_ERROR, anchor="w")
            lbl.pack(side="left", padx=(4, 0))
            self._cfg_labels[label] = lbl

        tk.Label(parent, text="↗ In Einstellungen bearbeiten",
                 font=F_SMALL, bg=C_BG, fg=C_ACCENT,
                 cursor="hand2").pack(anchor="w", pady=(4, 0))
        parent.winfo_toplevel().bind(
            "<<SettingsSaved>>", lambda e: self._update_config_display())
        self._update_config_display()

    def _update_config_display(self):
        s = self._settings
        token = s.get("token", "").strip()
        nip   = s.get("nip", "").strip()
        env   = s.get("env", "TEST")

        if token and len(token) > 8:
            token_disp = token[:4] + "••••" + token[-4:]
        else:
            token_disp = token

        for key, val in [("Token", token_disp), ("NIP", nip), ("Umgebung", env)]:
            lbl = self._cfg_labels.get(key)
            if lbl:
                lbl.config(text=val if val else "–",
                           fg=C_GREEN if val else C_ERROR)

    # ── Tab: Einstellungen ──────────────────────────────────────────────────

    def _build_settings_tab(self) -> tk.Frame:
        frame = tk.Frame(self._content, bg=C_BG)

        head = tk.Frame(frame, bg=C_BG, pady=14)
        head.pack(fill="x", padx=24)
        tk.Label(head, text="⚙  Einstellungen",
                 font=F_TITLE, bg=C_BG, fg=C_TEXT).pack(side="left")

        scroll_canvas = tk.Canvas(frame, bg=C_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical",
                                   command=scroll_canvas.yview)
        scroll_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        scroll_canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(scroll_canvas, bg=C_BG)
        inner_id = scroll_canvas.create_window((0, 0), window=inner,
                                               anchor="nw")
        def _resize(e):
            scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))
            scroll_canvas.itemconfig(inner_id, width=e.width)
        scroll_canvas.bind("<Configure>", _resize)

        pad = {"padx": 24, "pady": 6}

        # ── Abschnitt: KSeF-Verbindung ──
        self._settings_section(inner, "🔐  KSeF-Verbindung")

        # Umgebung
        env_card = self._card(inner)
        tk.Label(env_card, text="Umgebung", font=F_BODY,
                 bg=C_CARD, fg=C_TEXT).grid(row=0, column=0, sticky="w",
                                             padx=12, pady=(10, 4))
        self._env_var = tk.StringVar(value=self._settings.get("env", "TEST"))
        env_frame = tk.Frame(env_card, bg=C_CARD)
        env_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        env_descriptions = {
            "PRODUCTION": ("Produktion", "Echte Rechnungen. Rechtlich bindend.",   C_ERROR),
            "TEST":       ("Test",       "Tests mit echten NIP-Nummern.",          C_WARN),
            "DEMO":       ("Demo",       "Freie Tests, keine echten Daten nötig.", C_GREEN),
        }
        for env_key, (label, desc, color) in env_descriptions.items():
            row_f = tk.Frame(env_frame, bg=C_CARD)
            row_f.pack(fill="x", pady=3)
            rb = tk.Radiobutton(
                row_f, text=label, variable=self._env_var, value=env_key,
                font=F_BODY, bg=C_CARD, fg=C_TEXT,
                selectcolor=C_SURFACE, activebackground=C_CARD,
                activeforeground=C_TEXT, cursor="hand2",
                indicatoron=1,
            )
            rb.pack(side="left")
            badge = tk.Label(row_f, text=f"  {env_key}  ", font=F_SMALL,
                             bg=color, fg="#fff", padx=4, pady=1)
            badge.pack(side="left", padx=8)
            tk.Label(row_f, text=desc, font=F_SMALL,
                     bg=C_CARD, fg=C_MUTED).pack(side="left")

        # Token
        token_card = self._card(inner)
        token_card.grid_columnconfigure(0, weight=1)
        tk.Label(token_card, text="KSeF-Token", font=F_BODY,
                 bg=C_CARD, fg=C_TEXT).grid(row=0, column=0, sticky="w",
                                             padx=12, pady=(10, 2))
        tk.Label(token_card,
                 text="Den Token im KSeF-Portal unter 'Token' generieren.",
                 font=F_SMALL, bg=C_CARD, fg=C_MUTED).grid(
            row=1, column=0, sticky="w", padx=12)

        self._token_var = tk.StringVar(value=self._settings.get("token", ""))
        self._token_entry = tk.Entry(
            token_card, textvariable=self._token_var,
            font=F_MONO, bg=C_SURFACE, fg=C_TEXT,
            insertbackground=C_TEXT, relief="flat", show="•",
        )
        self._token_entry.grid(row=2, column=0, sticky="ew",
                               padx=12, pady=(6, 4), ipady=5)

        toggle_frame = tk.Frame(token_card, bg=C_CARD)
        toggle_frame.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 10))
        self._token_visible = False
        self._toggle_token_btn = tk.Label(
            toggle_frame, text="👁  Anzeigen", font=F_SMALL,
            bg=C_CARD, fg=C_ACCENT, cursor="hand2")
        self._toggle_token_btn.pack(side="left")
        self._toggle_token_btn.bind("<Button-1>", self._toggle_token)

        # NIP
        nip_card = self._card(inner)
        nip_card.grid_columnconfigure(0, weight=1)
        tk.Label(nip_card, text="NIP (Steuernummer des Ausstellers)",
                 font=F_BODY, bg=C_CARD, fg=C_TEXT).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        tk.Label(nip_card, text="10-stellige polnische Steuernummer.",
                 font=F_SMALL, bg=C_CARD, fg=C_MUTED).grid(
            row=1, column=0, sticky="w", padx=12)
        self._nip_var = tk.StringVar(value=self._settings.get("nip", ""))
        tk.Entry(nip_card, textvariable=self._nip_var, font=F_MONO,
                 bg=C_SURFACE, fg=C_TEXT, insertbackground=C_TEXT,
                 relief="flat").grid(row=2, column=0, sticky="ew",
                                     padx=12, pady=(6, 10), ipady=5)

        # ── Abschnitt: Zertifikat (optional) ──
        self._settings_section(inner, "📜  Zertifikat (optional, für XAdES-Auth)")

        cert_card = self._card(inner)
        cert_card.grid_columnconfigure(0, weight=1)
        tk.Label(cert_card,
                 text="Zertifikatsdatei (.p12 oder .pem)",
                 font=F_BODY, bg=C_CARD, fg=C_TEXT).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        tk.Label(cert_card,
                 text="Zertifikat aus dem MCU-Portal (mcu.ksef.mf.gov.pl).\n"
                      "Wenn leer, wird Token-Authentifizierung verwendet.",
                 font=F_SMALL, bg=C_CARD, fg=C_MUTED, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=12)

        self._cert_path_var = tk.StringVar(value=self._settings.get("cert_path", ""))
        cert_row = tk.Frame(cert_card, bg=C_CARD)
        cert_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 10))
        cert_row.grid_columnconfigure(0, weight=1)
        tk.Entry(cert_row, textvariable=self._cert_path_var, font=F_MONO,
                 bg=C_SURFACE, fg=C_MUTED, insertbackground=C_TEXT,
                 relief="flat").grid(row=0, column=0, sticky="ew", ipady=4)
        self._make_btn(cert_row, "…", self._pick_cert, small=True).grid(
            row=0, column=1, padx=(8, 0))

        # ── Abschnitt: CSV-Spalten-Mapping ──
        self._settings_section(inner, "📋  CSV-Pflichtfelder (Referenz)")

        ref_card = self._card(inner)
        fields = [
            ("rechnungsnummer", "Eindeutige Rechnungsnummer",     "FV/2026/06/0001"),
            ("datum",           "Ausstellungsdatum (ISO)",          "2026-06-23"),
            ("verkäufer_name",  "Name des Rechnungsausstellers",   "Muster GmbH"),
            ("verkäufer_nip",   "NIP Aussteller (10 Ziffern)",     "1234567890"),
            ("verkäufer_land",  "Ländercode 2-stellig",            "PL"),
            ("verkäufer_adresse","Straße, PLZ, Stadt",             "ul. Test 1 Warschau"),
            ("käufer_name",     "Name Empfänger",                  "Empfänger GmbH"),
            ("käufer_nip",      "NIP Empfänger (optional)",        "9876543210"),
            ("käufer_land",     "Ländercode Empfänger",            "PL"),
            ("käufer_adresse",  "Adresse Empfänger",               "ul. Odbiorcza 20"),
            ("position_name",   "Leistungsbezeichnung",            "Beratung"),
            ("menge",           "Menge",                            "10"),
            ("einheit",         "Mengeneinheit",                    "h"),
            ("einzelpreis_netto","Netto-Einzelpreis PLN",          "200.00"),
            ("mwst_satz",       "MwSt-Satz (23/8/5/0/zw/np)",     "23"),
        ]
        header_row = tk.Frame(ref_card, bg=C_SURFACE)
        header_row.pack(fill="x", padx=8, pady=(8, 0))
        for text, w in [("Spaltenname", 160), ("Beschreibung", 200), ("Beispiel", 140)]:
            tk.Label(header_row, text=text, font=F_SMALL,
                     bg=C_SURFACE, fg=C_MUTED, width=w//7, anchor="w",
                     padx=6, pady=3).pack(side="left")

        for i, (field, desc, ex) in enumerate(fields):
            row_f = tk.Frame(ref_card, bg=C_CARD if i % 2 == 0 else C_PANEL)
            row_f.pack(fill="x", padx=8)
            for text, w in [(field, 160), (desc, 200), (ex, 140)]:
                tk.Label(row_f, text=text, font=F_MONO if i == 0 else F_SMALL,
                         bg=row_f["bg"], fg=C_ACCENT if not i else C_TEXT,
                         width=w//7, anchor="w", padx=6, pady=3).pack(side="left")
        tk.Frame(ref_card, bg=C_CARD, height=8).pack()

        # ── Speichern-Button ──
        tk.Frame(inner, bg=C_BG, height=16).pack()
        save_row = tk.Frame(inner, bg=C_BG)
        save_row.pack(fill="x", padx=24, pady=(0, 24))
        self._make_btn(save_row, "💾  Einstellungen speichern",
                       self._save_settings, primary=True).pack(side="left")
        self._save_status_var = tk.StringVar()
        tk.Label(save_row, textvariable=self._save_status_var,
                 font=F_SMALL, bg=C_BG, fg=C_GREEN).pack(
            side="left", padx=12)

        return frame

    def _build_log_tab(self) -> tk.Frame:
        frame = tk.Frame(self._content, bg=C_BG)

        head = tk.Frame(frame, bg=C_BG, pady=14)
        head.pack(fill="x", padx=24)
        tk.Label(head, text="📋  Vollständiges Protokoll",
                 font=F_TITLE, bg=C_BG, fg=C_TEXT).pack(side="left")
        self._make_btn(head, "Löschen", self._clear_log).pack(side="right")

        self._log_area = scrolledtext.ScrolledText(
            frame, font=F_MONO, bg=C_CARD, fg=C_TEXT,
            relief="flat", state="disabled",
            wrap="word", padx=14, pady=10, bd=0,
        )
        self._log_area.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        for tag, color in [("ok", C_GREEN), ("warn", C_WARN),
                            ("err", C_ERROR), ("info", C_TEXT),
                            ("head", C_ACCENT)]:
            self._log_area.tag_config(tag, foreground=color)

        return frame

    # ── Hilfsmethoden ────────────────────────────────────────────────────────

    def _card(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=C_CARD, bd=0)
        f.pack(fill="x", padx=24, pady=(4, 0))
        f.grid_columnconfigure(0, weight=1)
        return f

    def _section_card(self, parent, label, top=6):
        tk.Frame(parent, bg=C_BG, height=top).pack()
        row = tk.Frame(parent, bg=C_BG)
        row.pack(fill="x")
        tk.Label(row, text=label, font=F_SECTION,
                 bg=C_BG, fg=C_MUTED).pack(side="left")
        tk.Frame(row, bg=C_BORDER, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    def _settings_section(self, parent, label):
        tk.Frame(parent, bg=C_BG, height=18).pack()
        row = tk.Frame(parent, bg=C_BG)
        row.pack(fill="x", padx=24)
        tk.Label(row, text=label, font=F_SECTION,
                 bg=C_BG, fg=C_MUTED).pack(side="left")
        tk.Frame(row, bg=C_BORDER, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    def _make_btn(self, parent, text, cmd, primary=False, small=False):
        bg = C_ACCENT if primary else C_SURFACE
        return tk.Button(
            parent, text=text, command=cmd, font=F_SECTION if primary else F_SMALL,
            bg=bg, fg="#fff" if primary else C_TEXT,
            activebackground=C_BTN_HVR, activeforeground="#fff",
            relief="flat", cursor="hand2",
            padx=8 if small else 14, pady=4 if small else 8,
        )

    def _apply_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                         foreground=C_TEXT, rowheight=22, font=F_MONO,
                         borderwidth=0)
        style.configure("Treeview.Heading", background=C_SURFACE,
                         foreground=C_MUTED, font=F_SMALL, relief="flat")
        style.map("Treeview", background=[("selected", C_ACCENT)],
                  foreground=[("selected", "#fff")])
        style.configure("TCombobox", fieldbackground=C_SURFACE,
                         background=C_SURFACE, foreground=C_TEXT,
                         selectbackground=C_ACCENT, bordercolor=C_BORDER)
        style.configure("Vertical.TScrollbar", background=C_SURFACE,
                         bordercolor=C_BG, arrowcolor=C_MUTED,
                         troughcolor=C_BG)

    # ── Aktionen ─────────────────────────────────────────────────────────────

    def _toggle_token(self, *_):
        self._token_visible = not self._token_visible
        self._token_entry.config(show="" if self._token_visible else "•")
        self._toggle_token_btn.config(
            text="🙈  Verbergen" if self._token_visible else "👁  Anzeigen")

    def _save_settings(self):
        self._settings["token"]     = self._token_var.get().strip()
        self._settings["nip"]       = _normalize_nip(self._nip_var.get())
        self._settings["env"]       = self._env_var.get()
        self._settings["cert_path"] = self._cert_path_var.get().strip()
        nip = self._settings["nip"]
        if nip and len(nip) != 10:
            messagebox.showwarning("NIP ungültig",
                f"NIP muss genau 10 Ziffern haben (aktuell: {len(nip)}).\n"
                f"Bereinigt: {nip}")
        save_settings(self._settings)
        self._save_status_var.set("✓ Gespeichert")
        self.after(2500, lambda: self._save_status_var.set(""))
        self._update_config_display()
        self._log("Einstellungen gespeichert.", "ok")
        self._slog("⚙ Einstellungen gespeichert.", "ok")

    def _pick_csv(self):
        path = filedialog.askopenfilename(
            title="CSV-Datei wählen",
            filetypes=[("CSV", "*.csv"), ("Alle", "*.*")])
        if not path:
            return
        self._csv_path = path
        self._csv_label_var.set(Path(path).name)
        self._load_csv(path)

    def _pick_cert(self):
        path = filedialog.askopenfilename(
            title="Zertifikat wählen",
            filetypes=[("PKCS12 / PEM", "*.p12 *.pem"), ("Alle", "*.*")])
        if path:
            self._cert_path_var.set(path)

    def _load_csv(self, path: str):
        self._rows.clear()
        for item in self._tree.get_children():
            self._tree.delete(item)
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                raw = _csv_reader(f)
            groups = group_rows(raw)
            self._rows = groups
            for group in groups:
                head = group[0]
                netto = _calc_netto(group)
                pos = len(group)
                self._tree.insert("", "end", values=(
                    head.get("rechnungsnummer", ""),
                    head.get("datum", ""),
                    head.get("kaeufer_name", ""),
                    f"{netto:,.2f}" + (f" ({pos} Pos.)" if pos > 1 else "")
                    + (" Gutschrift" if is_correction(group) else ""),
                ))
            self._slog(f"✓ CSV geladen: {len(self._rows)} Rechnung(en) aus {len(raw)} Zeile(n)", "ok")
            self._log(f"CSV geladen: {path} ({len(raw)} Zeilen → {len(self._rows)} Rechnungen)", "ok")
            self._status(f"{len(self._rows)} Rechnungen bereit")
        except Exception as exc:
            self._slog(f"✗ CSV-Fehler: {exc}", "err")
            self._log(f"CSV-Fehler: {exc}", "err")

    def _on_send(self):
        if not KSEF_AVAILABLE:
            messagebox.showerror("Fehler", "ksef2 nicht installiert.\npip install ksef2")
            return
        if not self._rows:
            messagebox.showwarning("Keine Daten", "Bitte zuerst eine CSV laden.")
            return
        token = self._settings.get("token", "").strip()
        nip   = self._settings.get("nip", "").strip()
        env   = self._settings.get("env", "TEST")
        if not nip:
            messagebox.showwarning(
                "Konfiguration unvollständig",
                "Bitte NIP unter ⚙ Einstellungen eingeben.")
            self._switch_tab("settings")
            return
        if not token and env != "TEST":
            messagebox.showwarning(
                "Konfiguration unvollständig",
                "Token wird benötigt für PRODUCTION/DEMO.\n"
                "Für TEST kann auch das Test-Zertifikat (ohne Token) verwendet werden.")
            self._switch_tab("settings")
            return

        self._send_btn.config(state="disabled")
        self._export_btn.config(state="disabled")
        self._results.clear()
        self._tx_log.clear()
        self._send_start = datetime.now()
        self._progress.start(12)
        self._status("Übermittle …")
        self._slog("─" * 44, "head")
        self._slog(f"Starte Übermittlung: {len(self._rows)} Rechnung(en) …", "head")
        self._log(f"Starte KSeF-Übermittlung ({len(self._rows)} Rechnungen)", "head")

        threading.Thread(target=self._send_thread, daemon=True).start()

    def _send_thread(self):
        s = self._settings
        env_map = {"PRODUCTION": Environment.PRODUCTION,
                   "TEST":       Environment.TEST,
                   "DEMO":       Environment.DEMO}
        env = env_map.get(s.get("env", "TEST"), Environment.TEST)

        try:
            client = Client(env)
            self._slog(f"↔ Verbinde ({s.get('env', 'TEST')}) …", "info")
            self._log(f"Verbinde mit KSeF {s.get('env', 'TEST')} …", "info")
            schema_info = schema_check_status()
            if schema_info:
                self._slog(f"⚠ Schemaprüfung nicht aktiv: {schema_info}", "warn")
                self._log(f"Schemaprüfung nicht aktiv: {schema_info}", "warn")
            else:
                self._log("Schemaprüfung aktiv (FA(3)-XSD)", "info")

            cert_path = s.get("cert_path", "").strip()
            if cert_path and Path(cert_path).exists():
                from ksef2.core.xades import (
                    load_certificate_and_key_from_p12,
                    load_certificate_from_pem, load_private_key_from_pem)
                self._slog("🔑 XAdES-Authentifizierung …", "info")
                if cert_path.endswith(".p12"):
                    pw = self._prompt_cert_password()
                    cert, key = load_certificate_and_key_from_p12(
                        cert_path, password=pw.encode())
                    auth = client.authentication.with_xades(
                        nip=s["nip"], cert=cert, private_key=key)
                else:
                    cert = load_certificate_from_pem(cert_path)
                    key  = load_private_key_from_pem(cert_path)
                    auth = client.authentication.with_xades(
                        nip=s["nip"], cert=cert, private_key=key)
            elif s.get("token", "").strip():
                self._slog("🔑 Token-Authentifizierung …", "info")
                auth = client.authentication.with_token(
                    ksef_token=s["token"], nip=s["nip"])
            else:
                self._slog("🔑 Test-Zertifikat-Authentifizierung …", "info")
                auth = client.authentication.with_test_certificate(nip=s["nip"])

            self._slog("✓ Authentifiziert. Öffne Sitzung …", "ok")
            self._log("Authentifiziert. Sitzung geöffnet.", "ok")
            session = auth.online_session(form_code=FormSchema.FA3)

            ok, err = 0, 0
            for i, group in enumerate(self._rows, 1):
                nr = group[0].get("rechnungsnummer", f"#{i}")
                pos = len(group)
                try:
                    self._slog(f"  [{i}/{len(self._rows)}] {nr} ({pos} Pos.)", "info")
                    for w in invoice_warnings(group):
                        self._slog(f"     ⚠ Hinweis: {w}", "warn")
                        self._log(f"{nr}  ⚠ {w}", "warn")
                    xml_bytes = build_invoice_xml(group)
                    response  = session.send_invoice_and_wait(
                        invoice_xml=xml_bytes, timeout=90.0)
                    data = extract_response(response)
                    result = {"rechnungsnummer": nr, "positionen": pos,
                              "status": "ok", "ksef_ref": data["reference_number"],
                              **data}
                    # QR-Verifizierungslink (KOD I) für die Warenwirtschaft
                    result["qr_url"] = build_qr_url(
                        s.get("env", "TEST"),
                        group[0].get("verkaeufer_nip", s.get("nip", "")),
                        group[0].get("datum", ""),
                        invoice_hash_b64=data.get("invoice_hash", ""),
                        xml_bytes=xml_bytes,
                    )

                    # UPO herunterladen (amtliche Empfangsbestätigung)
                    if data.get("upo_download_url") and data.get("ksef_number"):
                        upo_dest = DIR_UPO / f"UPO_{data['ksef_number']}.xml"
                        if download_upo(data["upo_download_url"], upo_dest):
                            result["upo_file"] = upo_dest.name
                            self._slog(f"     UPO gespeichert: {upo_dest.name}", "info")

                    self._results.append(result)
                    self._slog(f"  ✓  KSeF-Nr: {data['ksef_number']}", "ok")
                    self._log(f"{nr}  →  ✓  KSeF-Nr {data['ksef_number']}  "
                              f"(Ref {data['reference_number']})", "ok")
                    ok += 1
                except Exception as exc:
                    self._results.append({"rechnungsnummer": nr, "positionen": pos,
                                          "status": "fehler", "fehler": str(exc)})
                    self._slog(f"  ✗  {exc}", "err")
                    self._log(f"{nr}  →  ✗  {exc}", "err")
                    err += 1

            try:
                session.close()
            except Exception:
                pass

            summary = f"Fertig: {ok} ✓  /  {err} ✗"
            self._slog("─" * 44, "head")
            self._slog(summary, "ok" if err == 0 else "warn")
            self._log(summary, "ok" if err == 0 else "warn")

            # ── PDF, Protokoll, Archiv ──────────────────────────────────
            if self._results and self._csv_path:
                self._post_process(self._results, self._rows, s, self._send_start)

        except Exception as exc:
            import traceback
            self._slog(f"✗ Verbindungsfehler: {exc}", "err")
            self._log(f"Verbindungsfehler: {exc}", "err")
            self._log(traceback.format_exc(), "err")
        finally:
            self.after(0, self._send_done)

    def _post_process(self, results, groups, settings, start_time):
        """Erstellt PDF + Protokoll und archiviert die CSV (im Hintergrundthread)."""
        try:
            setup_dirs()
            ts       = start_time.strftime("%Y-%m-%d_%H-%M-%S")
            csv_stem = Path(self._csv_path).stem
            csv_name = Path(self._csv_path).name

            pdf_path   = DIR_ERGEBNISSE / f"{ts}_{csv_stem}.pdf"
            proto_path = DIR_PROTOKOLLE / f"{ts}_{csv_stem}_protokoll.txt"

            try:
                generate_pdf(results, groups, settings, pdf_path, csv_name, start_time)
                self.after(0, lambda p=pdf_path.name: self._slog(f"✓ PDF: {p}", "ok"))
            except Exception as e:
                self.after(0, lambda e=e: self._slog(f"⚠ PDF-Fehler: {e}", "warn"))

            try:
                write_protocol(results, groups, settings, csv_name,
                               start_time, proto_path, list(self._tx_log))
                self.after(0, lambda p=proto_path.name: self._slog(f"✓ Protokoll: {p}", "ok"))
            except Exception as e:
                self.after(0, lambda e=e: self._slog(f"⚠ Protokoll-Fehler: {e}", "warn"))

            # Rückmelde-CSV für die Warenwirtschaft (KSeF-Nr. + QR-URL, ohne Kopfzeile)
            try:
                fb_path = DIR_ERGEBNISSE / f"{ts}_{csv_stem}_ksef.csv"
                if write_feedback_csv(results, fb_path):
                    self.after(0, lambda p=fb_path.name: self._slog(f"✓ Rückmeldung: {p}", "ok"))
            except Exception as e:
                self.after(0, lambda e=e: self._slog(f"⚠ Rückmeldung-Fehler: {e}", "warn"))

            # CSV archivieren wenn sie im Rechnungen-Ordner liegt
            try:
                csv_path = Path(self._csv_path)
                if csv_path.exists() and csv_path.parent.resolve() == DIR_RECHNUNGEN.resolve():
                    ziel = DIR_ARCHIV / f"{ts}_{csv_name}"
                    csv_path.rename(ziel)
                    self.after(0, lambda n=ziel.name: self._slog(f"✓ Archiviert: {n}", "ok"))
            except Exception as e:
                self.after(0, lambda e=e: self._slog(f"⚠ Archiv-Fehler: {e}", "warn"))

        except Exception as e:
            self.after(0, lambda e=e: self._slog(f"⚠ Nachbearbeitung fehlgeschlagen: {e}", "warn"))

    def _send_done(self):
        self._progress.stop()
        self._send_btn.config(state="normal")
        if self._results:
            self._export_btn.config(state="normal")
        self._status("Übermittlung abgeschlossen.")

    def _on_close(self):
        self._settings["token"]     = self._token_var.get().strip()
        self._settings["nip"]       = _normalize_nip(self._nip_var.get())
        self._settings["env"]       = self._env_var.get()
        self._settings["cert_path"] = self._cert_path_var.get().strip()
        save_settings(self._settings)
        self.destroy()

    def _export_results(self):
        if not self._results:
            return
        path = filedialog.asksaveasfilename(
            title="Ergebnisse speichern",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._results, f, ensure_ascii=False, indent=2)
        self._slog(f"✓ Gespeichert: {Path(path).name}", "ok")
        self._log(f"Ergebnisse gespeichert: {path}", "ok")

    def _prompt_cert_password(self) -> str:
        dialog = tk.Toplevel(self)
        dialog.title("Zertifikat-Passwort")
        dialog.configure(bg=C_BG)
        dialog.geometry("330x140")
        dialog.grab_set()
        pw_var = tk.StringVar()
        tk.Label(dialog, text="Passwort für .p12-Zertifikat:",
                 font=F_BODY, bg=C_BG, fg=C_TEXT).pack(padx=20, pady=(20, 8))
        tk.Entry(dialog, textvariable=pw_var, show="•", font=F_MONO,
                 bg=C_SURFACE, fg=C_TEXT, insertbackground=C_TEXT,
                 relief="flat").pack(padx=20, fill="x", ipady=5)
        self._make_btn(dialog, "OK", dialog.destroy, primary=True).pack(pady=12)
        self.wait_window(dialog)
        return pw_var.get()

    def _clear_log(self):
        self._log_area.config(state="normal")
        self._log_area.delete("1.0", "end")
        self._log_area.config(state="disabled")

    def _slog(self, msg: str, tag: str = "info"):
        """Schreibt in den Mini-Log des Send-Tabs und in den TX-Log."""
        self._tx_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        def _ins():
            self._send_log.config(state="normal")
            self._send_log.insert("end", msg + "\n", tag)
            self._send_log.see("end")
            self._send_log.config(state="disabled")
        self.after(0, _ins)

    def _log(self, msg: str, tag: str = "info"):
        """Schreibt in das vollständige Protokoll."""
        def _ins():
            self._log_area.config(state="normal")
            self._log_area.insert("end", msg + "\n", tag)
            self._log_area.see("end")
            self._log_area.config(state="disabled")
        self.after(0, _ins)

    def _status(self, msg: str):
        self.after(0, lambda: self._status_var.set(msg))


# ── Entry-Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = KSeFApp()
    app.mainloop()




