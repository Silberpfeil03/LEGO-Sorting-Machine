import os
import requests
from PIL import Image, ImageTk
from io import BytesIO
import tkinter as tk
from tkinter import ttk
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput
from bs4 import BeautifulSoup
import colorsys
import pandas as pd
import numpy as np
from enum import Enum, auto
import threading
import time

BRICKOGNIZE_API_URL = "https://api.brickognize.com/predict/"
IMAGE_PATH = "/tmp/brick_image.jpg"

# Globale Variablen
lego_colors_df = None
current_set_parts = []  # Speichert die aktuell geladenen Set-Teile

# Kamera initialisieren
picam2 = Picamera2()

# Konfigurationsvorschläge
camera_config = picam2.create_still_configuration()
picam2.configure(camera_config)

# --- LED minimal: SPI (MOSI GPIO10) oder rpi_ws281x / NeoPixel-Erkennung ---
try:
    import spidev
    _LED_BACKEND = 'spi'
    print("LED Backend: spidev (SPI) erkannt")
except Exception:
    try:
        from rpi_ws281x import PixelStrip, Color
        _LED_BACKEND = 'rpi_ws281x'
        print("LED Backend: rpi_ws281x erkannt")
    except Exception:
        try:
            import board, neopixel
            _LED_BACKEND = 'neopixel'
            print("LED Backend: neopixel (CircuitPython) erkannt")
        except Exception:
            PixelStrip = None
            Color = None
            neopixel = None
            _LED_BACKEND = None
            print("Kein LED-Backend gefunden (spidev / rpi_ws281x / neopixel fehlt)")

LED_COUNT = 20
# Für SPI verwenden wir MOSI = GPIO10 (SPI0 MOSI)
SPI_BUS = 0
SPI_DEVICE = 0
SPI_MAX_SPEED_HZ = 3200000  # 3.2 MHz, gängiger Startwert für SPI->WS281x-Encoder
LED_PIN = 18  # weiterhin definiert, falls rpi_ws281x verwendet wird
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_BRIGHTNESS = 128  # 0-255, standard ~50%
LED_INVERT = False
LED_CHANNEL = 0

_LED_STRIP = None
_spi = None
# --- Automatik-Grundgerüst ---

# Beispielhafte Zustände für den Ablauf



def _encode_byte_to_spi(byte):
    """
    Encodiert ein Byte in 4 SPI-Bits pro Bitmap-Bit (Mapping: 1->0b1110, 0->0b1000).
    Rückgabe: list von Bytes (integers)
    """
    mapping = {0: 0b1000, 1: 0b1110}
    bits = []
    for bit in range(7, -1, -1):
        b = (byte >> bit) & 1
        v = mapping[b]
        # append 4 bits to bits list (MSB first)
        bits.extend([(v >> 3) & 1, (v >> 2) & 1, (v >> 1) & 1, v & 1])
    # pack bits into bytes
    out = []
    for i in range(0, len(bits), 8):
        byte_val = 0
        chunk = bits[i:i+8]
        for bit in chunk:
            byte_val = (byte_val << 1) | bit
        out.append(byte_val)
    return out

def _encode_pixel_to_spi(r, g, b):
    """
    WS2812 erwartet meist GRB Reihenfolge; hier verwenden wir GRB.
    Gibt Liste von Bytes zurück.
    """
    # Reorder to GRB
    seq = []
    seq.extend(_encode_byte_to_spi(g))
    seq.extend(_encode_byte_to_spi(r))
    seq.extend(_encode_byte_to_spi(b))
    return seq

def _send_spi_buffer(buffer_bytes):
    """
    Sendet Bytearray über SPI. Erwartet bereits codierte Bytes.
    """
    global _spi
    if _spi is None:
        print("_send_spi_buffer: kein SPI Device geöffnet")
        return
    try:
        # spidev expects list of ints
        _spi.xfer2(list(buffer_bytes))
        # Reset/ latching: sende einige Null-Bytes (>=50us * f) -> hier eine längere Pause per bytes
        # Schicke 50 Null-Bytes als Reset; zusätzlich kurzen Sleep
        _spi.xfer2([0x00] * 50)
    except Exception as e:
        print(f"SPI Sende-Fehler: {e}")

def init_led():
    """
    Initialisiert das passende Backend. Für SPI öffnet spidev(0,0) -> MOSI GPIO10.
    """
    global _LED_STRIP, _spi
    if _LED_STRIP is not None or _spi is not None:
        return
    try:
        if _LED_BACKEND == 'spi':
            _spi = spidev.SpiDev()
            _spi.open(SPI_BUS, SPI_DEVICE)
            _spi.max_speed_hz = SPI_MAX_SPEED_HZ
            _spi.mode = 0
            print(f"SPI initialisiert: bus={SPI_BUS}, device={SPI_DEVICE}, max_speed={SPI_MAX_SPEED_HZ}")
        elif _LED_BACKEND == 'rpi_ws281x':
            _LED_STRIP = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL)
            _LED_STRIP.begin()
            print("rpi_ws281x initialisiert: LEDs =", LED_COUNT, "Pin =", LED_PIN)
        elif _LED_BACKEND == 'neopixel':
            _LED_STRIP = neopixel.NeoPixel(board.D18, LED_COUNT, brightness=LED_BRIGHTNESS/255.0, auto_write=False)
            print("neopixel initialisiert: LEDs =", LED_COUNT, "Pin = board.D18")
        else:
            print("Kein LED-Backend zum Initialisieren")
    except PermissionError as e:
        print("LED Init PermissionError: bitte ggf. SPI aktivieren und passende Rechte prüfen:", e)
        _LED_STRIP = None
        _spi = None
    except Exception as e:
        print(f"LED Init Fehler: {e}")
        _LED_STRIP = None
        _spi = None

def set_ring_white():
    """Schaltet den Ring auf volles Weiß (255,255,255) bei eingestellter Helligkeit. Unterstützt SPI/rpi_ws281x/neopixel."""
    import time
    init_led()
    try:
        if _LED_BACKEND == 'spi' and _spi is not None:
            # Erzeuge SPI-codiertes Bytearray für alle Pixel (GRB)
            buf = []
            for _ in range(LED_COUNT):
                buf.extend(_encode_pixel_to_spi(255, 255, 255))
            _send_spi_buffer(buf)
        elif _LED_BACKEND == 'rpi_ws281x' and _LED_STRIP is not None:
            try:
                _LED_STRIP.setBrightness(LED_BRIGHTNESS)
            except Exception:
                pass
            for i in range(_LED_STRIP.numPixels()):
                _LED_STRIP.setPixelColor(i, Color(255, 255, 255))
            _LED_STRIP.show()
        elif _LED_BACKEND == 'neopixel' and _LED_STRIP is not None:
            try:
                _LED_STRIP.brightness = LED_BRIGHTNESS/255.0
            except Exception:
                pass
            for i in range(LED_COUNT):
                _LED_STRIP[i] = (255, 255, 255)
            _LED_STRIP.show()
        else:
            print("set_ring_white: kein Backend verfügbar")
        # Kurze Pause, damit LEDs stabil leuchten bevor Foto gemacht wird
        time.sleep(0.06)
    except Exception as e:
        print(f"Fehler set_ring_white: {e}")

def clear_ring():
    """Schaltet den Ring aus. Unterstützt SPI/rpi_ws281x/neopixel."""
    init_led()
    try:
        if _LED_BACKEND == 'spi' and _spi is not None:
            # Sende Null-Pixel -> encoded zeros
            buf = []
            for _ in range(LED_COUNT):
                buf.extend(_encode_pixel_to_spi(0, 0, 0))
            _send_spi_buffer(buf)
        elif _LED_BACKEND == 'rpi_ws281x' and _LED_STRIP is not None:
            for i in range(_LED_STRIP.numPixels()):
                _LED_STRIP.setPixelColor(i, Color(0, 0, 0))
            _LED_STRIP.show()
        elif _LED_BACKEND == 'neopixel' and _LED_STRIP is not None:
            _LED_STRIP.fill((0, 0, 0))
            _LED_STRIP.show()
    except Exception as e:
        print(f"Fehler clear_ring: {e}")

def capture_image(image_path):
    """
    Bild mit Picamera2 aufnehmen und lokal speichern.
    Vorher: LED-Ring komplett auf Weiß einschalten (volle Helligkeit).
    """
    try:
        # LED kurz auf Weiß setzen, damit das Motiv beleuchtet ist
        try:
            set_ring_white()
        except Exception as e:
            print(f"LED vor Aufnahme Fehler: {e}")

        picam2.start()
        picam2.capture_file(image_path)
        picam2.stop()

        # Nach der Aufnahme LED wieder ausschalten (optional)
        try:
            clear_ring()
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"Fehler bei der Bildaufnahme: {e}")
        return False

