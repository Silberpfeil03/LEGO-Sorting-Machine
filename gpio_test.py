#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPIO Test Tool - Raspberry Pi 3B
PWM Signal + Digital Output Test
"""

import tkinter as tk
from tkinter import ttk

# Versuche RPi.GPIO zu importieren
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("WARNUNG: RPi.GPIO nicht verfuegbar - Simulationsmodus")


# WiringPi zu BCM GPIO Mapping für Raspberry Pi 3B
# Format: WiringPi-Nr: {"bcm": BCM-GPIO, "physical": Physical-Pin, "name": Funktion}
WIRINGPI_PINS = {
    0:  {"bcm": 17, "physical": 11, "name": "GPIO"},
    1:  {"bcm": 18, "physical": 12, "name": "PWM0"},
    2:  {"bcm": 27, "physical": 13, "name": "GPIO"},
    3:  {"bcm": 22, "physical": 15, "name": "GPIO"},
    4:  {"bcm": 23, "physical": 16, "name": "GPIO"},
    5:  {"bcm": 24, "physical": 18, "name": "GPIO"},
    6:  {"bcm": 25, "physical": 22, "name": "GPIO"},
    7:  {"bcm": 4,  "physical": 7,  "name": "GPCLK0"},
    8:  {"bcm": 2,  "physical": 3,  "name": "SDA"},
    9:  {"bcm": 3,  "physical": 5,  "name": "SCL"},
    10: {"bcm": 8,  "physical": 24, "name": "SPI_CE0"},
    11: {"bcm": 7,  "physical": 26, "name": "SPI_CE1"},
    12: {"bcm": 10, "physical": 19, "name": "SPI_MOSI"},
    13: {"bcm": 9,  "physical": 21, "name": "SPI_MISO"},
    14: {"bcm": 11, "physical": 23, "name": "SPI_SCLK"},
    15: {"bcm": 14, "physical": 8,  "name": "UART_TXD"},
    16: {"bcm": 15, "physical": 10, "name": "UART_RXD"},
    21: {"bcm": 5,  "physical": 29, "name": "GPIO"},
    22: {"bcm": 6,  "physical": 31, "name": "GPIO"},
    23: {"bcm": 13, "physical": 33, "name": "PWM1"},
    24: {"bcm": 19, "physical": 35, "name": "PWM1"},
    25: {"bcm": 26, "physical": 37, "name": "GPIO"},
    26: {"bcm": 12, "physical": 32, "name": "PWM0"},
    27: {"bcm": 16, "physical": 36, "name": "GPIO"},
    28: {"bcm": 20, "physical": 38, "name": "GPIO"},
    29: {"bcm": 21, "physical": 40, "name": "GPIO"}
}

# Hardware-PWM-Pins (WiringPi-Nummerierung)
PWM_PINS = {
    1:  {"bcm": 18, "physical": 12, "channel": "PWM0"},
    26: {"bcm": 12, "physical": 32, "channel": "PWM0"},
    23: {"bcm": 13, "physical": 33, "channel": "PWM1"},
    24: {"bcm": 19, "physical": 35, "channel": "PWM1"}
}

# Alle verfügbaren GPIO-Pins (WiringPi-Nummerierung)
DIGITAL_PINS = WIRINGPI_PINS.copy()


class GPIOController:
    def __init__(self):
        self.pwm_pin = None
        self.pwm_object = None
        self.digital_pin = None
        self.digital_state = False
        
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
    
    def start_pwm(self, wiring_pin, frequency, duty_cycle):
        """Startet PWM auf angegebenem Pin (WiringPi-Nummer wird zu BCM konvertiert)"""
        # Stoppe vorheriges PWM
        self.stop_pwm()
        
        # Konvertiere WiringPi zu BCM
        bcm_pin = PWM_PINS[wiring_pin]["bcm"]
        
        if GPIO_AVAILABLE:
            try:
                GPIO.setup(bcm_pin, GPIO.OUT)
                self.pwm_object = GPIO.PWM(bcm_pin, frequency)
                self.pwm_object.start(duty_cycle)
                self.pwm_pin = bcm_pin
                return True
            except Exception as e:
                print(f"PWM Fehler: {e}")
                return False
        else:
            self.pwm_pin = bcm_pin
            print(f"SIMULATION: PWM auf WiringPi {wiring_pin} (BCM GPIO{bcm_pin}), {frequency}Hz, {duty_cycle}% Duty")
            return True
    
    def stop_pwm(self):
        """Stoppt PWM"""
        if self.pwm_object:
            self.pwm_object.stop()
            self.pwm_object = None
        if self.pwm_pin and GPIO_AVAILABLE:
            GPIO.cleanup(self.pwm_pin)
        self.pwm_pin = None
    
    def set_digital_pin(self, wiring_pin, state):
        """Setzt Digital-Pin auf HIGH oder LOW (WiringPi-Nummer wird zu BCM konvertiert)"""
        # Konvertiere WiringPi zu BCM
        bcm_pin = WIRINGPI_PINS[wiring_pin]["bcm"]
        
        if self.digital_pin and self.digital_pin != bcm_pin:
            if GPIO_AVAILABLE:
                GPIO.cleanup(self.digital_pin)
        
        self.digital_pin = bcm_pin
        self.digital_state = state
        
        if GPIO_AVAILABLE:
            try:
                GPIO.setup(bcm_pin, GPIO.OUT)
                GPIO.output(bcm_pin, GPIO.HIGH if state else GPIO.LOW)
                return True
            except Exception as e:
                print(f"Digital Pin Fehler: {e}")
                return False
        else:
            print(f"SIMULATION: WiringPi {wiring_pin} (BCM GPIO{bcm_pin}) = {'HIGH' if state else 'LOW'}")
            return True
    
    def cleanup(self):
        """Räumt GPIO auf"""
        self.stop_pwm()
        if GPIO_AVAILABLE:
            GPIO.cleanup()


class GPIOTestGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("GPIO Test - Raspberry Pi 3B (WiringPi)")
        self.root.geometry("550x420")
        
        self.controller = GPIOController()
        
        # Styles
        style = ttk.Style()
        style.configure("TButton", padding=5, font=("Arial", 10))
        style.configure("TLabel", font=("Arial", 10))
        
        # PWM Frame
        pwm_frame = ttk.LabelFrame(root, text="Hardware-PWM Signal", padding=10)
        pwm_frame.pack(fill="x", padx=10, pady=5)
        
        # PWM Pin Auswahl mit Details
        ttk.Label(pwm_frame, text="PWM Pin:").grid(row=0, column=0, sticky="w", pady=3)
        self.pwm_pin_var = tk.StringVar(value="1")
        
        # Erstelle Pin-Beschreibungen mit WiringPi-Nummerierung
        pwm_choices = [f"WiringPi {pin} (BCM {info['bcm']}, Pin {info['physical']}, {info['channel']})" 
                      for pin, info in PWM_PINS.items()]
        pwm_pin_combo = ttk.Combobox(pwm_frame, textvariable=self.pwm_pin_var, 
                                     values=pwm_choices, 
                                     width=42, state="readonly")
        pwm_pin_combo.grid(row=0, column=1, columnspan=2, padx=5, pady=3)
        pwm_pin_combo.current(0)  # WiringPi 1 (GPIO18) als Standard
        
        # Frequenz
        ttk.Label(pwm_frame, text="Frequenz (Hz):").grid(row=1, column=0, sticky="w", pady=3)
        self.pwm_freq_var = tk.StringVar(value="1000")
        freq_entry = ttk.Entry(pwm_frame, textvariable=self.pwm_freq_var, width=12)
        freq_entry.grid(row=1, column=1, padx=5, pady=3)
        
        # Duty Cycle
        ttk.Label(pwm_frame, text="Duty Cycle (%):").grid(row=2, column=0, sticky="w", pady=3)
        self.pwm_duty_var = tk.IntVar(value=50)
        duty_scale = ttk.Scale(pwm_frame, from_=0, to=100, variable=self.pwm_duty_var, 
                              orient="horizontal", length=150)
        duty_scale.grid(row=2, column=1, padx=5, pady=3)
        self.duty_label = ttk.Label(pwm_frame, text="50%")
        self.duty_label.grid(row=2, column=2, padx=5)
        
        # Update Duty Label
        def update_duty_label(*args):
            self.duty_label.config(text=f"{self.pwm_duty_var.get()}%")
        self.pwm_duty_var.trace_add("write", update_duty_label)
        
        # PWM Buttons
        btn_frame = ttk.Frame(pwm_frame)
        btn_frame.grid(row=3, column=0, columnspan=3, pady=8)
        
        ttk.Button(btn_frame, text="START PWM", command=self.start_pwm).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="STOP PWM", command=self.stop_pwm).pack(side="left", padx=3)
        
        # PWM Status
        self.pwm_status = ttk.Label(pwm_frame, text="Status: Aus", foreground="red")
        self.pwm_status.grid(row=4, column=0, columnspan=3, pady=3)
        
        # Digital Pin Frame
        digital_frame = ttk.LabelFrame(root, text="Digital Pin (HIGH/LOW)", padding=10)
        digital_frame.pack(fill="x", padx=10, pady=5)
        
        # Digital Pin Auswahl mit Details
        ttk.Label(digital_frame, text="GPIO Pin:").grid(row=0, column=0, sticky="w", pady=3)
        self.digital_pin_var = tk.StringVar(value="0")
        
        # Erstelle Pin-Beschreibungen mit WiringPi-Nummerierung
        digital_choices = [f"WiringPi {pin} (BCM {info['bcm']}, Pin {info['physical']}, {info['name']})" 
                          for pin, info in DIGITAL_PINS.items()]
        digital_pin_combo = ttk.Combobox(digital_frame, textvariable=self.digital_pin_var,
                                        values=digital_choices,
                                        width=42, state="readonly")
        digital_pin_combo.grid(row=0, column=1, columnspan=2, padx=5, pady=3)
        digital_pin_combo.current(0)  # WiringPi 0 (BCM 17) als Standard
        
        # HIGH/LOW Buttons
        btn_frame2 = ttk.Frame(digital_frame)
        btn_frame2.grid(row=1, column=0, columnspan=2, pady=8)
        
        ttk.Button(btn_frame2, text="HIGH", command=self.set_high).pack(side="left", padx=3)
        ttk.Button(btn_frame2, text="LOW", command=self.set_low).pack(side="left", padx=3)
        
        # Digital Status
        self.digital_status = ttk.Label(digital_frame, text="Status: -", foreground="gray")
        self.digital_status.grid(row=2, column=0, columnspan=2, pady=3)
        
        # Info Label
        info_frame = ttk.Frame(root)
        info_frame.pack(pady=8)
        
        if GPIO_AVAILABLE:
            ttk.Label(info_frame, text="⚡ Hardware-PWM verfügbar (WiringPi Nummerierung)", 
                     font=("Arial", 9, "bold"), foreground="green").pack()
            ttk.Label(info_frame, text="PWM0: WiringPi 1 & 26 | PWM1: WiringPi 23 & 24", 
                     font=("Arial", 8), foreground="blue").pack()
        else:
            ttk.Label(info_frame, text="⚠ SIMULATIONSMODUS (RPi.GPIO nicht verfügbar)", 
                     font=("Arial", 9, "bold"), foreground="orange").pack()
    
    def start_pwm(self):
        """Startet PWM Signal"""
        try:
            # Parse WiringPi Nummer aus "WiringPi 1 (BCM 18, Pin 12, PWM0)" Format
            pin_text = self.pwm_pin_var.get()
            wiring_pin = int(pin_text.split("WiringPi ")[1].split()[0])
            
            freq = int(self.pwm_freq_var.get())
            duty = self.pwm_duty_var.get()
            
            if freq < 1 or freq > 100000:
                self.pwm_status.config(text="Fehler: Frequenz 1-100000 Hz", foreground="red")
                return
            
            if self.controller.start_pwm(wiring_pin, freq, duty):
                pwm_info = PWM_PINS[wiring_pin]
                self.pwm_status.config(
                    text=f"PWM läuft: WiringPi {wiring_pin} (BCM {pwm_info['bcm']}, {pwm_info['channel']}), {freq}Hz, {duty}%", 
                    foreground="green")
            else:
                self.pwm_status.config(text="Fehler beim Starten", foreground="red")
        except (ValueError, KeyError) as e:
            self.pwm_status.config(text=f"Fehler: Ungültige Eingabe ({e})", foreground="red")
    
    def stop_pwm(self):
        """Stoppt PWM Signal"""
        self.controller.stop_pwm()
        self.pwm_status.config(text="Status: Aus", foreground="red")
    
    def set_high(self):
        """Setzt Pin auf HIGH"""
        try:
            # Parse WiringPi Nummer aus "WiringPi 0 (BCM 17, Pin 11, GPIO)" Format
            pin_text = self.digital_pin_var.get()
            wiring_pin = int(pin_text.split("WiringPi ")[1].split()[0])
            
            if self.controller.set_digital_pin(wiring_pin, True):
                pin_info = DIGITAL_PINS[wiring_pin]
                self.digital_status.config(
                    text=f"WiringPi {wiring_pin} (BCM {pin_info['bcm']}, Pin {pin_info['physical']}) = HIGH", 
                    foreground="green")
        except (ValueError, KeyError) as e:
            self.digital_status.config(text=f"Fehler: Ungültiger Pin ({e})", foreground="red")
    
    def set_low(self):
        """Setzt Pin auf LOW"""
        try:
            # Parse WiringPi Nummer aus "WiringPi 0 (BCM 17, Pin 11, GPIO)" Format
            pin_text = self.digital_pin_var.get()
            wiring_pin = int(pin_text.split("WiringPi ")[1].split()[0])
            
            if self.controller.set_digital_pin(wiring_pin, False):
                pin_info = DIGITAL_PINS[wiring_pin]
                self.digital_status.config(
                    text=f"WiringPi {wiring_pin} (BCM {pin_info['bcm']}, Pin {pin_info['physical']}) = LOW", 
                    foreground="orange")
        except (ValueError, KeyError) as e:
            self.digital_status.config(text=f"Fehler: Ungültiger Pin ({e})", foreground="red")
    
    def on_closing(self):
        """Cleanup beim Schließen"""
        self.controller.cleanup()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = GPIOTestGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
