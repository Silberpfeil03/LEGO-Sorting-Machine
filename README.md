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

# Install Python packages via pip
pip3 install requests
pip3 install Pillow
pip3 install beautifulsoup4
pip3 install pandas
pip3 install numpy
pip3 install openpyxl

# LED Strip support (choose one based on your hardware)
# For WS281x LEDs:
pip3 install rpi-ws281x

# OR for Adafruit NeoPixel:
pip3 install adafruit-circuitpython-neopixel

# For SPI-based LEDs:
pip3 install spidev
```

## 🔌 Pin Connections

Connect the components to your Raspberry Pi GPIO pins accordingly. The following BCM pin mapping is used in the code:

### Pin Configuration (BCM Mode)

| Component | BCM Pin | Function |
|-----------|---------|----------|
| Servo 1 | GPIO 18 | PWM Control |
| Servo 2 | GPIO 17 | Control |
| Sensor 1 | GPIO 27 | Input |
| Sensor 2 | GPIO 16 | Input |
| Stepper Motor | GPIO 13 | Control |
| Pin 5 | GPIO 25 | General I/O |
| Pin 6 | GPIO 23 | General I/O |
| Pin 7 | GPIO 24 | General I/O |
| LED Control | GPIO 14 | Output |
| LED Strip (SPI) | GPIO 10 | MOSI (SPI0) |

**⚠️ Make sure to connect your components according to these pin assignments before running the software.**

## 🚀 Usage

1. Ensure all hardware components are properly connected
2. Place the `lego_colors.CSV` or `lego_colors.xlsx` file in the same directory as the script
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