def load_lego_colors():
    """
    Lädt die LEGO-Farbtabelle aus der Excel-Datei.
    Erwartet Spalten: Name, R, G, B (und optional weitere wie ID, Hex, etc.)
    """
    global lego_colors_df
    
    # Pfad zur Excel-Datei im gleichen Ordner
    script_dir = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(script_dir, "lego_colors.xlsx")
    
    try:
        # Versuche verschiedene mögliche Dateinamen und Formate
        possible_names = [
            ("lego_colors.CSV", "csv_semicolon"),  # Ihre spezifische Datei
            ("lego_colors.xlsx", "excel"), 
            ("lego_colors.xls", "excel"), 
            ("LEGO_Colors.xlsx", "excel"),
            ("LegoColors.xlsx", "excel"),
            ("lego_colors.csv", "csv"),
            ("LEGO_Colors.csv", "csv"),
            ("lego_colors_example.csv", "csv")
        ]
        
        for filename, file_type in possible_names:
            full_path = os.path.join(script_dir, filename)
            if os.path.exists(full_path):
                print(f"Lade LEGO-Farbtabelle: {full_path}")
                
                if file_type == "excel":
                    lego_colors_df = pd.read_excel(full_path)
                elif file_type == "csv":
                    lego_colors_df = pd.read_csv(full_path)
                elif file_type == "csv_semicolon":
                    lego_colors_df = pd.read_csv(full_path, delimiter=';')
                
                # Debug: Zeige verfügbare Spalten
                print(f"Verfügbare Spalten: {list(lego_colors_df.columns)}")
                print(f"Anzahl Farben vor Filterung: {len(lego_colors_df)}")
                
                # Filtere spezielle/ungültige Einträge aus
                if 'Name' in lego_colors_df.columns:
                    # Entferne Einträge mit eckigen Klammern oder leeren Namen
                    lego_colors_df = lego_colors_df[
                        (~lego_colors_df['Name'].str.contains(r'^\[.*\]$', na=False, regex=True)) &
                        (lego_colors_df['Name'].notna()) &
                        (lego_colors_df['Name'].str.strip() != '')
                    ]
                    print(f"Anzahl gültiger Farben nach Filterung: {len(lego_colors_df)}")
                
                # Spezielle Behandlung für Ihre CSV-Struktur
                if 'RGB Value' in lego_colors_df.columns and 'Color Name' in lego_colors_df.columns:
                    print("Erkannte LEGO CSV-Struktur mit Hex-Werten")
                    # Konvertiere Hex-Werte zu RGB
                    rgb_values = []
                    for hex_val in lego_colors_df['RGB Value']:
                        try:
                            # Entferne mögliche Leerzeichen und füge # hinzu falls nötig
                            hex_clean = hex_val.strip()
                            if not hex_clean.startswith('#'):
                                hex_clean = '#' + hex_clean
                            
                            # Konvertiere Hex zu RGB
                            r = int(hex_clean[1:3], 16)
                            g = int(hex_clean[3:5], 16) 
                            b = int(hex_clean[5:7], 16)
                            rgb_values.append({'R': r, 'G': g, 'B': b})
                        except (ValueError, IndexError):
                            # Fallback für ungültige Hex-Werte
                            rgb_values.append({'R': 128, 'G': 128, 'B': 128})
                    
                    # Erstelle neue DataFrame mit RGB-Spalten
                    rgb_df = pd.DataFrame(rgb_values)
                    lego_colors_df = pd.concat([lego_colors_df, rgb_df], axis=1)
                    
                    # Umbenennen für Konsistenz
                    lego_colors_df = lego_colors_df.rename(columns={'Color Name': 'Name'})
                    
                else:
                    # Normale Spalten-Validierung für andere Formate
                    required_cols = ['R', 'G', 'B']
                    missing_cols = [col for col in required_cols if col not in lego_colors_df.columns]
                    
                    if missing_cols:
                        # Versuche alternative Spaltennamen
                        col_mapping = {
                            'Red': 'R', 'red': 'R', 'RED': 'R',
                            'Green': 'G', 'green': 'G', 'GREEN': 'G', 
                            'Blue': 'B', 'blue': 'B', 'BLUE': 'B',
                            'Name': 'Name', 'name': 'Name', 'COLOR': 'Name', 'Color': 'Name'
                        }
                        
                        for old_name, new_name in col_mapping.items():
                            if old_name in lego_colors_df.columns:
                                lego_colors_df = lego_colors_df.rename(columns={old_name: new_name})
                        
                        # Nochmal prüfen
                        missing_cols = [col for col in required_cols if col not in lego_colors_df.columns]
                        if missing_cols:
                            print(f"Fehlende Spalten nach Umbenennung: {missing_cols}")
                            lego_colors_df = None
                            return False
                
                return True
        
        print("Keine LEGO-Farbtabelle gefunden. Erstelle Fallback-Tabelle...")
        create_fallback_color_table()
        return True
        
    except Exception as e:
        print(f"Fehler beim Laden der LEGO-Farbtabelle: {e}")
        print("Erstelle Fallback-Tabelle...")
        create_fallback_color_table()
        return True

def create_fallback_color_table():
    """
    Erstellt eine einfache Fallback-Farbtabelle mit häufigen LEGO-Farben.
    """
    global lego_colors_df
    
    # Häufige LEGO-Farben mit ungefähren RGB-Werten
    fallback_colors = [
        {"Name": "Bright Red", "R": 196, "G": 40, "B": 28},
        {"Name": "Bright Blue", "R": 13, "G": 105, "B": 171},
        {"Name": "Bright Yellow", "R": 245, "G": 205, "B": 48},
        {"Name": "Dark Green", "R": 40, "G": 127, "B": 70},
        {"Name": "Bright Orange", "R": 218, "G": 133, "B": 65},
        {"Name": "White", "R": 242, "G": 243, "B": 242},
        {"Name": "Black", "R": 27, "G": 42, "B": 52},
        {"Name": "Dark Stone Grey", "R": 99, "G": 95, "B": 97},
        {"Name": "Medium Stone Grey", "R": 156, "G": 163, "B": 168},
        {"Name": "Bright Purple", "R": 129, "G": 43, "B": 157},
        {"Name": "Dark Red", "R": 114, "G": 22, "B": 17},
        {"Name": "Medium Blue", "R": 97, "G": 175, "B": 255},
        {"Name": "Bright Green", "R": 75, "G": 151, "B": 74},
        {"Name": "Dark Orange", "R": 160, "G": 95, "B": 52},
        {"Name": "Light Bluish Gray", "R": 156, "G": 163, "B": 168},
    ]
    
    lego_colors_df = pd.DataFrame(fallback_colors)
    print(f"Fallback-Farbtabelle mit {len(lego_colors_df)} Farben erstellt")

def find_closest_lego_color(target_rgb):
    """
    Findet die nächstliegende LEGO-Farbe basierend auf RGB-Abstand.
    
    :param target_rgb: Tuple (R, G, B) der zu suchenden Farbe
    :return: Dict mit Name, RGB-Werten und Abstand der nächsten Farbe
    """
    global lego_colors_df
    
    if lego_colors_df is None:
        load_lego_colors()
    
    if lego_colors_df is None or len(lego_colors_df) == 0:
        return {"name": "Unbekannt", "rgb": target_rgb, "distance": float('inf')}
    
    target_r, target_g, target_b = target_rgb
    
    # Berechne Euklidischen Abstand zu allen LEGO-Farben
    distances = []
    for _, row in lego_colors_df.iterrows():
        lego_r, lego_g, lego_b = row['R'], row['G'], row['B']
        
        # Euklidischer Abstand im RGB-Raum
        distance = np.sqrt((target_r - lego_r)**2 + (target_g - lego_g)**2 + (target_b - lego_b)**2)
        distances.append(distance)
    
    # Finde die nächste Farbe
    min_distance_idx = np.argmin(distances)
    closest_color = lego_colors_df.iloc[min_distance_idx]
    
    result = {
        "name": closest_color.get('Name', 'Unbekannt'),
        "rgb": (int(closest_color['R']), int(closest_color['G']), int(closest_color['B'])),
        "distance": distances[min_distance_idx],
        "confidence": max(0, 1 - (distances[min_distance_idx] / 255)),  # Normalisierte Konfidenz
        "color_id": closest_color.get('Color ID', None)  # LEGO Farb-ID falls verfügbar
    }
    
    return result

