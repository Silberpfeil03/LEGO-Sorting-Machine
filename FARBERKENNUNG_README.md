# LEGO-Farberkennung für Brick Recognizer

## Hauptfunktionen:

### 1. Excel/CSV-basierte LEGO-Farberkennung
- **`load_lego_colors()`**: Lädt offizielle LEGO-Farbtabelle aus Excel/CSV
- **`find_closest_lego_color()`**: Findet nächstliegende LEGO-Farbe per RGB-Abstand
- Unterstützt mehrere Dateiformate: .xlsx, .xls, .csv
- Automatischer Fallback auf integrierte Farbtabelle

### 2. Erweiterte Farberkennung (`get_dominant_color_simple`)
- Analysiert dominante Farbe in Bild-Regionen
- Filtert unwichtige Grau-/Weiß-/Schwarztöne
- Vergleicht mit offizieller LEGO-Farbtabelle
- Gibt sowohl erkannte als auch nächste LEGO-Farbe zurück

### 3. Präzise Farbabstandsberechnung
- Euklidischer Abstand im RGB-Farbraum
- Normalisierte Konfidenzwerte (0-100%)
- Farbabstand in RGB-Einheiten

### 3. Zentrale Region bestimmen (`get_center_region_from_image`)
- Berechnet automatisch eine zentrale Region des Bildes für die Farbanalyse
- Standard: 30% der Bildgröße, zentriert

### 4. Erweiterte API-Analyse (`identify_brick` - erweitert)
- Sucht nach Bounding Box-Informationen in der API-Antwort
- Unterstützte Schlüssel: "bbox", "bounding_box", "cage", "region", "box"
- Fallback auf zentrale Region wenn keine Bounding Box gefunden wird

### 5. GUI-Erweiterungen:
- **Farbanzeige-Frame**: Zeigt erkannte Farbe visuell an
- **Farbname und RGB-Werte**: Textuelle Darstellung der Farbergebnisse
- **"Nur Farbe analysieren" Button**: Ermöglicht separate Farbanalyse ohne API-Aufruf

## Einrichtung der LEGO-Farbtabelle:

### Unterstützte LEGO-Farbtabellen:

#### **Ihre CSV-Datei: `lego_colors.CSV`** ✅
**Spaltenstruktur (Semikolon-getrennt):**
- `Color ID`: LEGO Farb-ID (z.B. "4", "15", "25")
- `RGB Value`: Hex-Werte (z.B. "C91A09", "FFFFFF", "FE8A18")  
- `Color Name`: Farbname (z.B. "Red", "White", "Orange")
- `Preview`: (wird ignoriert)

#### **Alternative Formate:**
**Standard CSV/Excel:**
- `Name`: LEGO-Farbname (z.B. "Bright Red", "White")
- `R`: Rot-Wert (0-255)
- `G`: Grün-Wert (0-255)  
- `B`: Blau-Wert (0-255)
- Optional: `ID`, `Hex` für zusätzliche Informationen

**Unterstützte Dateiformate:**
- CSV mit Semikolons: `lego_colors.CSV` ⭐ (Ihre Datei)
- Excel: `lego_colors.xlsx`, `lego_colors.xls`
- Standard CSV: `lego_colors.csv`

### Fallback-System:
Falls keine Excel-Datei gefunden wird, verwendet das System eine integrierte Tabelle mit 15 häufigen LEGO-Farben.

## Verwendung:

### 1. **Automatische LEGO-Farberkennung**: 
   - Bei "Bild aufnehmen" wird automatisch die nächstliegende LEGO-Farbe ermittelt
   - Zeigt sowohl gemessene RGB-Werte als auch offizielle LEGO-RGB-Werte
   - Nutzt Bounding Box aus API (falls vorhanden) oder zentrale Region

