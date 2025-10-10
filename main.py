import sys
import threading
import time
import json
from functools import partial

import serial
import serial.tools.list_ports

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from PIL import Image, ImageDraw
import pystray

# --- Serial / Arduino interface part ---

BAUDRATE = 9600
SERIAL_TIMEOUT = 1.0  # seconds

def list_serial_ports():
    """Return a list of port names, e.g. ['COM3', '/dev/ttyUSB0']."""
    ports = serial.tools.list_ports.comports()
    return [p.device for p in ports]

def test_arduino_port(port):
    """Try opening `port`, reading a line, and see if it matches the expected format."""
    try:
        ser = serial.Serial(port, BAUDRATE, timeout=SERIAL_TIMEOUT)
        # Wait a short time for data to arrive / reset
        time.sleep(0.5)
        ser.reset_input_buffer()
        line = ser.readline().decode(errors='ignore').strip()
        ser.close()
        if line.startswith("POTS:") and "BUTTONS:" in line:
            return True
    except Exception as e:
        # print("Port test failed", port, e)
        pass
    return False

class ArduinoReader(threading.Thread):
    def __init__(self, port, on_data_callback):
        super().__init__(daemon=True)
        self.port = port
        self.on_data = on_data_callback
        self._stop_event = threading.Event()
        try:
            self.ser = serial.Serial(self.port, BAUDRATE, timeout=0.1)
        except Exception as e:
            self.ser = None
        self.lock = threading.Lock()

    def run(self):
        if not self.ser:
            return
        while not self._stop_event.is_set():
            try:
                line = self.ser.readline().decode(errors='ignore').strip()
                if line:
                    # Forward valid lines
                    self.on_data(line)
            except Exception as e:
                # perhaps disconnected
                break
        self.close()

    def close(self):
        with self.lock:
            if self.ser:
                try:
                    self.ser.close()
                except:
                    pass
                self.ser = None

    def stop(self):
        self._stop_event.set()
        self.close()


# --- GUI / Application logic ---

class ButtonMapping:
    def __init__(self, button_index):
        self.button_index = button_index
        # Defaults: no mapping
        self.keypresses = []  # list of strings, e.g. ["ctrl+z", "F5"]
        self.command = None   # shell command string

class PotMapping:
    def __init__(self, pot_index):
        self.pot_index = pot_index
        # Map type: "master", "mic", "app", or None
        self.mapping_type = None
        self.app_name = None
        self.min_val = 0
        self.max_val = 1023