def get_dominant_color_simple(image_path, region=None):
    """
    Ermittelt die dominante Farbe eines Bildes oder einer Region mit einfachen Mitteln.
    :param image_path: Pfad zum Bild
    :param region: Tuple (x, y, width, height) für die Region, None für ganzes Bild
    :return: Dict mit RGB-Werten und Farbname
    """
    try:
        # Bild laden
        img = Image.open(image_path)
        
        # Region ausschneiden falls angegeben
        if region:
            x, y, width, height = region
            img = img.crop((x, y, x + width, y + height))
        
        # Bild verkleinern für bessere Performance
        img = img.resize((150, 150))
        
        # RGB-Werte aller Pixel sammeln
        pixels = list(img.getdata())
        
        # Nur Pixel mit ausreichendem Kontrast berücksichtigen (um Grau/Weiß zu vermeiden)
        filtered_pixels = []
        for r, g, b in pixels:
            # Überspringe zu helle oder zu dunkle Pixel
            if 30 < r < 225 and 30 < g < 225 and 30 < b < 225:
                # Überspringe Grautöne (geringe Sättigung)
                hsv = colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)
                if hsv[1] > 0.2:  # Mindest-Sättigung
                    filtered_pixels.append((r, g, b))
        
        if not filtered_pixels:
            return {"color": "Unbekannt", "rgb": (128, 128, 128), "confidence": 0}
        
        # Durchschnittliche Farbe berechnen
        avg_r = sum(p[0] for p in filtered_pixels) // len(filtered_pixels)
        avg_g = sum(p[1] for p in filtered_pixels) // len(filtered_pixels)
        avg_b = sum(p[2] for p in filtered_pixels) // len(filtered_pixels)
        
        # LEGO-Farbe basierend auf RGB-Werten finden
        lego_color_info = find_closest_lego_color((avg_r, avg_g, avg_b))
        
        return {
            "color": lego_color_info["name"],
            "rgb": (avg_r, avg_g, avg_b),
            "lego_rgb": lego_color_info["rgb"],
            "confidence": len(filtered_pixels) / len(pixels),
            "color_distance": lego_color_info["distance"],
            "color_confidence": lego_color_info["confidence"]
        }
        
    except Exception as e:
        print(f"Fehler bei der Farberkennung: {e}")
        return {"color": "Unbekannt", "rgb": (128, 128, 128), "confidence": 0}

def classify_color(r, g, b):
    """
    Klassifiziert RGB-Werte in Farbnamen, speziell für LEGO-Steine.
    """
    # Konvertiere zu HSV für bessere Farberkennung
    h, s, v = colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)
    h_deg = h * 360
    s_percent = s * 100
    v_percent = v * 100
    
    # Spezielle Behandlung für Grau/Schwarz/Weiß
    if s_percent < 20:  # Niedrige Sättigung = Grautöne
        if v_percent < 20:
            return "Schwarz"
        elif v_percent > 80:
            return "Weiß"
        else:
            return "Grau"
    
    # Farbklassifikation basierend auf Hue
    if h_deg < 15 or h_deg >= 345:
        return "Rot"
    elif 15 <= h_deg < 45:
        return "Orange"
    elif 45 <= h_deg < 75:
        return "Gelb"
    elif 75 <= h_deg < 150:
        return "Grün"
    elif 150 <= h_deg < 210:
        return "Cyan/Türkis"
    elif 210 <= h_deg < 270:
        return "Blau"
    elif 270 <= h_deg < 315:
        return "Lila/Violett"
    elif 315 <= h_deg < 345:
        return "Pink/Magenta"
    
    return "Unbekannt"

def get_center_region_from_image(image_path, region_size_percent=30):
    """
    Bestimmt die zentrale Region eines Bildes für die Farberkennung.
    :param image_path: Pfad zum Bild
    :param region_size_percent: Prozentsatz der Bildgröße für die zentrale Region
    :return: Tuple (x, y, width, height) für die zentrale Region
    """
    try:
        img = Image.open(image_path)
        width, height = img.size
        
        # Berechne zentrale Region
        region_width = int(width * region_size_percent / 100)
        region_height = int(height * region_size_percent / 100)
        x = (width - region_width) // 2
        y = (height - region_height) // 2
        
        return (x, y, region_width, region_height)
    except Exception as e:
        print(f"Fehler bei der Regionserkennung: {e}")
        return None

def identify_brick(image_path):
    """
    Bild an die API schicken und das Ergebnis zurückgeben.
    Zusätzlich wird die vollständige API-Antwort zurückgegeben.
    """
    try:
        with open(image_path, "rb") as img_file:
            files = {"query_image": (os.path.basename(image_path), img_file, "image/jpeg")}
            headers = {"accept": "application/json"}
            response = requests.post(BRICKOGNIZE_API_URL, headers=headers, files=files)

            if response.status_code == 200:
                result = response.json()
                
                # Debug: Vollständige API-Antwort ausgeben
                print("API-Antwort:")
                print(result)
                
                items = result.get("items", [])
                if not items:
                    return None, None, None, 0, result
                best_item = max(items, key=lambda x: x.get("score", 0))
                brick_id = best_item.get("id")
                brick_name = best_item.get("name")
                img_url = best_item.get("img_url")
                return brick_id, brick_name, img_url, len(items), result
            else:
                print(f"Fehler bei der API-Anfrage: {response.status_code}")
                return None, None, None, 0, None
    except Exception as e:
        print(f"Fehler beim Hochladen des Bildes: {e}")
        return None, None, None, 0, None

