# LEGO Baustein-Sortiermaschine (LEGO Brick Sorting Machine)

A university project for automatically sorting LEGO bricks using computer vision, Raspberry Pi, and mechanical sorting mechanisms.

## 🎓 Project Overview

This is an open-source university project that uses a Raspberry Pi, camera module, and mechanical components to automatically identify and sort LEGO bricks by color and type. The system leverages the Brickognize API for brick recognition and controls servos, stepper motors, and LED strips for the sorting process.

## ⚠️ Disclaimer

**This project is provided as-is for educational purposes. We do not take responsibility for any damage, accidents, or injuries that may occur during the construction, operation, or use of this sorting machine. Use at your own risk.**

## 🔧 Hardware Requirements

### Raspberry Pi
- **Tested on:** Raspberry Pi 3 Model B+
- **Should work on:** Any Raspberry Pi with GPIO pins (Pi 2, Pi 3, Pi 4, etc.)

### Required Components

*Note: Detailed component list will be added here.*

**Essential Components:**
- Raspberry Pi (3B+ or similar)
- Raspberry Pi Camera Module (Picamera2 compatible)
- Servo motors (recommend using stronger servos for the Schleuse/gate mechanism)
- Stepper motor with driver
- LED strip (WS281x/NeoPixel compatible or SPI)
- Various sensors (IR/proximity sensors)
- Power supply suitable for servos and motors
- Connecting wires and breadboard/PCB

### 3D Printed Parts

All 3D parts are listed in the project repository. Print them using your preferred 3D printer:

1. Print out all the 3D parts listed in the project
2. **Note:** You might need to make some modifications to the **Schleuse** (gate/lock mechanism) depending on your specific servo choice
3. **Important:** Use a stronger servo for the Schleuse mechanism to ensure reliable operation

## 💾 Software Installation

### 1. Install Required Libraries on Your Raspberry Pi

First, update your system:
```bash
sudo apt-get update
sudo apt-get upgrade
```

### 2. Install Python Dependencies

Install the required Python packages:
```bash
# System packages
sudo apt-get install python3-pip python3-pil python3-numpy

# Camera support
sudo apt-get install python3-picamera2

# GPIO support
sudo apt-get install python3-rpi.gpio

# Install Python packages via pip (use --user flag or virtual environment to avoid permission issues)
pip3 install --user requests
pip3 install --user beautifulsoup4
pip3 install --user pandas
pip3 install --user openpyxl

# LED Strip support (choose one based on your hardware)
# For WS281x LEDs (recommended - uses GPIO 18):
pip3 install --user rpi-ws281x

# OR for Adafruit NeoPixel:
pip3 install --user adafruit-circuitpython-neopixel

# OR for SPI-based LEDs (alternative option - uses GPIO 10):
pip3 install --user spidev
```

## 🔌 Pin Connections

Connect the components to your Raspberry Pi GPIO pins accordingly. The following BCM pin mapping is used in the code:

### Pin Configuration (BCM Mode)

| Component | BCM Pin | Function | Notes |
|-----------|---------|----------|-------|
| Stepper Motor Speed (PWM) | GPIO 18 | PWM0 | Hardware PWM for speed control |
| Stepper Motor Direction | GPIO 17 | GPIO | 0=Up, 1=Down |
| Lower Limit Sensor | GPIO 27 | Input | Normally Open (1=triggered) |
| Upper Limit Sensor | GPIO 16 | Input | Pull-Down configuration |
| Sorting Servo | GPIO 13 | PWM1 | Hardware PWM, 270° servo |
| Gate/Schleuse Servo | GPIO 25 | GPIO | Software PWM for gate mechanism |
| Vibration Motor 1 | GPIO 23 | GPIO | Software PWM |
| Vibration Motor 2 | GPIO 24 | GPIO | Software PWM |
| Vibration Kill Pin | GPIO 14 | Output | Emergency stop for lower vibration motor |
| LED Strip (WS281x) | GPIO 18 | PWM | **Note:** Conflicts with Stepper PWM! |
| LED Strip (SPI Alternative) | GPIO 10 | MOSI (SPI0) | Use this if GPIO 18 is needed for stepper |

**⚠️ Important Notes:**
- **LED/Stepper Conflict:** GPIO 18 is used for both the Stepper Motor PWM and WS281x LED strip. If you need both, use the SPI option (GPIO 10) for LEDs instead.
- Make sure to connect your components according to these pin assignments before running the software.
- The system uses BCM (Broadcom) pin numbering mode.

## 🚀 Usage

1. Ensure all hardware components are properly connected
2. Make sure the `lego_colors.CSV` or `lego_colors.xlsx` file is in the same directory as the script
   - This file contains the LEGO color reference database
   - The existing files in the repository should work out of the box
   - Format includes LEGO color IDs, names, and RGB values for color matching
3. Run the main program:
```bash
python3 Raspi_Sortiermaschine_Code.py
```

4. The GUI interface will launch, allowing you to:
   - Capture images of LEGO bricks
   - Identify bricks using the Brickognize API
   - Control the sorting mechanism
   - Monitor the sorting process

## 📋 Assembly Notes

1. **Schleuse Mechanism:** The gate/lock mechanism (Schleuse) may require adjustments depending on your servo choice. Consider using a stronger servo for reliable operation.

2. **Servo Selection:** Standard hobby servos may not provide enough torque. **Use stronger servos** for critical movement components, especially the Schleuse.

3. **Calibration:** You may need to calibrate servo positions and sensor thresholds for your specific build.

## 📂 Project Files

- `Raspi_Sortiermaschine_Code.py` - Main control software
- `lego_colors.CSV` / `lego_colors.xlsx` - LEGO color reference database
- 3D printable parts (to be listed)

## 🤝 Contributing

This is an open-source project. Contributions, improvements, and adaptations are welcome!

## 📄 License

Open Source - Feel free to use, modify, and distribute this project.

## 🔗 External Services

This project uses the [Brickognize API](https://brickognize.com/) for LEGO brick recognition.

---

**Built with ❤️ as a university project**
