#!/usr/bin/env python3
"""
KSeF Batch-Uebermittlung
Liest alle CSV-Dateien aus dem Ordner 'Rechnungen' neben der EXE,
uebermittelt sie an KSeF und erstellt PDF + Textprotokoll nach 'Protokolle'.
"""

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from ksef_utils import (
    BASE_DIR, DIR_RECHNUNGEN, DIR_ARCHIV, DIR_ERGEBNISSE, DIR_UPO, DIR_PROTOKOLLE,
    SETTINGS_FILE,
    _csv_reader, _normalize_nip, group_rows, build_invoice_xml,
    generate_pdf, write_protocol, setup_dirs,
    extract_response, download_upo, invoice_warnings,
    build_qr_url, write_feedback_csv, schema_check_status,
)

# ── Einstellungen laden ───────────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ── Protokoll-Klasse ──────────────────────────────────────────────────────────

class Protokoll:
    def __init__(self):
        self.lines: list[str] = []
        self.start = datetime.now()

    def log(self, msg: str):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.lines.append(line)
        print(line, flush=True)

    def get_lines(self) -> list[str]:
        return list(self.lines)


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def _safe_console():
    """Verhindert Abstuerze bei umgeleiteter Ausgabe (Aufgabenplanung, Logdatei):
    dort ist stdout oft cp1252 und kann Zeichen wie '─' oder 'ł' nicht darstellen."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def main(no_wait: bool = False):
    _safe_console()
    setup_dirs()

    proto = Protokoll()
    proto.log("KSeF Batch-Uebermittlung gestartet")
    proto.log(f"Eingangsordner:    {DIR_RECHNUNGEN}")
    proto.log(f"Archivordner:      {DIR_ARCHIV}")
    proto.log(f"Ergebnisordner:    {DIR_ERGEBNISSE}")
    proto.log(f"Protokollordner:   {DIR_PROTOKOLLE}")

    # ── Einstellungen ──
    settings = load_settings()
    token = settings.get("token", "").strip()
    nip   = _normalize_nip(settings.get("nip", ""))
    env   = settings.get("env", "TEST")

    if not nip:
        proto.log("FEHLER: NIP fehlt. Bitte zuerst die Desktop-App oeffnen und Einstellungen speichern.")
        _end(proto, {"env": env, "csv_count": 0, "total": 0, "ok": 0, "err": 0}, no_wait)
        sys.exit(1)

    if not token and env != "TEST":
        proto.log(f"FEHLER: Token fehlt. Fuer {env} wird ein Token benoetigt.")
        _end(proto, {"env": env, "csv_count": 0, "total": 0, "ok": 0, "err": 0}, no_wait)
        sys.exit(1)

    auth_method = "Token" if token else "Test-Zertifikat"
    proto.log(f"Umgebung:          {env}")
    proto.log(f"NIP:               {nip}")
    proto.log(f"Authentifizierung: {auth_method}")
    schema_info = schema_check_status()
    proto.log("Schemapruefung:    " + ("aktiv (FA(3)-XSD)" if not schema_info
                                       else f"NICHT aktiv — {schema_info}"))

    # ── CSV-Dateien suchen ──
    csv_files = sorted(DIR_RECHNUNGEN.glob("*.csv"))
    if not csv_files:
        proto.log("Keine CSV-Dateien im Ordner 'Rechnungen' gefunden.")
        _end(proto, {"env": env, "csv_count": 0, "total": 0, "ok": 0, "err": 0}, no_wait)
        return

    proto.log(f"{len(csv_files)} CSV-Datei(en) gefunden: {[f.name for f in csv_files]}")

    # ── ksef2 laden ──
    try:
        from ksef2 import Client, Environment, FormSchema
    except ImportError:
        proto.log("FEHLER: ksef2 nicht installiert — pip install ksef2")
        _end(proto, {"env": env, "csv_count": len(csv_files), "total": 0, "ok": 0, "err": 0}, no_wait)
        sys.exit(1)

    env_map = {
        "PRODUCTION": Environment.PRODUCTION,
        "TEST":       Environment.TEST,
        "DEMO":       Environment.DEMO,
    }
    ksef_env = env_map.get(env, Environment.TEST)

    total_ok  = 0
    total_err = 0
    total_inv = 0

    for csv_file in csv_files:
        proto.log("")
        proto.log(f"{'─'*50}")
        proto.log(f"Verarbeite: {csv_file.name}")
        start_time = datetime.now()
        ts = start_time.strftime("%Y-%m-%d_%H-%M-%S")

        # CSV einlesen
        groups = []
        raw    = []
        try:
            with open(csv_file, newline="", encoding="utf-8-sig") as f:
                raw = _csv_reader(f)
            groups = group_rows(raw)
            proto.log(f"  {len(raw)} Zeile(n) → {len(groups)} Rechnung(en)")
        except Exception as exc:
            proto.log(f"  FEHLER beim Lesen: {exc}")
            proto.log(traceback.format_exc())
            total_err += 1
            continue

        if not groups:
            proto.log("  Datei ist leer, wird uebersprungen.")
            continue

        results: list[dict] = []
        file_ok  = 0
        file_err = 0

        # KSeF-Sitzung oeffnen
        session = None
        try:
            client = Client(ksef_env)
            if token:
                auth = client.authentication.with_token(ksef_token=token, nip=nip)
                proto.log("  Token-Authentifizierung …")
            else:
                auth = client.authentication.with_test_certificate(nip=nip)
                proto.log("  Test-Zertifikat-Authentifizierung …")

            session = auth.online_session(form_code=FormSchema.FA3)
            proto.log("  Sitzung geoeffnet.")

        except Exception as exc:
            proto.log(f"  FEHLER beim Verbinden: {exc}")
            proto.log(traceback.format_exc())
            for group in groups:
                nr = group[0].get("rechnungsnummer", "?")
                results.append({"rechnungsnummer": nr, "positionen": len(group),
                                 "status": "fehler", "fehler": f"Verbindungsfehler: {exc}"})
            total_err += len(groups)
            total_inv += len(groups)
            _save_outputs(results, groups, settings, csv_file.name, start_time, proto, ts)
            continue

        # Rechnungen senden
        for i, group in enumerate(groups, 1):
            nr  = group[0].get("rechnungsnummer", f"#{i}")
            pos = len(group)
            try:
                for w in invoice_warnings(group):
                    proto.log(f"  [{i}/{len(groups)}] {nr}  ⚠ Hinweis: {w}")
                xml_bytes = build_invoice_xml(group)
                response  = session.send_invoice_and_wait(
                    invoice_xml=xml_bytes, timeout=90.0)
                data = extract_response(response)
                result = {"rechnungsnummer": nr, "positionen": pos,
                          "status": "ok", "ksef_ref": data["reference_number"],
                          **data}
                # QR-Verifizierungslink (KOD I) für die Warenwirtschaft
                result["qr_url"] = build_qr_url(
                    env,
                    group[0].get("verkaeufer_nip", nip),
                    group[0].get("datum", ""),
                    invoice_hash_b64=data.get("invoice_hash", ""),
                    xml_bytes=xml_bytes,
                )

                # UPO herunterladen
                if data.get("upo_download_url") and data.get("ksef_number"):
                    upo_dest = DIR_UPO / f"UPO_{data['ksef_number']}.xml"
                    if download_upo(data["upo_download_url"], upo_dest):
                        result["upo_file"] = upo_dest.name
                        proto.log(f"           UPO: {upo_dest.name}")

                results.append(result)
                proto.log(f"  [{i}/{len(groups)}] {nr} ({pos} Pos.)  →  OK  "
                          f"KSeF-Nr: {data['ksef_number']}")
                file_ok += 1
            except Exception as exc:
                results.append({"rechnungsnummer": nr, "positionen": pos,
                                 "status": "fehler", "fehler": str(exc)})
                proto.log(f"  [{i}/{len(groups)}] {nr}  →  FEHLER: {exc}")
                file_err += 1

        # Sitzung schliessen
        try:
            session.close()
        except Exception:
            pass

        proto.log(f"  Ergebnis: {file_ok} OK / {file_err} Fehler")
        total_ok  += file_ok
        total_err += file_err
        total_inv += len(groups)

        # PDF, Protokoll, Archiv
        _save_outputs(results, groups, settings, csv_file.name, start_time, proto, ts)
        try:
            archiv_ziel = DIR_ARCHIV / f"{ts}_{csv_file.name}"
            csv_file.rename(archiv_ziel)
            proto.log(f"  Archiviert: {archiv_ziel.name}")
        except Exception as exc:
            proto.log(f"  Archiv-Fehler: {exc}")

    proto.log("")
    proto.log(f"{'='*50}")
    proto.log(f"GESAMT: {total_inv} Rechnungen  |  {total_ok} OK  |  {total_err} Fehler")

    stats = {
        "env": env, "csv_count": len(csv_files),
        "total": total_inv, "ok": total_ok, "err": total_err,
    }
    _end(proto, stats, no_wait)


def _save_outputs(results, groups, settings, csv_name, start_time, proto, ts):
    """Speichert PDF und Textprotokoll fuer eine CSV-Datei."""
    stem = Path(csv_name).stem

    pdf_path   = DIR_ERGEBNISSE / f"{ts}_{stem}.pdf"
    proto_path = DIR_PROTOKOLLE / f"{ts}_{stem}_protokoll.txt"

    try:
        generate_pdf(results, groups, settings, pdf_path, csv_name, start_time)
        proto.log(f"  PDF gespeichert:      {pdf_path.name}")
    except Exception as exc:
        proto.log(f"  PDF-Fehler: {exc}")

    try:
        write_protocol(results, groups, settings, csv_name,
                       start_time, proto_path, proto.get_lines())
        proto.log(f"  Protokoll gespeichert: {proto_path.name}")
    except Exception as exc:
        proto.log(f"  Protokoll-Fehler: {exc}")

    # Rückmelde-CSV für die Warenwirtschaft (KSeF-Nr. + QR-URL, ohne Kopfzeile)
    try:
        fb_path = DIR_ERGEBNISSE / f"{ts}_{stem}_ksef.csv"
        if write_feedback_csv(results, fb_path):
            proto.log(f"  Rückmeldung gespeichert: {fb_path.name}")
    except Exception as exc:
        proto.log(f"  Rückmeldung-Fehler: {exc}")


def _end(proto, stats, no_wait: bool):
    """Abschluss: Gesamtprotokoll und optionale Pause."""
    ts = proto.start.strftime("%Y-%m-%d_%H-%M-%S")
    overall_path = DIR_PROTOKOLLE / f"{ts}_gesamt_protokoll.txt"
    try:
        lines = [
            "=" * 60,
            "  KSeF Batch - Gesamtprotokoll",
            "=" * 60,
            f"  Start:      {proto.start.strftime('%d.%m.%Y %H:%M:%S')}",
            f"  Ende:       {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
            f"  Umgebung:   {stats.get('env', '?')}",
            f"  CSV-Dateien:{stats.get('csv_count', 0)}",
            f"  Rechnungen: {stats.get('total', 0)} gesamt  |  "
            f"{stats.get('ok', 0)} OK  |  {stats.get('err', 0)} Fehler",
            "=" * 60,
            "",
        ] + proto.get_lines() + [""]
        overall_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nGesamtprotokoll: {overall_path}", flush=True)
    except Exception as exc:
        print(f"Gesamtprotokoll-Fehler: {exc}", flush=True)

    if not no_wait:
        input("\nEnter druecken zum Beenden...")


if __name__ == "__main__":
    no_wait = "--no-wait" in sys.argv or "--auto" in sys.argv
    main(no_wait=no_wait)
