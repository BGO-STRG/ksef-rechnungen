#!/usr/bin/env python3
"""Gemeinsame Hilfsfunktionen fuer KSeF-Desktop-App und Batch-Prozessor."""

import base64
import csv
import hashlib
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

# ── Basisverzeichnis ──────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

DIR_RECHNUNGEN = BASE_DIR / "Rechnungen"
DIR_ARCHIV     = DIR_RECHNUNGEN / "Archiv"
DIR_ERGEBNISSE = DIR_RECHNUNGEN / "Ergebnisse"
DIR_UPO        = DIR_ERGEBNISSE / "UPO"
DIR_PROTOKOLLE = BASE_DIR / "Protokolle"
SETTINGS_FILE  = Path.home() / ".ksef_settings.json"

# Pflichtfelder (Position 1-15) + optionale Zusatzfelder (Position 16-21)
CSV_FIELDNAMES = [
    "rechnungsnummer", "datum",
    "verkaeufer_name", "verkaeufer_nip", "verkaeufer_land", "verkaeufer_adresse",
    "kaeufer_name", "kaeufer_nip", "kaeufer_land", "kaeufer_adresse",
    "position_name", "menge", "einheit", "einzelpreis_netto", "mwst_satz",
    # optionale Zusatzfelder
    "waehrung", "wechselkurs", "lieferdatum", "zahlungsart", "faelligkeit", "bankkonto",
    "belegrabatt",
    # optionale Felder fuer Gutschriften / Korrekturrechnungen (Position 23-27)
    "belegart", "korr_rechnungsnummer", "korr_datum", "korr_ksef_nr", "korrekturgrund",
]

# Auch deutsche Feldnamen mit Umlauten akzeptieren
CSV_FIELDNAMES_ALT = [
    "rechnungsnummer", "datum",
    "verkäufer_name", "verkäufer_nip", "verkäufer_land", "verkäufer_adresse",
    "käufer_name", "käufer_nip", "käufer_land", "käufer_adresse",
    "position_name", "menge", "einheit", "einzelpreis_netto", "mwst_satz",
    "währung", "wechselkurs", "lieferdatum", "zahlungsart", "fälligkeit", "bankkonto",
    "belegrabatt",
    "belegart", "korr_rechnungsnummer", "korr_datum", "korr_ksef_nr", "korrekturgrund",
]

# Werte in Spalte 'belegart', die eine Korrekturrechnung (Gutschrift) kennzeichnen
_CORRECTION_CODES = {"korrektur", "kor", "gutschrift", "korekta", "korygujaca",
                     "korygująca", "correction", "credit_note"}

# Normalisierung: Felder mit Umlauten → ohne Umlaute
_FIELD_ALIASES = {
    "verkäufer_name":    "verkaeufer_name",
    "verkäufer_nip":     "verkaeufer_nip",
    "verkäufer_land":    "verkaeufer_land",
    "verkäufer_adresse": "verkaeufer_adresse",
    "käufer_name":       "kaeufer_name",
    "käufer_nip":        "kaeufer_nip",
    "käufer_land":       "kaeufer_land",
    "käufer_adresse":    "kaeufer_adresse",
    "währung":           "waehrung",
    "fälligkeit":        "faelligkeit",
}

# EU-Mitgliedstaaten (für automatische 0%-Zuordnung: WDT vs. Export)
EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
}

# Abweichende USt-IdNr.-Praefixe (Laendercode ISO → EU-VAT-Praefix)
_VAT_PREFIX = {"GR": "EL"}

# Explizite 0%-Codes zur Übersteuerung der automatischen Länder-Zuordnung
_ZERO_WDT_CODES    = {"0-eu", "0eu", "0-ig", "0ig", "0-wdt", "0wdt", "wdt", "ig"}
_ZERO_EXPORT_CODES = {"0-exp", "0exp", "0-export", "0export", "export", "exp"}
_ZERO_DOMESTIC_CODES = {"0-inland", "0inland", "0-dom", "0dom", "0-krajowa", "0krajowa"}

# Lesbare Bezeichnungen der Steuerarten (für Protokoll/PDF)
_TAX_LABELS = {
    "23": "23%", "8": "8%", "5": "5%",
    "zero_domestic": "0% Inland",
    "zero_wdt":      "0% innergem. Lieferung (WDT)",
    "zero_export":   "0% Export",
    "zw": "steuerfrei (zw)", "np": "nicht steuerbar (np)",
    "oo": "Reverse Charge (oo)",
}


def _tax_kind(raw: str, buyer_country: str) -> str:
    """Ermittelt die Steuerart aus mwst_satz + Käuferland (reines Python, kein SDK).

    0% wird automatisch zugeordnet: PL → Inland, EU (≠PL) → WDT, sonst Export.
    Explizite Codes (0-eu, 0-exp, 0-dom) übersteuern die Automatik.
    """
    r = str(raw).strip().lower()
    bc = (buyer_country or "PL").strip().upper()
    if r in _ZERO_WDT_CODES:
        return "zero_wdt"
    if r in _ZERO_EXPORT_CODES:
        return "zero_export"
    if r in _ZERO_DOMESTIC_CODES:
        return "zero_domestic"
    if r == "0":
        if bc == "PL":
            return "zero_domestic"
        if bc in EU_COUNTRIES:
            return "zero_wdt"
        return "zero_export"
    if r in ("23", "8", "5"):
        return r
    if r in ("zw", "np", "oo"):
        return r
    if r == "22":
        return "22"  # in Polen unzulässig → wird in der Validierung abgewiesen
    return "unknown"


