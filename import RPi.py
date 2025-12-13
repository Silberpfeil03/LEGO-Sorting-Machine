import time
import sys
import tty
import termios
from picamera import PiCamera

# Pfad zum gespeicherten Bild
IMAGE_PATH = "/home/pi/captured.jpg"

# Kamera initialisieren
camera = PiCamera()
camera.resolution = (1024, 768)

def capture_image():
    camera.capture(IMAGE_PATH)
    print("📸 Bild aufgenommen: ", IMAGE_PATH)

def wait_for_keypress():
    print("⬇️  Drücke [Leertaste] zum Fotografieren, [q] zum Beenden")
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            key = sys.stdin.read(1)
            if key == ' ':
                capture_image()
            elif key == 'q':
                print("🚪 Beende Programm...")
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        camera.close()

if __name__ == "__main__":
    wait_for_keypress()
