#!/usr/bin/env python3
"""
KSeF Batch-Übermittlung
Liest alle CSV-Dateien aus dem Ordner 'Rechnungen' neben der EXE,
übermittelt sie an KSeF und schreibt ein Protokoll nach 'Protokolle'.
"""

import csv
import json
import sys
import traceback
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# Basisverzeichnis = Ordner der EXE (oder des Skripts)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

DIR_RECHNUNGEN = BASE_DIR / "Rechnungen"
DIR_ARCHIV     = DIR_RECHNUNGEN / "Archiv"
DIR_PROTOKOLLE = BASE_DIR / "Protokolle"
SETTINGS_FILE  = Path.home() / ".ksef_settings.json"


CSV_FIELDNAMES = [
    "rechnungsnummer", "datum",
    "verkäufer_name", "verkäufer_nip", "verkäufer_land", "verkäufer_adresse",
    "käufer_name", "käufer_nip", "käufer_land", "käufer_adresse",
    "position_name", "menge", "einheit", "einzelpreis_netto", "mwst_satz",
]

def _csv_reader(f):
    """DictReader mit automatischer Header-Erkennung."""
    sample = f.read(512)
    f.seek(0)
    has_header = sample.lstrip("﻿").startswith("rechnungsnummer")
    return csv.DictReader(
        f,
        fieldnames=None if has_header else CSV_FIELDNAMES,
        delimiter=",", quotechar='"', skipinitialspace=True,
    )

def setup_dirs():
    DIR_RECHNUNGEN.mkdir(exist_ok=True)
    DIR_ARCHIV.mkdir(exist_ok=True)
    DIR_PROTOKOLLE.mkdir(exist_ok=True)


def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def build_invoice_xml(row: dict):
    from ksef2.fa3 import FA3InvoiceBuilder, VatRate
    vat_key = str(row.get("mwst_satz", "23")).strip().lower()
    vat_map = {
        "23": VatRate.VAT_23, "22": VatRate.VAT_22,
        "8":  VatRate.VAT_8,  "5":  VatRate.VAT_5,
        "0":  VatRate.VAT_0,  "zw": VatRate.EXEMPT,
        "np": VatRate.NOT_SUBJECT,
    }
    vat_rate = vat_map.get(vat_key, VatRate.VAT_23)
    xml = (
        FA3InvoiceBuilder()
        .header(system_info="KSeF-Batch v1.0")
        .seller(
            name=row["verkäufer_name"].strip(),
            tax_id=row["verkäufer_nip"].strip(),
            country_code=row["verkäufer_land"].strip().upper(),
            address_line_1=row["verkäufer_adresse"].strip(),
        )
        .buyer(
            name=row["käufer_name"].strip(),
            tax_id=row["käufer_nip"].strip() or None,
            country_code=row["käufer_land"].strip().upper(),
            address_line_1=row["käufer_adresse"].strip(),
        )
        .standard()
        .issue_date(date.fromisoformat(row["datum"].strip()))
        .invoice_number(row["rechnungsnummer"].strip())
        .rows()
        .add_line(
            name=row["position_name"].strip(),
            quantity=Decimal(str(row["menge"]).strip()),
            unit_of_measure=row["einheit"].strip(),
            unit_price_net=Decimal(str(row["einzelpreis_netto"]).strip()),
            vat_rate=vat_rate,
        )
        .done()
        .done()
        .to_xml()
    )
    return xml.encode("utf-8") if isinstance(xml, str) else xml


class Protokoll:
    def __init__(self, path: Path):
        self.path = path
        self.lines: list[str] = []
        self.start = datetime.now()

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.lines.append(line)
        print(line)

    def save(self, stats: dict):
        header = [
            "=" * 60,
            "  KSeF Batch-Übertragungsprotokoll",
            "=" * 60,
            f"  Start:      {self.start.strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Ende:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Umgebung:   {stats.get('env', '?')}",
            f"  CSV-Dateien:{stats.get('csv_count', 0)}",
            f"  Rechnungen: {stats.get('total', 0)} gesamt  |  "
            f"{stats.get('ok', 0)} erfolgreich  |  {stats.get('err', 0)} Fehler",
            "=" * 60,
            "",
        ]
        content = "\n".join(header + self.lines) + "\n"
        self.path.write_text(content, encoding="utf-8")
        print(f"\nProtokoll gespeichert: {self.path}")