class MainApp:
    def __init__(self, root, minimized=False):
        self.root = root
        self.root.title("Arduino Control")
        self.minimized = minimized

        self.arduino_reader = None
        self.connected_port = None

        # State: latest readings
        self.latest_pots = [0]*5
        self.latest_buttons = [0]*14

        # Mappings
        self.button_mappings = {i: ButtonMapping(i) for i in range(14)}
        self.pot_mappings = {i: PotMapping(i) for i in range(5)}

        # GUI elements
        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Not connected")

        self._setup_gui()
        self._setup_tray()

        # If we should start minimized
        if self.minimized:
            self.root.withdraw()

        # After GUI is ready, scan ports
        self.scan_ports()

    def _setup_gui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        # Top: port selection
        row = 0
        ttk.Label(frm, text="Serial Port:").grid(row=row, column=0, sticky=tk.W)
        self.port_combo = ttk.Combobox(frm, textvariable=self.port_var, width=30, state="readonly")
        self.port_combo.grid(row=row, column=1, sticky=tk.W)
        btn_connect = ttk.Button(frm, text="Connect", command=self.on_connect_btn)
        btn_connect.grid(row=row, column=2, sticky=tk.W)
        row += 1

        ttk.Label(frm, textvariable=self.status_var).grid(row=row, column=0, columnspan=3, sticky=tk.W)
        row += 1

        # Buttons area
        btnframe = ttk.LabelFrame(frm, text="Buttons")
        btnframe.grid(row=row, column=0, columnspan=3, sticky=tk.W+tk.E, pady=10)
        for i in range(14):
            b = ttk.Button(btnframe, text=f"B{i}", command=partial(self.configure_button, i))
            b.grid(row=i//7, column=i%7, padx=5, pady=3)
        row += 1

        # Pots area
        potframe = ttk.LabelFrame(frm, text="Potentiometers")
        potframe.grid(row=row, column=0, columnspan=3, sticky=tk.W+tk.E)
        self.pot_labels = {}
        for i in range(5):
            ttk.Label(potframe, text=f"Poti {i}:").grid(row=i, column=0, sticky=tk.W)
            lab = ttk.Label(potframe, text="0")
            lab.grid(row=i, column=1, sticky=tk.W)
            self.pot_labels[i] = lab
            btn = ttk.Button(potframe, text="Configure", command=partial(self.configure_pot, i))
            btn.grid(row=i, column=2, padx=5, sticky=tk.W)

    def _setup_tray(self):
        # Create an icon image (dummy)
        icon_img = Image.new("RGB", (64, 64), color=(0, 0, 0))
        d = ImageDraw.Draw(icon_img)
        d.rectangle((0,0,63,63), fill=(20,20,20))
        d.text((10, 20), "A", fill=(200,200,200))

        menu = (
            pystray.MenuItem("Show", self.show_window),
            pystray.MenuItem("Exit", self.on_quit)
        )
        self.tray_icon = pystray.Icon("arduino_control", icon_img, "Arduino Ctrl", menu)

        # Run icon in background thread
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self):
        self.root.after(0, self.root.deiconify)

    def on_quit(self, *args):
        if messagebox.askokcancel("Quit", "Exit application?"):
            # Stop serial reader
            if self.arduino_reader:
                self.arduino_reader.stop()
            self.tray_icon.stop()
            self.root.quit()

    def scan_ports(self):
        ports = list_serial_ports()
        valid = []
        for p in ports:
            if test_arduino_port(p):
                valid.append(p)
        self.port_combo['values'] = valid
        if valid:
            self.port_var.set(valid[0])
        else:
            self.port_var.set("")

    def on_connect_btn(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "No port selected")
            return
        # Stop existing
        if self.arduino_reader:
            self.arduino_reader.stop()
        # Start new reader
        self.arduino_reader = ArduinoReader(port, self.on_serial_data)
        self.arduino_reader.start()
        self.connected_port = port
        self.status_var.set(f"Connected to {port}")

    def on_serial_data(self, line):
        # Called in background thread when a line arrives
        # Parse something like:
        #   POTS: 512, 345, 789, 123, 999 | BUTTONS: 0, 1, 0, 1, ...
        try:
            if not line.startswith("POTS:"):
                return
            part1, part2 = line.split("|", 1)
            # parse pots
            pots_str = part1.split(":", 1)[1].strip()
            pots_vals = [int(x.strip()) for x in pots_str.split(",")]
            # parse buttons
            btn_str = part2.split(":", 1)[1].strip()
            btn_vals = [int(x.strip()) for x in btn_str.split(",")]

            # Update state
            for i in range(min(5, len(pots_vals))):
                self.latest_pots[i] = pots_vals[i]
            for i in range(min(14, len(btn_vals))):
                self.latest_buttons[i] = btn_vals[i]

            # Schedule GUI update
            self.root.after(0, self.refresh_display)
            # Could also trigger mapped actions
            self.handle_mappings()
        except Exception as e:
            # ignore parse errors
            # print("Parse error:", line, e)
            pass

    def refresh_display(self):
        for i, lab in self.pot_labels.items():
            lab.config(text=str(self.latest_pots[i]))
        # Optionally also show button states somewhere

    def handle_mappings(self):
        # Called when new data arrives
        # For buttons: detect transitions (0->1) and fire mapped actions
        # For simplicity, we skip edge detection here, just if button pressed
        for i in range(14):
            if self.latest_buttons[i] == 1:
                mapping = self.button_mappings[i]
                if mapping.command:
                    # Run shell command
                    threading.Thread(target=lambda cmd=mapping.command: sys.stdout.write("\n")).start()
                    # Actually: os.system, subprocess, etc.
                for kp in mapping.keypresses:
                    # Simulate keypress (platform‑specific). You’d use e.g. pynput or keyboard module
                    pass

        # For pots: you might map to volume control
        # (This part is platform‑dependent — e.g. on Windows use `pycaw` or on Linux `pactl` / `amixer`)
        for i in range(5):
            pm = self.pot_mappings[i]
            if pm.mapping_type is not None:
                val = self.latest_pots[i]
                # Normalize within user min..max to 0.0–1.0 range
                norm = (val - pm.min_val) / (pm.max_val - pm.min_val) if pm.max_val > pm.min_val else 0.0
                norm = max(0.0, min(1.0, norm))
                # Then apply to master / mic / app
                # You need to implement platform‐specific control
                pass

    def configure_button(self, btn_idx):
        cfg = self.button_mappings[btn_idx]
        d = tk.Toplevel(self.root)
        d.title(f"Config Button {btn_idx}")
        d.grab_set()

        # Keypresses
        ttk.Label(d, text="Keypresses (comma separated):").pack(padx=10, pady=5)
        ent = ttk.Entry(d, width=40)
        ent.pack(padx=10)
        ent.insert(0, ",".join(cfg.keypresses))

        # Command
        ttk.Label(d, text="Shell command to run:").pack(padx=10, pady=5)
        ent2 = ttk.Entry(d, width=40)
        ent2.pack(padx=10)
        if cfg.command:
            ent2.insert(0, cfg.command)

        def on_ok():
            kp = [x.strip() for x in ent.get().split(",") if x.strip()]
            cfg.keypresses = kp
            cmd = ent2.get().strip()
            cfg.command = cmd if cmd else None
            d.destroy()

        ttk.Button(d, text="OK", command=on_ok).pack(pady=10)

    def configure_pot(self, pot_idx):
        pm = self.pot_mappings[pot_idx]
        d = tk.Toplevel(self.root)
        d.title(f"Config Pot {pot_idx}")
        d.grab_set()

        # Mapping type
        ttk.Label(d, text="Map to:").pack(padx=10, pady=5)
        mapvar = tk.StringVar(value=pm.mapping_type or "")
        cb = ttk.Combobox(d, textvariable=mapvar, values=["", "master", "mic", "app"], state="readonly")
        cb.pack(padx=10)

        # App name
        ttk.Label(d, text="App name (if mapping to app):").pack(padx=10, pady=5)
        ent_app = ttk.Entry(d, width=30)
        ent_app.pack(padx=10)
        if pm.app_name:
            ent_app.insert(0, pm.app_name)

        # Min/Max values
        ttk.Label(d, text="Min value:").pack(padx=10, pady=2)
        ent_min = ttk.Entry(d, width=10)
        ent_min.pack(padx=10)
        ent_min.insert(0, str(pm.min_val))
        ttk.Label(d, text="Max value:").pack(padx=10, pady=2)
        ent_max = ttk.Entry(d, width=10)
        ent_max.pack(padx=10)
        ent_max.insert(0, str(pm.max_val))

        def on_ok():
            pm.mapping_type = mapvar.get() or None
            pm.app_name = ent_app.get().strip() if pm.mapping_type == "app" else None
            try:
                pm.min_val = int(ent_min.get().strip())
                pm.max_val = int(ent_max.get().strip())
            except:
                pass
            d.destroy()

        ttk.Button(d, text="OK", command=on_ok).pack(pady=10)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)
        self.root.mainloop()


def main():
    minimized = False
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--minimized", "-m"):
            minimized = True

    root = tk.Tk()
    app = MainApp(root, minimized=minimized)
    app.run()

if __name__ == "__main__":
    main()