def _vat_builder_args(kind: str):
    """Liefert (VatRate, SaleCategory|None) für den FA(3)-Builder."""
    from ksef2.fa3 import VatRate, SaleCategory
    return {
        "23": (VatRate.VAT_23, None),
        "22": (VatRate.VAT_22, None),  # unüblich, aber wird wie angegeben übermittelt
        "8":  (VatRate.VAT_8,  None),
        "5":  (VatRate.VAT_5,  None),
        "zero_domestic": (VatRate.VAT_0, SaleCategory.ZERO_DOMESTIC),
        "zero_wdt":      (VatRate.VAT_0, SaleCategory.ZERO_WDT),
        "zero_export":   (VatRate.VAT_0, SaleCategory.ZERO_EXPORT),
        "zw": (VatRate.EXEMPT, None),
        "np": (VatRate.NOT_SUBJECT, None),
        "oo": (VatRate.REVERSE_CHARGE, None),
    }.get(kind, (VatRate.VAT_23, None))


def _tax_label(kind: str) -> str:
    return _TAX_LABELS.get(kind, kind)


# Zahlungsart: Benutzer-Eingabe (DE/PL/EN/Code) → FA(3)-via()-Literal
PAYMENT_FORMS = {
    "bar": "cash", "gotowka": "cash", "gotówka": "cash", "cash": "cash", "1": "cash",
    "karte": "card", "karta": "card", "card": "card", "2": "card",
    "gutschein": "voucher", "bon": "voucher", "voucher": "voucher", "3": "voucher",
    "scheck": "check", "czek": "check", "check": "check", "4": "check",
    "kredit": "credit", "kredyt": "credit", "credit": "credit", "5": "credit",
    "ueberweisung": "bank_transfer", "überweisung": "bank_transfer",
    "przelew": "bank_transfer", "transfer": "bank_transfer",
    "bank_transfer": "bank_transfer", "ueberw": "bank_transfer", "6": "bank_transfer",
    "mobil": "mobile", "mobile": "mobile", "mobilna": "mobile", "7": "mobile",
}


def setup_dirs():
    """Legt alle benoetigten Ordner an (idempotent)."""
    for d in (DIR_RECHNUNGEN, DIR_ARCHIV, DIR_ERGEBNISSE, DIR_UPO, DIR_PROTOKOLLE):
        d.mkdir(parents=True, exist_ok=True)


def _normalize_nip(raw: str) -> str:
    return "".join(c for c in raw if c.isdigit())


def _safe_text(s: str) -> str:
    """Ersetzt Sonderzeichen fuer PDF-Ausgabe (fpdf2 core fonts, Latin-1)."""
    if not s:
        return ""
    table = str.maketrans({
        "ä": "ae", "ö": "oe", "ü": "ue",
        "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
        "ß": "ss",
        "ą": "a",  "Ą": "A",
        "ć": "c",  "Ć": "C",
        "ę": "e",  "Ę": "E",
        "ł": "l",  "Ł": "L",
        "ń": "n",  "Ń": "N",
        "ó": "o",  "Ó": "O",
        "ś": "s",  "Ś": "S",
        "ź": "z",  "Ź": "Z",
        "ż": "z",  "Ż": "Z",
    })
    return s.translate(table)


def _norm_row(row: dict) -> dict:
    """Normalisiert Feldnamen (mit Umlauten → ohne Umlaute)."""
    return {_FIELD_ALIASES.get(k, k): v for k, v in row.items()}


def _csv_reader(f) -> list[dict]:
    """Liest CSV mit/ohne BOM, mit/ohne Header, CR/LF oder LF, quoted fields."""
    rows_raw = list(csv.reader(f, delimiter=",", quotechar='"', skipinitialspace=True))
    if not rows_raw:
        return []
    first = [c.strip().lstrip("﻿") for c in rows_raw[0]]
    first_norm = first[0].lower() if first else ""
    if first_norm in ("rechnungsnummer",):
        fieldnames = [c.strip().lower() for c in first]
        data = rows_raw[1:]
    else:
        fieldnames = CSV_FIELDNAMES_ALT
        data = rows_raw
    rows = []
    for row in data:
        if not any(c.strip() for c in row):
            continue
        d = dict(zip(fieldnames, row))
        rows.append(_norm_row(d))
    return rows


