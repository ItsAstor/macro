import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import simpledialog, ttk
from pynput import keyboard, mouse

# --- Auto-Hide Console Window ---
try:
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # 0 = SW_HIDE
except Exception:
    pass

# --- Force Windows DPI Awareness ---
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_DIR = os.path.join(SCRIPT_DIR, "macros")
os.makedirs(MACRO_DIR, exist_ok=True)

events = []
is_recording = False
is_playing = False
start_time = 0.0
last_move_time = 0.0

mouse_ctrl = mouse.Controller()
kb_ctrl = keyboard.Controller()
popup_queue = queue.Queue()
app_instance = None


def get_macro_path(name):
    clean_name = name.strip()
    if not clean_name.endswith(".json"):
        clean_name += ".json"
    return os.path.join(MACRO_DIR, clean_name)


# --- Serialization Helpers ---
def serialize_key(key):
    try:
        return {"type": "char", "val": key.char}
    except AttributeError:
        return {"type": "special", "val": str(key).split(".")[-1]}


def deserialize_key(data):
    if data["type"] == "char":
        return data["val"]
    return getattr(keyboard.Key, data["val"], None)


def serialize_button(button):
    return str(button).split(".")[-1]


def deserialize_button(name):
    return getattr(mouse.Button, name, mouse.Button.left)


# --- Mouse Trackers ---
def on_move(x, y):
    global last_move_time
    if is_recording:
        now = time.time()
        if now - last_move_time >= 0.008:
            events.append({"action": "mouse_move", "time": now - start_time, "x": int(x), "y": int(y)})
            last_move_time = now


def on_click(x, y, button, pressed):
    if is_recording:
        events.append({
            "action": "mouse_click",
            "time": time.time() - start_time,
            "x": int(x),
            "y": int(y),
            "button": serialize_button(button),
            "pressed": pressed
        })


def on_scroll(x, y, dx, dy):
    if is_recording:
        events.append({
            "action": "mouse_scroll",
            "time": time.time() - start_time,
            "x": int(x),
            "y": int(y),
            "dx": dx,
            "dy": dy
        })


# --- Core Macro Actions ---
def toggle_record():
    global is_recording, is_playing, start_time, events

    if is_playing:
        popup_queue.put("Cannot record while playback is active!")
        return

    if not is_recording:
        events = []
        is_recording = True
        start_time = time.time()
        selected = app_instance.selected_macro.get() if app_instance else "default"
        popup_queue.put(f"● RECORDING [{selected}]")
        if app_instance:
            app_instance.root.after(0, lambda: app_instance.status_lbl.config(text=f"Status: Recording ({selected})...", fg="#FF5555"))
    else:
        is_recording = False
        target_name = app_instance.selected_macro.get() if app_instance else "default"
        filepath = get_macro_path(target_name)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(events, f)
            popup_queue.put(f"■ SAVED: {target_name} ({len(events)} events)")
        except Exception as e:
            popup_queue.put(f"Save error: {e}")

        if app_instance:
            app_instance.root.after(0, app_instance.refresh_file_list)
            app_instance.root.after(0, lambda: app_instance.status_lbl.config(text="Status: Ready", fg="#50FA7B"))


def start_single_playback():
    _trigger_playback(loop=False)


def start_infinite_playback():
    _trigger_playback(loop=True)


def stop_playback_or_exit():
    global is_playing, is_recording
    if is_playing:
        is_playing = False
        popup_queue.put("Playback Cancelled")
        if app_instance:
            app_instance.root.after(0, lambda: app_instance.status_lbl.config(text="Status: Ready", fg="#50FA7B"))
    elif is_recording:
        toggle_record()
    else:
        shutdown_application()


def shutdown_application():
    global is_playing, is_recording
    is_playing = False
    is_recording = False
    if app_instance:
        try:
            app_instance.root.destroy()
        except Exception:
            pass
    os._exit(0)


def _trigger_playback(loop=False):
    global is_playing
    if is_recording:
        popup_queue.put("Cannot play while recording!")
        return

    if is_playing:
        is_playing = False
        popup_queue.put("Playback Stopped")
        if app_instance:
            app_instance.root.after(0, lambda: app_instance.status_lbl.config(text="Status: Ready", fg="#50FA7B"))
        return

    threading.Thread(target=_play_engine, args=(loop,), daemon=True).start()


