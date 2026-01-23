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
import functools

# GPIO Import am Anfang (wie in sensor_test.py)
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
    print("✓ RPi.GPIO verfügbar")
except ImportError:
    GPIO_AVAILABLE = False
    GPIO = None
    print("⚠ RPi.GPIO nicht verfügbar - Simulationsmodus")

BRICKOGNIZE_API_URL = "https://api.brickognize.com/predict/"
IMAGE_PATH = "/tmp/brick_image.jpg"
MOTION_PREVIEW_PATH = "/tmp/motion_preview.jpg"

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

# --- Hardware GPIO Pins (WiringPi-Nummerierung mit BCM-Mapping) ---
# Stepper Motor Pins
STEPPER_PWM_PIN = {
    "wiring": 1,   # WiringPi 1
    "bcm": 18,     # BCM GPIO18
    "physical": 12,
    "function": "PWM0",
    "description": "Stepper Motor Geschwindigkeit (PWM-Signal)"
}

STEPPER_DIR_PIN = {
    "wiring": 0,   # WiringPi 0
    "bcm": 17,     # BCM GPIO17
    "physical": 11,
    "function": "GPIO",
    "description": "Stepper Motor Richtung (0=Hoch, 1=Runter)"
}

# Endschalter/Anschlag Sensoren
SENSOR_LOWER_PIN = {
    "wiring": 2,   # WiringPi 2
    "bcm": 27,     # BCM GPIO27
    "physical": 13,
    "function": "GPIO_INPUT",
    "description": "Unterer Anschlag Sensor - NO (1=unten angeschlagen)"
}

SENSOR_UPPER_PIN = {
    "wiring": 13,  # WiringPi 13
    "bcm": 23,     # BCM GPIO23
    "physical": 33,
    "function": "GPIO_INPUT",
    "description": "Oberer Anschlag Sensor - Pull-Down"
}

# Servo/Sortier Pins
SERVO_SORT_PIN = {
    "wiring": 23,  # WiringPi 23
    "bcm": 13,     # BCM GPIO13
    "physical": 33,
    "function": "PWM1",
    "pwm_type": "hardware",
    "description": "Sortierung Servo (270° Servo) - Hardware-PWM"
}

SERVO_GATE_PIN = {
    "wiring": 6,   # WiringPi 6
    "bcm": 25,     # BCM GPIO25
    "physical": 22,
    "function": "GPIO",
    "pwm_type": "software",
    "description": "Klappe/Schleuse Servo - Software-PWM"
}

# Rüttler/Vibration Pins
VIBRATION_1_PIN = {
    "wiring": 4,   # WiringPi 4
    "bcm": 23,     # BCM GPIO23
    "physical": 16,
    "function": "GPIO",
    "pwm_type": "software",
    "description": "Rüttler 1 - Software-PWM"
}

VIBRATION_2_PIN = {
    "wiring": 5,   # WiringPi 5
    "bcm": 24,     # BCM GPIO24
    "physical": 18,
    "function": "GPIO",
    "pwm_type": "software",
    "description": "Rüttler 2 - Software-PWM"
}

# INFO: 
# - SERVO_SORT verwendet Hardware-PWM1 (präzise für 270° Servo)
# - SERVO_GATE verwendet Software-PWM (ausreichend für Klappen-Servo)
# - VIBRATION verwendet Software-PWM (aktiv nur in WARTEN_AUF_TEIL)
# - Kein Pin-Konflikt mehr!

