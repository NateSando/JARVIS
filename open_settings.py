import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

# pyrefly: ignore [missing-import]
from PyQt6.QtWidgets import QApplication
# pyrefly: ignore [missing-import]
from desktop_app.settings_window import SettingsWindow

def main():
    app = QApplication(sys.argv)
    window = SettingsWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