def main():
    setup_dirs()

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    proto_path = DIR_PROTOKOLLE / f"{ts}_protokoll.txt"
    proto = Protokoll(proto_path)

    proto.log("KSeF Batch-Übermittlung gestartet")
    proto.log(f"Eingangsordner:  {DIR_RECHNUNGEN}")
    proto.log(f"Archivordner:    {DIR_ARCHIV}")
    proto.log(f"Protokollordner: {DIR_PROTOKOLLE}")

    # Einstellungen laden
    settings = load_settings()
    token = settings.get("token", "").strip()
    nip   = settings.get("nip", "").strip()
    env   = settings.get("env", "TEST")

    if not token or not nip:
        proto.log("FEHLER: Token oder NIP fehlen. Bitte zuerst die Desktop-App öffnen und Einstellungen speichern.")
        proto.save({"env": env, "csv_count": 0, "total": 0, "ok": 0, "err": 0})
        input("\nEnter drücken zum Beenden...")
        sys.exit(1)

    proto.log(f"Umgebung: {env}  |  NIP: {nip}")

    # CSV-Dateien suchen
    csv_files = sorted(DIR_RECHNUNGEN.glob("*.csv"))
    if not csv_files:
        proto.log("Keine CSV-Dateien im Ordner 'Rechnungen' gefunden.")
        proto.save({"env": env, "csv_count": 0, "total": 0, "ok": 0, "err": 0})
        input("\nEnter drücken zum Beenden...")
        return

    proto.log(f"{len(csv_files)} CSV-Datei(en) gefunden: {[f.name for f in csv_files]}")

    # ksef2 importieren
    try:
        from ksef2 import Client, Environment, FormSchema
    except ImportError:
        proto.log("FEHLER: ksef2 nicht installiert. pip install ksef2")
        proto.save({"env": env, "csv_count": len(csv_files), "total": 0, "ok": 0, "err": 0})
        input("\nEnter drücken zum Beenden...")
        sys.exit(1)

    env_map = {
        "PRODUCTION": Environment.PRODUCTION,
        "TEST":       Environment.TEST,
        "DEMO":       Environment.DEMO,
    }
    ksef_env = env_map.get(env, Environment.TEST)

    total_ok  = 0
    total_err = 0
    total_rows = 0

    for csv_file in csv_files:
        proto.log("")
        proto.log(f"── Verarbeite: {csv_file.name} ──")

        rows = []
        try:
            with open(csv_file, newline="", encoding="utf-8") as f:
                rows = list(_csv_reader(f))
            proto.log(f"   {len(rows)} Rechnung(en) geladen")
        except Exception as exc:
            proto.log(f"   FEHLER beim Lesen: {exc}")
            total_err += 1
            continue

        if not rows:
            proto.log("   Datei ist leer, wird übersprungen.")
            continue

        # KSeF-Sitzung öffnen
        try:
            client = Client(ksef_env)
            auth    = client.authentication.with_token(ksef_token=token, nip=nip)
            session = auth.online_session(form_code=FormSchema.FA3)
            proto.log("   Sitzung geöffnet.")
        except Exception as exc:
            proto.log(f"   FEHLER beim Verbinden: {exc}")
            proto.log(traceback.format_exc())
            total_err += len(rows)
            total_rows += len(rows)
            continue

        file_ok  = 0
        file_err = 0

        for i, row in enumerate(rows, 1):
            nr = row.get("rechnungsnummer", f"#{i}")
            try:
                xml_bytes = build_invoice_xml(row)
                response  = session.send_invoice(invoice_xml=xml_bytes)
                ref = getattr(response, "reference_number", str(response))
                proto.log(f"   [{i}/{len(rows)}] {nr}  →  OK  Ref: {ref}")
                file_ok += 1
            except Exception as exc:
                proto.log(f"   [{i}/{len(rows)}] {nr}  →  FEHLER: {exc}")
                file_err += 1

        try:
            session.close()
        except Exception:
            pass

        proto.log(f"   Ergebnis: {file_ok} OK / {file_err} Fehler")
        total_ok  += file_ok
        total_err += file_err
        total_rows += len(rows)

        # CSV ins Archiv verschieben
        archiv_ziel = DIR_ARCHIV / f"{ts}_{csv_file.name}"
        csv_file.rename(archiv_ziel)
        proto.log(f"   Archiviert: {archiv_ziel.name}")

    proto.log("")
    proto.log(f"Fertig: {total_rows} Rechnungen  |  {total_ok} OK  |  {total_err} Fehler")

    stats = {
        "env": env, "csv_count": len(csv_files),
        "total": total_rows, "ok": total_ok, "err": total_err,
    }
    proto.save(stats)
    input("\nEnter drücken zum Beenden...")


if __name__ == "__main__":
    main()