### 2. **Intelligente Teil-ID-Erkennung mit Farbkontrolle** 🆕
   - **Primär: Teil-ID Matching**: Erkennung basiert hauptsächlich auf der Teil-ID
   - **Sekundär: Farbkontrolle**: Farbe dient zur Unterscheidung bei mehreren Farbvarianten
   - **Automatische Variantenerkennung**: Erkennt wenn ein Teil in mehreren Farben im Set vorhanden ist
   - **Set-Integration**: Überprüft ob erkanntes Teil im geladenen Set enthalten ist
   - **Status-System**: Klare Anzeige ob Teil gefunden/nicht im Set/mehrere Varianten vorhanden

### 3. **Erweiterte Farbanzeige**:
   - **Zweigeteilte Farbanzeige**: Links erkannte Farbe, rechts nächste LEGO-Farbe
   - **Genauigkeitswerte**: Farbabstand und prozentuale Übereinstimmung
   - **Detaillierte Informationen**: Beide RGB-Werte, LEGO-Farbname und offizielle LEGO Color ID
   - **Automatische Filterung**: Entfernt ungültige Farben wie "[Unknown]" und "[No Color/Any Color]"
   - **BrickLink-Matching**: Zeigt passende Teile aus dem geladenen Set an

### 4. **Erweiterte Analyse-Tools**:
   - **"Nur Farbe analysieren"**: Reine Farbanalyse ohne Set-Vergleich
   - **"Mit Set vergleichen"**: Set-Übersicht ohne Teil-ID
   - **"Debug Set"**: Detaillierte Set-Informationen und Parsing-Status
   - **"Farbvarianten"**: Zeigt welche Teile in mehreren Farben im Set vorhanden sind

## 🎯 **Neue Logik: ID-First mit RGB-Präzisionskontrolle**

### **Optimierter Erkennungsablauf:**
1. **Teil-ID Erkennung** (Brickognize API) - ohne Farbberücksichtigung
2. **Set-Abgleich**: Ist das Teil im geladenen Set enthalten?
3. **RGB-Präzisionskontrolle**: Gemessene RGB ↔ LEGO-Standard-RGB Vergleich
4. **Bei mehreren Farbvarianten**: Beste RGB-Übereinstimmung wird ausgewählt

### **RGB-basierte Bewertung:**
- **90-100%**: ✅ Excellente Übereinstimmung
- **70-89%**: ⚠️ Gute Übereinstimmung  
- **<70%**: ❌ Schwache Übereinstimmung

### **Beispiel-Ausgaben:**
```
✅ Teil gefunden: 3001
Menge im Set: 4x
Set-Farbe: Bright Red
Gemessen: RGB(198, 42, 30)
LEGO-Standard: RGB(196, 40, 28)
RGB-Übereinstimmung: 95.2% ✅

⚠️ 3 Farbvarianten im Set
Beste RGB-Übereinstimmung ausgewählt

❌ Teil 3010 nicht in diesem Set enthalten
```

### **Neue Analyse-Tools:**
- **"RGB-Vergleich"**: Vergleicht gemessene RGB mit allen Set-Farben
- **Präzise Farbabstände**: Euklidischer RGB-Abstand in Zahlen
- **Automatische Sortierung**: Beste RGB-Matches zuerst

## API-Integration:

Das System sucht automatisch nach Bounding Box-Informationen in der API-Antwort:

```json
{
  "items": [
    {
      "id": "12345",
      "name": "Brick 2x4", 
      "cage": {"x": 100, "y": 150, "width": 200, "height": 100}
    }
  ]
}
```

**Unterstützte Schlüssel:** `cage`, `bbox`, `bounding_box`, `region`, `box`

## Vorteile der Excel-Integration:

✅ **Exakte LEGO-Farben** aus offizieller Datenbank (140+ Farben)  
✅ **Präzise Hex-zu-RGB Konvertierung** für perfekte Farbwerte  
✅ **LEGO Color IDs** für eindeutige Farbidentifikation  
✅ **Automatische Filterung** ungültiger/spezieller Einträge  
✅ **Robustes Format-System** mit mehreren Fallback-Optionen  
✅ **Semikolon-CSV Support** für Ihre bestehende Dateistruktur