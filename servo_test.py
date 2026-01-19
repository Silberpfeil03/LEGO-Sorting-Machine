#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Servo Test Tool - Raspberry Pi 3B
Hardware-PWM für Servo-Steuerung
"""

import tkinter as tk
from tkinter import ttk
import time

# Versuche RPi.GPIO zu importieren
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("WARNUNG: RPi.GPIO nicht verfuegbar - Simulationsmodus")


# Hardware-PWM-Pins (WiringPi-Nummerierung mit BCM Mapping)
PWM_PINS = {
    1:  {"bcm": 18, "physical": 12, "channel": "PWM0"},
    26: {"bcm": 12, "physical": 32, "channel": "PWM0"},
    23: {"bcm": 13, "physical": 33, "channel": "PWM1"},
    24: {"bcm": 19, "physical": 35, "channel": "PWM1"}
}


class ServoController:
    def __init__(self):
        self.pwm_pin = None
        self.pwm_object = None
        self.servo_frequency = 50  # Standard Servo Frequenz: 50Hz
        
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
    
    def init_servo(self, wiring_pin):
        """Initialisiert Servo auf angegebenem PWM Pin"""
        # Stoppe vorheriges Servo
        self.stop_servo()
        
        # Konvertiere WiringPi zu BCM
        bcm_pin = PWM_PINS[wiring_pin]["bcm"]
        
        if GPIO_AVAILABLE:
            try:
                GPIO.setup(bcm_pin, GPIO.OUT)
                self.pwm_object = GPIO.PWM(bcm_pin, self.servo_frequency)
                self.pwm_object.start(0)  # Start mit 0% Duty (keine Bewegung)
                self.pwm_pin = bcm_pin
                print(f"Servo initialisiert auf BCM GPIO{bcm_pin} (50Hz)")
                return True
            except Exception as e:
                print(f"Servo Init Fehler: {e}")
                return False
        else:
            self.pwm_pin = bcm_pin
            print(f"SIMULATION: Servo auf BCM GPIO{bcm_pin}")
            return True
    
    def set_angle(self, angle):
        """
        Setzt Servo auf bestimmten Winkel (0-180°)
        
        Servo PWM Mapping:
        - 0°   = 2.5% Duty Cycle (0.5ms bei 50Hz)
        - 90°  = 7.5% Duty Cycle (1.5ms bei 50Hz)
        - 180° = 12.5% Duty Cycle (2.5ms bei 50Hz)
        """
        if not self.pwm_object and not GPIO_AVAILABLE:
            print(f"SIMULATION: Servo → {angle}°")
            return True
        
        if not self.pwm_object:
            return False
        
        try:
            # Begrenze Winkel auf 0-180°
            angle = max(0, min(180, angle))
            
            # Konvertiere Winkel zu Duty Cycle (2.5% - 12.5%)
            duty_cycle = 2.5 + (angle / 180.0) * 10.0
            
            self.pwm_object.ChangeDutyCycle(duty_cycle)
            print(f"Servo: {angle}° (Duty: {duty_cycle:.2f}%)")
            return True
            
        except Exception as e:
            print(f"Servo Ansteuerung Fehler: {e}")
            return False
    
    def stop_servo(self):
        """Stoppt Servo (PWM auf 0)"""
        if self.pwm_object:
            self.pwm_object.ChangeDutyCycle(0)
            time.sleep(0.1)
            self.pwm_object.stop()
            self.pwm_object = None
        
        if self.pwm_pin and GPIO_AVAILABLE:
            GPIO.cleanup(self.pwm_pin)
        
        self.pwm_pin = None
        print("Servo gestoppt")
    
    def cleanup(self):
        """GPIO Cleanup"""
        self.stop_servo()
        if GPIO_AVAILABLE:
            GPIO.cleanup()


class ServoTestGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Servo Test - Raspberry Pi 3B")
        self.root.geometry("500x500")
        
        self.controller = ServoController()
        self.servo_initialized = False
        
        # Styles
        style = ttk.Style()
        style.configure("TButton", padding=5, font=("Arial", 10))
        style.configure("TLabel", font=("Arial", 10))
        
        # Header
        header_frame = tk.Frame(root, bg='#2196f3', height=50)
        header_frame.pack(fill='x')
        tk.Label(header_frame, text="🔧 Servo Tester", 
                font=("Arial", 14, "bold"), bg='#2196f3', fg='white').pack(pady=10)
        
        # PWM Pin Auswahl Frame
        pin_frame = ttk.LabelFrame(root, text="PWM Pin Auswahl", padding=10)
        pin_frame.pack(fill="x", padx=10, pady=10)
        
        ttk.Label(pin_frame, text="PWM Pin:").grid(row=0, column=0, sticky="w", pady=5)
        self.pwm_pin_var = tk.StringVar(value="23")
        
        # Erstelle Pin-Beschreibungen
        pwm_choices = [f"WiringPi {pin} (BCM {info['bcm']}, Pin {info['physical']}, {info['channel']})" 
                      for pin, info in PWM_PINS.items()]
        pwm_pin_combo = ttk.Combobox(pin_frame, textvariable=self.pwm_pin_var,
                                     values=pwm_choices,
                                     width=42, state="readonly")
        pwm_pin_combo.grid(row=0, column=1, padx=5, pady=5)
        pwm_pin_combo.current(2)  # WiringPi 23 (GPIO13) als Standard
        
        # Init Button
        ttk.Button(pin_frame, text="Servo Initialisieren", 
                  command=self.init_servo).grid(row=1, column=0, columnspan=2, pady=8)
        
        self.pin_status = ttk.Label(pin_frame, text="Status: Nicht initialisiert", foreground="red")
        self.pin_status.grid(row=2, column=0, columnspan=2)
        
        # Servo Steuerung Frame
        self.control_frame = ttk.LabelFrame(root, text="Servo Steuerung", padding=10)
        self.control_frame.pack(fill="x", padx=10, pady=10)
        
        # Winkel Slider
        ttk.Label(self.control_frame, text="Winkel (0-180°):").grid(row=0, column=0, sticky="w", pady=5)
        
        self.angle_var = tk.IntVar(value=90)
        angle_slider = ttk.Scale(self.control_frame, from_=0, to=180, 
                                variable=self.angle_var,
                                orient="horizontal", length=300,
                                command=self.on_angle_change)
        angle_slider.grid(row=0, column=1, padx=10, pady=5)
        
        self.angle_label = ttk.Label(self.control_frame, text="90°", font=("Arial", 12, "bold"))
        self.angle_label.grid(row=0, column=2, padx=5)
        
        # Schnell-Positionen
        ttk.Label(self.control_frame, text="Schnellzugriff:").grid(row=1, column=0, sticky="w", pady=10)
        
        quick_frame = tk.Frame(self.control_frame)
        quick_frame.grid(row=1, column=1, columnspan=2, pady=10)
        
        ttk.Button(quick_frame, text="0°", width=8,
                  command=lambda: self.set_angle(0)).pack(side="left", padx=3)
        ttk.Button(quick_frame, text="45°", width=8,
                  command=lambda: self.set_angle(45)).pack(side="left", padx=3)
        ttk.Button(quick_frame, text="90°", width=8,
                  command=lambda: self.set_angle(90)).pack(side="left", padx=3)
        ttk.Button(quick_frame, text="135°", width=8,
                  command=lambda: self.set_angle(135)).pack(side="left", padx=3)
        ttk.Button(quick_frame, text="180°", width=8,
                  command=lambda: self.set_angle(180)).pack(side="left", padx=3)
        
        # Test-Sequenzen
        test_frame = ttk.LabelFrame(root, text="Test-Sequenzen", padding=10)
        test_frame.pack(fill="x", padx=10, pady=10)
        
        ttk.Button(test_frame, text="Sweep Test (0° → 180° → 0°)", 
                  command=self.sweep_test).pack(pady=3)
        ttk.Button(test_frame, text="Mittelposition Test (90°)", 
                  command=lambda: self.set_angle(90)).pack(pady=3)
        
        # Stop Button
        stop_frame = tk.Frame(root)
        stop_frame.pack(pady=10)
        
        ttk.Button(stop_frame, text="⏹ SERVO STOPPEN", 
                  command=self.stop_servo,
                  style="TButton").pack()
        
        # Status Label
        self.status_label = ttk.Label(root, text="Bereit", font=("Arial", 9))
        self.status_label.pack(pady=5)
        
        # Info
        info_frame = tk.Frame(root, bg='#f0f0f0')
        info_frame.pack(fill='x', side='bottom')
        
        if GPIO_AVAILABLE:
            info_text = "⚡ Hardware-PWM verfügbar | 50Hz Standard-Frequenz"
            color = "green"
        else:
            info_text = "⚠ SIMULATIONSMODUS (RPi.GPIO nicht verfügbar)"
            color = "orange"
        
        ttk.Label(info_frame, text=info_text, 
                 font=("Arial", 8), foreground=color, background='#f0f0f0').pack(pady=5)
        
        # Deaktiviere Steuerung initial
        self.set_control_state(False)
    
    def set_control_state(self, enabled):
        """Aktiviert/Deaktiviert Steuerelemente"""
        state = "normal" if enabled else "disabled"
        for child in self.control_frame.winfo_children():
            if isinstance(child, (ttk.Button, ttk.Scale)):
                child.config(state=state)
            elif isinstance(child, tk.Frame):
                for subchild in child.winfo_children():
                    if isinstance(subchild, ttk.Button):
                        subchild.config(state=state)
    
    def init_servo(self):
        """Initialisiert Servo"""
        try:
            pin_text = self.pwm_pin_var.get()
            wiring_pin = int(pin_text.split("WiringPi ")[1].split()[0])
            
            if self.controller.init_servo(wiring_pin):
                pwm_info = PWM_PINS[wiring_pin]
                self.pin_status.config(
                    text=f"✓ Servo bereit: BCM {pwm_info['bcm']} ({pwm_info['channel']}, 50Hz)",
                    foreground="green")
                self.servo_initialized = True
                self.set_control_state(True)
                self.status_label.config(text="Servo initialisiert - Bereit zur Steuerung")
            else:
                self.pin_status.config(text="✗ Initialisierung fehlgeschlagen", foreground="red")
        except Exception as e:
            self.pin_status.config(text=f"Fehler: {e}", foreground="red")
    
    def on_angle_change(self, value):
        """Callback wenn Slider bewegt wird"""
        angle = int(float(value))
        self.angle_label.config(text=f"{angle}°")
        if self.servo_initialized:
            self.set_angle(angle)
    
    def set_angle(self, angle):
        """Setzt Servo auf Winkel"""
        if not self.servo_initialized:
            self.status_label.config(text="⚠ Bitte erst Servo initialisieren!")
            return
        
        self.angle_var.set(angle)
        self.angle_label.config(text=f"{angle}°")
        
        if self.controller.set_angle(angle):
            self.status_label.config(text=f"✓ Servo Position: {angle}°")
        else:
            self.status_label.config(text=f"✗ Fehler beim Setzen auf {angle}°")
    
    def sweep_test(self):
        """Führt Sweep-Test durch"""
        if not self.servo_initialized:
            self.status_label.config(text="⚠ Bitte erst Servo initialisieren!")
            return
        
        self.status_label.config(text="🔄 Sweep Test läuft...")
        self.root.update()
        
        try:
            # 0° → 180°
            for angle in range(0, 181, 5):
                self.set_angle(angle)
                self.root.update()
                time.sleep(0.05)
            
            time.sleep(0.3)
            
            # 180° → 0°
            for angle in range(180, -1, -5):
                self.set_angle(angle)
                self.root.update()
                time.sleep(0.05)
            
            self.status_label.config(text="✓ Sweep Test abgeschlossen")
        except Exception as e:
            self.status_label.config(text=f"✗ Test Fehler: {e}")
    
    def stop_servo(self):
        """Stoppt Servo"""
        self.controller.stop_servo()
        self.servo_initialized = False
        self.set_control_state(False)
        self.pin_status.config(text="Status: Gestoppt", foreground="red")
        self.status_label.config(text="Servo gestoppt")
    
    def on_closing(self):
        """Cleanup beim Schließen"""
        self.controller.cleanup()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ServoTestGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
