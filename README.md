# KSeF Rechnungsübermittlung

Desktop-App für die automatische Übermittlung von Rechnungen an das polnische
KSeF-System (Krajowy System e-Faktur, API 2.0 / FA(3)-Format).

---

## EXE bauen (Windows)

**Voraussetzung:** Python 3.10+ installiert (https://python.org)

**Einmalig:**
```
build_exe.bat
```
→ Danach liegt die fertige EXE unter `dist\KSeF_Rechnungen.exe`

---

## Direkt starten (ohne EXE, alle Betriebssysteme)

```bash
pip install -r requirements.txt
python ksef_app.py
```

---

## Bedienung

Die App hat drei Bereiche (Seitenleiste):

### ⚙ Einstellungen (zuerst!)
- **Umgebung** wählen: DEMO (für erste Tests), TEST, oder PRODUCTION
- **KSeF-Token** eingeben (aus dem KSeF-Portal)
- **NIP** eingeben (10-stellige polnische Steuernummer)
- Optional: Zertifikat (.p12 oder .pem) für XAdES-Authentifizierung
- **Speichern** – die Einstellungen werden lokal gespeichert

### ✈ Senden
1. CSV-Datei laden (Vorlage: `rechnungen_vorlage.csv`)
2. Vorschau prüfen
3. „Alle Rechnungen senden" klicken
4. Ergebnisse (KSeF-Referenznummern) als JSON exportieren

### 📋 Protokoll
Vollständiger Log aller Aktionen.

---

## CSV-Format

| Spalte | Beispiel |
|--------|---------|
| rechnungsnummer | FV/2026/06/0001 |
| datum | 2026-06-23 |
| verkäufer_name | Muster GmbH |
| verkäufer_nip | 1234567890 |
| verkäufer_land | PL |
| verkäufer_adresse | ul. Przykładowa 1, 00-001 Warszawa |
| käufer_name | Empfänger Sp. z o.o. |
| käufer_nip | 9876543210 |
| käufer_land | PL |
| käufer_adresse | ul. Odbiorcza 20 Kraków |
| position_name | Beratungsleistung |
| menge | 10 |
| einheit | h |
| einzelpreis_netto | 200.00 |
| mwst_satz | 23 |

**MwSt-Sätze:** 23, 22, 8, 5, 0, zw (befreit), np (nicht steuerbar)

---

## Authentifizierung

**Token** (einfachste Methode):
Portal: https://ksef.mf.gov.pl → „Token" → Token generieren

**Zertifikat** (XAdES, für Unternehmen):
MCU-Portal: https://mcu.ksef.mf.gov.pl → Zertifikat beantragen & herunterladen

---

## Umgebungen

| Name | URL | Wofür |
|------|-----|-------|
| DEMO | api-demo.ksef.mf.gov.pl | Erste Tests, keine echten Daten |
| TEST | api-test.ksef.mf.gov.pl | Tests mit echten NIPs |
| PRODUCTION | api.ksef.mf.gov.pl | Echte Rechnungen (rechtsverbindlich) |

> ⚠ Bitte zuerst mit DEMO testen!
