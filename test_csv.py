import csv
from decimal import Decimal

CSV_FIELDNAMES = [
    "rechnungsnummer", "datum",
    "verkäufer_name", "verkäufer_nip", "verkäufer_land", "verkäufer_adresse",
    "käufer_name", "käufer_nip", "käufer_land", "käufer_adresse",
    "position_name", "menge", "einheit", "einzelpreis_netto", "mwst_satz",
]

PFLICHTFELDER = [
    "rechnungsnummer", "datum", "verkäufer_name", "verkäufer_nip",
    "verkäufer_land", "verkäufer_adresse", "käufer_name",
    "käufer_land", "käufer_adresse", "position_name",
    "menge", "einheit", "einzelpreis_netto", "mwst_satz",
]

VAT_GUELTIG = {"23", "22", "8", "5", "0", "zw", "np"}

def _csv_reader(f):
    sample = f.read(512)
    f.seek(0)
    has_header = sample.lstrip("﻿").startswith("rechnungsnummer")
    return csv.DictReader(
        f,
        fieldnames=None if has_header else CSV_FIELDNAMES,
        delimiter=",", quotechar='"', skipinitialspace=True,
    )

def group_rows(rows):
    groups = {}
    for row in rows:
        nr = row.get("rechnungsnummer", "").strip()
        groups.setdefault(nr, []).append(row)
    return list(groups.values())

path = r"D:\Claude\ksef-app\dist\Rechnungen\KSEF-Export-Neu.csv"
with open(path, newline="", encoding="utf-8") as f:
    raw = list(_csv_reader(f))

groups = group_rows(raw)
print(f"Zeilen eingelesen : {len(raw)}")
print(f"Rechnungen        : {len(groups)}")
print()

fehler_gesamt = 0

for g in groups:
    head = g[0]
    nr      = head.get("rechnungsnummer", "")
    kaeufer = head.get("käufer_name", "")
    datum   = head.get("datum", "")
    pos     = len(g)
    print(f"Rechnung {nr}  |  {datum}  |  {kaeufer}  |  {pos} Pos.")

    for i, row in enumerate(g, 1):
        menge   = row.get("menge", "")
        einheit = row.get("einheit", "").lower()
        preis   = row.get("einzelpreis_netto", "")
        name    = row.get("position_name", "")
        mwst    = row.get("mwst_satz", "").strip().lower()
        print(f"  Pos {i}: {name}  |  {menge} {einheit} x {preis} PLN  |  MwSt {mwst}%")

        # Decimal-Prüfung
        for feld, wert in [("menge", menge), ("einzelpreis_netto", preis)]:
            try:
                Decimal(str(wert).strip())
            except Exception:
                print(f"  FEHLER: '{feld}' kein gültiger Dezimalwert: '{wert}'")
                fehler_gesamt += 1

        # MwSt-Prüfung
        if mwst not in VAT_GUELTIG:
            print(f"  FEHLER: mwst_satz ungültig: '{mwst}' (erlaubt: {VAT_GUELTIG})")
            fehler_gesamt += 1

    # Pflichtfelder
    for feld in PFLICHTFELDER:
        if not head.get(feld, "").strip():
            print(f"  FEHLER: Pflichtfeld leer: '{feld}'")
            fehler_gesamt += 1

    # Datum-Format
    try:
        from datetime import date
        date.fromisoformat(head.get("datum", "").strip())
    except Exception:
        print(f"  FEHLER: Datum ungültig: '{head.get('datum', '')}'  (erwartet: YYYY-MM-DD)")
        fehler_gesamt += 1

    print()

if fehler_gesamt == 0:
    print("Ergebnis: OK - keine Fehler gefunden.")
else:
    print(f"Ergebnis: {fehler_gesamt} Fehler gefunden.")
