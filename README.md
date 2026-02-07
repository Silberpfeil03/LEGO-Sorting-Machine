# 🧱 LEGO Sorting Machine

> A university project for automatically sorting LEGO bricks according to Set Numbers using computer vision, Raspberry Pi, and mechanical sorting mechanisms.

---

## 📖 Project Overview

This is an **open-source university project** that uses a Raspberry Pi, camera module, and mechanical components to automatically identify and sort LEGO bricks by **color and type**. The system leverages the [Brickognize API](https://brickognize.com/) for brick recognition and controls servos, stepper motors, and LED strips for the sorting process according to the given LEGO Sets.

> ⚠️ **Disclaimer:** This project is provided as-is for educational purposes. We do not take responsibility for any damage, accidents, or injuries that may occur during the construction, operation, or use of this sorting machine. **Use at your own risk.**

> **Trademark Notice:** LEGO® is a trademark of the LEGO Group of companies, which does not sponsor, authorize, or endorse this project. All references to LEGO® are for identification purposes only.

---

## 🔧 Hardware Requirements

### Raspberry Pi

| | |
|---|---|
| **Tested on** | Raspberry Pi 3 Model B+ |
| **Should work on** | Any Raspberry Pi with GPIO pins (Pi 2, Pi 3, Pi 4, etc.) |

### Essential Components

- Raspberry Pi (3B+ or similar)
- Raspberry Pi Camera Module (Picamera2 compatible)
- Servo motors (recommend using stronger servos for the Schleuse/gate mechanism)
- Stepper motor with driver
- LED strip (WS281x/NeoPixel compatible or SPI)
- Power supply suitable for servos and motors
- Connecting wires and breadboard/PCB

### 3D Printed Parts

All 3D parts are listed in the project repository. Print them using your preferred 3D printer.

> **⚠️ Note:** You might need to make some modifications to the Schleuse (gate/lock mechanism) depending on your specific servo choice. Use a **stronger servo** for the Schleuse mechanism to ensure reliable operation.

---

## 💾 Software Installation

### 1. Update your Raspberry Pi

```bash
sudo apt-get update
sudo apt-get upgrade
```

### 2. Install Python Dependencies

**System packages:**

```bash
sudo apt-get install python3-pip python3-pil python3-numpy
sudo apt-get install python3-picamera2
sudo apt-get install python3-rpi.gpio
```

**Python packages** (use `--user` flag or a virtual environment):

```bash
pip3 install --user requests
pip3 install --user beautifulsoup4
pip3 install --user pandas
pip3 install --user openpyxl
```

**LED Strip support** (choose one based on your hardware):

```bash
# Option 1: WS281x LEDs (recommended – uses GPIO 12)
pip3 install --user rpi-ws281x

# Option 2: Adafruit NeoPixel
pip3 install --user adafruit-circuitpython-neopixel

# Option 3: SPI-based LEDs (alternative – uses GPIO 12)
pip3 install --user spidev
```

---

## 🔌 Pin Connections

Connect the components to your Raspberry Pi GPIO pins. The following **WiringPi pin mapping** is used in the code:

| Component | WiringPi Pin | Function | Notes |
|---|:---:|---|---|
| Stepper Motor Speed (PWM) | 1 | PWM0 | Hardware PWM for speed control |
| Stepper Motor Direction | 0 | GPIO | `0` = Up, `1` = Down |
| Lower Limit Sensor | 2 | Input | Normally Open (`1` = triggered) |
| Upper Limit Sensor | 27 | Input | Pull-Down configuration |
| Sorting Servo | 23 | PWM1 | Hardware PWM, 270° servo |
| Gate/Schleuse Servo | 6 | GPIO | Software PWM for gate mechanism |
| Vibration Motor 1 | 4 | GPIO | Software PWM |
| Vibration Motor 2 | 5 | GPIO | Software PWM |
| Vibration Kill Pin | 8 | Output | Emergency stop for lower vibration motor |
| LED Strip (SPI Alternative) | 12 | MOSI (SPI0) | Use if GPIO 18 is occupied |


---

## 🚀 Usage

1. Ensure all hardware components are properly connected.
2. Make sure `lego_colors.CSV` or `lego_colors.xlsx` is in the same directory as the script.
   - This file contains the LEGO color reference database (color IDs, names, RGB values).
   - The existing files in the repository should work out of the box.
3. Run the main program:

```bash
python3 Raspi_Sortiermaschine_Code.py
```

4. The **GUI interface** will launch, allowing you to:
   - 📸 Capture images of LEGO bricks
   - 🔍 Identify bricks using the Brickognize API
   - ⚙️ Control the sorting mechanism
   - 📊 Monitor the sorting process

---

## 📋 Assembly Notes

| Topic | Details |
|---|---|
| **Schleuse Mechanism** | The gate/lock mechanism may require adjustments depending on your servo choice. Consider using a stronger servo for reliable operation. |
| **Servo Selection** | Standard hobby servos may not provide enough torque. Use stronger servos for critical components, especially the Schleuse. |
| **Calibration** | You may need to calibrate servo positions for your specific build. |

---

## 📂 Project Files

| File | Description |
|---|---|
| `Raspi_Sortiermaschine_Code.py` | Main control software |
| `lego_colors.CSV` / `lego_colors.xlsx` | LEGO color reference database |
| 3D printable parts | Can be found in the printing folder|

---


## 📄 License

Open Source – Feel free to use, modify, and distribute this project.

---

## 🔗 External Services

This project uses the [Brickognize API](https://brickognize.com/) for LEGO brick recognition.