def display_image_from_url(img_url, label):
    """
    Lade ein Bild von einer URL und zeige es im GUI-Label an.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bricklink.com/"
        }
        response = requests.get(img_url, headers=headers)
        if response.status_code == 200:
            img_data = BytesIO(response.content)
            img = Image.open(img_data)
            img = img.resize((200, 200))
            photo = ImageTk.PhotoImage(img)
            label.config(image=photo)
            label.image = photo
        else:
            print(f"Fehler beim Herunterladen des Bildes: {response.status_code}")
    except Exception as e:
        print(f"Fehler beim Anzeigen des Bildes: {e}")

def clear_image(label):
    """
    Entferne das Bild vom GUI-Label.
    """
    label.config(image="")
    label.image = None

def update_rgb_comparison_display(detected_rgb, set_rgb=None, match_percentage=0.0, set_color_name=""):
    """
    Aktualisiert die RGB-Vergleichsanzeige.
    
    :param detected_rgb: Erkannte RGB-Werte (R, G, B)
    :param set_rgb: Set-RGB-Werte (R, G, B) oder None
    :param match_percentage: Übereinstimmungsgrad 0-100
    :param set_color_name: Name der Set-Farbe
    """
    if not detected_rgb:
        return
    
    # RGB-Werte Text
    rgb_text = f"Gemessen: RGB{detected_rgb}"
    if set_rgb:
        rgb_text += f"   Set: RGB{set_rgb}"
    
    rgb_values_label.config(text=rgb_text)
    
    # Visueller Farbvergleich
    if set_rgb:
        # Erstelle Bild mit zwei Farbblöcken
        comparison_img = Image.new('RGB', (300, 60))
        
        # Linker Block: Erkannte Farbe
        for x in range(145):
            for y in range(60):
                comparison_img.putpixel((x, y), detected_rgb)
        
        # Mittlere Linie (schwarz)
        for x in range(145, 155):
            for y in range(60):
                comparison_img.putpixel((x, y), (0, 0, 0))
        
        # Rechter Block: Set-Farbe
        for x in range(155, 300):
            for y in range(60):
                comparison_img.putpixel((x, y), set_rgb)
        
        # Text auf Bild schreiben (optional)
        color_photo = ImageTk.PhotoImage(comparison_img)
        color_comparison_label.config(image=color_photo)
        color_comparison_label.image = color_photo
        
        # Match-Anzeige
        match_text = f"Übereinstimmung: {match_percentage:.1f}%"
        if set_color_name:
            match_text += f" ({set_color_name})"
            
        # Bewertung hinzufügen
        if match_percentage >= 90:
            match_text += " ✅ Exzellent"
        elif match_percentage >= 70:
            match_text += " ⚠️ Gut"  
        else:
            match_text += " ❌ Schwach"
            
        rgb_match_label.config(text=match_text)
        
    else:
        # Nur erkannte Farbe zeigen
        single_img = Image.new('RGB', (300, 60), detected_rgb)
        color_photo = ImageTk.PhotoImage(single_img)
        color_comparison_label.config(image=color_photo)
        color_comparison_label.image = color_photo
        rgb_match_label.config(text="Keine Set-Farbe zum Vergleich")

def clear_rgb_display():
    """
    Leert die RGB-Anzeige.
    """
    color_comparison_label.config(image="")
    color_comparison_label.image = None
    rgb_values_label.config(text="RGB-Werte: ---")
    rgb_match_label.config(text="RGB-Übereinstimmung: ---")

def show_last_captured_image():
    """
    Zeige das zuletzt aufgenommene Bild im GUI an.
    """
    try:
        img = Image.open(IMAGE_PATH)
        img = img.resize((200, 200))
        photo = ImageTk.PhotoImage(img)
        last_photo_label.config(image=photo)
        last_photo_label.image = photo
    except Exception as e:
        last_photo_label.config(image="", text="Kein Bild vorhanden")
        last_photo_label.image = None

def capture_and_identify():
    """
    Aufnahme eines Bildes und Senden an die API zur Erkennung.
    Zusätzlich wird eine Farberkennung durchgeführt.
    """
    result_label.config(text="Nehme ein Bild auf...")
    clear_image(image_label)
    clear_rgb_display()

    if capture_image(IMAGE_PATH):
        show_last_captured_image()  # Zeige das letzte Bild an
        
        # Brick-Erkennung über API
        current_part_id, brick_name, img_url, num_detected, api_response = identify_brick(IMAGE_PATH)
        
        # Farberkennung durchführen
        color_info = None
        
        # Überprüfe ob die API Bounding Box-Informationen liefert
        region = None
        if api_response and "items" in api_response and api_response["items"]:
            best_item = max(api_response["items"], key=lambda x: x.get("score", 0))
            
            # Suche nach verschiedenen möglichen Bounding Box-Schlüsseln
            for key in ["bbox", "bounding_box", "cage", "region", "box"]:
                if key in best_item:
                    bbox_data = best_item[key]
                    print(f"Gefundene Bounding Box ({key}): {bbox_data}")
                    
                    # Versuche verschiedene Formate zu parsen
                    if isinstance(bbox_data, dict):
                        # Format: {"x": 10, "y": 20, "width": 100, "height": 80}
                        if all(k in bbox_data for k in ["x", "y", "width", "height"]):
                            region = (bbox_data["x"], bbox_data["y"], bbox_data["width"], bbox_data["height"])
                    elif isinstance(bbox_data, list) and len(bbox_data) == 4:
                        # Format: [x, y, width, height] oder [x1, y1, x2, y2]
                        region = tuple(bbox_data)
                    break
        
        # Wenn keine Bounding Box gefunden, verwende die zentrale Region
        if region is None:
            region = get_center_region_from_image(IMAGE_PATH)
            print(f"Verwende zentrale Region: {region}")
        
        # Farberkennung durchführen
        if region:
            color_info = get_dominant_color_simple(IMAGE_PATH, region)
            print(f"Erkannte Farbe: {color_info}")
        
        # Ergebnis anzeigen
        if current_part_id:
            info = f"ID: {current_part_id}\nName: {brick_name}\n"
            info += f"Erkannte Teile: {num_detected}\n"
            
            if color_info:
                detected_rgb = color_info.get("rgb", (0, 0, 0))
                info += f"Gemessene RGB: {detected_rgb}"
                
                # Vergleiche mit BrickLink-Set falls verfügbar
                if current_set_parts:
                    comparison_result = compare_with_bricklink_by_id_and_color(
                        current_part_id, color_info, current_set_parts
                    )
                    
                    if comparison_result:
                        status = comparison_result['match_status']
                        info += f"\n\nSet-Vergleich:"
                        
                        if status == 'ID_FOUND':
                            match = comparison_result['primary_match']
                            info += f"\n✅ Teil gefunden: {match['part_id']}"
                            info += f"\nMenge im Set: {match['quantity']}x"
                            
                            # RGB-Vergleich anzeigen
                            detected_rgb = match.get('detected_rgb')
                            set_rgb = match.get('lego_rgb')
                            rgb_match = match.get('rgb_match_percentage', 0)
                            set_color_name = match.get('lego_color', '')
                            
                            if detected_rgb and set_rgb:
                                # Aktualisiere die visuelle RGB-Anzeige
                                update_rgb_comparison_display(
                                    detected_rgb, set_rgb, rgb_match, set_color_name
                                )
                            elif detected_rgb:
                                # Nur erkannte Farbe anzeigen
                                update_rgb_comparison_display(detected_rgb)
                                
                            # Zeige Hinweis bei mehreren Varianten
                            if comparison_result['id_variants'] > 1:
                                info += f"\n⚠️ {comparison_result['id_variants']} Farbvarianten im Set"
                                info += f"\nBeste RGB-Übereinstimmung ausgewählt"
                                
                        elif status == 'ID_NOT_IN_SET':
                            info += f"\n❌ {comparison_result['note']}"
                            
                        elif status == 'NO_ID':
                            info += f"\n❓ {comparison_result['note']}"
            
            result_label.config(text=info)
            if img_url:
                display_image_from_url(img_url, image_label)
            else:
                clear_image(image_label)
        else:
            result_text = "Kein Teil erkannt"
            if color_info:
                detected_rgb = color_info.get("rgb", (0, 0, 0))
                result_text += f"\nGemessene RGB: {detected_rgb}"
                # Nur erkannte Farbe anzeigen, ohne Set-Vergleich
                update_rgb_comparison_display(detected_rgb)
            result_label.config(text=result_text)
            clear_image(image_label)
    else:
        result_label.config(text="Bildaufnahme fehlgeschlagen")
        clear_image(image_label)
        last_photo_label.config(image="", text="Kein Bild vorhanden")
        last_photo_label.image = None

def show_start_page():
    frame_start.pack(fill="both", expand=True)
    frame_recognition.pack_forget()

def show_recognition_page():
    frame_start.pack_forget()
    frame_recognition.pack(fill="both", expand=True)

def extract_color_from_part_name(part_name):
    """
    Extrahiert den Farbnamen aus einem BrickLink-Teilenamen.
    BrickLink-Namen haben normalerweise das Format: "Color Name Part Description"
    
    :param part_name: Der vollständige Teilename von BrickLink
    :return: Extrahierter Farbname oder leerer String
    """
    if not part_name:
        return ""
    
    # Häufige BrickLink-Farbnamen (erste Wörter des Teilnamens)
    common_colors = [
        # Grundfarben
        "Black", "White", "Red", "Blue", "Yellow", "Green", "Orange", "Purple",
        "Pink", "Brown", "Gray", "Grey", "Tan", 
        
        # Erweiterte Farbnamen (BrickLink-Stil)
        "Bright Red", "Bright Blue", "Bright Yellow", "Bright Green", "Bright Orange",
        "Dark Red", "Dark Blue", "Dark Yellow", "Dark Green", "Dark Orange", "Dark Gray", "Dark Grey",
        "Light Gray", "Light Grey", "Light Blue", "Light Green", "Light Yellow",
        "Medium Blue", "Medium Green", "Medium Orange",
        
        # Spezielle LEGO-Farben
        "Reddish Brown", "Dark Tan", "Light Bluish Gray", "Dark Bluish Gray",
        "Brick Yellow", "Earth Orange", "Sand Red", "Sand Blue", "Sand Green",
        "Olive Green", "Dark Olive Green",
        
        # Transparente Farben
        "Trans-Clear", "Trans-Red", "Trans-Blue", "Trans-Yellow", "Trans-Green",
        "Trans-Orange", "Trans-Purple", "Trans-Pink", "Trans-Black",
        "Trans-Light Blue", "Trans-Dark Blue", "Trans-Bright Green",
        
        # Chrome und Metallic
        "Chrome Black", "Chrome Blue", "Chrome Green", "Chrome Gold", "Chrome Silver",
        "Metallic Silver", "Metallic Gold", "Pearl Gold", "Pearl White",
        
        # Modulex und spezielle Serien
        "Modulex White", "Modulex Black", "Modulex Red", "Modulex Blue",
        "Duplo Green", "Fabuland Orange"
    ]
    
    # Sortiere nach Länge (längste zuerst), um "Dark Blue" vor "Blue" zu finden
    common_colors.sort(key=len, reverse=True)
    
    # Suche nach bekannten Farbmustern am Anfang des Namens
    part_name_lower = part_name.lower()
    
    for color in common_colors:
        color_lower = color.lower()
        if part_name_lower.startswith(color_lower + " ") or part_name_lower == color_lower:
            return color
    
    # Fallback: Versuche das erste Wort zu extrahieren (könnte eine unbekannte Farbe sein)
    first_word = part_name.split()[0] if part_name.split() else ""
    
    # Prüfe ob das erste Wort wie ein Farbname aussieht (keine Zahlen, keine Sonderzeichen)
    if first_word and first_word.isalpha() and len(first_word) > 2:
        return first_word
    
    return ""

def match_bricklink_color_to_lego(bricklink_color):
    """
    Versucht einen BrickLink-Farbnamen mit einem LEGO-Farbnamen aus der CSV zu matchen.
    
    :param bricklink_color: Farbname von BrickLink
    :return: Matching LEGO-Farbname oder ursprünglicher Name
    """
    global lego_colors_df
    
    if not bricklink_color or lego_colors_df is None:
        return bricklink_color
    
    # Direkte Übereinstimmung suchen
    bricklink_lower = bricklink_color.lower().strip()
    
    for _, row in lego_colors_df.iterrows():
        lego_name = row.get('Name', '').lower().strip()
        if lego_name == bricklink_lower:
            return row.get('Name', bricklink_color)
    
    # Fuzzy Matching - suche nach Teilübereinstimmungen
    for _, row in lego_colors_df.iterrows():
        lego_name = row.get('Name', '').lower().strip()
        
        # Verschiedene Matching-Strategien
        if (bricklink_lower in lego_name or 
            lego_name in bricklink_lower or
            bricklink_lower.replace(' ', '') == lego_name.replace(' ', '')):
            return row.get('Name', bricklink_color)
    
    # Spezielle Mappings für häufige Unterschiede
    color_mappings = {
        'gray': 'light bluish gray',
        'grey': 'light bluish gray', 
        'dark gray': 'dark bluish gray',
        'dark grey': 'dark bluish gray',
        'brick yellow': 'yellow',
        'bright red': 'red',
        'bright blue': 'blue',
        'bright yellow': 'yellow',
        'bright green': 'green',
        'bright orange': 'orange'
    }
    
    mapped_color = color_mappings.get(bricklink_lower)
    if mapped_color:
        # Suche nach der gemappten Farbe
        for _, row in lego_colors_df.iterrows():
            if row.get('Name', '').lower().strip() == mapped_color:
                return row.get('Name', bricklink_color)
    
    return bricklink_color

def get_parts_from_set(set_id):
    """
    Extrahiert Teile-IDs, Mengen, Namen und Bild-URLs eines LEGO-Sets von der alten BrickLink-Inventarseite.
    :param set_id: Die Setnummer als String, z.B. "4723" oder "4723-1"
    :return: Liste von Dicts mit keys: id, qty, img_url, name, color_name
    """
    if "-" not in set_id:
        set_id = f"{set_id}-1"
    url = f"https://www.bricklink.com/catalogItemInv.asp?S={set_id}"
    print(f"Rufe URL auf: {url}")
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        print(f"HTTP Status: {response.status_code}")
        if response.status_code != 200:
            print("Fehler beim Laden der Seite.")
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        parts = []
        
        # Debug: Analysiere die Tabellenstruktur
        debug_rows = soup.find_all("tr", class_="IV_ITEM")[:2]  # Erste 2 Zeilen für Debug
        for i, debug_row in enumerate(debug_rows):
            debug_cols = debug_row.find_all("td")
            print(f"\nDebug Row {i+1}: {len(debug_cols)} Spalten gefunden")
            for j, col in enumerate(debug_cols):
                col_text = col.get_text(strip=True)[:50]  # Erste 50 Zeichen
                col_links = [a.get('href', '') for a in col.find_all('a')]
                print(f"  Spalte {j}: '{col_text}' | Links: {col_links[:2]}")
        
        for row in soup.find_all("tr", class_="IV_ITEM"):
            cols = row.find_all("td")
            if len(cols) >= 3:  # Mindestens 3 Spalten (flexibler gemacht)
                # Bild-URL extrahieren
                img_tag = cols[0].find("img")
                img_url = img_tag["src"] if img_tag else ""
                if img_url and img_url.startswith("//"):
                    img_url = "https:" + img_url
                
                # Anzahl extrahieren
                qty = cols[1].text.strip().replace("\xa0", "")
                
                # Teile-ID extrahieren (flexibel in verschiedenen Spalten suchen)
                part_id = ""
                part_name = ""
                color_name = ""
                
                # Suche Teil-ID in den Spalten (normalerweise Spalte 2 oder 3)
                for col_idx in range(2, min(len(cols), 5)):
                    part_link = cols[col_idx].find("a")
                    if (part_link 
                        and part_link.text.strip() 
                        and "?P=" in part_link.get("href", "")):
                        part_id = part_link.text.strip()
                        break
                
                if part_id:  # Nur wenn eine gültige Teil-ID gefunden wurde
                    # Versuche Namen aus verschiedenen Spalten zu extrahieren
                    for col_idx in range(len(cols)):
                        if col_idx == 0 or col_idx == 1:  # Überspringe Bild und Anzahl
                            continue
                            
                        col_text = cols[col_idx].get_text(strip=True)
                        
                        # Überspringe die Spalte mit der Teil-ID
                        if col_text == part_id:
                            continue
                            
                        # Überspringe sehr kurze oder offensichtlich irrelevante Texte
                        if (len(col_text) > 5 
                            and col_text not in ['Catalog', 'Info', 'Buy', 'Sell']
                            and not col_text.isdigit()):
                            
                            part_name = col_text
                            # Versuche Farbname zu extrahieren
                            color_name = extract_color_from_part_name(part_name)
                            
                            # Wenn wir einen vernünftigen Namen gefunden haben, breche ab
                            if color_name or len(part_name) > 10:
                                break
                    
                    # Alternative: Versuche Farbinformation aus dem img alt-Text zu extrahieren
                    if not color_name and img_tag:
                        img_alt = img_tag.get('alt', '')
                        if img_alt:
                            color_name = extract_color_from_part_name(img_alt)
                    
                    # Debug-Output für problematische Fälle
                    if not part_name or part_name in ['Catalog', 'Info']:
                        print(f"Debug: Problematischer Teil-Name für ID {part_id}")
                        for idx, col in enumerate(cols):
                            print(f"  Spalte {idx}: '{col.get_text(strip=True)[:30]}'")
                    
                    parts.append({
                        "id": part_id, 
                        "qty": qty, 
                        "img_url": img_url,
                        "name": part_name if part_name not in ['Catalog', 'Info', ''] else f"Teil {part_id}",
                        "color_name": color_name
                    })
        print(f"Insgesamt gefunden: {len(parts)} Teile")
        return parts
    except Exception as e:
        print(f"Fehler: {e}")
        return []

def get_set_image_url(set_id):
    """
    Extrahiert die Bild-URL des Sets von der alten BrickLink-Inventarseite.
    :param set_id: Die Setnummer als String, z.B. "4723" oder "4723-1"
    :return: Bild-URL als String oder ""
    """
    if "-" not in set_id:
        set_id = f"{set_id}-1"
    url = f"https://www.bricklink.com/catalogItemInv.asp?S={set_id}"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code != 200:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        # Suche das Set-Bild direkt unter dem Titel
        img = soup.find("img", {"alt": lambda x: x and x.startswith("Set No:")})
        if img:
            img_url = img.get("src", "")
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):

                img_url = "https://www.bricklink.com" + img_url
            return img_url
        return ""
    except Exception as e:
        print(f"Fehler beim Laden des Set-Bildes: {e}")
        return ""

# Beispielaufruf:
if __name__ == "__main__":
    setnummer = "4723"
    teile = get_parts_from_set(setnummer)
    print("Teile-IDs:", teile)

# --- GUI erstellen ---
root = tk.Tk()
root.title("Brick Recognizer")

# Variable für die Setnummer
set_number = tk.StringVar(root)

# --- Startseite ---
frame_start = ttk.Frame(root)
frame_start.pack(fill="both", expand=True)

label_intro = ttk.Label(frame_start, text="Bitte Setnummer eingeben:", font=("Helvetica", 12))
label_intro.pack(pady=10)

entry_set_number = ttk.Entry(frame_start, textvariable=set_number, font=("Helvetica", 12))
entry_set_number.pack(pady=5)

button_to_recognition = ttk.Button(
    frame_start, text="Zur Bilderkennung", command=show_recognition_page
)
button_to_recognition.pack(pady=20)

# Label für Set-Infos
set_info_label = ttk.Label(frame_start, text="", font=("Helvetica", 10), wraplength=350, justify="left")
set_info_label.pack(pady=5)

set_image_label = ttk.Label(frame_start)
set_image_label.pack(pady=5)

def search_set():
    global current_set_parts
    
    set_id = set_number.get().strip()
    if not set_id:
        set_info_label.config(text="Bitte eine Setnummer eingeben.")
        return
    parts = get_parts_from_set(set_id)
    current_set_parts = parts  # Speichere für Farbvergleiche
    # Setnamen auslesen (optional, aus Seitentitel)
    url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?S={set_id}#T=I"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.find("title").text if soup.find("title") else ""
        set_name = title.split("|")[0].strip() if "|" in title else title.strip()
    except Exception:
        set_name = ""
    if parts:
        info = f"Setname: {set_name}\nTeile (max. 5):\n"
        for part in parts[:5]:
            part_info = f"ID: {part['id']}, Menge: {part['qty']}"
            
            # Zeige Farbe und Namen falls verfügbar
            if part.get('color_name'):
                # Versuche BrickLink-Farbe mit LEGO-Farbe zu matchen
                matched_color = match_bricklink_color_to_lego(part['color_name'])
                part_info += f"\nFarbe: {matched_color}"
            
            if part.get('name'):
                # Kürze sehr lange Namen
                display_name = part['name'][:50] + "..." if len(part['name']) > 50 else part['name']
                part_info += f"\nName: {display_name}"
            
            info += part_info + "\n\n"
            
        if len(parts) > 5:
            info += f"... und {len(parts) - 5} weitere Teile"
        
        set_info_label.config(text=info)
        # Optional: Zeige das erste Bild im GUI
        if parts[0]["img_url"]:
            display_image_from_url(parts[0]["img_url"], set_image_label)
        else:
            clear_image(set_image_label)
        set_img_url = get_set_image_url(set_id)
        if set_img_url:
            display_image_from_url(set_img_url, set_image_label)
        else:
            clear_image(set_image_label)
    else:
        set_info_label.config(text="Keine Teile gefunden oder ungültige Setnummer.")

# Button zum Suchen des Sets
button_search_set = ttk.Button(
    frame_start, text="Set suchen", command=search_set
)
button_search_set.pack(pady=5)

# --- Bilderkennungsseite ---
frame_recognition = ttk.Frame(root)

frame_controls = ttk.Frame(frame_recognition)
frame_controls.pack(pady=10)

frame_result = ttk.Frame(frame_recognition)
frame_result.pack(pady=10)

capture_button = ttk.Button(frame_controls, text="Bild aufnehmen", command=capture_and_identify)
capture_button.pack(pady=10)

def calculate_rgb_distance(rgb1, rgb2):
    """
    Berechnet den Euklidischen Abstand zwischen zwei RGB-Werten.
    
    :param rgb1: Tuple (R, G, B) 
    :param rgb2: Tuple (R, G, B)
    :return: Abstand als Float
    """
    if not rgb1 or not rgb2:
        return float('inf')
    
    r1, g1, b1 = rgb1
    r2, g2, b2 = rgb2
    
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5

def get_lego_rgb_for_color_name(color_name):
    """
    Findet die RGB-Werte einer LEGO-Farbe in der CSV-Datenbank.
    
    :param color_name: Name der LEGO-Farbe
    :return: RGB-Tuple oder None
    """
    global lego_colors_df
    
    if lego_colors_df is None or not color_name:
        return None
    
    # Suche exakte Übereinstimmung
    match = lego_colors_df[lego_colors_df['Name'].str.lower() == color_name.lower()]
    
    if not match.empty:
        row = match.iloc[0]
        return (int(row['R']), int(row['G']), int(row['B']))
    
    return None

def compare_with_bricklink_by_id_and_color(detected_part_id, detected_color_info, bricklink_parts):
    """
    1. Sucht Teil-ID im Set
    2. Bei mehreren Farbvarianten: Vergleicht gemessene RGB mit LEGO-Standard-RGB
    
    :param detected_part_id: Erkannte Teil-ID von der Brickognize API
    :param detected_color_info: Dict mit gemessenen RGB-Werten  
    :param bricklink_parts: Liste der Teile vom Set mit Farbinformationen
    :return: Dict mit Vergleichsergebnissen
    """
    if not bricklink_parts:
        return None
    
    # Gemessene RGB-Werte aus der Kamera
    detected_rgb = detected_color_info.get('rgb', (0, 0, 0)) if detected_color_info else (0, 0, 0)
    
    # 1. Suche nach exakter Teil-ID (ohne Berücksichtigung der Farbe)
    id_matches = []
    other_parts = []
    
    for part in bricklink_parts:
        part_id = part.get('id', '')
        part_color_name = part.get('color_name', '')
        
        if detected_part_id and part_id == detected_part_id:
            # Für jede Farbvariante: Berechne RGB-Abstand
            lego_color_name = match_bricklink_color_to_lego(part_color_name) if part_color_name else ''
            lego_standard_rgb = get_lego_rgb_for_color_name(lego_color_name)
            
            rgb_distance = float('inf')
            rgb_match_percentage = 0.0
            
            if lego_standard_rgb and detected_rgb:
                rgb_distance = calculate_rgb_distance(detected_rgb, lego_standard_rgb)
                # Normalisiere Abstand zu Prozent (0-100%, wobei 100% = perfekt)
                max_distance = 441.67  # Maximaler RGB-Abstand (sqrt(255²+255²+255²))
                rgb_match_percentage = max(0, (1 - rgb_distance / max_distance)) * 100
            
            id_matches.append({
                'part_id': part_id,
                'part_name': part.get('name', ''),
                'bricklink_color': part_color_name,
                'lego_color': lego_color_name,
                'lego_rgb': lego_standard_rgb,
                'detected_rgb': detected_rgb,
                'rgb_distance': rgb_distance,
                'rgb_match_percentage': rgb_match_percentage,
                'quantity': part.get('qty', '0'),
                'match_type': 'ID-Match'
            })
        else:
            # Sammle andere Teile für Fallback
            other_parts.append({
                'part_id': part_id,
                'part_name': part.get('name', ''),
                'bricklink_color': part_color_name,
                'lego_color': match_bricklink_color_to_lego(part_color_name) if part_color_name else '',
                'quantity': part.get('qty', '0'),
                'match_type': 'Anderes Teil'
            })
    
    # Ergebnis-Prioritäten:
    if id_matches:
        # 1. Priorität: ID-Matches, sortiert nach RGB-Ähnlichkeit
        id_matches.sort(key=lambda x: x['rgb_match_percentage'], reverse=True)
        
        primary_match = id_matches[0]
        result = {
            'detected_part_id': detected_part_id,
            'detected_rgb': detected_rgb,
            'match_status': 'ID_FOUND',
            'primary_match': primary_match,
            'id_variants': len(id_matches),
            'matching_parts': id_matches,
            'total_matches': len(id_matches)
        }
        
        # Füge Hinweis hinzu bei mehreren Farbvarianten
        if len(id_matches) > 1:
            result['color_note'] = f"Teil in {len(id_matches)} Farben im Set vorhanden"
            result['best_rgb_match'] = primary_match['rgb_match_percentage']
            
        return result
        
    elif detected_part_id:
        # 2. Priorität: ID erkannt, aber nicht im Set vorhanden
        return {
            'detected_part_id': detected_part_id,
            'detected_rgb': detected_rgb,
            'match_status': 'ID_NOT_IN_SET',
            'primary_match': None,
            'matching_parts': [],
            'total_matches': 0,
            'note': f"Teil {detected_part_id} nicht in diesem Set enthalten"
        }
        
    else:
        # 3. Priorität: Keine ID erkannt, zeige Set-Übersicht
        return {
            'detected_part_id': None,
            'detected_rgb': detected_rgb,
            'match_status': 'NO_ID',
            'primary_match': None,
            'matching_parts': other_parts[:5],  # Zeige ersten 5 Teile als Referenz
            'total_matches': 0,
            'note': 'Keine Teil-ID erkannt. Überprüfen Sie die Erkennung.'
        }

def compare_colors_with_bricklink(detected_color_info, bricklink_parts):
    """
    Legacy-Funktion für reinen Farbvergleich (Rückwärtskompatibilität).
    """
    return compare_with_bricklink_by_id_and_color(None, detected_color_info, bricklink_parts)

def calculate_color_name_similarity(color1, color2):
    """
    Berechnet die Ähnlichkeit zwischen zwei Farbnamen (0.0 - 1.0).
    """
    if not color1 or not color2:
        return 0.0
    
    color1_lower = color1.lower().strip()
    color2_lower = color2.lower().strip()
    
    # Exakte Übereinstimmung
    if color1_lower == color2_lower:
        return 1.0
    
    # Teilweise Übereinstimmung
    if color1_lower in color2_lower or color2_lower in color1_lower:
        return 0.8
    
    # Wortweise Vergleich
    words1 = set(color1_lower.split())
    words2 = set(color2_lower.split())
    
    if words1 & words2:  # Gemeinsame Wörter
        return len(words1 & words2) / len(words1 | words2)
    
    return 0.0

# analyze_color_only Funktion entfernt

# compare_with_set Funktion entfernt

# Alle Debug-Funktionen entfernt

# --- Automatik: Zustandsmaschine (Grundgerüst) ---

class AutomationState(Enum):
    INIT = auto()
    WARTEN_AUF_TEIL = auto()
    BILD_AUFNEHMEN = auto()
    ERKENNEN = auto()
    SORTIEREN = auto()
    FERTIG = auto()


class AutomationController:
    """
    Einfaches Grundgerüst einer Zustandsmaschine für den Maschinenablauf.
    Keine Logik implementiert – nur die Struktur und Hooks.
    """

    def __init__(self, tk_root: tk.Tk):
        self.root = tk_root
        self.state = AutomationState.INIT
        self.running = False
        self.thread = None
        # Platzhalter: GPIO-Setup (optional)
        self._gpio_initialized = False
        # Cache für Teile aus ausgewählten Sets (Aggregation)
        self.set_numbers: list[str] = []
        self.cached_set_parts: list[dict] = []
        # Tracking für gefundene Teile: key=(part_id, color_name), value=gefundene_anzahl
        self.found_parts: dict[tuple, int] = {}
        # Automatik-Fenster
        self.automation_window = None
        self.progress_label = None
        self.percentage_label = None
        self.set_name_label = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.state = AutomationState.INIT
        # Erstelle Automatik-Fenster
        self._create_automation_window()
        # Nicht den Tk-Hauptthread blockieren: separater Thread
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        # Schließe Automatik-Fenster
        if self.automation_window:
            try:
                self.automation_window.destroy()
            except Exception:
                pass
            self.automation_window = None

    def _run_loop(self):
        """Hauptschleife der Automatik. Ruft periodisch tick() auf."""
        while self.running:
            self.tick()
            time.sleep(0.05)  # 20 Hz Takt, anpassbar

    def tick(self):
        """
        Ein einzelner Schritt der Zustandsmaschine. Hier können später
        Sensorabfragen, Zeitbedingungen und Aktionen eingefügt werden.
        """
        global current_set_parts
        # Beispielhafte Konsolen-Ausgabe zur Sichtbarkeit
        # print(f"[AUTOMATION] State: {self.state.name}")

        match self.state:
            case AutomationState.INIT:
                # TODO: Setup, Sensor-Reset, LED-Status, Home-Fahrt
                self._ensure_gpio()
                # Sets abfragen (ein oder mehrere, kommasepariert)
                try:
                    import tkinter.simpledialog as simpledialog
                    user_input = simpledialog.askstring(
                        "Sets wählen",
                        "Welche Setnummer(n) sollen sortiert werden?\nMehrere mit Komma trennen, z.B. 4723-1,31058",
                        parent=self.root
                    )
                except Exception:
                    user_input = None
                if user_input:
                    # Normalisiere und lade Teilelisten
                    self.set_numbers = [s.strip() for s in user_input.split(',') if s.strip()]
                    aggregated_parts = []
                    for sn in self.set_numbers:
                        parts = get_parts_from_set(sn)
                        if parts:
                            aggregated_parts.extend(parts)
                    # Optional: Duplikate zusammenführen (gleiche id+color)
                    merged: dict[tuple, dict] = {}
                    for p in aggregated_parts:
                        key = (p.get('id'), p.get('color_name'))
                        if key in merged:
                            # Menge aufsummieren, falls numerisch
                            try:
                                merged[key]['qty'] = str(int(merged[key]['qty']) + int(str(p.get('qty','0')).strip() or '0'))
                            except Exception:
                                pass
                        else:
                            merged[key] = p.copy()
                    self.cached_set_parts = list(merged.values())
                    # Globale Referenz für bestehende Vergleichslogik aktualisieren
                    try:
                        current_set_parts = self.cached_set_parts
                    except Exception:
                        pass
                    # Kurze Info ins GUI
                    set_info = f"Automatik: {len(self.cached_set_parts)} Teile aus {len(self.set_numbers)} Set(s) geladen"
                    try:
                        result_label.config(text=set_info)
                    except Exception:
                        pass
                    # Initialisiere Tracking und zeige Teileliste
                    self.found_parts = {}
                    self._update_parts_list()
                else:
                    # Falls keine Eingabe: benutze bereits geladene globale Teile (falls vorhanden)
                    try:
                        self.cached_set_parts = current_set_parts or []
                    except Exception:
                        self.cached_set_parts = []
                # Übergang ins Warten
                self.state = AutomationState.WARTEN_AUF_TEIL

            case AutomationState.WARTEN_AUF_TEIL:
                # TODO: ERkennen von Teileinwurf mit Kamera/ Bild änderung
                # Beispiel (Platzhalter): if self._read_part_present():
                #     self.state = AutomationState.BILD_AUFNEHMEN
                pass

            case AutomationState.BILD_AUFNEHMEN:
                # Bild aufnehmen; bei Erfolg weiter zur Erkennung, sonst zurück warten
                result_label.config(text="Automatik: Nehme Bild auf...")
                clear_image(image_label)
                clear_rgb_display()
                if capture_image(IMAGE_PATH):
                    show_last_captured_image()
                    self.state = AutomationState.ERKENNEN
                else:
                    result_label.config(text="Automatik: Bildaufnahme fehlgeschlagen – warte auf Teil")
                    self.state = AutomationState.WARTEN_AUF_TEIL

            case AutomationState.ERKENNEN:
                # Erkennung durchführen (Brick + Farbe) und GUI aktualisieren
                result_label.config(text="Automatik: Erkenne Teil...")
                current_part_id, brick_name, img_url, num_detected, api_response = identify_brick(IMAGE_PATH)
                color_info = None
                region = None
                if api_response and "items" in api_response and api_response["items"]:
                    best_item = max(api_response["items"], key=lambda x: x.get("score", 0))
                    for key in ["bbox", "bounding_box", "cage", "region", "box"]:
                        if key in best_item:
                            bbox_data = best_item[key]
                            if isinstance(bbox_data, dict):
                                if all(k in bbox_data for k in ["x", "y", "width", "height"]):
                                    region = (bbox_data["x"], bbox_data["y"], bbox_data["width"], bbox_data["height"])
                            elif isinstance(bbox_data, list) and len(bbox_data) == 4:
                                region = tuple(bbox_data)
                            break
                if region is None:
                    region = get_center_region_from_image(IMAGE_PATH)
                if region:
                    color_info = get_dominant_color_simple(IMAGE_PATH, region)
                # Ergebnisse anzeigen
                if current_part_id:
                    info = f"ID: {current_part_id}\nName: {brick_name}\nErkannte Teile: {num_detected}"
                    if color_info:
                        detected_rgb = color_info.get("rgb", (0, 0, 0))
                        info += f"\nGemessene RGB: {detected_rgb}"
                        if current_set_parts:
                            comparison_result = compare_with_bricklink_by_id_and_color(current_part_id, color_info, current_set_parts)
                            if comparison_result and comparison_result['match_status'] == 'ID_FOUND':
                                match = comparison_result['primary_match']
                                set_rgb = match.get('lego_rgb')
                                rgb_match = match.get('rgb_match_percentage', 0)
                                set_color_name = match.get('lego_color', '')
                                update_rgb_comparison_display(detected_rgb, set_rgb, rgb_match, set_color_name)
                                # Tracking: Markiere Teil als gefunden
                                part_key = (current_part_id, match.get('bricklink_color', ''))
                                self.found_parts[part_key] = self.found_parts.get(part_key, 0) + 1
                                self._update_parts_list()
                    result_label.config(text=info)
                    if img_url:
                        display_image_from_url(img_url, image_label)
                    else:
                        clear_image(image_label)
                else:
                    result_text = "Automatik: Kein Teil erkannt"
                    if color_info:
                        detected_rgb = color_info.get("rgb", (0, 0, 0))
                        result_text += f"\nGemessene RGB: {detected_rgb}"
                        update_rgb_comparison_display(detected_rgb)
                    result_label.config(text=result_text)
                    clear_image(image_label)
                # Weiter zum Sortieren
                self.state = AutomationState.SORTIEREN

            case AutomationState.SORTIEREN:
                # TODO: Aktor/Servo/Relais ansteuern basierend auf Erkennung
                # Abschlusszustand
                self.state = AutomationState.FERTIG

            case AutomationState.FERTIG:
                # TODO: Abschluss, optional zurück nach WARTEN_AUF_TEIL für kontinuierlichen Betrieb
                # Hier stoppen wir vorerst die Schleife
                self.stop()

            case _:
                # Unerwarteter Zustand -> stoppen
                self.stop()

    # --- GPIO Platzhalter-Methoden ---
    def _ensure_gpio(self):
        """Initialisiert GPIOs (optional). Hier nur Platzhalter – keine echte Logik."""
        if self._gpio_initialized:
            return
        try:
            # Beispiel: import RPi.GPIO as GPIO
            # GPIO.setmode(GPIO.BCM)
            # GPIO.setup(23, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Sensor/Schalter
            self._gpio_initialized = True
        except Exception:
            # Auf Nicht-Raspi-Systemen oder ohne Bibliothek einfach ignorieren
            self._gpio_initialized = False

    def _read_part_present(self) -> bool:
        """
        Liest ein Eingangssignal (Platzhalter). Später mit RPi.GPIO ersetzen.
        """
        # Beispiel: return GPIO.input(23) == GPIO.LOW
        return False

    def _create_automation_window(self):
        """Erstellt das Fullscreen-Fenster für den Automatik-Modus."""
        if self.automation_window:
            return
        
        self.automation_window = tk.Toplevel(self.root)
        self.automation_window.title("Automatik-Sortierung")
        self.automation_window.attributes('-fullscreen', True)
        self.automation_window.configure(bg='#2b2b2b')
        
        # Escape-Taste zum Beenden
        self.automation_window.bind('<Escape>', lambda e: self.stop())
        
        # Header
        header_frame = tk.Frame(self.automation_window, bg='#1e1e1e', height=100)
        header_frame.pack(fill='x', side='top')
        header_frame.pack_propagate(False)
        
        title_label = tk.Label(
            header_frame,
            text="🔧 Automatische Sortierung",
            font=('Helvetica', 32, 'bold'),
            bg='#1e1e1e',
            fg='white'
        )
        title_label.pack(pady=25)
        
        # Hauptbereich zentriert
        main_frame = tk.Frame(self.automation_window, bg='#2b2b2b')
        main_frame.pack(fill='both', expand=True)
        
        # Zentrierter Content
        center_frame = tk.Frame(main_frame, bg='#2b2b2b')
        center_frame.place(relx=0.5, rely=0.5, anchor='center')
        
        # Set-Name
        self.set_name_label = tk.Label(
            center_frame,
            text="Lade Set-Informationen...",
            font=('Helvetica', 20),
            bg='#2b2b2b',
            fg='#aaaaaa'
        )
        self.set_name_label.pack(pady=(0, 40))
        
        # Fortschritts-Anzeige (große Zahlen)
        self.progress_label = tk.Label(
            center_frame,
            text="0 / 0",
            font=('Helvetica', 80, 'bold'),
            bg='#2b2b2b',
            fg='#4caf50'
        )
        self.progress_label.pack(pady=20)
        
        # Text "Teile gefunden"
        progress_text_label = tk.Label(
            center_frame,
            text="Teile gefunden",
            font=('Helvetica', 24),
            bg='#2b2b2b',
            fg='#888888'
        )
        progress_text_label.pack(pady=(0, 30))
        
        # Prozentanzeige
        self.percentage_label = tk.Label(
            center_frame,
            text="0%",
            font=('Helvetica', 48, 'bold'),
            bg='#2b2b2b',
            fg='#2196f3'
        )
        self.percentage_label.pack(pady=20)
        
        # Footer mit Hinweisen
        footer_frame = tk.Frame(self.automation_window, bg='#1e1e1e', height=60)
        footer_frame.pack(fill='x', side='bottom')
        footer_frame.pack_propagate(False)
        
        hint_label = tk.Label(
            footer_frame,
            text="ESC = Beenden  |  Teile werden automatisch erkannt",
            font=('Helvetica', 14),
            bg='#1e1e1e',
            fg='#888888'
        )
        hint_label.pack(pady=15)

    def _update_parts_list(self):
        """Aktualisiert die Fortschrittsanzeige im Automatik-Fenster."""
        if not self.progress_label or not self.percentage_label:
            return
        
        if not self.cached_set_parts:
            return
        
        # Berechne Gesamtanzahl benötigter Teile (mit Mehrfachzählung)
        total_needed = 0
        for part in self.cached_set_parts:
            try:
                qty = int(part.get('qty', '1'))
                total_needed += qty
            except Exception:
                total_needed += 1
        
        # Zähle gefundene Teile (Summe aller gefundenen)
        total_found = sum(self.found_parts.values())
        
        # Berechne Prozentsatz
        percentage = int((total_found / total_needed * 100)) if total_needed > 0 else 0
        
        # Aktualisiere Labels
        try:
            self.progress_label.config(text=f"{total_found} / {total_needed}")
            self.percentage_label.config(text=f"{percentage}%")
            
            # Farbe ändern je nach Fortschritt
            if percentage == 100:
                self.progress_label.config(fg='#4caf50')  # Grün
                self.percentage_label.config(fg='#4caf50')
            elif percentage > 0:
                self.progress_label.config(fg='#2196f3')  # Blau
                self.percentage_label.config(fg='#2196f3')
            else:
                self.progress_label.config(fg='#888888')  # Grau
                self.percentage_label.config(fg='#888888')
            
            # Set-Namen aktualisieren (nur beim ersten Mal)
            if self.set_name_label and self.set_numbers:
                set_text = f"Set(s): {', '.join(self.set_numbers)}"
                self.set_name_label.config(text=set_text)
        except Exception as e:
            print(f"Fehler beim Aktualisieren der Anzeige: {e}")

result_label = ttk.Label(frame_result, text="Ergebnisse werden hier angezeigt...", font=("Helvetica", 12))
result_label.pack()

image_label = ttk.Label(frame_result)
image_label.pack()

# RGB-Vergleich Anzeige
rgb_frame = ttk.LabelFrame(frame_result, text="RGB-Vergleich", padding="10")
rgb_frame.pack(pady=10, fill="x")

# RGB-Werte Anzeige
rgb_values_label = ttk.Label(rgb_frame, text="RGB-Werte: ---", font=("Helvetica", 10))
rgb_values_label.pack()

# Visueller Farbvergleich (zwei Farbblöcke nebeneinander)
color_comparison_label = ttk.Label(rgb_frame)
color_comparison_label.pack(pady=5)

# RGB-Abstand und Match-Prozent
rgb_match_label = ttk.Label(rgb_frame, text="RGB-Übereinstimmung: ---", font=("Helvetica", 9))
rgb_match_label.pack()

# Label für das zuletzt aufgenommene Bild
last_image_label = ttk.Label(frame_result, text="Letztes aufgenommenes Bild:")
last_image_label.pack()
last_photo_label = ttk.Label(frame_result)
last_photo_label.pack()

button_back = ttk.Button(
    frame_recognition, text="Zurück zur Startseite", command=show_start_page
)
button_back.pack(pady=10)

# Automatik-Controller instanziieren und Button zum Starten hinzufügen
automation = AutomationController(root)

automation_button = ttk.Button(frame_controls, text="Automatik starten", command=automation.start)
automation_button.pack(pady=5)

# Kamera vorbereiten
try:
    picam2.start_preview()
except Exception as e:
    print(f"Fehler beim Start der Kamera-Vorschau: {e}")

# LEGO-Farbtabelle laden
print("Initialisiere LEGO-Farberkennung...")
load_lego_colors()

# LED-Initialisierung / Diagnose direkt nach Laden
try:
    init_led()
    if _LED_STRIP is None:
        print("LED-Strip nicht initialisiert. Prüfe Installation/Permissions (sudo) und Hardware-Wiring an GPIO18.")
    else:
        print("LED-Strip initialisiert (bereit).")
except Exception as e:
    print(f"LED Init nach Farbtabelle fehlgeschlagen: {e}")

# Starte mit der Startseite
show_start_page()

# GUI starten
root.mainloop()

# Kamera stoppen
picam2.close()