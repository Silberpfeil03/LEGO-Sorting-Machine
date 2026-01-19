#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sensor Input Test Tool - Raspberry Pi 3B
Test für Endschalter/Anschlag-Sensoren (NC/NO)
"""

import tkinter as tk
from tkinter import ttk
import threading
import time

# Versuche RPi.GPIO zu importieren
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("WARNUNG: RPi.GPIO nicht verfuegbar - Simulationsmodus")


# WiringPi zu BCM GPIO Mapping
WIRINGPI_PINS = {
    0:  {"bcm": 17, "physical": 11, "name": "GPIO"},
    1:  {"bcm": 18, "physical": 12, "name": "PWM0"},
    2:  {"bcm": 27, "physical": 13, "name": "GPIO"},
    3:  {"bcm": 22, "physical": 15, "name": "GPIO"},
    4:  {"bcm": 23, "physical": 16, "name": "GPIO"},
    5:  {"bcm": 24, "physical": 18, "name": "GPIO"},
    6:  {"bcm": 25, "physical": 22, "name": "GPIO"},
    7:  {"bcm": 4,  "physical": 7,  "name": "GPCLK0"},
    21: {"bcm": 5,  "physical": 29, "name": "GPIO"},
    22: {"bcm": 6,  "physical": 31, "name": "GPIO"},
    25: {"bcm": 26, "physical": 37, "name": "GPIO"},
    27: {"bcm": 16, "physical": 36, "name": "GPIO"},
    28: {"bcm": 20, "physical": 38, "name": "GPIO"},
    29: {"bcm": 21, "physical": 40, "name": "GPIO"}
}

# Standard Sensor-Pins aus Sortiermaschine
DEFAULT_SENSORS = {
    "upper": {"wiring": 3, "bcm": 22, "physical": 15, "type": "NC", "desc": "Oberer Anschlag (NC)"},
    "lower": {"wiring": 2, "bcm": 27, "physical": 13, "type": "NO", "desc": "Unterer Anschlag (NO)"}
}


class SensorMonitor:
    def __init__(self):
        self.sensor1_pin = None
        self.sensor2_pin = None
        self.monitoring = False
        self.monitor_thread = None
        self.gpio_initialized = False
        
        # Callbacks für Status-Updates
        self.sensor1_callback = None
        self.sensor2_callback = None
        
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
    
    def init_sensors(self, pin1_bcm, pin2_bcm, use_pulldown=False):
        """Initialisiert Sensor-Pins als Input"""
        self.cleanup()
        
        if GPIO_AVAILABLE:
            try:
                if use_pulldown:
                    GPIO.setup(pin1_bcm, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                    GPIO.setup(pin2_bcm, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                    print(f"Sensoren initialisiert mit Pull-Down: GPIO{pin1_bcm}, GPIO{pin2_bcm}")
                else:
                    GPIO.setup(pin1_bcm, GPIO.IN)
                    GPIO.setup(pin2_bcm, GPIO.IN)
                    print(f"Sensoren initialisiert ohne Pull-Up/Down: GPIO{pin1_bcm}, GPIO{pin2_bcm}")
                
                self.sensor1_pin = pin1_bcm
                self.sensor2_pin = pin2_bcm
                self.gpio_initialized = True
                return True
                
            except Exception as e:
                print(f"Sensor Init Fehler: {e}")
                return False
        else:
            self.sensor1_pin = pin1_bcm
            self.sensor2_pin = pin2_bcm
            self.gpio_initialized = False
            print(f"SIMULATION: Sensoren auf GPIO{pin1_bcm}, GPIO{pin2_bcm}")
            return True
    
    def read_sensors(self):
        """Liest aktuellen Status beider Sensoren"""
        if not self.gpio_initialized:
            # Simulation: Wechsle zufällig zwischen 0 und 1
            import random
            return (random.randint(0, 1), random.randint(0, 1))
        
        try:
            val1 = GPIO.input(self.sensor1_pin)
            val2 = GPIO.input(self.sensor2_pin)
            return (val1, val2)
        except Exception as e:
            print(f"Sensor Lesefehler: {e}")
            return (None, None)
    
    def start_monitoring(self, callback1, callback2, interval=0.05):
        """Startet kontinuierliche Überwachung in separatem Thread"""
        self.monitoring = True
        self.sensor1_callback = callback1
        self.sensor2_callback = callback2
        
        def monitor_loop():
            while self.monitoring:
                val1, val2 = self.read_sensors()
                
                if val1 is not None and self.sensor1_callback:
                    self.sensor1_callback(val1)
                if val2 is not None and self.sensor2_callback:
                    self.sensor2_callback(val2)
                
                time.sleep(interval)
        
        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()
        print("Sensor-Monitoring gestartet")
    
    def stop_monitoring(self):
        """Stoppt Überwachung"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1.0)
        print("Sensor-Monitoring gestoppt")
    
    def cleanup(self):
        """GPIO Cleanup"""
        self.stop_monitoring()
        if self.gpio_initialized and GPIO_AVAILABLE:
            try:
                if self.sensor1_pin:
                    GPIO.cleanup(self.sensor1_pin)
                if self.sensor2_pin:
                    GPIO.cleanup(self.sensor2_pin)
                print("Sensor GPIO bereinigt")
            except:
                pass
        self.sensor1_pin = None
        self.sensor2_pin = None
        self.gpio_initialized = False


class SensorTestGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Sensor Input Test - Raspberry Pi 3B")
        self.root.geometry("600x550")
        
        self.monitor = SensorMonitor()
        self.monitoring_active = False
        
        # Styles
        style = ttk.Style()
        style.configure("TButton", padding=5, font=("Arial", 10))
        style.configure("TLabel", font=("Arial", 10))
        
        # Header
        header_frame = tk.Frame(root, bg='#4caf50', height=50)
        header_frame.pack(fill='x')
        tk.Label(header_frame, text="📡 Sensor Input Monitor", 
                font=("Arial", 14, "bold"), bg='#4caf50', fg='white').pack(pady=10)
        
        # Konfiguration Frame
        config_frame = ttk.LabelFrame(root, text="Sensor Konfiguration", padding=10)
        config_frame.pack(fill="x", padx=10, pady=10)
        
        # Sensor 1
        ttk.Label(config_frame, text="Sensor 1 (Oben):").grid(row=0, column=0, sticky="w", pady=5)
        self.sensor1_var = tk.StringVar(value="3")
        
        sensor_choices = [f"WiringPi {pin} (BCM {info['bcm']}, Pin {info['physical']})" 
                         for pin, info in WIRINGPI_PINS.items()]
        sensor1_combo = ttk.Combobox(config_frame, textvariable=self.sensor1_var,
                                     values=sensor_choices, width=35, state="readonly")
        sensor1_combo.grid(row=0, column=1, padx=5, pady=5)
        sensor1_combo.current(3)  # WiringPi 3 (GPIO22)
        
        ttk.Label(config_frame, text="Typ:").grid(row=0, column=2, padx=(10,5))
        self.sensor1_type = ttk.Label(config_frame, text="NC", font=("Arial", 10, "bold"))
        self.sensor1_type.grid(row=0, column=3)
        
        # Sensor 2
        ttk.Label(config_frame, text="Sensor 2 (Unten):").grid(row=1, column=0, sticky="w", pady=5)
        self.sensor2_var = tk.StringVar(value="2")
        
        sensor2_combo = ttk.Combobox(config_frame, textvariable=self.sensor2_var,
                                     values=sensor_choices, width=35, state="readonly")
        sensor2_combo.grid(row=1, column=1, padx=5, pady=5)
        sensor2_combo.current(2)  # WiringPi 2 (GPIO27)
        
        ttk.Label(config_frame, text="Typ:").grid(row=1, column=2, padx=(10,5))
        self.sensor2_type = ttk.Label(config_frame, text="NO", font=("Arial", 10, "bold"))
        self.sensor2_type.grid(row=1, column=3)
        
        # Pull-Down Option
        self.pulldown_var = tk.BooleanVar(value=False)
        pulldown_check = ttk.Checkbutton(config_frame, text="Interne Pull-Down Widerstände aktivieren",
                                         variable=self.pulldown_var)
        pulldown_check.grid(row=2, column=0, columnspan=4, pady=10, sticky="w")
        
        # Start/Stop Buttons
        btn_frame = tk.Frame(config_frame)
        btn_frame.grid(row=3, column=0, columnspan=4, pady=10)
        
        self.start_btn = ttk.Button(btn_frame, text="▶ Monitoring Starten", 
                                    command=self.start_monitoring)
        self.start_btn.pack(side="left", padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="⏹ Monitoring Stoppen", 
                                   command=self.stop_monitoring, state="disabled")
        self.stop_btn.pack(side="left", padx=5)
        
        # Sensor Status Anzeige
        status_frame = ttk.LabelFrame(root, text="Live Sensor Status", padding=15)
        status_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Sensor 1 Anzeige
        sensor1_frame = tk.Frame(status_frame, relief="solid", borderwidth=2, bg='#e0e0e0')
        sensor1_frame.pack(fill="x", pady=10)
        
        tk.Label(sensor1_frame, text="Sensor 1 (Oben - NC)", 
                font=("Arial", 11, "bold"), bg='#e0e0e0').pack(pady=5)
        
        self.sensor1_status = tk.Label(sensor1_frame, text="•", 
                                       font=("Arial", 72), bg='#e0e0e0', fg='gray')
        self.sensor1_status.pack()
        
        self.sensor1_text = tk.Label(sensor1_frame, text="Nicht initialisiert",
                                     font=("Arial", 12), bg='#e0e0e0')
        self.sensor1_text.pack(pady=5)
        
        self.sensor1_value = tk.Label(sensor1_frame, text="GPIO: -",
                                      font=("Courier", 10), bg='#e0e0e0')
        self.sensor1_value.pack(pady=2)
        
        # Sensor 2 Anzeige
        sensor2_frame = tk.Frame(status_frame, relief="solid", borderwidth=2, bg='#e0e0e0')
        sensor2_frame.pack(fill="x", pady=10)
        
        tk.Label(sensor2_frame, text="Sensor 2 (Unten - NO)", 
                font=("Arial", 11, "bold"), bg='#e0e0e0').pack(pady=5)
        
        self.sensor2_status = tk.Label(sensor2_frame, text="•", 
                                       font=("Arial", 72), bg='#e0e0e0', fg='gray')
        self.sensor2_status.pack()
        
        self.sensor2_text = tk.Label(sensor2_frame, text="Nicht initialisiert",
                                     font=("Arial", 12), bg='#e0e0e0')
        self.sensor2_text.pack(pady=5)
        
        self.sensor2_value = tk.Label(sensor2_frame, text="GPIO: -",
                                      font=("Courier", 10), bg='#e0e0e0')
        self.sensor2_value.pack(pady=2)
        
        # Info
        info_frame = tk.Frame(root, bg='#f0f0f0')
        info_frame.pack(fill='x', side='bottom')
        
        info_text = "NC=Normally Closed (0=frei, 1=angeschlagen) | NO=Normally Open (1=frei, 0=angeschlagen)"
        ttk.Label(info_frame, text=info_text, font=("Arial", 8), 
                 foreground="blue", background='#f0f0f0').pack(pady=5)
        
        if GPIO_AVAILABLE:
            ttk.Label(info_frame, text="⚡ RPi.GPIO verfügbar", 
                     font=("Arial", 8, "bold"), foreground="green", 
                     background='#f0f0f0').pack(pady=2)
        else:
            ttk.Label(info_frame, text="⚠ SIMULATIONSMODUS", 
                     font=("Arial", 8, "bold"), foreground="orange",
                     background='#f0f0f0').pack(pady=2)
    
    def start_monitoring(self):
        """Startet Sensor-Monitoring"""
        try:
            # Parse Pin-Nummern
            pin1_text = self.sensor1_var.get()
            wiring_pin1 = int(pin1_text.split("WiringPi ")[1].split()[0])
            bcm_pin1 = WIRINGPI_PINS[wiring_pin1]["bcm"]
            
            pin2_text = self.sensor2_var.get()
            wiring_pin2 = int(pin2_text.split("WiringPi ")[1].split()[0])
            bcm_pin2 = WIRINGPI_PINS[wiring_pin2]["bcm"]
            
            use_pulldown = self.pulldown_var.get()
            
            # Initialisiere Sensoren
            if self.monitor.init_sensors(bcm_pin1, bcm_pin2, use_pulldown):
                # Starte Monitoring
                self.monitor.start_monitoring(
                    self.update_sensor1_status,
                    self.update_sensor2_status,
                    interval=0.05  # 20Hz Update-Rate
                )
                
                self.monitoring_active = True
                self.start_btn.config(state="disabled")
                self.stop_btn.config(state="normal")
                
                self.sensor1_text.config(text="Monitoring aktiv...")
                self.sensor2_text.config(text="Monitoring aktiv...")
            else:
                self.sensor1_text.config(text="Initialisierung fehlgeschlagen!")
                self.sensor2_text.config(text="Initialisierung fehlgeschlagen!")
                
        except Exception as e:
            print(f"Start Fehler: {e}")
            self.sensor1_text.config(text=f"Fehler: {e}")
    
    def stop_monitoring(self):
        """Stoppt Sensor-Monitoring"""
        self.monitor.stop_monitoring()
        self.monitoring_active = False
        
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        
        self.sensor1_status.config(fg='gray')
        self.sensor2_status.config(fg='gray')
        self.sensor1_text.config(text="Gestoppt")
        self.sensor2_text.config(text="Gestoppt")
        self.sensor1_value.config(text="GPIO: -")
        self.sensor2_value.config(text="GPIO: -")
    
    def update_sensor1_status(self, value):
        """Update Sensor 1 Anzeige (NC - Normally Closed)"""
        # NC: 0=frei (Kontakt offen), 1=angeschlagen (Kontakt geschlossen)
        if value == 1:
            color = 'red'
            text = "ANGESCHLAGEN"
        else:
            color = 'green'
            text = "FREI"
        
        self.sensor1_status.config(fg=color)
        self.sensor1_text.config(text=text)
        self.sensor1_value.config(text=f"GPIO: {value} ({'HIGH' if value else 'LOW'})")
    
    def update_sensor2_status(self, value):
        """Update Sensor 2 Anzeige (NO - Normally Open)"""
        # NO: 1=frei (Kontakt offen), 0=angeschlagen (Kontakt geschlossen)
        if value == 0:
            color = 'red'
            text = "ANGESCHLAGEN"
        else:
            color = 'green'
            text = "FREI"
        
        self.sensor2_status.config(fg=color)
        self.sensor2_text.config(text=text)
        self.sensor2_value.config(text=f"GPIO: {value} ({'HIGH' if value else 'LOW'})")
    
    def on_closing(self):
        """Cleanup beim Schließen"""
        self.monitor.cleanup()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = SensorTestGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