def group_rows(rows: list[dict]) -> list[list[dict]]:
    """Gruppiert CSV-Zeilen nach rechnungsnummer; behaelt Reihenfolge."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        nr = row.get("rechnungsnummer", "").strip()
        if not nr:
            continue
        groups.setdefault(nr, []).append(row)
    return list(groups.values())


def _row_tax_kind(row: dict, buyer_country: str) -> str:
    """Steuerart einer CSV-Zeile (Wrapper um _tax_kind mit mwst_satz)."""
    return _tax_kind(row.get("mwst_satz", "23"), buyer_country)


def _invoice_currency(group: list[dict]) -> str:
    """Liefert den Waehrungscode einer Rechnung (Standard PLN)."""
    if not group:
        return "PLN"
    return group[0].get("waehrung", "").strip().upper() or "PLN"


def is_correction(group: list[dict]) -> bool:
    """True, wenn die Rechnung als Korrekturrechnung (Gutschrift, KOR) zu senden ist."""
    if not group:
        return False
    return str(group[0].get("belegart", "")).strip().lower() in _CORRECTION_CODES


def _line_nettos(group: list[dict]) -> list[Decimal]:
    """Netto pro Position (Menge × Einzelpreis) VOR Belegrabatt."""
    nettos = []
    for row in group:
        try:
            m = Decimal(str(row.get("menge", "0")).strip().replace(",", "."))
            p = Decimal(str(row.get("einzelpreis_netto", "0")).strip().replace(",", "."))
            nettos.append(m * p)
        except InvalidOperation:
            nettos.append(Decimal("0"))
    return nettos


def _belegrabatt_total(group: list[dict]) -> Decimal:
    """Gesamter Netto-Belegrabatt der Rechnung. Akzeptiert absoluten Betrag oder
    Prozent ('10%'). Prozent bezieht sich auf das Positions-Netto vor Rabatt."""
    if not group or is_correction(group):
        # Bei Gutschriften wird kein Belegrabatt verarbeitet (Nachlass = negativer Preis)
        return Decimal("0")
    raw = str(group[0].get("belegrabatt", "")).strip().replace(" ", "")
    if not raw:
        return Decimal("0")
    try:
        if raw.endswith("%"):
            pct = Decimal(raw[:-1].replace(",", "."))
            base = sum(_line_nettos(group))
            return (base * pct / Decimal("100")).quantize(Decimal("0.01"), ROUND_HALF_UP)
        return Decimal(raw.replace(",", ".")).quantize(Decimal("0.01"), ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal("0")


def _line_discounts(group: list[dict]) -> list[Decimal]:
    """Verteilt den Belegrabatt proportional (nach Netto) auf die Positionen.
    Die Summe der Positionsrabatte entspricht exakt dem Gesamt-Belegrabatt."""
    nettos = _line_nettos(group)
    total_disc = _belegrabatt_total(group)
    total_net = sum(nettos)
    if total_disc <= 0 or total_net <= 0:
        return [Decimal("0")] * len(group)
    disc = [(total_disc * n / total_net).quantize(Decimal("0.01"), ROUND_HALF_UP)
            for n in nettos]
    # Rundungsdifferenz der Position mit dem größten Netto zuschlagen
    diff = total_disc - sum(disc)
    if diff != 0:
        k = max(range(len(nettos)), key=lambda i: nettos[i])
        disc[k] += diff
    return disc


def _calc_netto(group: list[dict]) -> Decimal:
    """Rechnungs-Netto NACH Belegrabatt (für Anzeige/Summen)."""
    return sum(_line_nettos(group)) - _belegrabatt_total(group)


def _totals_by_currency(results: list[dict], groups_map: dict) -> dict[str, Decimal]:
    """Summiert Netto-Betraege pro Waehrung (verschiedene Waehrungen nicht mischen)."""
    totals: dict[str, Decimal] = {}
    for r in results:
        group = groups_map.get(r.get("rechnungsnummer", ""), [{}])
        cur = _invoice_currency(group)
        totals[cur] = totals.get(cur, Decimal("0")) + _calc_netto(group)
    return totals


def _format_totals(totals: dict[str, Decimal]) -> str:
    """Formatiert Waehrungssummen als 'X PLN | Y EUR'."""
    if not totals:
        return "0.00"
    return "  |  ".join(f"{amount:,.2f} {cur}" for cur, amount in sorted(totals.items()))


def _parse_decimal(val: str, field: str, nr: str) -> Decimal:
    cleaned = str(val).strip().replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Rechnung {nr}: Feld '{field}' kein gueltiger Dezimalwert: '{val}'")


def _invoice_warnings(group: list[dict]) -> list[str]:
    """Sammelt auffaellige Werte als HINWEISE (blockiert NICHT die Uebermittlung).

    Die Rechnung wird immer abgegeben; diese Hinweise dienen nur der Protokollierung,
    damit fragwuerdige Werte nachvollziehbar sind. Inhaltliche Korrektheit liegt beim
    Anwender.
    """
    warnings = []
    head = group[0]

    for field in ("verkaeufer_name", "verkaeufer_nip", "kaeufer_name", "datum", "rechnungsnummer"):
        if not head.get(field, "").strip():
            warnings.append(f"Pflichtfeld leer: {field}")

    datum = head.get("datum", "").strip()
    if datum:
        try:
            date.fromisoformat(datum)
        except ValueError:
            warnings.append(f"Datum '{datum}' nicht im Format YYYY-MM-DD")

    # Währung & Wechselkurs
    waehrung = head.get("waehrung", "").strip().upper()
    if waehrung and len(waehrung) != 3:
        warnings.append(f"Währung '{waehrung}' unüblich (erwartet 3-Buchstaben-Code)")
    kurs = head.get("wechselkurs", "").strip()
    if waehrung and waehrung != "PLN":
        if not kurs:
            warnings.append(f"Fremdwährung '{waehrung}' ohne Wechselkurs — KSeF kann ablehnen")
        else:
            try:
                Decimal(kurs.replace(",", "."))
            except InvalidOperation:
                warnings.append(f"Wechselkurs '{kurs}' kein gültiger Dezimalwert")

    for feld, label in (("lieferdatum", "Lieferdatum"), ("faelligkeit", "Fälligkeit")):
        wert = head.get(feld, "").strip()
        if wert:
            try:
                date.fromisoformat(wert)
            except ValueError:
                warnings.append(f"{label} '{wert}' nicht im Format YYYY-MM-DD")

    zahlungsart = head.get("zahlungsart", "").strip().lower()
    if zahlungsart and zahlungsart not in PAYMENT_FORMS:
        warnings.append(f"Zahlungsart '{zahlungsart}' unbekannt — wird ignoriert")

    buyer_country = head.get("kaeufer_land", "PL").strip().upper() or "PL"
    line_kinds = []
    for i, row in enumerate(group, 1):
        for field in ("menge", "einzelpreis_netto"):
            val = row.get(field, "").strip()
            try:
                d = Decimal(val.replace(",", "."))
                if field == "einzelpreis_netto" and d.as_tuple().exponent < -8:
                    warnings.append(f"Pos.{i}: Einzelpreis '{val}' hat mehr als 8 "
                                    f"Nachkommastellen — wird auf 8 gerundet (FA(3)-Maximum)")
            except InvalidOperation:
                warnings.append(f"Pos.{i}: Feld '{field}' kein Dezimalwert: '{val}'")
        mwst = row.get("mwst_satz", "").strip().lower()
        kind = _tax_kind(mwst, buyer_country)
        line_kinds.append(kind)
        if kind == "22":
            warnings.append(f"Pos.{i}: Steuersatz '22' — in Polen gilt der Regelsatz 23% "
                            f"(Wert wird wie angegeben übermittelt)")
        elif kind == "unknown":
            warnings.append(f"Pos.{i}: mwst_satz '{mwst}' unbekannt — wird als 23% übermittelt")

    # Gutschrift / Korrekturrechnung
    nettos = _line_nettos(group)
    if is_correction(group):
        if not head.get("korr_rechnungsnummer", "").strip():
            warnings.append("Gutschrift ohne korr_rechnungsnummer (Nummer der Original"
                            "rechnung) — XML kann nicht gebaut werden")
        korr_datum = head.get("korr_datum", "").strip()
        if not korr_datum:
            warnings.append("Gutschrift ohne korr_datum (Datum der Originalrechnung) — "
                            "XML kann nicht gebaut werden")
        else:
            try:
                date.fromisoformat(korr_datum)
            except ValueError:
                warnings.append(f"korr_datum '{korr_datum}' nicht im Format YYYY-MM-DD")
        if not head.get("korr_ksef_nr", "").strip():
            warnings.append("Gutschrift ohne korr_ksef_nr — Originalrechnung wird als "
                            "'außerhalb KSeF ausgestellt' gekennzeichnet")
        if head.get("belegrabatt", "").strip():
            warnings.append("Belegrabatt wird bei Gutschriften ignoriert — Nachlass als "
                            "negativen Einzelpreis angeben")
        for i, row in enumerate(group, 1):
            try:
                m = Decimal(str(row.get("menge", "0")).strip().replace(",", "."))
                p = Decimal(str(row.get("einzelpreis_netto", "0")).strip().replace(",", "."))
            except InvalidOperation:
                continue
            if m < 0 and p < 0:
                warnings.append(f"Pos.{i}: Menge UND Einzelpreis negativ — ergibt einen "
                                f"POSITIVEN Betrag (Nachbelastung statt Gutschrift)")
        if sum(nettos) > 0:
            warnings.append(f"Gutschrift mit positivem Netto ({sum(nettos):,.2f}) — "
                            f"wird als Nachbelastung übermittelt")
    else:
        for i, n in enumerate(nettos, 1):
            if n < 0:
                warnings.append(f"Pos.{i}: negativer Betrag ({n:,.2f}) ohne belegart="
                                f"korrektur — wird als NORMALE Rechnung übermittelt")
        if head.get("korr_rechnungsnummer", "").strip():
            warnings.append("korr_rechnungsnummer angegeben, aber belegart ist nicht "
                            "'korrektur' — Korrekturangaben werden ignoriert")

    if "zero_wdt" in line_kinds and not head.get("kaeufer_nip", "").strip():
        warnings.append("0% innergemeinschaftliche Lieferung (WDT) ohne USt-IdNr. des "
                        "Käufers — KSeF kann ablehnen")

    # Belegrabatt-Prüfung
    rabatt = _belegrabatt_total(group)
    if rabatt > 0:
        base = sum(_line_nettos(group))
        if rabatt >= base:
            warnings.append(f"Belegrabatt ({rabatt}) ist >= Positions-Netto ({base}) — "
                            f"Netto würde 0 oder negativ; KSeF kann ablehnen")

    return warnings


# Öffentlicher Alias (nicht blockierende Hinweise, für Sende-Protokoll)
invoice_warnings = _invoice_warnings


def _entity_kwargs(name: str, nip: str, land: str, adresse: str) -> dict:
    """Baut die seller()/buyer()-Argumente; auslaendische Steuer-IDs -> eu_vat_id."""
    kw = {"name": name, "country_code": land, "address_line_1": adresse}
    nip = nip.replace(" ", "")
    if nip:
        if land == "PL":
            kw["tax_id"] = nip
        elif land in EU_COUNTRIES:
            # EU-Partner: USt-IdNr. mit Laenderpraefix. Griechenland nutzt 'EL', nicht 'GR'.
            prefix = _VAT_PREFIX.get(land, land)
            if nip[:2].isalpha():
                vat = nip.upper()
                if vat[:2] == "GR":
                    vat = "EL" + vat[2:]
            else:
                vat = f"{prefix}{nip}"
            kw["eu_vat_id"] = vat
        else:
            # Nicht-EU-Partner (z. B. RS, CH, GB): Steuernummer als sonstige Kennung
            # (FA(3): KodKraju + NrID), kein EU-USt-Praefix
            kw["other_id"] = nip
    return kw


def build_invoice_xml(group: list[dict]) -> bytes:
    """Baut die FA(3)-XML. Blockiert NICHT auf inhaltliche Fehler — es wird immer
    versucht zu übermitteln. Nur technisch Unbaubares (z.B. ungültiges Datum,
    nicht-numerische Beträge) führt zwangsläufig zu einer Ausnahme."""
    from ksef2.fa3 import FA3InvoiceBuilder

    head = group[0]
    nr = head["rechnungsnummer"].strip()

    try:
        issue_date = date.fromisoformat(head["datum"].strip())
    except ValueError as e:
        raise ValueError(f"Ungültiges Datum in Rechnung {nr}: {e}")

    seller_kwargs = _entity_kwargs(
        name=head["verkaeufer_name"].strip(),
        nip=head["verkaeufer_nip"].strip(),
        land=head.get("verkaeufer_land", "PL").strip().upper() or "PL",
        adresse=head.get("verkaeufer_adresse", "").strip(),
    )
    buyer_kwargs = _entity_kwargs(
        name=head["kaeufer_name"].strip(),
        nip=head.get("kaeufer_nip", "").strip(),
        land=head.get("kaeufer_land", "PL").strip().upper() or "PL",
        adresse=head.get("kaeufer_adresse", "").strip(),
    )

    base = (
        FA3InvoiceBuilder()
        .header(system_info="KSeF-App v1.0")
        .seller(**seller_kwargs)
        .buyer(**buyer_kwargs)
    )

    if is_correction(group):
        # Gutschrift / Korrekturrechnung (RodzajFaktury = KOR), Differenzmethode:
        # die Positionen enthalten nur die Aenderung (negative Menge oder Preis).
        korr_nr = head.get("korr_rechnungsnummer", "").strip()
        korr_datum_raw = head.get("korr_datum", "").strip()
        if not korr_nr:
            raise ValueError(f"Gutschrift {nr}: korr_rechnungsnummer (Nummer der "
                             f"Originalrechnung) fehlt")
        try:
            korr_datum = date.fromisoformat(korr_datum_raw)
        except ValueError:
            raise ValueError(f"Gutschrift {nr}: korr_datum '{korr_datum_raw}' fehlt oder "
                             f"nicht im Format YYYY-MM-DD")
        korr_ksef = head.get("korr_ksef_nr", "").strip()
        body = base.correction().issue_date(issue_date).invoice_number(nr)
        korr = body.correction().add_corrected_invoice(
            issue_date=korr_datum,
            invoice_number=korr_nr,
            ksef_id=korr_ksef or None,
            outside_ksef=not korr_ksef,
        )
        grund = head.get("korrekturgrund", "").strip()
        if grund:
            korr = korr.reason(grund)
        body = korr.done()
    else:
        body = base.standard().issue_date(issue_date).invoice_number(nr)

    # Währung (Standard PLN) + Wechselkurs bei Fremdwährung
    waehrung = head.get("waehrung", "").strip().upper() or "PLN"
    body = body.currency(waehrung)
    kurs = head.get("wechselkurs", "").strip()
    if waehrung != "PLN" and kurs:
        body = body.vat_currency_exchange_rate(Decimal(kurs.replace(",", ".")))

    # Liefer-/Leistungsdatum (P_6)
    lieferdatum = head.get("lieferdatum", "").strip()
    if lieferdatum:
        body = body.date_of_supply(date.fromisoformat(lieferdatum))

    # Zahlungsangaben (Zahlungsart, Fälligkeit, Bankkonto)
    zahlungsart = head.get("zahlungsart", "").strip().lower()
    faelligkeit = head.get("faelligkeit", "").strip()
    bankkonto   = head.get("bankkonto", "").strip()
    if zahlungsart or faelligkeit or bankkonto:
        pay = body.payment()
        if zahlungsart in PAYMENT_FORMS:
            pay = pay.via(PAYMENT_FORMS[zahlungsart])
        if faelligkeit:
            pay = pay.due_on(date.fromisoformat(faelligkeit))
        if bankkonto:
            pay = pay.bank_account(account_number=bankkonto.replace(" ", ""))
        body = pay.done()

    builder = body.rows()

    buyer_country = head.get("kaeufer_land", "PL").strip().upper() or "PL"
    # Belegrabatt proportional auf die Positionen verteilen (senkt Netto + MwSt)
    line_disc = _line_discounts(group)
    for i, row in enumerate(group, 1):
        kind = _tax_kind(row.get("mwst_satz", "23"), buyer_country)
        vat, sale_cat = _vat_builder_args(kind)
        qty   = _parse_decimal(row.get("menge", "1"), "menge", nr)
        price = _parse_decimal(row.get("einzelpreis_netto", "0"), "einzelpreis_netto", nr)
        # FA(3) erlaubt max. 8 Nachkommastellen im Einzelpreis (Typ TKwotowy2)
        if price.as_tuple().exponent < -8:
            price = price.quantize(Decimal("0.00000001"), ROUND_HALF_UP)
        name  = row.get("position_name", "").strip() or "Position"
        unit  = row.get("einheit", "szt").strip().lower() or "szt"
        line_kwargs = dict(
            name=name,
            quantity=qty,
            unit_of_measure=unit,
            unit_price_net=price,
            vat_rate=vat,
        )
        if sale_cat is not None:
            line_kwargs["sale_category"] = sale_cat
        if line_disc[i - 1] > 0:
            line_kwargs["discount_amount"] = line_disc[i - 1]
        builder = builder.add_line(**line_kwargs)

    xml = builder.done().done().to_xml()
    xml_bytes = xml.encode("utf-8") if isinstance(xml, str) else xml

    # Vor dem Senden gegen das offizielle FA(3)-Schema pruefen: was hier scheitert,
    # wuerde KSeF sicher ablehnen (z. B. Fehler 450) — dann gar nicht erst senden.
    errors = validate_invoice_xml(xml_bytes)
    if errors:
        raise ValueError(f"Rechnung {nr} verletzt das FA(3)-Schema, nicht gesendet: "
                         + " | ".join(errors))
    return xml_bytes


# ── FA(3)-Schemapruefung ──────────────────────────────────────────────────────

_SCHEMA = None          # geladenes XMLSchema (Cache)
_SCHEMA_STATUS = ""     # Grund, falls die Pruefung nicht verfuegbar ist


def _load_schema():
    """Laedt das FA(3)-XSD aus dem ksef2-Paket (einmalig)."""
    global _SCHEMA, _SCHEMA_STATUS
    if _SCHEMA is not None or _SCHEMA_STATUS:
        return _SCHEMA
    try:
        import ksef2
        from lxml import etree
        xsd = Path(ksef2.__file__).parent / "infra" / "schema" / "fa3" / "definitions" / "schemat.xsd"
        if not xsd.exists():
            _SCHEMA_STATUS = f"Schema-Datei nicht gefunden: {xsd}"
            return None
        _SCHEMA = etree.XMLSchema(etree.parse(str(xsd)))
    except Exception as exc:
        _SCHEMA_STATUS = f"Schema nicht ladbar: {exc}"
    return _SCHEMA


def schema_check_status() -> str:
    """Leer, wenn die Schemapruefung aktiv ist, sonst der Grund (fuer das Protokoll)."""
    _load_schema()
    return _SCHEMA_STATUS


def validate_invoice_xml(xml_bytes: bytes) -> list[str]:
    """Prueft die XML gegen das FA(3)-Schema. Liefert lesbare Fehlermeldungen
    (max. 5); leere Liste = gueltig oder Pruefung nicht verfuegbar."""
    schema = _load_schema()
    if schema is None:
        return []
    import re
    from lxml import etree
    if schema.validate(etree.fromstring(xml_bytes)):
        return []
    msgs = []
    for err in schema.error_log:
        msg = re.sub(r"\{https?://[^}]*\}", "", err.message)   # Namespace entfernen
        msgs.append(msg)
        if len(msgs) >= 5:
            break
    return msgs


# ── KSeF-Antwort auswerten ────────────────────────────────────────────────────

def _fmt_dt(dt) -> str:
    if dt is None:
        return ""
    try:
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return str(dt)


def extract_response(response) -> dict:
    """Extrahiert alle verfuegbaren Felder aus einer KSeF-Antwort robust."""
    def g(attr):
        return getattr(response, attr, None)

    status      = g("status")
    status_code = getattr(status, "code", None) if status is not None else None
    status_desc = getattr(status, "description", None) if status is not None else None

    url = g("upo_download_url")

    return {
        "ksef_number":            g("ksef_number") or "",
        "reference_number":       g("reference_number") or "",
        "invoice_hash":           g("invoice_hash") or "",
        "acquisition_date":       _fmt_dt(g("acquisition_date")),
        "invoicing_date":         _fmt_dt(g("invoicing_date")),
        "permanent_storage_date": _fmt_dt(g("permanent_storage_date")),
        "invoicing_mode":         g("invoicing_mode") or "",
        "ordinal_number":         g("ordinal_number"),
        "status_code":            status_code,
        "status_desc":            status_desc,
        "upo_download_url":       str(url) if url else "",
    }


def download_upo(url: str, dest: Path) -> bool:
    """Laedt die UPO-XML (amtliche Empfangsbestaetigung) herunter."""
    if not url:
        return False
    import urllib.request
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "KSeF-App"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception:
        return False


# ── QR-Verifizierungscode (KOD I) ─────────────────────────────────────────────

def _qr_base(env: str) -> str:
    """Basis-URL des QR-Verifizierungsdienstes je Umgebung."""
    return ("https://qr.ksef.mf.gov.pl" if str(env).strip().upper() == "PRODUCTION"
            else "https://qr-test.ksef.mf.gov.pl")


def build_qr_url(env: str, seller_nip: str, issue_date_iso: str,
                 invoice_hash_b64: str = "", xml_bytes: bytes | None = None) -> str:
    """Baut die KOD-I-Verifizierungs-URL (Inhalt des QR-Codes) einer KSeF-Rechnung.

    Format:  {basis}/invoice/{NIP}/{DD-MM-YYYY}/{SHA256 als Base64URL}
    Der Hash stammt bevorzugt aus der KSeF-Antwort (invoice_hash_b64); ersatzweise
    wird er aus xml_bytes berechnet.
    """
    nip = "".join(c for c in str(seller_nip) if c.isdigit())
    try:
        datum = date.fromisoformat(str(issue_date_iso).strip()).strftime("%d-%m-%Y")
    except ValueError:
        datum = str(issue_date_iso).strip()
    if invoice_hash_b64:
        h = invoice_hash_b64.replace("+", "-").replace("/", "_").rstrip("=")
    elif xml_bytes is not None:
        h = base64.urlsafe_b64encode(hashlib.sha256(xml_bytes).digest()).decode().rstrip("=")
    else:
        return ""
    return f"{_qr_base(env)}/invoice/{nip}/{datum}/{h}"


def write_feedback_csv(results: list[dict], path: Path) -> Path | None:
    """Schreibt eine einfache CSV OHNE Kopfzeile fuer die Warenwirtschaft:
    rechnungsnummer, KSeF-Nummer, QR-URL — eine Zeile je erfolgreicher Rechnung."""
    rows = [
        [r.get("rechnungsnummer", ""), r.get("ksef_number", ""), r.get("qr_url", "")]
        for r in results
        if r.get("status") == "ok" and r.get("ksef_number")
    ]
    if not rows:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=",", quotechar='"',
                       quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerows(rows)
    return path


# ── PDF-Generierung ───────────────────────────────────────────────────────────

def generate_pdf(
    results: list[dict],
    groups: list[list[dict]],
    settings: dict,
    output_path: Path,
    csv_name: str,
    start_time: datetime,
) -> None:
    from fpdf import FPDF

    groups_map = {g[0].get("rechnungsnummer", "").strip(): g for g in groups}
    ok_count  = sum(1 for r in results if r.get("status") == "ok")
    err_count = len(results) - ok_count
    totals    = _totals_by_currency(results, groups_map)
    end_time = datetime.now()

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=12)

    # ── Kopfzeile ──
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(30, 50, 120)
    pdf.cell(0, 10, "KSeF - Uebertragungsprotokoll", new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, f"Erstellt: {end_time.strftime('%d.%m.%Y %H:%M:%S')}",
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # ── Konfigurationsblock ──
    pdf.set_fill_color(235, 240, 255)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 7, "Konfiguration", new_x="LMARGIN", new_y="NEXT", fill=True)

    pdf.set_font("Helvetica", "", 9)
    env  = settings.get("env", "TEST")
    nip  = settings.get("nip", "")
    auth = "Token" if settings.get("token", "").strip() else "Test-Zertifikat"
    infos = [
        ("Umgebung",          env),
        ("NIP",               nip),
        ("Authentifizierung", auth),
        ("CSV-Datei",         _safe_text(csv_name)),
        ("Start",             start_time.strftime("%d.%m.%Y %H:%M:%S")),
        ("Ende",              end_time.strftime("%d.%m.%Y %H:%M:%S")),
        ("Dauer",             f"{int((end_time - start_time).total_seconds())} Sekunden"),
    ]
    for label, val in infos:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(45, 6, f"{label}:")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, val, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Zusammenfassung ──
    pdf.set_font("Helvetica", "B", 11)
    summary_color = (0, 150, 80) if err_count == 0 else (200, 50, 50)
    pdf.set_text_color(*summary_color)
    pdf.cell(0, 8,
             f"Ergebnis: {ok_count} erfolgreich  |  {err_count} Fehler  |"
             f"  Gesamtnetto: {_safe_text(_format_totals(totals))}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    # ── Tabellen-Header ──
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    # Nr | Datum | Kaeufer | Pos | Netto | Whg | Status | KSeF-Nummer | Referenz | Eingang
    cw = [14, 18, 37, 8, 22, 11, 13, 57, 57, 0]
    cw[-1] = page_w - sum(cw[:-1])
    headers = ["Nr.", "Datum", "Kaeufer", "Pos", "Netto", "Whg", "Status",
               "KSeF-Nummer", "Referenz", "Eingang"]
    row_h = 7

    pdf.set_fill_color(30, 50, 120)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 7)
    for w, h in zip(cw, headers):
        pdf.cell(w, row_h, h, border=0, fill=True)
    pdf.ln()

    # ── Tabellenzeilen ──
    fill = False
    for r in results:
        nr      = r.get("rechnungsnummer", "")
        group   = groups_map.get(nr, [{}])
        head    = group[0] if group else {}
        datum   = head.get("datum", "")
        kaeufer = _safe_text(head.get("kaeufer_name", ""))
        pos     = r.get("positionen", len(group))
        netto   = _calc_netto(group)
        cur     = _invoice_currency(group)
        is_ok   = r.get("status") == "ok"
        status  = "OK" if is_ok else "FEHLER"
        ksef_nr = r.get("ksef_number", "") or ""
        ref     = r.get("reference_number", "") or r.get("ksef_ref", "")
        eingang = r.get("acquisition_date", "") or ""

        if fill:
            pdf.set_fill_color(245, 247, 255)
        else:
            pdf.set_fill_color(255, 255, 255)

        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(0, 0, 0)

        kaeufer_short = kaeufer[:23] + ".." if len(kaeufer) > 23 else kaeufer

        pdf.cell(cw[0], row_h, str(nr), border="B", fill=True)
        pdf.cell(cw[1], row_h, datum, border="B", fill=True)
        pdf.cell(cw[2], row_h, kaeufer_short, border="B", fill=True)
        pdf.cell(cw[3], row_h, str(pos), border="B", fill=True, align="C")
        pdf.cell(cw[4], row_h, f"{netto:,.2f}", border="B", fill=True, align="R")
        pdf.cell(cw[5], row_h, cur, border="B", fill=True, align="C")

        pdf.set_text_color(0, 150, 80) if is_ok else pdf.set_text_color(200, 50, 50)
        pdf.cell(cw[6], row_h, status, border="B", fill=True, align="C")

        pdf.set_text_color(20, 20, 20)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(cw[7], row_h, _safe_text(ksef_nr), border="B", fill=True)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(cw[8], row_h, _safe_text(str(ref)), border="B", fill=True)
        pdf.cell(cw[9], row_h, eingang, border="B", fill=True)
        pdf.ln()
        fill = not fill

    # ── Fehlerdetails ──
    errors = [r for r in results if r.get("status") != "ok"]
    if errors:
        pdf.ln(6)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(200, 50, 50)
        pdf.cell(0, 7, "Fehlerdetails:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(0, 0, 0)
        for r in errors:
            msg = _safe_text(str(r.get("fehler", "Unbekannter Fehler")))
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(page_w, 5, f"  Rechnung {r.get('rechnungsnummer')}: {msg}",
                           new_x="LMARGIN", new_y="NEXT")

    # ── Fusszeile ──
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 5,
             f"Generiert von KSeF-App | {end_time.strftime('%d.%m.%Y %H:%M:%S')}",
             new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.output(str(output_path))


# ── Textprotokoll ─────────────────────────────────────────────────────────────

def write_protocol(
    results: list[dict],
    groups: list[list[dict]],
    settings: dict,
    csv_name: str,
    start_time: datetime,
    proto_path: Path,
    log_lines: list[str] | None = None,
) -> None:
    groups_map = {g[0].get("rechnungsnummer", "").strip(): g for g in groups}
    ok_count    = sum(1 for r in results if r.get("status") == "ok")
    err_count   = len(results) - ok_count
    totals      = _totals_by_currency(results, groups_map)
    end_time = datetime.now()

    env  = settings.get("env", "TEST")
    nip  = settings.get("nip", "")
    auth = "Token" if settings.get("token", "").strip() else "Test-Zertifikat"

    sep = "=" * 70

    lines = [
        sep,
        "  KSeF - Uebertragungsprotokoll",
        sep,
        f"  CSV-Datei:         {csv_name}",
        f"  Start:             {start_time.strftime('%d.%m.%Y %H:%M:%S')}",
        f"  Ende:              {end_time.strftime('%d.%m.%Y %H:%M:%S')}",
        f"  Dauer:             {int((end_time - start_time).total_seconds())} Sekunden",
        f"  Umgebung:          {env}",
        f"  NIP:               {nip}",
        f"  Authentifizierung: {auth}",
        "",
        f"  Rechnungen gesamt: {len(results)}",
        f"  Erfolgreich:       {ok_count}",
        f"  Fehler:            {err_count}",
        f"  Gesamtnetto:       {_format_totals(totals)}",
        sep,
        "",
        "  EINZELERGEBNISSE",
        "-" * 70,
    ]

    for r in results:
        nr    = r.get("rechnungsnummer", "?")
        group = groups_map.get(nr, [{}])
        head  = group[0] if group else {}
        netto = _calc_netto(group)
        pos   = r.get("positionen", len(group))
        cur   = _invoice_currency(group)

        lines += [
            "",
            f"  Rechnung Nr.:  {nr}",
            f"  Datum:         {head.get('datum', '')}",
            f"  Verkäufer:     {head.get('verkaeufer_name', '')}  (NIP: {head.get('verkaeufer_nip', '')})",
            f"  Käufer:        {head.get('kaeufer_name', '')}  (NIP: {head.get('kaeufer_nip', '')})",
            f"  Positionen:    {pos}",
            f"  Währung:       {cur}",
        ]
        if is_correction(group):
            ksef_ref = head.get("korr_ksef_nr", "").strip() or "außerhalb KSeF"
            lines += [
                f"  Belegart:      GUTSCHRIFT / Korrekturrechnung (KOR)",
                f"  Korrigiert:    {head.get('korr_rechnungsnummer','').strip()} vom "
                f"{head.get('korr_datum','').strip()}  (KSeF: {ksef_ref})",
            ]
            if head.get("korrekturgrund", "").strip():
                lines.append(f"  Grund:         {head.get('korrekturgrund','').strip()}")
        # Zusatzangaben nur wenn vorhanden
        if head.get("wechselkurs", "").strip() and cur != "PLN":
            lines.append(f"  Wechselkurs:   {head.get('wechselkurs','').strip()}  (1 {cur} → PLN)")
        if head.get("lieferdatum", "").strip():
            lines.append(f"  Lieferdatum:   {head.get('lieferdatum','').strip()}")
        if head.get("zahlungsart", "").strip():
            lines.append(f"  Zahlungsart:   {head.get('zahlungsart','').strip()}")
        if head.get("faelligkeit", "").strip():
            lines.append(f"  Fällig bis:    {head.get('faelligkeit','').strip()}")
        if head.get("bankkonto", "").strip():
            lines.append(f"  Bankkonto:     {head.get('bankkonto','').strip()}")
        if head.get("belegrabatt", "").strip():
            lines.append(f"  Belegrabatt:   {head.get('belegrabatt','').strip()} "
                         f"(netto, auf Positionen verteilt)")

        buyer_country = head.get("kaeufer_land", "PL").strip().upper() or "PL"
        for i, row in enumerate(group, 1):
            try:
                m = Decimal(str(row.get("menge", "0")).strip().replace(",", "."))
                p = Decimal(str(row.get("einzelpreis_netto", "0")).strip().replace(",", "."))
                zeile = m * p
            except InvalidOperation:
                zeile = Decimal("0")
            tax_txt = _tax_label(_tax_kind(row.get("mwst_satz", ""), buyer_country))
            lines.append(
                f"    Pos.{i}: {row.get('position_name','').strip()}"
                f"  |  {row.get('menge','')} {row.get('einheit','')} x {row.get('einzelpreis_netto','')} {cur}"
                f" = {zeile:,.2f} {cur}  |  Steuer: {tax_txt}"
            )

        rabatt = _belegrabatt_total(group)
        if rabatt > 0:
            brutto_netto = sum(_line_nettos(group))
            lines += [
                f"  Zwischensumme: {brutto_netto:,.2f} {cur}",
                f"  Belegrabatt:  -{rabatt:,.2f} {cur}",
                f"  Netto gesamt:  {netto:,.2f} {cur}",
            ]
        else:
            lines += [
                f"  Netto gesamt:  {netto:,.2f} {cur}",
            ]

        if r.get("status") == "ok":
            status_txt = "OK"
            if r.get("status_code"):
                status_txt = f"OK  (Code {r.get('status_code')} - {r.get('status_desc','')})"
            lines += [
                f"  Status:           {status_txt}",
                f"  KSeF-Nummer:      {r.get('ksef_number', '')}",
                f"  Referenznummer:   {r.get('reference_number', '') or r.get('ksef_ref','')}",
                f"  Rechnungs-Hash:   {r.get('invoice_hash', '')}",
                f"  Eingang bei KSeF: {r.get('acquisition_date', '')}",
                f"  Rechnungsdatum:   {r.get('invoicing_date', '')}",
                f"  Dauerhaft gesp.:  {r.get('permanent_storage_date', '')}",
                f"  Modus:            {r.get('invoicing_mode', '')}",
                f"  UPO-Datei:        {r.get('upo_file', '(nicht heruntergeladen)')}",
                f"  UPO-Download-URL: {r.get('upo_download_url', '')}",
            ]
        else:
            lines += [
                f"  Status:           FEHLER",
                f"  Fehlermeldung:    {r.get('fehler', '')}",
            ]

    lines += ["", "-" * 70]

    if log_lines:
        lines += ["", "  UEBERTRAGUNGSLOG", "-" * 70]
        lines += log_lines

    lines += ["", sep, "  Ende des Protokolls", sep, ""]

    proto_path.write_text("\n".join(lines), encoding="utf-8")