def _play_engine(loop=False):
    global is_playing
    target_name = app_instance.selected_macro.get() if app_instance else "default"
    filepath = get_macro_path(target_name)

    if not os.path.exists(filepath):
        popup_queue.put(f"File not found: {target_name}")
        return

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            playback_data = json.load(f)
    except Exception as e:
        popup_queue.put("Failed to read JSON!")
        return

    if not playback_data:
        popup_queue.put("Recording file is empty!")
        return

    is_playing = True
    mode = "Looping" if loop else "Single Play"
    popup_queue.put(f"▶ PLAYING [{target_name}]: {mode}")
    if app_instance:
        app_instance.root.after(0, lambda: app_instance.status_lbl.config(text=f"Status: Playing ({mode})...", fg="#BD93F9"))

    while is_playing:
        play_start = time.time()

        for event in playback_data:
            if not is_playing:
                break

            target_delay = event["time"]
            current_delay = time.time() - play_start
            wait_time = target_delay - current_delay
            if wait_time > 0:
                time.sleep(wait_time)

            act = event["action"]
            if act == "mouse_move":
                mouse_ctrl.position = (event["x"], event["y"])
            elif act == "mouse_click":
                mouse_ctrl.position = (event["x"], event["y"])
                btn = deserialize_button(event["button"])
                if event["pressed"]:
                    mouse_ctrl.press(btn)
                else:
                    mouse_ctrl.release(btn)
            elif act == "mouse_scroll":
                mouse_ctrl.scroll(event["dx"], event["dy"])
            elif act == "key_press":
                k = deserialize_key(event["key"])
                if k:
                    kb_ctrl.press(k)
            elif act == "key_release":
                k = deserialize_key(event["key"])
                if k:
                    kb_ctrl.release(k)

        if not loop:
            break
        time.sleep(0.05)

    is_playing = False
    popup_queue.put("Playback Finished")
    if app_instance:
        app_instance.root.after(0, lambda: app_instance.status_lbl.config(text="Status: Ready", fg="#50FA7B"))


# --- Keystroke Filter ---
def on_key_press_raw(key):
    # Ignore hotkey triggers so they aren't logged into the recorded sequence
    if key in (keyboard.Key.f10, keyboard.Key.f9):
        return

    if is_recording:
        events.append({
            "action": "key_press",
            "time": time.time() - start_time,
            "key": serialize_key(key)
        })


def on_key_release_raw(key):
    if key in (keyboard.Key.f10, keyboard.Key.f9):
        return

    if is_recording:
        events.append({
            "action": "key_release",
            "time": time.time() - start_time,
            "key": serialize_key(key)
        })