# --- Stepper Motor Controller ---
class StepperController:
    """
    Stepper Motor Controller für Schieber-Mechanik.
    Der Schieber fährt hoch und runter und transportiert Teile aus einer Kiste.
    """
    def __init__(self):
        self.pwm_pin = None
        self.dir_pin = None
        self.pwm_object = None
        self.gpio_initialized = False
        
        # Fahrtrichtung basierend auf DIR-Pin (LOW=up, HIGH=down)
        self.last_direction = "unknown"  # "up", "down", "unknown"
        self.is_moving = False
        
        # Motor-Parameter
        self.default_speed_hz = 500  # Standard Geschwindigkeit in Hz (50% von 1000 Hz)
        self.steps_per_cycle = 200   # Schritte für eine volle Bewegung (anpassbar)
        
        # Geschwindigkeits-Einstellung (10-100%)
        self.speed_percentage = 50  # Standard: 50%
        self.max_speed_hz = 1000    # Maximale Geschwindigkeit bei 100%
        
        # Kontinuierliche Bewegung mit Auto-Reverse
        self.continuous_mode = False
        self.current_direction = "unknown"  # "up", "down", "unknown"
        self.continuous_speed_hz = 500  # Geschwindigkeit für kontinuierlichen Betrieb (wird aus speed_percentage berechnet)
        
    def init_gpio(self):
        """Initialisiert GPIO für Stepper Motor"""
        if self.gpio_initialized:
            return
        
        if not GPIO_AVAILABLE:
            print("WARNUNG: RPi.GPIO nicht verfügbar - Stepper Simulationsmodus")
            self.gpio_initialized = False
            return
        
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            
            # DIR Pin (Direction) als Output
            self.dir_pin = STEPPER_DIR_PIN["bcm"]
            GPIO.setup(self.dir_pin, GPIO.OUT)
            GPIO.output(self.dir_pin, GPIO.LOW)  # Initial: Hoch (0)
            
            # PWM Pin (Geschwindigkeit) als PWM
            self.pwm_pin = STEPPER_PWM_PIN["bcm"]
            GPIO.setup(self.pwm_pin, GPIO.OUT)
            self.pwm_object = GPIO.PWM(self.pwm_pin, self.default_speed_hz)
            
            # Sensor Pins als Input mit Pull-Down (wie in sensor_test.py getestet)
            GPIO.setup(SENSOR_LOWER_PIN["bcm"], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.setup(SENSOR_UPPER_PIN["bcm"], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            
            self.gpio_initialized = True
            print(f"Stepper GPIO initialisiert: DIR=GPIO{self.dir_pin}, PWM=GPIO{self.pwm_pin}")
            print(f"Sensor Pins initialisiert: LOWER=GPIO{SENSOR_LOWER_PIN['bcm']} (NO), UPPER=GPIO{SENSOR_UPPER_PIN['bcm']} (NC)")
            
            # Sofort Sensor-Status testen
            self.test_sensors()
            
            # Initiale Richtung ist "up" (DIR=Low)
            self.last_direction = "up"
            
        except Exception as e:
            print(f"Stepper GPIO Init Fehler: {e}")
            import traceback
            traceback.print_exc()
            self.gpio_initialized = False
    
    def test_sensors(self):
        """Testet Sensor-Pins und gibt Status aus (Debug)"""
        if not self.gpio_initialized:
            print("DEBUG: GPIO nicht initialisiert - kann Sensoren nicht testen")
            return
        
        try:
            print("\n" + "="*60)
            print("SENSOR DEBUG TEST")
            print("="*60)
            
            # Sensor-Werte lesen
            lower_val = GPIO.input(SENSOR_LOWER_PIN["bcm"])
            upper_val = GPIO.input(SENSOR_UPPER_PIN["bcm"])
            
            print(f"Unterer Sensor (NO) - GPIO{SENSOR_LOWER_PIN['bcm']}:")
            print(f"  Roh-Wert: {lower_val} ({'HIGH' if lower_val else 'LOW'})")
            print(f"  Status: {'ANGESCHLAGEN' if lower_val == 0 else 'FREI'}")
            print(f"  Erwartung: 0=angeschlagen, 1=frei")
            
            print(f"\nOberer Sensor (NC) - GPIO{SENSOR_UPPER_PIN['bcm']}:")
            print(f"  Roh-Wert: {upper_val} ({'HIGH' if upper_val else 'LOW'})")
            print(f"  Status: {'ANGESCHLAGEN' if upper_val == 1 else 'FREI'}")
            print(f"  Erwartung: 1=angeschlagen, 0=frei")
            
            print("="*60 + "\n")
            
        except Exception as e:
            print(f"FEHLER beim Sensor-Test: {e}")
            import traceback
            traceback.print_exc()
    
    def _get_current_direction(self):
        """Liest aktuelle Fahrtrichtung vom DIR-Pin (LOW=up, HIGH=down)"""
        if not self.gpio_initialized:
            return self.last_direction
        
        try:
            import RPi.GPIO as GPIO
            dir_state = GPIO.input(self.dir_pin)
            # DIR Pin: LOW=0=Hoch, HIGH=1=Runter
            if dir_state == GPIO.LOW:
                self.last_direction = "up"
            else:
                self.last_direction = "down"
            
            return self.last_direction
            
        except Exception as e:
            print(f"DIR-Pin Lesefehler: {e}")
            return self.last_direction
    
    def move_up(self, speed_hz=None):
        """
        Fährt Schieber nach OBEN bis oberer Sensor anschlägt.
        :param speed_hz: Geschwindigkeit in Hz (optional)
        """
        if not self.gpio_initialized:
            print("SIMULATION: Schieber fährt HOCH")
            time.sleep(1.0)
            self.last_direction = "up"
            return True
        
        try:
            import RPi.GPIO as GPIO
            GPIO.output(self.dir_pin, GPIO.LOW)
            self.last_direction = "up"
            
            # PWM starten
            speed = speed_hz if speed_hz else self.default_speed_hz
            self.pwm_object.ChangeFrequency(speed)
            self.pwm_object.start(50)  # 50% Duty Cycle
            
            self.is_moving = True
            print(f"Schieber fährt HOCH ({speed}Hz)...")
            
            # Warte auf oberen Sensor (NC: 1 = angeschlagen, Kontakt öffnet)
            timeout = 10  # 10 Sekunden Timeout
            start_time = time.time()
            
            while True:
                if GPIO.input(SENSOR_UPPER_PIN["bcm"]) == 1:
                    # Oben angekommen
                    break
                
                if time.time() - start_time > timeout:
                    print("WARNUNG: Timeout beim Hochfahren!")
                    break
                
                time.sleep(0.05)
            
            # PWM stoppen
            self.pwm_object.stop()
            self.is_moving = False
            print("Schieber Position: OBEN erreicht")
            
            return True
            
        except Exception as e:
            print(f"Fehler beim Hochfahren: {e}")
            if self.pwm_object:
                self.pwm_object.stop()
            self.is_moving = False
            return False
    
    def move_down(self, speed_hz=None):
        """
        Fährt Schieber nach UNTEN bis unterer Sensor anschlägt.
        :param speed_hz: Geschwindigkeit in Hz (optional)
        """
        if not self.gpio_initialized:
            print("SIMULATION: Schieber fährt RUNTER")
            time.sleep(1.0)
            self.last_direction = "down"
            return True
        
        try:
            import RPi.GPIO as GPIO
            
            self.last_direction = "down"
            
            # PWM starten
            speed = speed_hz if speed_hz else self.default_speed_hz
            self.pwm_object.ChangeFrequency(speed)
            self.pwm_object.start(50)  # 50% Duty Cycle
            
            self.is_moving = True
            print(f"Schieber fährt RUNTER ({speed}Hz)...")
            
            # Warte auf unteren Sensor (NO: 0 = angeschlagen, Kontakt schließt)
            timeout = 10  # 10 Sekunden Timeout
            start_time = time.time()
            
            while True:
                if GPIO.input(SENSOR_LOWER_PIN["bcm"]) == 0:
                    # Unten angekommen
                    break
                
                if time.time() - start_time > timeout:
                    print("WARNUNG: Timeout beim Runterfahren!")
                    break
                
                time.sleep(0.05)
            
            # PWM stoppen
            self.pwm_object.stop()
            self.is_moving = False
            print("Schieber Position: UNTEN erreicht")
            
            return True
            
        except Exception as e:
            print(f"Fehler beim Runterfahren: {e}")
            if self.pwm_object:
                self.pwm_object.stop()
            self.is_moving = False
            return False
    
    def home(self):
        """
        Fährt Schieber in Home-Position (unten).
        Wird beim Start verwendet.
        """
        print("Schieber: Fahre zur Home-Position (unten)...")
        return self.move_down()
    

    def start_continuous_push(self):
        """
        Startet kontinuierliche Schieber-Bewegung mit automatischer Richtungsumkehr.
        Der Schieber fährt ständig hoch/runter zwischen den Sensoren und kehrt automatisch um.
        Muss mit update_continuous_motion() regelmäßig aufgerufen werden!
        """
        if not self.gpio_initialized:
            print("SIMULATION: Kontinuierliche Schieber-Bewegung gestartet")
            self.is_moving = True
            self.continuous_mode = True
            return True
        
        try:
            import RPi.GPIO as GPIO
            
            # Wenn continuous_mode bereits aktiv war: Richtung beibehalten
            # Sonst: Starte initial mit Runter
            if self.current_direction == "unknown":
                # Erster Start - default: Runter
                self.current_direction = "down"
            # Ansonsten: current_direction bleibt erhalten von update_continuous_motion()
            
            # Setze DIR-Pin entsprechend current_direction
            if self.current_direction == "up":
                GPIO.output(self.dir_pin, GPIO.HIGH)
            else:
                GPIO.output(self.dir_pin, GPIO.LOW)
            
            self.last_direction = self.current_direction
            
            # PWM starten
            self.pwm_object.ChangeFrequency(self.continuous_speed_hz)
            self.pwm_object.start(50)
            
            self.is_moving = True
            self.continuous_mode = True
            print(f"Kontinuierliche Auto-Reverse Bewegung gestartet ({self.continuous_speed_hz}Hz, Richtung: {self.current_direction})")
            return True
            
        except Exception as e:
            print(f"Fehler beim Starten der kontinuierlichen Bewegung: {e}")
            return False
    
    def update_continuous_motion(self):
        """
        Prüft Sensoren und kehrt Richtung automatisch um wenn Endposition erreicht.
        MUSS regelmäßig aufgerufen werden wenn continuous_mode aktiv ist!
        """
        if not self.continuous_mode or not self.gpio_initialized:
            return
        
        try:
            lower_sensor = GPIO.input(SENSOR_LOWER_PIN["bcm"])
            upper_sensor = GPIO.input(SENSOR_UPPER_PIN["bcm"])
            
            # Debug: Sensor-Werte ausgeben (alle 2 Sekunden)
            if not hasattr(self, '_last_sensor_debug') or time.time() - self._last_sensor_debug > 2.0:
                print(f"[SENSOR] Unten={lower_sensor} ({'ANGESCHLAGEN' if lower_sensor == 1 else 'FREI'}), Oben={upper_sensor} ({'ANGESCHLAGEN' if upper_sensor == 1 else 'FREI'}), Richtung={self.current_direction}")
                self._last_sensor_debug = time.time()
            
            # Unterer Sensor (NO): 0 = angeschlagen (Kontakt geschlossen)
            if lower_sensor == 1 and self.current_direction == "down":
                # Unten angekommen - kehre um nach Oben
                print("🔄 Schieber: UNTEN erreicht → Stoppe Motor → Richtung: HOCH")
                
                # PWM stoppen für saubere Richtungsänderung
                if self.pwm_object:
                    self.pwm_object.stop()
                
                # Richtung ändern
                GPIO.output(self.dir_pin, GPIO.HIGH)  # Richtung Hoch
                self.current_direction = "up"
                self.last_direction = "up"
                
                # PWM wieder starten
                self.pwm_object.ChangeFrequency(self.continuous_speed_hz)
                self.pwm_object.start(50)
            
            # Oberer Sensor (NC): 1 = angeschlagen (Kontakt geschlossen)
            elif upper_sensor == 1 and self.current_direction == "up":
                # Oben angekommen - kehre um nach Unten
                print("🔄 Schieber: OBEN erreicht → Stoppe Motor → Richtung: RUNTER")
                
                # PWM stoppen für saubere Richtungsänderung
                if self.pwm_object:
                    self.pwm_object.stop()
                
                # Richtung ändern
                GPIO.output(self.dir_pin, GPIO.LOW)  # Richtung Runter
                self.current_direction = "down"
                self.last_direction = "down"
                
                # PWM wieder starten
                self.pwm_object.ChangeFrequency(self.continuous_speed_hz)
                self.pwm_object.start(50)
        
        except Exception as e:
            print(f"Fehler beim Update der kontinuierlichen Bewegung: {e}")
    
    def set_speed_percentage(self, percentage):
        """
        Setzt die Geschwindigkeit in Prozent (10-100%).
        10% = 100 Hz, 100% = 1000 Hz
        """
        # Begrenze auf 10-100%
        percentage = max(10, min(100, percentage))
        self.speed_percentage = percentage
        
        # Berechne Hz-Wert (10% = 100 Hz, 100% = 1000 Hz)
        self.default_speed_hz = int((percentage / 100.0) * self.max_speed_hz)
        self.continuous_speed_hz = self.default_speed_hz
        
        print(f"Schieber-Geschwindigkeit auf {percentage}% ({self.default_speed_hz} Hz) gesetzt")
        
        # Wenn kontinuierlicher Modus läuft, aktualisiere Frequenz
        if self.continuous_mode and self.gpio_initialized and self.pwm_object:
            try:
                self.pwm_object.ChangeFrequency(self.continuous_speed_hz)
            except Exception as e:
                print(f"Fehler beim Aktualisieren der Geschwindigkeit: {e}")
    
    def stop(self):
        """Stoppt Motor sofort"""
        if self.pwm_object:
            self.pwm_object.stop()
        self.is_moving = False
        self.continuous_mode = False
        print("Stepper Motor gestoppt")
    
    def cleanup(self):
        """GPIO Cleanup"""
        self.stop()
        if self.gpio_initialized:
            try:
                GPIO.cleanup([self.pwm_pin, self.dir_pin, 
                             SENSOR_LOWER_PIN["bcm"], SENSOR_UPPER_PIN["bcm"]])
                print("Stepper GPIO bereinigt")
            except:
                pass
        self.gpio_initialized = False

# --- Servo Controller ---
class ServoController:
    """
    Dual Servo Controller für Sortierung und Klappe.
    - Sortier-Servo: Hardware-PWM (180° Servo, präzise Positionierung)
    - Klappen-Servo: Software-PWM (einfache On/Off Bewegung)
    """
    def __init__(self):
        self.sort_servo_pin = None
        self.gate_servo_pin = None
        self.sort_pwm = None  # Hardware-PWM Objekt
        self.gate_pwm = None  # Software-PWM Objekt
        self.servo_frequency = 50  # 50Hz Standard für Servos
        self.gpio_initialized = False
        
        # Positionen für 180° Sortier-Servo (kann angepasst werden)
        self.sort_positions = {
            "box1": 0,      # Kiste 1
            "box2": 60,     # Kiste 2
            "box3": 120,    # Kiste 3
            "box4": 180,    # Kiste 4 (Ausschuss)
            "center": 90    # Mittelposition
        }
        
        # Positionen für Klappen-Servo
        self.gate_positions = {
            "open": 0,      # Klappe offen
            "closed": 90    # Klappe geschlossen
        }
    
    def init_servos(self):
        """Initialisiert beide Servos"""
        if self.gpio_initialized:
            return True
        
        if not GPIO_AVAILABLE:
            print("WARNUNG: RPi.GPIO nicht verfügbar - Servo Simulationsmodus")
            self.gpio_initialized = False
            return False
        
        try:
            # Sortier-Servo: Hardware-PWM auf GPIO13
            self.sort_servo_pin = SERVO_SORT_PIN["bcm"]
            GPIO.setup(self.sort_servo_pin, GPIO.OUT)
            self.sort_pwm = GPIO.PWM(self.sort_servo_pin, self.servo_frequency)
            self.sort_pwm.start(0)  # Start mit 0% Duty
            
            # Klappen-Servo: Software-PWM auf GPIO25
            self.gate_servo_pin = SERVO_GATE_PIN["bcm"]
            GPIO.setup(self.gate_servo_pin, GPIO.OUT)
            self.gate_pwm = GPIO.PWM(self.gate_servo_pin, self.servo_frequency)
            self.gate_pwm.start(0)  # Start mit 0% Duty
            
            self.gpio_initialized = True
            print(f"Servos initialisiert:")
            print(f"  Sortier-Servo: GPIO{self.sort_servo_pin} (Hardware-PWM, 50Hz)")
            print(f"  Klappen-Servo: GPIO{self.gate_servo_pin} (Software-PWM, 50Hz)")
            
            # Setze auf Standardpositionen
            self.set_sort_position("center")
            self.close_gate()  # Klappe geschlossen halten für Haltemoment
            
            return True
            
        except Exception as e:
            print(f"Servo Init Fehler: {e}")
            import traceback
            traceback.print_exc()
            self.gpio_initialized = False
            return False
    
    def _angle_to_duty_cycle(self, angle):
        """
        Konvertiert Winkel (0-180°) zu Duty Cycle (2.5% - 12.5%)
        
        Standard 180° Servo Mapping:
        - 0°   = 2.5% Duty Cycle (0.5ms Puls bei 50Hz)
        - 90°  = 7.5% Duty Cycle (1.5ms Puls)
        - 180° = 12.5% Duty Cycle (2.5ms Puls)
        """
        # Begrenze Winkel auf 0-180°
        angle = max(0, min(180, angle))
        
        # Lineare Interpolation: 0° → 2.5%, 180° → 12.5%
        duty_cycle = 2.5 + (angle / 180.0) * 10.0
        
        return duty_cycle
    
    def set_sort_angle(self, angle):
        """
        Setzt Sortier-Servo auf bestimmten Winkel (0-180°)
        :param angle: Winkel in Grad (0-180)
        """
        if not self.gpio_initialized:
            print("SIMULATION: Sortier-Servo → {angle}°")
            return False
        
        try:
            duty_cycle = self._angle_to_duty_cycle(angle)
            self.sort_pwm.ChangeDutyCycle(duty_cycle)
            print(f"Sortier-Servo: {angle}° (Duty: {duty_cycle:.2f}%)")
            time.sleep(0.3)  # Warte auf Servo-Bewegung
            self.sort_pwm.ChangeDutyCycle(0)  # Stoppe Puls (verhindert Servo-Zittern)
            return True
            
        except Exception as e:
            print(f"Sortier-Servo Fehler: {e}")
            return False
    
    def set_sort_position(self, position_name):
        """
        Setzt Sortier-Servo auf vordefinierte Position
        :param position_name: "box1", "box2", "box3", "box4", "center"
        """
        if position_name not in self.sort_positions:
            print(f"Unbekannte Position: {position_name}")
            return False
        
        angle = self.sort_positions[position_name]
        print(f"Sortier-Servo → Position '{position_name}' ({angle}°)")
        return self.set_sort_angle(angle)
    
    def set_gate_angle(self, angle):
        """
        Setzt Klappen-Servo auf bestimmten Winkel (0-180°)
        :param angle: Winkel in Grad (0-180)
        """
        if not self.gpio_initialized:
            print(f"SIMULATION: Klappen-Servo → {angle}°")
            return False
        
        try:
            # Begrenze auf 0-180° für Standard-Servo
            angle = max(0, min(180, angle))
            duty_cycle = 2.5 + (angle / 180.0) * 10.0
            
            self.gate_pwm.ChangeDutyCycle(duty_cycle)
            print(f"Klappen-Servo: {angle}° (Duty: {duty_cycle:.2f}%)")
            time.sleep(0.3)  # Warte auf Servo-Bewegung
            # KEIN ChangeDutyCycle(0) - Servo hält Position für Haltemoment
            return True
            
        except Exception as e:
            print(f"Klappen-Servo Fehler: {e}")
            return False
    
    def set_gate_position(self, position_name):
        """
        Setzt Klappen-Servo auf vordefinierte Position
        :param position_name: "open", "closed"
        """
        if position_name not in self.gate_positions:
            print(f"Unbekannte Klappenposition: {position_name}")
            return False
        
        angle = self.gate_positions[position_name]
        print(f"Klappen-Servo → Position '{position_name}' ({angle}°)")
        return self.set_gate_angle(angle)
    
    def open_gate(self):
        """Öffnet Klappe"""
        return self.set_gate_position("open")
    
    def close_gate(self):
        """Schließt Klappe"""
        return self.set_gate_position("closed")
    
    def stop(self):
        """Stoppt beide Servos (PWM auf 0)"""
        if self.sort_pwm:
            self.sort_pwm.ChangeDutyCycle(0)
        if self.gate_pwm:
            self.gate_pwm.ChangeDutyCycle(0)
        print("Servos gestoppt")
    
    def cleanup(self):
        """GPIO Cleanup"""
        self.stop()
        if self.gpio_initialized:
            try:
                if self.sort_pwm:
                    self.sort_pwm.stop()
                if self.gate_pwm:
                    self.gate_pwm.stop()
                
                GPIO.cleanup([self.sort_servo_pin, self.gate_servo_pin])
                print("Servo GPIO bereinigt")
            except:
                pass
        self.gpio_initialized = False

# Globale Servo-Instanz
_servo_controller = None

def get_servo_controller():
    """Gibt Singleton Servo Controller zurück"""
    global _servo_controller
    if _servo_controller is None:
        _servo_controller = ServoController()
        _servo_controller.init_servos()
    return _servo_controller

# Globale Stepper-Instanz
_stepper_controller = None

def get_stepper_controller():
    """Gibt Singleton Stepper Controller zurück"""
    global _stepper_controller
    if _stepper_controller is None:
        _stepper_controller = StepperController()
        _stepper_controller.init_gpio()
    return _stepper_controller

# --- Vibration/Rüttler Controller ---
class VibrationController:
    """
    Dual Vibration Motor Controller für Teile-Förderung.
    - Beide Rüttler: Software-PWM
    - Nur aktiv in WARTEN_AUF_TEIL State (wenn auf Teile gewartet wird)
    - Duty Cycle einstellbar (50% Standard)
    - Pattern: 2 Sekunden rütteln, 1 Sekunde Pause, wiederholt
    """
    def __init__(self):
        self.vib1_pin = None
        self.vib2_pin = None
        self.vib1_pwm = None  # Software-PWM Objekt
        self.vib2_pwm = None  # Software-PWM Objekt
        self.pwm_frequency = 100  # 100Hz für Vibrationsmotoren
        self.gpio_initialized = False
        self.is_running = False
        
        # Duty Cycle (0-100%) - einstellbar
        self.duty_cycle = 50  # Standard: 50%
        
        # Pattern-Steuerung für 2s rütteln / 1s Pause
        self.pattern_active = False
        self.pattern_start_time = 0
        self.pattern_phase = "vibrate"  # "vibrate" oder "pause"
        self.vibrate_duration = 2.0  # 2 Sekunden rütteln
        self.pause_duration = 1.0    # 1 Sekunde Pause
    
    def init_vibration(self):
        """Initialisiert beide Rüttler"""
        if self.gpio_initialized:
            return True
        
        if not GPIO_AVAILABLE:
            print("WARNUNG: RPi.GPIO nicht verfügbar - Rüttler Simulationsmodus")
            self.gpio_initialized = False
            return False
        
        try:
            # Rüttler 1: Software-PWM auf GPIO23
            self.vib1_pin = VIBRATION_1_PIN["bcm"]
            GPIO.setup(self.vib1_pin, GPIO.OUT)
            self.vib1_pwm = GPIO.PWM(self.vib1_pin, self.pwm_frequency)
            self.vib1_pwm.start(0)  # Start mit 0% Duty
            
            # Rüttler 2: Software-PWM auf GPIO24
            self.vib2_pin = VIBRATION_2_PIN["bcm"]
            GPIO.setup(self.vib2_pin, GPIO.OUT)
            self.vib2_pwm = GPIO.PWM(self.vib2_pin, self.pwm_frequency)
            self.vib2_pwm.start(0)  # Start mit 0% Duty
            
            self.gpio_initialized = True
            print(f"Rüttler initialisiert auf GPIO{self.vib1_pin} und GPIO{self.vib2_pin}")
            return True
        except Exception as e:
            print(f"Fehler bei Rüttler-Initialisierung: {e}")
            self.gpio_initialized = False
            return False
    
    def start(self):
        """Startet Rüttler im Pattern-Modus (2s rütteln / 1s Pause)"""
        if not self.gpio_initialized:
            print("Rüttler nicht initialisiert - kann nicht starten")
            return
        
        if self.pattern_active:
            return  # Bereits im Pattern-Modus
        
        try:
            self.pattern_active = True
            self.pattern_phase = "vibrate"
            self.pattern_start_time = time.time()
            
            # Starte mit Rütteln
            self.vib1_pwm.ChangeDutyCycle(self.duty_cycle)
            self.vib2_pwm.ChangeDutyCycle(self.duty_cycle)
            self.is_running = True
            print(f"Rüttler Pattern gestartet: {self.vibrate_duration}s rütteln / {self.pause_duration}s Pause")
        except Exception as e:
            print(f"Fehler beim Starten der Rüttler: {e}")
    
    def update_pattern(self):
        """
        Aktualisiert das Rüttel-Pattern (2s rütteln / 1s Pause).
        MUSS regelmäßig aufgerufen werden wenn pattern_active ist!
        """
        if not self.pattern_active or not self.gpio_initialized:
            return
        
        try:
            elapsed = time.time() - self.pattern_start_time
            
            if self.pattern_phase == "vibrate":
                # Prüfe ob Rüttelphase vorbei ist
                if elapsed >= self.vibrate_duration:
                    # Wechsel zu Pause
                    self.vib1_pwm.ChangeDutyCycle(0)
                    self.vib2_pwm.ChangeDutyCycle(0)
                    self.is_running = False
                    self.pattern_phase = "pause"
                    self.pattern_start_time = time.time()
                    print("Rüttler: Pause-Phase")
            
            elif self.pattern_phase == "pause":
                # Prüfe ob Pause vorbei ist
                if elapsed >= self.pause_duration:
                    # Wechsel zu Rütteln
                    self.vib1_pwm.ChangeDutyCycle(self.duty_cycle)
                    self.vib2_pwm.ChangeDutyCycle(self.duty_cycle)
                    self.is_running = True
                    self.pattern_phase = "vibrate"
                    self.pattern_start_time = time.time()
                    print("Rüttler: Rüttel-Phase")
        
        except Exception as e:
            print(f"Fehler beim Pattern-Update: {e}")
    
    def stop(self):
        """Stoppt beide Rüttler und beendet Pattern-Modus"""
        if not self.gpio_initialized:
            return
        
        try:
            self.vib1_pwm.ChangeDutyCycle(0)
            self.vib2_pwm.ChangeDutyCycle(0)
            self.is_running = False
            self.pattern_active = False
            self.pattern_phase = "vibrate"
            print("Rüttler gestoppt")
        except Exception as e:
            print(f"Fehler beim Stoppen der Rüttler: {e}")
    
    def set_duty_cycle(self, duty):
        """Setzt Duty Cycle (0-100%) - wird sofort angewendet wenn aktiv"""
        self.duty_cycle = max(0, min(100, duty))  # Clamp auf 0-100
        
        if self.is_running and self.gpio_initialized:
            try:
                self.vib1_pwm.ChangeDutyCycle(self.duty_cycle)
                self.vib2_pwm.ChangeDutyCycle(self.duty_cycle)
                print(f"Rüttler Duty Cycle auf {self.duty_cycle}% gesetzt")
            except Exception as e:
                print(f"Fehler beim Setzen des Duty Cycles: {e}")
    
    def test(self, duration=2.0):
        """Testet Rüttler für eine bestimmte Zeit (Sekunden)"""
        if not self.gpio_initialized:
            print("Rüttler nicht initialisiert - Test nicht möglich")
            return
        
        print(f"Teste Rüttler für {duration}s mit {self.duty_cycle}% Duty Cycle...")
        was_running = self.is_running
        
        if not was_running:
            self.start()
        
        time.sleep(duration)
        
        if not was_running:
            self.stop()
        
        print("Rüttler-Test abgeschlossen")
    
    def cleanup(self):
        """GPIO Cleanup"""
        self.stop()
        if self.gpio_initialized:
            try:
                if self.vib1_pwm:
                    self.vib1_pwm.stop()
                if self.vib2_pwm:
                    self.vib2_pwm.stop()
                
                GPIO.cleanup([self.vib1_pin, self.vib2_pin])
                print("Rüttler GPIO bereinigt")
            except:
                pass
        self.gpio_initialized = False

# Globale Vibration-Instanz
_vibration_controller = None

def get_vibration_controller():
    """Gibt Singleton Vibration Controller zurück"""
    global _vibration_controller
    if _vibration_controller is None:
        _vibration_controller = VibrationController()
        _vibration_controller.init_vibration()
    return _vibration_controller

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

def display_image_from_url(img_url, label, max_size=(200, 200)):
    """
    Lade ein Bild von einer URL und zeige es im GUI-Label an.
    :param max_size: Tuple (width, height) für maximale Bildgröße
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
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
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

# --- GUI erstellen ---
root = tk.Tk()
root.title("🧱 LEGO Sortiermaschine")
root.attributes('-fullscreen', True)
root.configure(bg='#1e1e1e')

# ESC zum Beenden
root.bind('<Escape>', lambda e: root.quit())

# --- Settings Window Funktion ---
def open_settings_window(parent_root, automation_controller=None):
    """Öffnet das Einstellungsfenster für Servo-Konfiguration, Teile-Erkennung und Kamera"""
    settings_win = tk.Toplevel(parent_root)
    settings_win.title("Einstellungen")
    settings_win.geometry("670x750")
    settings_win.configure(bg='#1e1e1e')
    settings_win.resizable(False, False)
    
    # Servo-Controller Referenz holen
    servo = get_servo_controller()
    
    # Hauptcontainer
    main_frame = tk.Frame(settings_win, bg='#1e1e1e')
    main_frame.pack(fill='both', expand=True, padx=20, pady=20)
    
    # Titel
    title = tk.Label(
        main_frame,
        text="⚙️ Einstellungen",
        font=('Helvetica', 16, 'bold'),
        bg='#1e1e1e',
        fg='white'
    )
    title.pack(pady=(0, 15))
    
    # Tab-Widget erstellen
    notebook = ttk.Notebook(main_frame)
    notebook.pack(fill='both', expand=True, pady=(0, 15))
    
    # Style für Tabs
    style = ttk.Style()
    style.theme_use('default')
    style.configure('TNotebook', background='#1e1e1e', borderwidth=0)
    style.configure('TNotebook.Tab', background='#2b2b2b', foreground='white', 
                    padding=[20, 10], font=('Helvetica', 10, 'bold'))
    style.map('TNotebook.Tab', background=[('selected', '#3a7ca5')], 
              foreground=[('selected', 'white')])
    
    # Tab 1: Haupteinstellungen mit Scrollbar
    main_tab_container = tk.Frame(notebook, bg='#1e1e1e')
    notebook.add(main_tab_container, text='Haupteinstellungen')
    
    # Canvas und Scrollbar für Haupteinstellungen
    main_canvas = tk.Canvas(main_tab_container, bg='#1e1e1e', highlightthickness=0)
    main_scrollbar = tk.Scrollbar(main_tab_container, orient='vertical', command=main_canvas.yview)
    main_scrollable_frame = tk.Frame(main_canvas, bg='#1e1e1e')
    
    main_scrollable_frame.bind(
        '<Configure>',
        lambda e: main_canvas.configure(scrollregion=main_canvas.bbox('all'))
    )
    
    main_canvas.create_window((0, 0), window=main_scrollable_frame, anchor='nw')
    main_canvas.configure(yscrollcommand=main_scrollbar.set)
    
    main_canvas.pack(side='left', fill='both', expand=True)
    main_scrollbar.pack(side='right', fill='y')
    
    # Mausrad-Scrolling aktivieren
    def on_mouse_wheel(event):
        main_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    main_canvas.bind_all("<MouseWheel>", on_mouse_wheel)
    
    main_tab = main_scrollable_frame  # Referenz für bestehenden Code
    
    # Tab 2: Debug/Erweitert mit Scrollbar
    debug_tab_container = tk.Frame(notebook, bg='#1e1e1e')
    notebook.add(debug_tab_container, text='Debug / Erweitert')
    
    # Canvas und Scrollbar für Debug
    debug_canvas = tk.Canvas(debug_tab_container, bg='#1e1e1e', highlightthickness=0)
    debug_scrollbar = tk.Scrollbar(debug_tab_container, orient='vertical', command=debug_canvas.yview)
    debug_scrollable_frame = tk.Frame(debug_canvas, bg='#1e1e1e')
    
    debug_scrollable_frame.bind(
        '<Configure>',
        lambda e: debug_canvas.configure(scrollregion=debug_canvas.bbox('all'))
    )
    
    debug_canvas.create_window((0, 0), window=debug_scrollable_frame, anchor='nw')
    debug_canvas.configure(yscrollcommand=debug_scrollbar.set)
    
    debug_canvas.pack(side='left', fill='both', expand=True)
    debug_scrollbar.pack(side='right', fill='y')
    
    # Mausrad-Scrolling aktivieren
    def on_debug_mouse_wheel(event):
        debug_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    debug_canvas.bind_all("<MouseWheel>", on_debug_mouse_wheel)
    
    debug_tab = debug_scrollable_frame  # Referenz für bestehenden Code
    
    # ==================== HAUPTEINSTELLUNGEN TAB ====================
    
    # --- Sortier-Servo Positionen ---
    sort_frame = tk.LabelFrame(
        main_tab,
        text="Kisten-Positionen (0-180°)",
        font=('Helvetica', 12, 'bold'),
        bg='#2b2b2b',
        fg='white',
        relief='flat',
        bd=2
    )
    sort_frame.pack(fill='x', pady=(10, 15), padx=10)
    
    # Position Slider für jede Box
    box_sliders = {}
    box_names = ["box1", "box2", "box3", "box4"]
    box_labels = ["Box 1", "Box 2", "Box 3", "Box 4 (Ausschuss)"]
    
    for idx, (box_name, box_label) in enumerate(zip(box_names, box_labels)):
        row_frame = tk.Frame(sort_frame, bg='#2b2b2b')
        row_frame.pack(fill='x', padx=10, pady=5)
        
        # Label
        label = tk.Label(
            row_frame,
            text=f"{box_label}:",
            font=('Helvetica', 10),
            bg='#2b2b2b',
            fg='white',
            width=20,
            anchor='w'
        )
        label.pack(side='left')
        
        # Wert-Anzeige
        value_label = tk.Label(
            row_frame,
            text=f"{servo.sort_positions[box_name]}°",
            font=('Helvetica', 10, 'bold'),
            bg='#2b2b2b',
            fg='#00ff00',
            width=6
        )
        value_label.pack(side='right', padx=(5, 0))
        
        # Test-Button
        test_btn = tk.Button(
            row_frame,
            text="Test",
            font=('Helvetica', 9),
            bg='#3a7ca5',
            fg='white',
            activebackground='#4a8cb5',
            relief='flat',
            padx=10,
            command=lambda bn=box_name: servo.set_sort_position(bn)
        )
        test_btn.pack(side='right', padx=(5, 5))
        
        # Slider
        slider = tk.Scale(
            row_frame,
            from_=0,
            to=180,
            orient='horizontal',
            bg='#2b2b2b',
            fg='white',
            highlightthickness=0,
            troughcolor='#3a3a3a',
            activebackground='#4a4a4a',
            command=lambda val, bn=box_name, vl=value_label: update_slider_value(val, bn, vl)
        )
        slider.set(servo.sort_positions[box_name])
        slider.pack(side='right', fill='x', expand=True, padx=(0, 5))
        
        box_sliders[box_name] = slider
    
    def update_slider_value(value, box_name, value_label):
        """Update Slider Anzeige"""
        value_label.config(text=f"{value}°")
        servo.sort_positions[box_name] = int(value)
    
    # --- Schieber Geschwindigkeit ---
    stepper = get_stepper_controller()
    
    stepper_frame = tk.LabelFrame(
        main_tab,
        text="Schieber-Geschwindigkeit",
        font=('Helvetica', 12, 'bold'),
        bg='#2b2b2b',
        fg='white',
        relief='flat',
        bd=2
    )
    stepper_frame.pack(fill='x', pady=(0, 15), padx=10)
    
    # Info-Text
    stepper_info = tk.Label(
        stepper_frame,
        text="Geschwindigkeit des Schiebers beim Teile-Transport",
        font=('Helvetica', 9),
        bg='#2b2b2b',
        fg='#cccccc',
        justify='left'
    )
    stepper_info.pack(pady=(10, 5), padx=10)
    
    # Geschwindigkeits-Slider
    speed_row_frame = tk.Frame(stepper_frame, bg='#2b2b2b')
    speed_row_frame.pack(fill='x', padx=10, pady=10)
    
    speed_label = tk.Label(
        speed_row_frame,
        text="Geschwindigkeit:",
        font=('Helvetica', 10),
        bg='#2b2b2b',
        fg='white',
        width=20,
        anchor='w'
    )
    speed_label.pack(side='left')
    
    # Wert-Anzeige
    speed_value_label = tk.Label(
        speed_row_frame,
        text=f"{stepper.speed_percentage}% ({stepper.default_speed_hz} Hz)",
        font=('Helvetica', 10, 'bold'),
        bg='#2b2b2b',
        fg='#00ff00',
        width=15
    )
    speed_value_label.pack(side='right', padx=(5, 0))
    
    # Slider (10-100%)
    speed_slider = tk.Scale(
        speed_row_frame,
        from_=10,
        to=100,
        orient='horizontal',
        bg='#2b2b2b',
        fg='white',
        highlightthickness=0,
        troughcolor='#3a3a3a',
        activebackground='#4a4a4a',
        resolution=5,  # Schritte von 5%
        command=lambda val: update_stepper_speed(val, speed_value_label, stepper)
    )
    speed_slider.set(stepper.speed_percentage)
    speed_slider.pack(side='right', fill='x', expand=True, padx=(10, 5))
    
    def update_stepper_speed(value, value_label, stepper_ctrl):
        """Update Schieber-Geschwindigkeit"""
        percentage = int(value)
        hz_value = int((percentage / 100.0) * stepper_ctrl.max_speed_hz)
        value_label.config(text=f"{percentage}% ({hz_value} Hz)")
        stepper_ctrl.set_speed_percentage(percentage)
    
    # --- Klappen-Servo Steuerung ---
    gate_frame = tk.LabelFrame(
        main_tab,
        text="Klappen-Steuerung",
        font=('Helvetica', 12, 'bold'),
        bg='#2b2b2b',
        fg='white',
        relief='flat',
        bd=2
    )
    gate_frame.pack(fill='x', pady=(0, 15), padx=10)
    
    gate_control_frame = tk.Frame(gate_frame, bg='#2b2b2b')
    gate_control_frame.pack(pady=15)
    
    # Klappe öffnen Button
    open_gate_btn = tk.Button(
        gate_control_frame,
        text="🔓 Klappe öffnen",
        font=('Helvetica', 11, 'bold'),
        bg='#2d6a2e',
        fg='white',
        activebackground='#3d7a3e',
        activeforeground='white',
        relief='flat',
        padx=20,
        pady=10,
        cursor='hand2',
        command=servo.open_gate
    )
    open_gate_btn.pack(side='left', padx=5)
    
    # Klappe schließen Button
    close_gate_btn = tk.Button(
        gate_control_frame,
        text="🔒 Klappe schließen",
        font=('Helvetica', 11, 'bold'),
        bg='#8b2e2e',
        fg='white',
        activebackground='#9b3e3e',
        activeforeground='white',
        relief='flat',
        padx=20,
        pady=10,
        cursor='hand2',
        command=servo.close_gate
    )
    close_gate_btn.pack(side='left', padx=5)
    
    # --- Rüttler Steuerung ---
    vibration_frame = tk.LabelFrame(
        main_tab,
        text="Rüttler-Steuerung",
        font=('Helvetica', 12, 'bold'),
        bg='#2b2b2b',
        fg='white',
        relief='flat',
        bd=2
    )
    vibration_frame.pack(fill='x', pady=(0, 15), padx=10)
    
    # Vibration Controller Referenz
    vibration = get_vibration_controller()
    
    # Duty Cycle Slider
    duty_row_frame = tk.Frame(vibration_frame, bg='#2b2b2b')
    duty_row_frame.pack(fill='x', padx=10, pady=10)
    
    duty_label = tk.Label(
        duty_row_frame,
        text="Intensität (Duty Cycle):",
        font=('Helvetica', 10),
        bg='#2b2b2b',
        fg='white',
        width=20,
        anchor='w'
    )
    duty_label.pack(side='left')
    
    # Wert-Anzeige
    duty_value_label = tk.Label(
        duty_row_frame,
        text=f"{vibration.duty_cycle}%",
        font=('Helvetica', 10, 'bold'),
        bg='#2b2b2b',
        fg='#00ff00',
        width=6
    )
    duty_value_label.pack(side='right', padx=(5, 0))
    
    # Duty Cycle Slider
    duty_slider = tk.Scale(
        duty_row_frame,
        from_=0,
        to=100,
        orient='horizontal',
        bg='#2b2b2b',
        fg='white',
        highlightthickness=0,
        troughcolor='#3a3a3a',
        activebackground='#4a4a4a',
        command=lambda val: update_duty_cycle(val, duty_value_label, vibration)
    )
    duty_slider.set(vibration.duty_cycle)
    duty_slider.pack(side='right', fill='x', expand=True, padx=(10, 5))
    
    def update_duty_cycle(value, value_label, vib_ctrl):
        """Update Duty Cycle"""
        value_label.config(text=f"{value}%")
        vib_ctrl.set_duty_cycle(int(value))
    
    # Test Button
    test_row_frame = tk.Frame(vibration_frame, bg='#2b2b2b')
    test_row_frame.pack(pady=(0, 15))
    
    test_vibration_btn = tk.Button(
        test_row_frame,
        text="🔊 Rüttler testen (2 Sek.)",
        font=('Helvetica', 11, 'bold'),
        bg='#6a4c2e',
        fg='white',
        activebackground='#7a5c3e',
        activeforeground='white',
        relief='flat',
        padx=20,
        pady=10,
        cursor='hand2',
        command=lambda: vibration.test(duration=2.0)
    )
    test_vibration_btn.pack()
    
    # ==================== DEBUG / ERWEITERT TAB ====================
    
    # --- Teile-Erkennung (Motion Detection) ---
    if automation_controller:
        motion_frame = tk.LabelFrame(
            debug_tab,
            text="Teile-Erkennung (Schleuse)",
            font=('Helvetica', 12, 'bold'),
            bg='#2b2b2b',
            fg='white',
            relief='flat',
            bd=2
        )
        motion_frame.pack(fill='x', pady=(10, 15), padx=10)
        
        # Info-Text
        info_label = tk.Label(
            motion_frame,
            text="Empfindlichkeit der Bewegungserkennung in der Schleuse.\nNiedriger Wert = empfindlicher, höherer Wert = weniger empfindlich.",
            font=('Helvetica', 9),
            bg='#2b2b2b',
            fg='#cccccc',
            justify='left',
            wraplength=550
        )
        info_label.pack(pady=(10, 5), padx=10)
        
        # Empfindlichkeit Slider
        sensitivity_row_frame = tk.Frame(motion_frame, bg='#2b2b2b')
        sensitivity_row_frame.pack(fill='x', padx=10, pady=10)
        
        sensitivity_label = tk.Label(
            sensitivity_row_frame,
            text="Schwellwert (Pixel):",
            font=('Helvetica', 10),
            bg='#2b2b2b',
            fg='white',
            width=20,
            anchor='w'
        )
        sensitivity_label.pack(side='left')
        
        # Wert-Anzeige
        sensitivity_value_label = tk.Label(
            sensitivity_row_frame,
            text=f"{automation_controller.motion_threshold}",
            font=('Helvetica', 10, 'bold'),
            bg='#2b2b2b',
            fg='#00ff00',
            width=6
        )
        sensitivity_value_label.pack(side='right', padx=(5, 0))
        
        # Empfindlichkeits-Slider (500-5000 Pixel)
        sensitivity_slider = tk.Scale(
            sensitivity_row_frame,
            from_=500,
            to=5000,
            orient='horizontal',
            bg='#2b2b2b',
            fg='white',
            highlightthickness=0,
            troughcolor='#3a3a3a',
            activebackground='#4a4a4a',
            resolution=100,  # Schritte von 100
            command=lambda val: update_motion_threshold(val, sensitivity_value_label, automation_controller)
        )
        sensitivity_slider.set(automation_controller.motion_threshold)
        sensitivity_slider.pack(side='right', fill='x', expand=True, padx=(10, 5))
        
        def update_motion_threshold(value, value_label, automation_ctrl):
            """Update Motion Threshold"""
            value_label.config(text=f"{value}")
            automation_ctrl.motion_threshold = int(value)
            print(f"Motion Threshold gesetzt auf: {value} Pixel")
        
        # Empfehlungen
        recommendation_label = tk.Label(
            motion_frame,
            text="Empfohlen: 2000 (Standard) | Sehr empfindlich: 500-1000 | Wenig empfindlich: 3000-5000",
            font=('Helvetica', 8, 'italic'),
            bg='#2b2b2b',
            fg='#888888',
            justify='left'
        )
        recommendation_label.pack(pady=(0, 10), padx=10)
    
    # --- Kamera Preview Steuerung ---
    if automation_controller:
        camera_frame = tk.LabelFrame(
            debug_tab,
            text="Kamera Preview",
            font=('Helvetica', 12, 'bold'),
            bg='#2b2b2b',
            fg='white',
            relief='flat',
            bd=2
        )
        camera_frame.pack(fill='x', pady=(0, 15), padx=10)
        
        # Info-Text
        camera_info_label = tk.Label(
            camera_frame,
            text="Kamera-Vorschau aktivieren/deaktivieren.\nDeaktivierung kann Systemressourcen sparen.",
            font=('Helvetica', 9),
            bg='#2b2b2b',
            fg='#cccccc',
            justify='left',
            wraplength=550
        )
        camera_info_label.pack(pady=(10, 5), padx=10)
        
        # Live-Vorschau-Bereich
        preview_display_frame = tk.Frame(camera_frame, bg='#1a1a1a', relief='solid', bd=2)
        preview_display_frame.pack(pady=(5, 10), padx=10)
        
        # Label für Kamera-Vorschau
        preview_image_label = tk.Label(
            preview_display_frame,
            bg='#1a1a1a',
            text="📷 Kamera-Vorschau\n(wird bei Aktivierung angezeigt)",
            font=('Helvetica', 10),
            fg='#888888',
            width=60,
            height=15
        )
        preview_image_label.pack(padx=5, pady=5)
        
        # Variable für Update-Job
        preview_update_job = [None]  # Liste verwenden für Closure
        
        def update_preview_image():
            """Aktualisiert das Kamera-Vorschaubild"""
            try:
                if automation_controller.preview_enabled and preview_update_job[0] is not None:
                    # Temporäres Bild aufnehmen
                    temp_preview_path = "/tmp/settings_preview.jpg"
                    picam2.start()
                    picam2.capture_file(temp_preview_path)
                    picam2.stop()
                    
                    # Bild laden und skalieren
                    img = Image.open(temp_preview_path)
                    # Auf 400x300 skalieren für Anzeige
                    img.thumbnail((400, 300), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    
                    # Bild anzeigen
                    preview_image_label.config(image=photo, text="")
                    preview_image_label.image = photo  # Referenz behalten
                    
                    # Nächstes Update in 500ms
                    preview_update_job[0] = settings_win.after(500, update_preview_image)
            except Exception as e:
                print(f"Fehler beim Aktualisieren der Vorschau: {e}")
                # Bei Fehler trotzdem weiter versuchen
                if preview_update_job[0] is not None:
                    preview_update_job[0] = settings_win.after(1000, update_preview_image)
        
        # Status-Anzeige
        preview_status_frame = tk.Frame(camera_frame, bg='#2b2b2b')
        preview_status_frame.pack(pady=(5, 10))
        
        preview_status_label = tk.Label(
            preview_status_frame,
            text=f"Status: {'🟢 Aktiviert' if automation_controller.preview_enabled else '🔴 Deaktiviert'}",
            font=('Helvetica', 10, 'bold'),
            bg='#2b2b2b',
            fg='#00ff00' if automation_controller.preview_enabled else '#ff4444',
            justify='center'
        )
        preview_status_label.pack()
        
        # Buttons
        preview_button_frame = tk.Frame(camera_frame, bg='#2b2b2b')
        preview_button_frame.pack(pady=(0, 15))
        
        def toggle_preview(enable, status_label):
            """Aktiviert oder deaktiviert die Kamera-Preview"""
            try:
                automation_controller.preview_enabled = enable
                if enable:
                    picam2.start_preview()
                    status_label.config(
                        text="Status: 🟢 Aktiviert",
                        fg='#00ff00'
                    )
                    print("Kamera Preview aktiviert")
                    # Starte Live-Vorschau-Updates
                    if preview_update_job[0] is None:
                        preview_update_job[0] = settings_win.after(100, update_preview_image)
                else:
                    # Stoppe Live-Vorschau-Updates
                    if preview_update_job[0] is not None:
                        settings_win.after_cancel(preview_update_job[0])
                        preview_update_job[0] = None
                    
                    picam2.stop_preview()
                    status_label.config(
                        text="Status: 🔴 Deaktiviert",
                        fg='#ff4444'
                    )
                    # Setze Platzhalter zurück
                    preview_image_label.config(
                        image='',
                        text="📷 Kamera-Vorschau\n(wird bei Aktivierung angezeigt)",
                        fg='#888888'
                    )
                    preview_image_label.image = None
                    print("Kamera Preview deaktiviert")
            except Exception as e:
                print(f"Fehler beim Umschalten der Preview: {e}")
                status_label.config(
                    text=f"Status: ⚠️ Fehler",
                    fg='#ffaa00'
                )
        
        # Preview aktivieren Button
        enable_preview_btn = tk.Button(
            preview_button_frame,
            text="🟢 Aktivieren",
            font=('Helvetica', 11, 'bold'),
            bg='#2d6a2e',
            fg='white',
            activebackground='#3d7a3e',
            activeforeground='white',
            relief='flat',
            padx=20,
            pady=10,
            cursor='hand2',
            command=lambda: toggle_preview(True, preview_status_label)
        )
        enable_preview_btn.pack(side='left', padx=5)
        
        # Preview deaktivieren Button
        disable_preview_btn = tk.Button(
            preview_button_frame,
            text="🔴 Deaktivieren",
            font=('Helvetica', 11, 'bold'),
            bg='#8b2e2e',
            fg='white',
            activebackground='#9b3e3e',
            activeforeground='white',
            relief='flat',
            padx=20,
            pady=10,
            cursor='hand2',
            command=lambda: toggle_preview(False, preview_status_label)
        )
        disable_preview_btn.pack(side='left', padx=5)
        
        # Starte Preview-Updates wenn bereits aktiviert
        if automation_controller.preview_enabled:
            preview_update_job[0] = settings_win.after(100, update_preview_image)
    
    # --- Sensor Status Anzeige ---
    sensor_frame = tk.LabelFrame(
        debug_tab,
        text="Sensor Status (Live)",
        font=('Helvetica', 12, 'bold'),
        bg='#2b2b2b',
        fg='white',
        relief='flat',
        bd=2
    )
    sensor_frame.pack(fill='x', pady=(0, 15), padx=10)
    
    # Info-Text
    sensor_info_label = tk.Label(
        sensor_frame,
        text="Live-Anzeige der Sensor-Werte. Updates alle 200ms.",
        font=('Helvetica', 9),
        bg='#2b2b2b',
        fg='#cccccc',
        justify='left'
    )
    sensor_info_label.pack(pady=(10, 5), padx=10)
    
    # Sensor-Anzeige Container
    sensor_display_frame = tk.Frame(sensor_frame, bg='#2b2b2b')
    sensor_display_frame.pack(pady=(10, 15), padx=10, fill='x')
    
    # Unterer Sensor
    lower_sensor_frame = tk.Frame(sensor_display_frame, bg='#1a1a1a', relief='solid', bd=2)
    lower_sensor_frame.pack(side='left', expand=True, fill='both', padx=(0, 5))
    
    lower_sensor_title = tk.Label(
        lower_sensor_frame,
        text="Unterer Sensor (NO)",
        font=('Helvetica', 10, 'bold'),
        bg='#1a1a1a',
        fg='white'
    )
    lower_sensor_title.pack(pady=(10, 5))
    
    lower_sensor_pin_label = tk.Label(
        lower_sensor_frame,
        text=f"GPIO {SENSOR_LOWER_PIN['bcm']} (WiringPi {SENSOR_LOWER_PIN['wiring']})",
        font=('Helvetica', 8),
        bg='#1a1a1a',
        fg='#888888'
    )
    lower_sensor_pin_label.pack(pady=(0, 10))
    
    lower_sensor_value_label = tk.Label(
        lower_sensor_frame,
        text="---",
        font=('Helvetica', 14, 'bold'),
        bg='#1a1a1a',
        fg='#888888',
        width=15
    )
    lower_sensor_value_label.pack(pady=(5, 5))
    
    lower_sensor_status_label = tk.Label(
        lower_sensor_frame,
        text="Status: ---",
        font=('Helvetica', 9),
        bg='#1a1a1a',
        fg='#888888'
    )
    lower_sensor_status_label.pack(pady=(5, 10))
    
    # Oberer Sensor
    upper_sensor_frame = tk.Frame(sensor_display_frame, bg='#1a1a1a', relief='solid', bd=2)
    upper_sensor_frame.pack(side='left', expand=True, fill='both', padx=(5, 0))
    
    upper_sensor_title = tk.Label(
        upper_sensor_frame,
        text="Oberer Sensor (Pull-Down)",
        font=('Helvetica', 10, 'bold'),
        bg='#1a1a1a',
        fg='white'
    )
    upper_sensor_title.pack(pady=(10, 5))
    
    upper_sensor_pin_label = tk.Label(
        upper_sensor_frame,
        text=f"GPIO {SENSOR_UPPER_PIN['bcm']} (WiringPi {SENSOR_UPPER_PIN['wiring']})",
        font=('Helvetica', 8),
        bg='#1a1a1a',
        fg='#888888'
    )
    upper_sensor_pin_label.pack(pady=(0, 10))
    
    upper_sensor_value_label = tk.Label(
        upper_sensor_frame,
        text="---",
        font=('Helvetica', 14, 'bold'),
        bg='#1a1a1a',
        fg='#888888',
        width=15
    )
    upper_sensor_value_label.pack(pady=(5, 5))
    
    upper_sensor_status_label = tk.Label(
        upper_sensor_frame,
        text="Status: ---",
        font=('Helvetica', 9),
        bg='#1a1a1a',
        fg='#888888'
    )
    upper_sensor_status_label.pack(pady=(5, 10))
    
    # Update-Job für Sensor-Werte
    sensor_update_job = [None]
    
    def update_sensor_display():
        """Aktualisiert die Sensor-Anzeige"""
        try:
            if sensor_update_job[0] is not None:
                stepper = get_stepper_controller()
                
                if stepper.gpio_initialized and GPIO_AVAILABLE:
                    # Sensor-Werte lesen
                    lower_val = GPIO.input(SENSOR_LOWER_PIN["bcm"])
                    upper_val = GPIO.input(SENSOR_UPPER_PIN["bcm"])
                    
                    # Unterer Sensor (NO: 0=angeschlagen, 1=frei)
                    lower_sensor_value_label.config(
                        text=f"{'HIGH (1)' if lower_val else 'LOW (0)'}",
                        fg='#00ff00' if lower_val else '#ff4444'
                    )
                    
                    if lower_val == 0:
                        lower_sensor_status_label.config(
                            text="Status: 🔴 ANGESCHLAGEN",
                            fg='#ff4444'
                        )
                    else:
                        lower_sensor_status_label.config(
                            text="Status: 🟢 FREI",
                            fg='#00ff00'
                        )
                    
                    # Oberer Sensor (Pull-Down: 1=angeschlagen, 0=frei)
                    upper_sensor_value_label.config(
                        text=f"{'HIGH (1)' if upper_val else 'LOW (0)'}",
                        fg='#00ff00' if upper_val else '#ff4444'
                    )
                    
                    if upper_val == 1:
                        upper_sensor_status_label.config(
                            text="Status: 🔴 ANGESCHLAGEN",
                            fg='#ff4444'
                        )
                    else:
                        upper_sensor_status_label.config(
                            text="Status: 🟢 FREI",
                            fg='#00ff00'
                        )
                else:
                    # GPIO nicht verfügbar - Simulationsmodus
                    lower_sensor_value_label.config(text="SIMULATION", fg='#888888')
                    lower_sensor_status_label.config(text="Status: GPIO nicht verfügbar", fg='#888888')
                    upper_sensor_value_label.config(text="SIMULATION", fg='#888888')
                    upper_sensor_status_label.config(text="Status: GPIO nicht verfügbar", fg='#888888')
                
                # Nächstes Update in 200ms
                sensor_update_job[0] = settings_win.after(200, update_sensor_display)
        except Exception as e:
            print(f"Fehler beim Aktualisieren der Sensor-Anzeige: {e}")
            # Bei Fehler trotzdem weiter versuchen
            if sensor_update_job[0] is not None:
                sensor_update_job[0] = settings_win.after(500, update_sensor_display)
    
    # Starte Sensor-Updates
    sensor_update_job[0] = settings_win.after(100, update_sensor_display)
    
    # Cleanup beim Schließen des Fensters
    def on_closing():
        # Stoppe Preview-Updates
        if automation_controller and 'preview_update_job' in locals():
            if preview_update_job[0] is not None:
                settings_win.after_cancel(preview_update_job[0])
                preview_update_job[0] = None
        
        # Stoppe Sensor-Updates
        if sensor_update_job[0] is not None:
            settings_win.after_cancel(sensor_update_job[0])
            sensor_update_job[0] = None
        
        settings_win.destroy()
    
    settings_win.protocol("WM_DELETE_WINDOW", on_closing)
    
    # --- Buttons unten ---
    button_frame = tk.Frame(main_frame, bg='#1e1e1e')
    button_frame.pack(pady=(15, 0))
    
    # Schließen Button
    close_btn = tk.Button(
        button_frame,
        text="Schließen",
        font=('Helvetica', 11),
        bg='#3a3a3a',
        fg='white',
        activebackground='#4a4a4a',
        activeforeground='white',
        relief='flat',
        padx=30,
        pady=8,
        cursor='hand2',
        command=settings_win.destroy
    )
    close_btn.pack()
    
    # Fenster zentrieren
    settings_win.update_idletasks()
    x = parent_root.winfo_x() + (parent_root.winfo_width() // 2) - (settings_win.winfo_width() // 2)
    y = parent_root.winfo_y() + (parent_root.winfo_height() // 2) - (settings_win.winfo_height() // 2)
    settings_win.geometry(f"+{x}+{y}")
    
    # Fenster modal machen
    settings_win.transient(parent_root)
    settings_win.grab_set()

# Hauptcontainer (minimal Padding für 7-Zoll Display)
main_container = tk.Frame(root, bg='#1e1e1e')
main_container.pack(fill='both', expand=True, padx=5, pady=5)

# Header (kompakt)
header_frame = tk.Frame(main_container, bg='#1e1e1e')
header_frame.pack(fill='x', pady=(0, 5))

title_label = tk.Label(
    header_frame,
    text="LEGO Sortierer",
    font=('Helvetica', 18, 'bold'),
    bg='#1e1e1e',
    fg='white'
)
title_label.pack(side='left', expand=True)

# Settings-Button rechts oben (wird später mit automation verknüpft)
settings_button = tk.Button(
    header_frame,
    text="⚙️",
    font=('Helvetica', 16),
    bg='#3a3a3a',
    fg='white',
    activebackground='#4a4a4a',
    activeforeground='white',
    relief='flat',
    bd=0,
    padx=15,
    pady=5,
    cursor='hand2'
)
settings_button.pack(side='right')

# Set-Eingabe Bereich (kompakt)
set_input_frame = tk.Frame(main_container, bg='#2b2b2b', relief='flat', bd=1)
set_input_frame.pack(fill='x', pady=(0, 5))

set_label = tk.Label(
    set_input_frame,
    text="Setnummer:",
    font=('Helvetica', 12),
    bg='#2b2b2b',
    fg='white'
)
set_label.pack(pady=(5, 3))

set_number = tk.StringVar(root)
entry_set_number = tk.Entry(
    set_input_frame,
    textvariable=set_number,
    font=('Helvetica', 18),
    bg='#3c3c3c',
    fg='white',
    insertbackground='white',
    relief='flat',
    justify='center'
)
entry_set_number.pack(pady=(0, 5), padx=10, ipady=8)

# Variable für Touch-Tastatur
touch_keyboard_popup = None

def show_numeric_keyboard():
    """Zeigt eine Touch-Tastatur für Zahleneingabe an."""
    global touch_keyboard_popup
    
    # Wenn Tastatur bereits offen, nicht öffnen
    if touch_keyboard_popup is not None and touch_keyboard_popup.winfo_exists():
        touch_keyboard_popup.lift()
        return
    
    # Erstelle Popup-Fenster
    touch_keyboard_popup = tk.Toplevel(root)
    touch_keyboard_popup.title("Setnummer eingeben")
    touch_keyboard_popup.geometry("600x420")
    touch_keyboard_popup.config(bg='#1e1e1e')
    
    # Positioniere über dem Eingabefeld (relativ zu Root-Fenster)
    try:
        x = root.winfo_x() + (root.winfo_width() // 2) - 300
        y = root.winfo_y() + 250
        touch_keyboard_popup.geometry(f"600x420+{x}+{y}")
    except:
        pass
    
    # Mache Fenster "always on top"
    touch_keyboard_popup.attributes('-topmost', True)
    
    # Setze als transitentes Fenster relativ zum Hauptfenster
    touch_keyboard_popup.transient(root)
    
    # Titel-Label
    title_label = tk.Label(
        touch_keyboard_popup,
        text="Setnummer eingeben (z.B. 4723-1,31058)",
        font=('Helvetica', 14, 'bold'),
        bg='#1e1e1e',
        fg='white'
    )
    title_label.pack(pady=10)
    
    # Display für aktuelle Eingabe (Live-Anzeige)
    display_frame = tk.Frame(touch_keyboard_popup, bg='#2b2b2b', relief='flat', bd=2)
    display_frame.pack(fill='x', padx=10, pady=(0, 15))
    
    display_label = tk.Label(
        display_frame,
        textvariable=set_number,
        font=('Helvetica', 20, 'bold'),
        bg='#2b2b2b',
        fg='#4caf50',
        height=2,
        relief='flat'
    )
    display_label.pack(fill='both', expand=True, padx=10, pady=10)
    
    # Tastatur-Frame
    keyboard_frame = tk.Frame(touch_keyboard_popup, bg='#1e1e1e')
    keyboard_frame.pack(fill='both', expand=True, padx=10, pady=10)
    
    # Tastenlayout (ähnlich wie Handy-Numpad)
    button_layout = [
        ['1', '2', '3'],
        ['4', '5', '6'],
        ['7', '8', '9'],
        ['0', ',', '⌫'],
    ]
    
    def add_to_entry(char):
        """Fügt Zeichen zum Eingabefeld hinzu."""
        print(f"Button geklickt: {char}")  # Debug
        try:
            if char == '⌫':  # Backspace
                current = set_number.get()
                set_number.set(current[:-1])
                print(f"Backspace: '{current}' -> '{current[:-1]}'")
            else:
                new_value = set_number.get() + char
                set_number.set(new_value)
                print(f"Hinzugefügt: '{char}' -> '{new_value}'")
        except Exception as e:
            print(f"Fehler in add_to_entry: {e}")
    
    def close_keyboard():
        """Schließt die Tastatur."""
        print("Fertig-Button geklickt")
        try:
            global touch_keyboard_popup
            if touch_keyboard_popup:
                touch_keyboard_popup.grab_release()  # Event-Grab freigeben
                touch_keyboard_popup.destroy()
                touch_keyboard_popup = None
        except Exception as e:
            print(f"Fehler beim Schließen: {e}")
    
    def clear_entry():
        """Löscht die komplette Eingabe."""
        print("Löschen-Button geklickt")
        try:
            set_number.set("")
        except Exception as e:
            print(f"Fehler beim Löschen: {e}")
    
    # Erstelle Buttons - WICHTIG: Buttons in Liste speichern damit sie nicht gelöscht werden
    buttons = []
    for row_idx, row in enumerate(button_layout):
        row_frame = tk.Frame(keyboard_frame, bg='#1e1e1e')
        row_frame.pack(fill='both', expand=True, pady=5)
        
        for col_idx, char in enumerate(row):
            # Farben für verschiedene Button-Typen
            if char == '⌫':
                bg_color = '#ff5722'  # Rot für Backspace
                active_bg = '#d84315'
            elif char == ',':
                bg_color = '#ff9800'  # Orange für Komma
                active_bg = '#f57c00'
            else:
                bg_color = '#2196f3'  # Blau für Ziffern
                active_bg = '#1976d2'
            
            btn = tk.Button(
                row_frame,
                text=char,
                font=('Helvetica', 22, 'bold'),
                bg=bg_color,
                fg='white',
                activebackground=active_bg,
                activeforeground='white',
                relief='flat',
                bd=0,
                cursor='hand2'
            )
            # Setze Command nach Erstellung mit functools.partial
            import functools
            btn.config(command=functools.partial(add_to_entry, char))
            btn.pack(side='left', fill='both', expand=True, padx=3)
            buttons.append(btn)  # Referenz behalten
    
    # Bottom-Button Frame
    bottom_frame = tk.Frame(touch_keyboard_popup, bg='#1e1e1e')
    bottom_frame.pack(fill='x', padx=10, pady=(10, 10))
    
    # Clear-Button (links)
    clear_btn = tk.Button(
        bottom_frame,
        text="⊘ Löschen",
        font=('Helvetica', 14, 'bold'),
        bg='#d32f2f',
        fg='white',
        activebackground='#c62828',
        activeforeground='white',
        relief='flat',
        bd=0,
        command=clear_entry,
        cursor='hand2'
    )
    clear_btn.pack(side='left', fill='both', expand=True, padx=3)
    
    # OK-Button (rechts)
    ok_button = tk.Button(
        bottom_frame,
        text="✓ Fertig",
        font=('Helvetica', 14, 'bold'),
        bg='#4caf50',
        fg='white',
        activebackground='#388e3c',
        activeforeground='white',
        relief='flat',
        bd=0,
        command=close_keyboard,
        cursor='hand2'
    )
    ok_button.pack(side='left', fill='both', expand=True, padx=3)
    
    # Wenn Fenster geschlossen wird
    def on_close():
        global touch_keyboard_popup
        try:
            if touch_keyboard_popup:
                touch_keyboard_popup.grab_release()  # Event-Grab freigeben
        except:
            pass
        touch_keyboard_popup = None
    
    touch_keyboard_popup.protocol("WM_DELETE_WINDOW", lambda: (on_close(), touch_keyboard_popup.destroy() if touch_keyboard_popup else None))
    
    # JETZT erst Events abfangen und Fokus setzen (nachdem alle Widgets erstellt sind)
    touch_keyboard_popup.update_idletasks()  # Stelle sicher, dass alles gerendert ist
    touch_keyboard_popup.grab_set()  # Fange alle Events ab
    touch_keyboard_popup.focus_force()  # Fokus setzen

# Bind Klick auf Entry-Feld zum Zeigen der Tastatur
entry_set_number.bind('<Button-1>', lambda e: show_numeric_keyboard())

set_hint_label = tk.Label(
    set_input_frame,
    text="Mit Komma trennen (4723-1,31058)",
    font=('Helvetica', 10),
    bg='#2b2b2b',
    fg='#888888'
)
set_hint_label.pack(pady=(0, 5))

# Button-Frame für Set-Verwaltung
set_button_frame = tk.Frame(set_input_frame, bg='#2b2b2b')
set_button_frame.pack(pady=(0, 15))

load_set_button = tk.Button(
    set_button_frame,
    text="Laden",
    font=('Helvetica', 14, 'bold'),
    bg='#2196f3',
    fg='white',
    activebackground='#1976d2',
    activeforeground='white',
    relief='flat',
    bd=0,
    padx=20,
    pady=8,
    cursor='hand2'
)
load_set_button.pack(side='left', padx=3)

clear_sets_button = tk.Button(
    set_button_frame,
    text="Reset",
    font=('Helvetica', 14, 'bold'),
    bg='#ff5722',
    fg='white',
    activebackground='#e64a19',
    activeforeground='white',
    relief='flat',
    bd=0,
    padx=20,
    pady=8,
    cursor='hand2'
)
clear_sets_button.pack(side='left', padx=5)

# Set-Info Anzeige
set_info_frame = tk.Frame(main_container, bg='#2b2b2b', relief='flat', bd=2)
set_info_frame.pack(fill='x', pady=(0, 20))
set_info_frame.pack_forget()  # Initial versteckt

set_info_title = tk.Label(
    set_info_frame,
    text="Sets:",
    font=('Helvetica', 14, 'bold'),
    bg='#2b2b2b',
    fg='white'
)
set_info_title.pack(pady=(8, 5))

# Container für Set-Details (Text + Bilder)
set_details_container = tk.Frame(set_info_frame, bg='#2b2b2b')
set_details_container.pack(pady=(0, 15), padx=20, fill='both')

set_info_label = tk.Label(
    set_details_container,
    text="",
    font=('Helvetica', 11),
    bg='#2b2b2b',
    fg='#4caf50',
    justify='left'
)
set_info_label.pack(side='left', fill='both', expand=True)

# Container für Set-Bilder
set_images_container = tk.Frame(set_details_container, bg='#2b2b2b')
set_images_container.pack(side='left', padx=(20, 0))

# Status-Label
status_label = tk.Label(
    main_container,
    text="Bereit",
    font=('Helvetica', 14),
    bg='#1e1e1e',
    fg='#4caf50'
)
status_label.pack(pady=(0, 8))

# Fortschrittsbereich (initial versteckt)
progress_frame = tk.Frame(main_container, bg='#2b2b2b', relief='flat', bd=2)
progress_frame.pack_forget()  # Initial versteckt

# Aktueller Status während Sortierung
current_status_label = tk.Label(
    progress_frame,
    text="Sortierung läuft...",
    font=('Helvetica', 13, 'bold'),
    bg='#2b2b2b',
    fg='#2196f3'
)
current_status_label.pack(pady=(5, 5))

# Scrollable Canvas für Set-Fortschritt (reduzierte Höhe für 7-Zoll)
progress_canvas_frame = tk.Frame(progress_frame, bg='#2b2b2b')
progress_canvas_frame.pack(fill='both', expand=True, padx=10, pady=5)

progress_canvas = tk.Canvas(
    progress_canvas_frame,
    bg='#2b2b2b',
    highlightthickness=0,
    height=100
)
progress_canvas.pack(fill='both', expand=True)

# Frame für die Set-Progress-Widgets
sets_progress_frame = tk.Frame(progress_canvas, bg='#2b2b2b')
progress_canvas.create_window((0, 0), window=sets_progress_frame, anchor='nw')
sets_progress_frame.bind('<Configure>', lambda e: progress_canvas.configure(scrollregion=progress_canvas.bbox('all')))

# Steuerungs-Buttons (Pause/Stop) - kompakter
control_buttons_frame = tk.Frame(progress_frame, bg='#2b2b2b')
control_buttons_frame.pack(pady=(8, 8))

pause_button = tk.Button(
    control_buttons_frame,
    text="Pause",
    font=('Helvetica', 12, 'bold'),
    bg='#ff9800',
    fg='white',
    activebackground='#f57c00',
    activeforeground='white',
    relief='flat',
    bd=0,
    padx=15,
    pady=6,
    cursor='hand2'
)
pause_button.pack(side='left', padx=3)

stop_button = tk.Button(
    control_buttons_frame,
    text="Stop",
    font=('Helvetica', 12, 'bold'),
    bg='#f44336',
    fg='white',
    activebackground='#d32f2f',
    activeforeground='white',
    relief='flat',
    bd=0,
    padx=15,
    pady=6,
    cursor='hand2'
)
stop_button.pack(side='left', padx=5)

# Debug-Panel für Stepper/Sensoren (nur während Automatik sichtbar)
debug_frame = tk.Frame(progress_frame, bg='#1e1e1e', relief='flat', bd=2)
debug_frame.pack(pady=(5, 5), padx=10, fill='x')

debug_title = tk.Label(
    debug_frame,
    text="🔧 Debug Info",
    font=('Helvetica', 10, 'bold'),
    bg='#1e1e1e',
    fg='#4caf50'
)
debug_title.pack(pady=(3, 3))

# Stepper Status
stepper_status_label = tk.Label(
    debug_frame,
    text="Schieber: ---",
    font=('Courier', 9),
    bg='#1e1e1e',
    fg='#ffffff',
    justify='left'
)
stepper_status_label.pack(pady=2)

# Sensor Status
sensor_status_label = tk.Label(
    debug_frame,
    text="Sensoren: Oben=? | Unten=?",
    font=('Courier', 9),
    bg='#1e1e1e',
    fg='#ffffff',
    justify='left'
)
sensor_status_label.pack(pady=2)

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

    def __init__(self, tk_root: tk.Tk, progress_frame, current_status_lbl, status_lbl, set_info_frame, set_info_lbl, set_images_container, start_btn, pause_btn, stop_btn, sets_progress_frame_ref, set_input_frame_ref, stepper_status_lbl, sensor_status_lbl):
        self.root = tk_root
        self.state = AutomationState.INIT
        self.running = False
        self.paused = False
        self.thread = None
        # Platzhalter: GPIO-Setup (optional)
        self._gpio_initialized = False
        # Cache für Teile aus ausgewählten Sets (Aggregation)
        self.set_numbers: list[str] = []
        self.cached_set_parts: list[dict] = []
        self.loaded_sets_info: list[dict] = []  # Info über geladene Sets
        # Tracking für gefundene Teile pro Set: dict[set_number, dict[(part_id, color_name), count]]
        self.found_parts_per_set: dict[str, dict[tuple, int]] = {}
        self.parts_per_set: dict[str, list[dict]] = {}  # Original-Teile pro Set
        # GUI-Referenzen
        self.progress_frame = progress_frame
        self.current_status_label = current_status_lbl
        self.status_label = status_lbl
        self.set_info_frame = set_info_frame
        self.set_info_label = set_info_lbl
        self.set_images_container = set_images_container
        self.sets_progress_frame = sets_progress_frame_ref
        self.set_input_frame = set_input_frame_ref
        self.set_image_labels: list[tk.Label] = []  # Liste der Bild-Labels
        self.start_button = start_btn
        self.pause_button = pause_btn
        self.stop_button = stop_btn
        self.progress_bars: dict[str, tk.Canvas] = {}  # Fortschrittsbalken pro Set
        self.progress_labels: dict[str, tk.Label] = {}  # Labels pro Set
        
        # Motion Detection Variablen
        self.previous_frame = None
        self.motion_threshold = 1000  # Anzahl geänderter Pixel für Erkennung (empfindlicher)
        self.motion_detection_active = False
        self.part_detected = False
        self.last_motion_time = 0
        
        # Kamera Preview Einstellung
        self.preview_enabled = True  # Standardmäßig aktiviert
        
        # Sortier-Logik: Box-Zuordnung für Servo
        # Box 1-4: Sortier-Positionen (box1, box2, box3, box4 im ServoController)
        self.box_positions = ["box1", "box2", "box3", "box4"]  # Position-Namen für Servo
        self.set_to_box: dict[str, int] = {}  # Mapping: set_number -> box_index (0-3)
        self.reject_box = 3  # Box 4 (Index 3) für Ausschuss
        self.current_part_box = self.reject_box  # Aktuell zu sortierende Box
        self.current_detected_part_id = None  # Zuletzt erkannte Teil-ID
        
        # LED-System initialisieren
        try:
            init_led()
            print("LED-System initialisiert")
        except Exception as e:
            print(f"LED-Initialisierung fehlgeschlagen: {e}")
        
        # Stepper Controller initialisieren
        self.stepper = get_stepper_controller()
        print("Stepper Controller bereit")
        
        # Servo Controller initialisieren
        self.servo = get_servo_controller()
        print("Servo Controller bereit")
        
        # Vibration Controller initialisieren
        self.vibration = get_vibration_controller()
        print("Vibration Controller bereit")
        
        # Debug-Labels Referenzen speichern
        self.stepper_status_label = stepper_status_lbl
        self.sensor_status_label = sensor_status_lbl
        print(f"Debug-Labels zugewiesen: stepper={stepper_status_lbl}, sensor={sensor_status_lbl}")

    def _update_debug_display(self):
        """Aktualisiert Debug-Anzeige mit Stepper und Sensor Status"""
        try:
            if not self.stepper_status_label or not self.sensor_status_label:
                print("Debug-Labels nicht vorhanden!")
                return
            
            # Stepper Status - nur PWM und Richtung
            moving_status = "LÄUFT" if self.stepper.is_moving else "STEHT"
            direction = self.stepper.last_direction.upper()
            
            # PWM Status
            pwm_status = "AN" if (self.stepper.pwm_object is not None and self.stepper.is_moving) else "AUS"
            
            stepper_text = f"Schieber: {moving_status} | PWM: {pwm_status} | Richtung: {direction}"
            self.stepper_status_label.config(text=stepper_text)
            
            # Sensor Status
            upper_status = "?"
            lower_status = "?"
            
            if self.stepper.gpio_initialized:
                try:
                    upper_val = GPIO.input(SENSOR_UPPER_PIN["bcm"])
                    lower_val = GPIO.input(SENSOR_LOWER_PIN["bcm"])
                    # Debug: Erste 5 Lesungen ausgeben
                    if not hasattr(self, '_debug_read_count'):
                        self._debug_read_count = 0
                    
                    if self._debug_read_count < 5:
                        print(f"[DEBUG #{self._debug_read_count}] Sensor-Werte: Oben(GPIO{SENSOR_UPPER_PIN['bcm']})={upper_val}, Unten(GPIO{SENSOR_LOWER_PIN['bcm']})={lower_val}")
                        self._debug_read_count += 1
                    
                    # SENSOR_UPPER (NC): 1 = angeschlagen (Kontakt geschlossen)
                    # SENSOR_LOWER (NO): 0 = angeschlagen (Kontakt geschlossen)
                    upper_status = "ANGESCHLAGEN" if upper_val == 1 else "FREI"
                    lower_status = "ANGESCHLAGEN" if lower_val == 0 else "FREI"
                except Exception as e:
                    upper_status = f"ERR"
                    lower_status = f"ERR"
                    print(f"❌ Sensor Lesefehler: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                upper_status = "SIM"
                lower_status = "SIM"
            
            sensor_text = f"Sensoren: Oben={upper_status} | Unten={lower_status}"
            self.sensor_status_label.config(text=sensor_text)
            
        except Exception as e:
            print(f"Debug Display Update Fehler: {e}")
            import traceback
            traceback.print_exc()

    def _assign_boxes_to_sets(self):
        """Ordnet geladenen Sets Boxen zu (max 3 Sets, Rest = Ausschuss)."""
        self.set_to_box.clear()
        
        # Maximal 3 Sets können gleichzeitig sortiert werden
        available_boxes = min(len(self.set_numbers), 3)
        
        for idx, set_num in enumerate(self.set_numbers[:3]):
            self.set_to_box[set_num] = idx
            print(f"Set {set_num} -> Box {idx + 1} (Position: {self.box_positions[idx]})")
        
        if len(self.set_numbers) > 3:
            print(f"Warnung: Mehr als 3 Sets geladen. Nur die ersten 3 werden sortiert.")
            for extra_set in self.set_numbers[3:]:
                print(f"  Set {extra_set} wird ignoriert")
        
        print(f"Ausschuss -> Box 4 (Position: {self.box_positions[self.reject_box]})")
    
    def _determine_target_box(self, part_id: str, part_color: str) -> int:
        """Bestimmt die Ziel-Box basierend auf Teil-ID und Farbe.
        
        :param part_id: Erkannte Teil-ID
        :param part_color: Erkannte Farbe (BrickLink-Name)
        :return: Box-Index (0-3)
        """
        if not part_id:
            return self.reject_box  # Kein Teil erkannt -> Ausschuss
        
        # Prüfe in welchem Set dieses Teil vorkommt
        for set_num in self.set_numbers[:3]:  # Nur erste 3 Sets
            if set_num in self.parts_per_set:
                for part in self.parts_per_set[set_num]:
                    if part.get('id') == part_id:
                        # Teil-ID passt, prüfe auch Farbe wenn vorhanden
                        if part_color and part.get('color_name'):
                            if part.get('color_name').lower() == part_color.lower():
                                return self.set_to_box[set_num]
                        else:
                            # Keine Farb-Info, nutze nur ID
                            return self.set_to_box[set_num]
        
        # Teil passt zu keinem geladenen Set -> Ausschuss
        return self.reject_box

    def load_sets(self):
        """Lädt Sets aus dem Eingabefeld und zeigt Informationen an."""
        user_input = set_number.get().strip()
        if not user_input:
            self.status_label.config(text="Bitte Setnummer eingeben", fg='#ff5722')
            return
        
        self.status_label.config(text="Lade Sets...", fg='#2196f3')
        
        # Normalisiere und lade Teilelisten
        new_set_numbers = [s.strip() for s in user_input.split(',') if s.strip()]
        
        for sn in new_set_numbers:
            if sn in self.set_numbers:
                continue  # Set bereits geladen
            
            parts = get_parts_from_set(sn)
            if parts:
                # Hole Set-Namen von BrickLink
                set_name = ""
                set_img_url = ""
                set_id_normalized = sn if "-" in sn else f"{sn}-1"
                
                try:
                    url = f"https://www.bricklink.com/v2/catalog/catalogitem.page?S={set_id_normalized}#T=I"
                    headers = {"User-Agent": "Mozilla/5.0"}
                    response = requests.get(url, headers=headers)
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, "html.parser")
                        title = soup.find("title").text if soup.find("title") else ""
                        set_name = title.split("|")[0].strip() if "|" in title else title.strip()
                except Exception as e:
                    print(f"Fehler beim Laden des Set-Namens: {e}")
                    set_name = f"Set {sn}"
                
                # Hole Set-Bild
                set_img_url = get_set_image_url(sn)
                
                self.set_numbers.append(sn)
                
                # Speichere Teile pro Set (vor Aggregation)
                self.parts_per_set[sn] = parts.copy()
                
                # Initialisiere Tracking für dieses Set
                self.found_parts_per_set[sn] = {}
                
                # Speichere Set-Info
                total_qty = sum(int(p.get('qty', '1')) for p in parts)
                self.loaded_sets_info.append({
                    'set_number': sn,
                    'set_name': set_name,
                    'set_img_url': set_img_url,
                    'part_count': len(parts),
                    'total_qty': total_qty
                })
                
                # Füge Teile hinzu
                for p in parts:
                    key = (p.get('id'), p.get('color_name'))
                    if key in [(existing.get('id'), existing.get('color_name')) for existing in self.cached_set_parts]:
                        # Teil bereits vorhanden, erhöhe Menge
                        for existing in self.cached_set_parts:
                            if (existing.get('id'), existing.get('color_name')) == key:
                                try:
                                    existing['qty'] = str(int(existing['qty']) + int(p.get('qty', '1')))
                                except:
                                    pass
                                break
                    else:
                        # Neues Teil hinzufügen
                        self.cached_set_parts.append(p.copy())
        
        # Aktualisiere globale Referenz
        global current_set_parts
        current_set_parts = self.cached_set_parts
        
        # Ordne Sets den Boxen zu (max 3 Sets)
        self._assign_boxes_to_sets()
        
        # Zeige Set-Informationen
        if self.loaded_sets_info:
            self._update_set_info_display()
            
            # Zeige Set-Bilder
            self._update_set_images()
            
            self.set_info_frame.pack(fill='x', pady=(0, 20))
            self.status_label.config(text="Sets geladen - bereit zum Starten", fg='#4caf50')
            
            # Eingabefeld leeren für nächste Eingabe
            set_number.set("")
        else:
            self.status_label.config(text="Keine gültigen Sets gefunden", fg='#ff5722')
    
    def _update_set_images(self):
        """Aktualisiert die Set-Bild-Anzeige."""
        # Lösche alte Bilder
        for label in self.set_image_labels:
            label.destroy()
        self.set_image_labels.clear()
        
        # Zeige neue Bilder (maximal 3 nebeneinander)
        for set_info in self.loaded_sets_info[-3:]:  # Nur die letzten 3 Sets
            if set_info.get('set_img_url'):
                img_label = tk.Label(self.set_images_container, bg='#2b2b2b')
                img_label.pack(side='top', pady=5)
                self.set_image_labels.append(img_label)
                
                # Lade Bild asynchron
                try:
                    display_image_from_url(set_info['set_img_url'], img_label, max_size=(150, 150))
                except Exception as e:
                    print(f"Fehler beim Laden des Set-Bildes: {e}")
    
    def _update_set_info_display(self):
        """Aktualisiert die Set-Info-Anzeige mit Fortschritt und Box-Zuordnung."""
        info_text = ""
        for idx, set_info in enumerate(self.loaded_sets_info, 1):
            set_num = set_info['set_number']
            set_display_name = set_info['set_name'] if set_info['set_name'] else f"Set {set_num}"
            
            # Hole Box-Zuordnung
            box_info = ""
            if set_num in self.set_to_box:
                box_num = self.set_to_box[set_num] + 1
                box_info = f" -> Box {box_num}"
            
            # Berechne Fortschritt für dieses Set
            if set_num in self.parts_per_set:
                total_qty = sum(int(p.get('qty', '1')) for p in self.parts_per_set[set_num])
                found_qty = sum(self.found_parts_per_set.get(set_num, {}).values())
                percentage = int((found_qty / total_qty * 100)) if total_qty > 0 else 0
                
                if percentage == 100:
                    progress_icon = "V"
                elif percentage > 0:
                    progress_icon = ">"
                else:
                    progress_icon = "O"
                
                info_text += f"{idx}. {set_display_name}{box_info}\n"
                info_text += f"   {progress_icon} {found_qty}/{total_qty} Teile ({percentage}%)\n\n"
            else:
                info_text += f"{idx}. {set_display_name}{box_info}\n"
                info_text += f"   {set_info['part_count']} verschiedene Teile, {set_info['total_qty']} gesamt\n\n"
        
        self.set_info_label.config(text=info_text)
    
    def _create_progress_visualizations(self):
        """Erstellt Fortschrittsbalken für jedes geladene Set mit Box-Zuordnung."""
        for widget in self.sets_progress_frame.winfo_children():
            widget.destroy()
        self.progress_bars.clear()
        self.progress_labels.clear()
        
        for set_num in self.set_numbers:
            set_info = next((s for s in self.loaded_sets_info if s['set_number'] == set_num), None)
            if not set_info:
                continue
            
            set_display_name = set_info['set_name'] if set_info['set_name'] else f"Set {set_num}"
            
            # Füge Box-Info hinzu
            if set_num in self.set_to_box:
                box_num = self.set_to_box[set_num] + 1
                set_display_name += f" (Box {box_num})"
            
            set_container = tk.Frame(self.sets_progress_frame, bg='#2b2b2b')
            set_container.pack(fill='x', pady=(10, 5), padx=10)
            
            name_label = tk.Label(
                set_container,
                text=set_display_name,
                font=('Helvetica', 14, 'bold'),
                bg='#2b2b2b',
                fg='white'
            )
            name_label.pack(anchor='w')
            
            bar_container = tk.Frame(set_container, bg='#1a1a1a', relief='flat', bd=1, height=30)
            bar_container.pack(fill='x', pady=(5, 0))
            bar_container.pack_propagate(False)
            
            progress_bar = tk.Canvas(
                bar_container,
                bg='#1a1a1a',
                highlightthickness=0,
                height=30
            )
            progress_bar.pack(fill='both', expand=True, padx=2, pady=2)
            self.progress_bars[set_num] = progress_bar
            
            progress_text = tk.Label(
                set_container,
                text="0% (0/0)",
                font=('Helvetica', 12),
                bg='#2b2b2b',
                fg='#888888'
            )
            progress_text.pack(anchor='w', pady=(2, 0))
            self.progress_labels[set_num] = progress_text
    
    def _update_progress_visualization(self):
        """Aktualisiert die Fortschrittsbalken für alle Sets."""
        for set_num in self.set_numbers:
            if set_num not in self.parts_per_set:
                continue
            
            total_qty = sum(int(p.get('qty', '1')) for p in self.parts_per_set[set_num])
            found_qty = sum(self.found_parts_per_set.get(set_num, {}).values())
            percentage = int((found_qty / total_qty * 100)) if total_qty > 0 else 0
            
            if set_num in self.progress_bars:
                bar = self.progress_bars[set_num]
                bar.delete('all')
                
                bar_width = bar.winfo_width() if bar.winfo_width() > 1 else 300
                bar_height = bar.winfo_height() if bar.winfo_height() > 1 else 30
                bar.create_rectangle(0, 0, bar_width, bar_height, fill='#333333', outline='')
                
                if percentage == 100:
                    color = '#4caf50'
                elif percentage >= 50:
                    color = '#2196f3'
                elif percentage > 0:
                    color = '#ff9800'
                else:
                    color = '#555555'
                
                filled_width = (percentage / 100) * (bar_width - 4)
                bar.create_rectangle(2, 2, 2 + filled_width, bar_height - 2, fill=color, outline='')
                bar.create_text(bar_width / 2, bar_height / 2, text=f"{percentage}%", fill='white', font=('Helvetica', 12, 'bold'))
            
            if set_num in self.progress_labels:
                self.progress_labels[set_num].config(text=f"{percentage}% ({found_qty}/{total_qty})")

    def clear_sets(self):
        """Löscht alle geladenen Sets."""
        self.set_numbers = []
        self.cached_set_parts = []
        self.loaded_sets_info = []
        self.found_parts_per_set = {}
        self.parts_per_set = {}
        
        # Lösche Set-Bilder
        for label in self.set_image_labels:
            label.destroy()
        self.set_image_labels.clear()
        
        global current_set_parts
        current_set_parts = []
        
        self.set_info_frame.pack_forget()
        self.status_label.config(text="Bereit", fg='#4caf50')
        set_number.set("")

    def start(self):
        if self.running:
            return
        
        # Prüfe ob Sets geladen wurden
        if not self.cached_set_parts:
            self.status_label.config(text="Bitte erst Sets laden!", fg='#ff5722')
            return
        
        self.running = True
        self.paused = False
        self.state = AutomationState.INIT
        
        # Verstecke Start-Button und Setnummer-Eingabe
        self.start_button.pack_forget()
        self.set_input_frame.pack_forget()
        
        # Zeige Fortschrittsbereich an gleicher Stelle
        self.progress_frame.pack(fill='both', expand=True, pady=20)
        self.status_label.config(text="Automatik läuft...", fg='#2196f3')
        
        # Tracking zurücksetzen
        for set_num in self.set_numbers:
            self.found_parts_per_set[set_num] = {}
        
        # Erstelle Fortschrittsbalken-Visualisierung
        self._create_progress_visualizations()
        self._update_progress_visualization()
        
        # Stepper in Home-Position fahren
        try:
            self.current_status_label.config(text="Initialisiere Stepper...", fg='#ff9800')
            self.stepper.home()
            print("Stepper bereit")
        except Exception as e:
            print(f"Stepper Home-Position Fehler: {e}")
        
        # Nicht den Tk-Hauptthread blockieren: separater Thread
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def toggle_pause(self):
        """Pausiert oder setzt die Automatik fort."""
        if not self.running:
            return
        
        self.paused = not self.paused
        
        if self.paused:
            # Schieber bei Pause stoppen
            if self.stepper.continuous_mode:
                try:
                    self.stepper.stop()
                    print("Schieber pausiert")
                except Exception as e:
                    print(f"Schieber Pause Fehler: {e}")
            
            self.pause_button.config(text="▶ Fortsetzen", bg='#4caf50', activebackground='#45a049')
            self.status_label.config(text="Pausiert", fg='#ff9800')
        else:
            # Schieber bei Fortsetzen wieder starten (wenn vorher aktiv)
            if self.state == AutomationState.WARTEN_AUF_TEIL:
                try:
                    self.stepper.start_continuous_push()
                    print("Schieber fortgesetzt")
                except Exception as e:
                    print(f"Schieber Fortsetzung Fehler: {e}")
            
            self.pause_button.config(text="⏸ Pause", bg='#ff9800', activebackground='#f57c00')
            self.status_label.config(text="Automatik läuft...", fg='#2196f3')
    
    def stop(self):
        self.running = False
        self.paused = False
        
        # State zurücksetzen
        self.state = AutomationState.INIT
        
        # Motion Detection deaktivieren
        self.motion_detection_active = False
        self.previous_frame = None
        
        # LED ausschalten
        self._set_led_brightness(0)
        
        # Stepper stoppen
        try:
            self.stepper.stop()
            print("Stepper gestoppt")
        except Exception as e:
            print(f"Stepper Stop Fehler: {e}")
        
        # Rüttler stoppen
        try:
            self.vibration.stop()
            print("Rüttler gestoppt")
        except Exception as e:
            print(f"Rüttler Stop Fehler: {e}")
        
        # Verstecke Fortschrittsbereich
        self.progress_frame.pack_forget()
        
        # Zeige Setnummer-Eingabe und Start-Button wieder
        self.set_input_frame.pack(fill='x', pady=(0, 20))
        self.start_button.pack(pady=20)
        
        # Setze Pause-Button zurück
        self.pause_button.config(text="⏸ Pause", bg='#ff9800', activebackground='#f57c00')
        
        self.status_label.config(text="Gestoppt", fg='#4caf50')

    def _run_loop(self):
        """Hauptschleife der Automatik. Ruft periodisch tick() auf."""
        while self.running:
            if not self.paused:
                self.tick()
            time.sleep(0.05)  # 20 Hz Takt, anpassbar

    def tick(self):
        """
        Ein einzelner Schritt der Zustandsmaschine. Hier können später
        Sensorabfragen, Zeitbedingungen und Aktionen eingefügt werden.
        """
        global current_set_parts
        
        # Update Stepper kontinuierliche Bewegung (Auto-Reverse)
        if self.stepper.continuous_mode:
            self.stepper.update_continuous_motion()
        
        # Update Debug Display
        self._update_debug_display()
        
        # Beispielhafte Konsolen-Ausgabe zur Sichtbarkeit
        # print(f"[AUTOMATION] State: {self.state.name}")

        match self.state:
            case AutomationState.INIT:
                # Setup, Sensor-Reset, LED-Status, Home-Fahrt
                self._ensure_gpio()
                
                # LED-Ring auf 30% für Motion Detection
                self._set_led_brightness(30)
                
                # Reset Motion Detection
                self.previous_frame = None
                self.motion_detection_active = True
                self.part_detected = False
                
                # Sets sind bereits geladen, direkt weiter
                self.current_status_label.config(text="⏳ Warte auf Teil...", fg='#ff9800')
                self.state = AutomationState.WARTEN_AUF_TEIL

            case AutomationState.WARTEN_AUF_TEIL:
                # Schieber bewegt sich kontinuierlich (Teile nachschieben)
                # bis Motion Detection ein Teil erkennt
                # RÜTTLER AKTIV in diesem State mit Pattern (2s rütteln / 1s Pause)
                
                # Sicherheitsprüfung: Wenn nicht mehr running, nichts starten
                if not self.running:
                    return
                
                if self.motion_detection_active:
                    # Starte Rüttler falls noch nicht gestartet
                    if not self.vibration.pattern_active:
                        try:
                            self.vibration.start()
                        except Exception as e:
                            print(f"Rüttler Start Fehler: {e}")
                    else:
                        # Update Rüttel-Pattern
                        try:
                            self.vibration.update_pattern()
                        except Exception as e:
                            print(f"Rüttler Pattern Update Fehler: {e}")
                    
                    # Starte Stepper falls noch nicht gestartet
                    if not self.stepper.is_moving:
                        try:
                            self.stepper.start_continuous_push()
                        except Exception as e:
                            print(f"Stepper Start Fehler: {e}")
                    
                    # Stepper Richtungs-Update (Auto-Reverse bei Sensoren)
                    try:
                        self.stepper.update_continuous_motion()
                    except Exception as e:
                        print(f"Stepper Motion Update Fehler: {e}")
                    
                    # Motion Detection: Erkenne Teileinwurf durch Bildänderung
                    if self._detect_motion():
                        # Teil erkannt - STOPPE Schieber und Rüttler sofort!
                        self.current_status_label.config(text="✔ Teil erkannt - Schieber stoppt!", fg='#4caf50')
                        print("Teil-Einwurf erkannt - Stoppe Schieber und Rüttler")
                        
                        try:
                            self.stepper.stop()
                        except Exception as e:
                            print(f"Stepper Stop Fehler: {e}")
                        
                        try:
                            self.vibration.stop()
                        except Exception as e:
                            print(f"Rüttler Stop Fehler: {e}")
                        
                        # Warte bis Teil ruhig liegt
                        self._wait_for_part_settled()
                        
                        # Motion Detection deaktivieren
                        self.motion_detection_active = False
                        
                        # Weiter zur Bildaufnahme
                        self.state = AutomationState.BILD_AUFNEHMEN
                    else:
                        # Kleine Pause um CPU zu schonen (ca. 20 FPS)
                        time.sleep(0.05)

            case AutomationState.BILD_AUFNEHMEN:
                # LED auf 100% für optimale Bildqualität
                self._set_led_brightness(100)
                time.sleep(0.10)  # Kurz warten bis LED stabilisiert ist
                
                # Bild aufnehmen; bei Erfolg weiter zur Erkennung, sonst zurück warten
                self.current_status_label.config(text="⏳ Nehme Bild auf...", fg='#2196f3')
                
                if capture_image(IMAGE_PATH):
                    self.state = AutomationState.ERKENNEN
                else:
                    self.current_status_label.config(text="✗ Fehler bei Bildaufnahme", fg='#f44336')
                    # LED zurück auf 30%
                    self._set_led_brightness(30)
                    # Stepper und Rüttler wieder starten
                    try:
                        self.stepper.start_continuous_push()
                        print("Stepper nach Bildaufnahme-Fehler wieder gestartet")
                    except Exception as e:
                        print(f"Stepper Restart Fehler: {e}")
                    try:
                        self.vibration.start()
                        print("Rüttler nach Bildaufnahme-Fehler wieder gestartet")
                    except Exception as e:
                        print(f"Rüttler Restart Fehler: {e}")
                    # Motion Detection reaktivieren
                    self.motion_detection_active = True
                    self.previous_frame = None
                    self.state = AutomationState.WARTEN_AUF_TEIL

            case AutomationState.ERKENNEN:
                # Erkennung durchführen (Brick + Farbe) und GUI aktualisieren
                self.current_status_label.config(text=" Erkenne Teil...", fg='#2196f3')
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
                                # Finde heraus, zu welchem Set dieses Teil gehört
                                part_key = (current_part_id, match.get('bricklink_color', ''))
                                
                                for set_num in self.set_numbers:
                                    if set_num in self.parts_per_set:
                                        # Prüfe ob Teil in diesem Set ist
                                        for part in self.parts_per_set[set_num]:
                                            if (part.get('id'), part.get('color_name')) == part_key:
                                                # Teil gehört zu diesem Set
                                                self.found_parts_per_set[set_num][part_key] = self.found_parts_per_set[set_num].get(part_key, 0) + 1
                                                print(f"Teil erkannt: {current_part_id}")
                                                break
                                
                                # Aktualisiere Set-Info-Anzeige mit neuem Fortschritt
                                self._update_set_info_display()
                                # Aktualisiere Fortschritts-Visualisierung
                                self._update_progress_visualization()
                                
                                # Bestimme Ziel-Box für Sortierung
                                part_color_name = match.get('bricklink_color', '')
                                self.current_part_box = self._determine_target_box(current_part_id, part_color_name)
                                self.current_detected_part_id = current_part_id
                                
                                box_num = self.current_part_box + 1
                                if self.current_part_box == self.reject_box:
                                    print(f"Teil {current_part_id} -> Ausschuss (Box {box_num})")
                                else:
                                    assigned_set = [s for s, b in self.set_to_box.items() if b == self.current_part_box][0]
                                    print(f"Teil {current_part_id} -> Set {assigned_set} (Box {box_num})")
                # Wenn kein Teil erkannt oder nicht im Set -> Ausschuss
                if not current_part_id or not hasattr(self, 'current_part_box'):
                    self.current_part_box = self.reject_box
                    self.current_detected_part_id = None
                    print("Kein gültiges Teil erkannt -> Ausschuss")
                
                # Weiter zum Sortieren
                self.state = AutomationState.SORTIEREN

            case AutomationState.SORTIEREN:
                # Sortiere Teil in die bestimmte Box
                target_position = self.box_positions[self.current_part_box]
                box_name = f"Box {self.current_part_box + 1}"
                
                if self.current_part_box == self.reject_box:
                    box_name += " (Ausschuss)"
                else:
                    assigned_set = [s for s, b in self.set_to_box.items() if b == self.current_part_box]
                    if assigned_set:
                        box_name += f" (Set {assigned_set[0]})"
                
                self.current_status_label.config(text=f"Sortiere -> {box_name}", fg='#2196f3')
                print(f"Sortiere Teil in {box_name} (Servo-Position: {target_position})")
                
                # Schieber bleibt GESTOPPT während Sortierung!
                # Sortier-Servo auf Zielposition
                try:
                    self.servo.set_sort_position(target_position)
                    print(f"Sortier-Servo auf {target_position}")
                except Exception as e:
                    print(f"Sortier-Servo Fehler: {e}")
                time.sleep(1)  # Warte bis Schleuse gestellt ist
                # Klappe öffnen
                try:
                    self.servo.open_gate()
                    print("Klappe geöffnet")
                except Exception as e:
                    print(f"Klappen-Servo Fehler: {e}")
                

                time.sleep(0.5)  # Warte bis Teil gefallen ist
                
                # Klappe schließen
                try:
                    self.servo.close_gate()
                    print("Klappe geschlossen")
                except Exception as e:
                    print(f"Klappen-Servo Fehler: {e}")
                
                time.sleep(0.2)  # Kurze Pause nach Sortierung
                
                # LED zurück auf 30% für Motion Detection
                self._set_led_brightness(30)
                
                # Stepper wieder starten für kontinuierlichen Betrieb
                try:
                    self.stepper.start_continuous_push()
                    print("Stepper nach Sortierung wieder gestartet")
                except Exception as e:
                    print(f"Stepper Restart Fehler: {e}")
                
                # Motion Detection reaktivieren für nächstes Teil
                self.motion_detection_active = True
                self.previous_frame = None  # Reset Frame-Vergleich
                
                # Zurück zum Warten für kontinuierlichen Betrieb
                self.current_status_label.config(text="Warte auf naechstes Teil...", fg='#ff9800')
                self.state = AutomationState.WARTEN_AUF_TEIL

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

    def _set_led_brightness(self, brightness_percent):
        """
        Steuert die LED-Ring Helligkeit mit vorhandenen Funktionen.
        :param brightness_percent: 0 = AUS, >0 = EIN (volle Helligkeit)
        """
        try:
            if brightness_percent == 0:
                clear_ring()
                print(f"LED Ring: AUS")
            else:
                set_ring_white()
                print(f"LED Ring: EIN ({brightness_percent}% angefordert)")
        except Exception as e:
            print(f"LED Steuerung fehlgeschlagen: {e}")
    
    def _capture_low_res_frame(self):
        """
        Nimmt ein Low-Resolution Frame für Motion Detection auf.
        Gibt Grayscale NumPy Array zurück (ohne cv2, nur PIL).
        """
        try:
            # Konfiguration für schnelle Low-Res Aufnahme
            config_lowres = picam2.create_still_configuration(
                main={"size": (320, 240), "format": "RGB888"}
            )
            picam2.configure(config_lowres)
            picam2.start()
            
            # Aufnahme
            frame = picam2.capture_array()
            picam2.stop()
            
            # Konvertiere zu PIL Image
            img = Image.fromarray(frame, mode='RGB')
            
            # Zu Grayscale konvertieren
            gray_img = img.convert('L')
            
            # Zu NumPy Array fÃ¤Â¼r einfache Berechnungen
            gray_array = np.array(gray_img, dtype=np.float32)
            
            return gray_array
        except Exception as e:
            print(f"Fehler bei Low-Res Capture: {e}")
            return None
    
    def _detect_motion(self):
        """
        Prüft ob Bewegung im Kamerabild erkannt wurde.
        Gibt True zurück wenn signifikante Änderung detektiert wurde.
        (Ohne cv2, nur NumPy)
        """
        try:
            # Aktuellen Frame aufnehmen
            current_frame = self._capture_low_res_frame()
            if current_frame is None:
                return False
            
            # Beim ersten Durchlauf: Frame speichern und kein Motion
            if self.previous_frame is None:
                self.previous_frame = current_frame
                return False
            
            # Frame-Differenz berechnen (Absolute Differenz)
            frame_diff = np.abs(current_frame - self.previous_frame)
            
            # Schwellwert anwenden: Pixel mit Differenz > 30 zÃ¤Â¤hlen als Bewegung
            threshold_value = 30
            motion_mask = frame_diff > threshold_value
            
            # Anzahl der geÃ¤Â¤nderten Pixel zÃ¤Â¤hlen
            changed_pixels = np.sum(motion_mask)
            
            # Debug-Output
            print(f"Motion Detection: {changed_pixels} Pixel geändert (Schwelle: {self.motion_threshold})")
            
            # Frame für nächsten Vergleich speichern
            self.previous_frame = current_frame
            
            # Prüfe ob Schwellwert überschritten
            if changed_pixels > self.motion_threshold:
                self.last_motion_time = time.time()
                return True
            
            return False
            
        except Exception as e:
            print(f"Fehler bei Motion Detection: {e}")
            return False
    
    def _wait_for_part_settled(self):
        """
        Wartet bis das Teil ruhig liegt (keine Bewegung mehr).
        """
        print("Warte bis Teil ruhig liegt...")
        settled_count = 0
        required_settled_frames = 3  # 3 Frames ohne Bewegung = Teil liegt ruhig
        
        while settled_count < required_settled_frames:
            time.sleep(0.1)
            if not self._detect_motion():
                settled_count += 1
            else:
                settled_count = 0  # Reset wenn noch Bewegung
        
        print("Teil liegt ruhig!")

    def _read_part_present(self) -> bool:
        """
        Liest ein Eingangssignal (Platzhalter). Später mit RPi.GPIO ersetzen.
        """
        # Beispiel: return GPIO.input(23) == GPIO.LOW
        return False





# Start-Button (kompakt)
start_button = tk.Button(
    main_container,
    text="START",
    font=('Helvetica', 14, 'bold'),
    bg='#4caf50',
    fg='white',
    activebackground='#45a049',
    activeforeground='white',
    relief='flat',
    bd=0,
    padx=25,
    pady=10,
    cursor='hand2'
)
start_button.pack(pady=5)

# Footer (minimal)
footer_label = tk.Label(
    main_container,
    text="ESC = Beenden",
    font=('Helvetica', 8),
    bg='#1e1e1e',
    fg='#666666'
)
footer_label.pack(side='bottom', pady=2)

# Automatik-Controller instanziieren
automation = AutomationController(
    root,
    progress_frame,
    current_status_label,
    status_label,
    set_info_frame,
    set_info_label,
    set_images_container,
    start_button,
    pause_button,
    stop_button,
    sets_progress_frame,
    set_input_frame,
    stepper_status_label,
    sensor_status_label
)

# Button-Commands zuweisen
load_set_button.config(command=automation.load_sets)
clear_sets_button.config(command=automation.clear_sets)
start_button.config(command=automation.start)
pause_button.config(command=automation.toggle_pause)
stop_button.config(command=automation.stop)
settings_button.config(command=lambda: open_settings_window(root, automation))

# Kamera vorbereiten
try:
    if automation.preview_enabled:
        picam2.start_preview()
        print("Kamera Preview gestartet")
    else:
        print("Kamera Preview deaktiviert (kann in Einstellungen aktiviert werden)")
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

# GUI starten
root.mainloop()

# Kamera stoppen
picam2.close()