# --- Main Dashboard & Toast System ---
class MacroApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Macro Manager")
        self.root.geometry("380x370")
        self.root.configure(bg="#21222C")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        self.root.protocol("WM_DELETE_WINDOW", shutdown_application)

        # Dropdown styling with pure black font
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "BlackText.TCombobox",
            foreground="#000000",
            fieldbackground="#E2E8F0",
            background="#CBD5E1",
            selectforeground="#000000",
            selectbackground="#E2E8F0",
            font=("Segoe UI", 10, "bold")
        )
        style.map(
            "BlackText.TCombobox",
            fieldbackground=[("readonly", "#E2E8F0")],
            foreground=[("readonly", "#000000")]
        )

        self.root.option_add("*TCombobox*Listbox*foreground", "#000000")
        self.root.option_add("*TCombobox*Listbox*background", "#E2E8F0")
        self.root.option_add("*TCombobox*Listbox*font", ("Segoe UI", 10, "bold"))

        # --- Dropdown / File Select ---
        select_frame = tk.Frame(self.root, bg="#21222C")
        select_frame.pack(fill="x", padx=20, pady=(15, 5))

        tk.Label(select_frame, text="Active Macro File:", font=("Segoe UI", 10, "bold"), fg="#F8F8F2", bg="#21222C").pack(anchor="w")

        self.selected_macro = tk.StringVar(value="default")
        self.file_dropdown = ttk.Combobox(
            select_frame,
            textvariable=self.selected_macro,
            style="BlackText.TCombobox",
            state="readonly"
        )
        self.file_dropdown.pack(fill="x", pady=(5, 5))

        btn_row = tk.Frame(select_frame, bg="#21222C")
        btn_row.pack(fill="x")

        tk.Button(btn_row, text="+ New Profile", command=self.create_new_profile, bg="#44475A", fg="#FFFFFF", relief="flat", font=("Segoe UI", 9)).pack(side="left", expand=True, fill="x", padx=(0, 2))
        tk.Button(btn_row, text="Refresh", command=self.refresh_file_list, bg="#44475A", fg="#FFFFFF", relief="flat", font=("Segoe UI", 9)).pack(side="right", expand=True, fill="x", padx=(2, 0))

        # --- Action Buttons ---
        act_frame = tk.Frame(self.root, bg="#21222C")
        act_frame.pack(fill="x", padx=20, pady=10)

        tk.Button(act_frame, text="Record / Stop  [F10]", command=toggle_record, bg="#FF5555", fg="#FFFFFF", font=("Segoe UI", 10, "bold"), relief="flat", pady=6).pack(fill="x", pady=3)
        tk.Button(act_frame, text="Play Once  [Ctrl + F9]", command=start_single_playback, bg="#50FA7B", fg="#282A36", font=("Segoe UI", 10, "bold"), relief="flat", pady=6).pack(fill="x", pady=3)
        tk.Button(act_frame, text="Infinite Loop  [Shift + F9]", command=start_infinite_playback, bg="#BD93F9", fg="#282A36", font=("Segoe UI", 10, "bold"), relief="flat", pady=6).pack(fill="x", pady=3)
        tk.Button(act_frame, text="Stop / Exit  [Esc]", command=stop_playback_or_exit, bg="#6272A4", fg="#FFFFFF", font=("Segoe UI", 9), relief="flat", pady=4).pack(fill="x", pady=3)

        # Status Label
        self.status_lbl = tk.Label(self.root, text="Status: Ready", font=("Segoe UI", 10), fg="#50FA7B", bg="#21222C")
        self.status_lbl.pack(pady=(5, 10))

        # Floating Toast Banner Setup
        self.toast = tk.Toplevel(self.root)
        self.toast.withdraw()
        self.toast.overrideredirect(True)
        self.toast.attributes("-topmost", True)
        try:
            self.toast.wm_attributes("-disabled", True)
        except Exception:
            pass

        self.toast_label = tk.Label(
            self.toast,
            text="",
            font=("Segoe UI", 11, "bold"),
            fg="#FFFFFF",
            bg="#1E1E2E",
            padx=18,
            pady=8,
            relief="solid",
            bd=1
        )
        self.toast_label.pack()
        self.toast_timer = None

        self.refresh_file_list()
        self.check_queue()

    def refresh_file_list(self):
        files = [f.replace(".json", "") for f in os.listdir(MACRO_DIR) if f.endswith(".json")]
        if not files:
            files = ["default"]
        self.file_dropdown["values"] = files
        if self.selected_macro.get() not in files:
            self.selected_macro.set(files[0])

    def create_new_profile(self):
        name = simpledialog.askstring("New Profile", "Enter macro name:", parent=self.root)
        if name and name.strip():
            clean = name.strip().replace(".json", "")
            path = get_macro_path(clean)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump([], f)
            self.refresh_file_list()
            self.selected_macro.set(clean)

    def check_queue(self):
        try:
            while not popup_queue.empty():
                msg = popup_queue.get_nowait()
                self.show_toast(msg)
        except Exception:
            pass
        self.root.after(50, self.check_queue)

    def show_toast(self, message):
        if self.toast_timer:
            self.root.after_cancel(self.toast_timer)

        self.toast_label.config(text=message)
        self.toast.update_idletasks()

        w = self.toast.winfo_reqwidth()
        h = self.toast.winfo_reqheight()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        x = (screen_w - w) // 2
        y = screen_h - h - 80

        self.toast.geometry(f"{w}x{h}+{x}+{y}")
        self.toast.deiconify()
        self.toast_timer = self.root.after(2000, self.toast.withdraw)


# ==============================================================================
#                      CONFIGURABLE KEYBINDS SECTION
# ==============================================================================
KEYBIND_MAPPINGS = {
    "<f10>": toggle_record,
    "<ctrl>+<f9>": start_single_playback,      # Play Once: Ctrl + F9
    "<shift>+<f9>": start_infinite_playback,   # Infinite Loop: Shift + F9
    "<esc>": stop_playback_or_exit
}

if __name__ == "__main__":
    app_instance = MacroApp()

    m_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
    k_raw_listener = keyboard.Listener(on_press=on_key_press_raw, on_release=on_key_release_raw)
    hotkey_listener = keyboard.GlobalHotKeys(KEYBIND_MAPPINGS)

    m_listener.daemon = True
    k_raw_listener.daemon = True
    hotkey_listener.daemon = True

    m_listener.start()
    k_raw_listener.start()
    hotkey_listener.start()

    popup_queue.put("Macro Manager Ready")
    app_instance.root.mainloop()