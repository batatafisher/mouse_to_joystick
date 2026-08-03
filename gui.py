import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

from core import Config, MouseToJoystick, find_candidate_mice
from evdev import InputDevice, ecodes


# Layout of clickable regions on the gamepad diagram: pad_code (must match
# the BTN_* capabilities the virtual gamepad advertises in core.py),
# a short glyph drawn on the shape, the shape kind, and its canvas bbox.
BUTTON_LAYOUT = [
    {"code": "BTN_TL2",    "glyph": "ZL", "shape": "rect", "coords": (85, 12, 155, 36),   "name": "ZL (Left Trigger)",  "color": "#54606c", "hover": "#6d7a88"},
    {"code": "BTN_TR2",    "glyph": "ZR", "shape": "rect", "coords": (405, 12, 475, 36),  "name": "ZR (Right Trigger)", "color": "#54606c", "hover": "#6d7a88"},
    {"code": "BTN_TL",     "glyph": "L",  "shape": "rect", "coords": (85, 40, 155, 64),   "name": "L (Left Bumper)",    "color": "#54606c", "hover": "#6d7a88"},
    {"code": "BTN_TR",     "glyph": "R",  "shape": "rect", "coords": (405, 40, 475, 64),  "name": "R (Right Bumper)",   "color": "#54606c", "hover": "#6d7a88"},
    {"code": "BTN_SELECT", "glyph": "-",  "shape": "rect", "coords": (243, 138, 283, 158), "name": "Select",            "color": "#495159", "hover": "#5c6670"},
    {"code": "BTN_START",  "glyph": "+",  "shape": "rect", "coords": (288, 138, 328, 158), "name": "Start",             "color": "#495159", "hover": "#5c6670"},
    {"code": "BTN_THUMBL", "glyph": "L3", "shape": "oval", "coords": (118, 88, 172, 142),  "name": "L3 (Left Stick Click)",  "color": "#495159", "hover": "#5c6670"},
    {"code": "BTN_THUMBR", "glyph": "R3", "shape": "oval", "coords": (368, 168, 422, 222), "name": "R3 (Right Stick Click)", "color": "#495159", "hover": "#5c6670"},
    {"code": "BTN_Y",      "glyph": "Y",  "shape": "oval", "coords": (412, 66, 448, 102),  "name": "Y Button", "color": "#c9a038", "hover": "#e0b64a"},
    {"code": "BTN_X",      "glyph": "X",  "shape": "oval", "coords": (384, 94, 420, 130),  "name": "X Button", "color": "#3a6fc4", "hover": "#4f8ae0"},
    {"code": "BTN_B",      "glyph": "B",  "shape": "oval", "coords": (440, 94, 476, 130),  "name": "B Button", "color": "#c9453f", "hover": "#e05a53"},
    {"code": "BTN_A",      "glyph": "A",  "shape": "oval", "coords": (412, 122, 448, 158), "name": "A Button", "color": "#3f9b4e", "hover": "#54b664"},
]


class App(tk.Tk):
    _DEFAULT_HOVER_TEXT = "Left-click a button on the pad to map it to a mouse button. Right-click to clear it."

    def __init__(self):
        super().__init__()
        self.title("Mouse to Joystick")
        self.resizable(False, False)

        self.config_obj = Config.load()
        self.engine: Optional[MouseToJoystick] = None
        self._device_paths = []
        self._mapping_active = False

        self._build_ui()
        self.after(10, self._center_window)

    def _center_window(self):
        """Best-effort centering. Note: many tiling window managers (i3,
        sway, Hyprland, etc.) ignore explicit position requests entirely and
        place windows themselves — if that's your setup, this will have no
        visible effect and you'd need a WM-side floating/center rule for
        this window instead."""
        self.update_idletasks()
        try:
            self.eval(f"tk::PlaceWindow {self._w} center")
        except tk.TclError:
            w, h = self.winfo_width(), self.winfo_height()
            x = (self.winfo_screenwidth() - w) // 2
            y = (self.winfo_screenheight() - h) // 2
            self.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # Main Notebook for tabs
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        frm_axes = ttk.Frame(notebook, padding=12)
        frm_buttons = ttk.Frame(notebook, padding=12)

        notebook.add(frm_axes, text="Motion & Axes")
        notebook.add(frm_buttons, text="Button Mapping")

        # ---- Tab 1: Motion & Axes ----
        ttk.Label(frm_axes, text="Mouse device").grid(row=0, column=0, sticky="w", **pad)
        self.device_combo = ttk.Combobox(frm_axes, width=40, state="readonly")
        self.device_combo.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Button(frm_axes, text="Rescan", command=self._rescan_devices).grid(row=0, column=3, **pad)
        self._rescan_devices()

        self.deadzone_var = self._add_slider(frm_axes, 1, "Deadzone start (%)", 0, 100, self.config_obj.deadzone_offset * 100)
        self.sensitivity_var = self._add_slider(frm_axes, 2, "Sensitivity", 1, 100, self.config_obj.sensitivity * 1000)
        self.smoothing_var = self._add_slider(frm_axes, 3, "Smoothing", 1, 100, self.config_obj.smoothing_alpha * 100)
        self.decay_var = self._add_slider(frm_axes, 4, "Spring-back speed", 1, 100, self.config_obj.decay_rate * 10)

        self.grab_var = tk.BooleanVar(value=self.config_obj.grab_enabled)
        ttk.Checkbutton(frm_axes, text="Grab mouse exclusively", variable=self.grab_var,
                        command=self._on_grab_toggle).grid(row=5, column=0, columnspan=4, sticky="w", **pad)

        self.invert_x_var = tk.BooleanVar(value=self.config_obj.invert_x)
        self.invert_y_var = tk.BooleanVar(value=self.config_obj.invert_y)
        ttk.Checkbutton(frm_axes, text="Invert X", variable=self.invert_x_var, command=self._push_live_config).grid(row=6, column=0, sticky="w", **pad)
        ttk.Checkbutton(frm_axes, text="Invert Y", variable=self.invert_y_var, command=self._push_live_config).grid(row=6, column=1, sticky="w", **pad)

        ttk.Separator(frm_axes, orient="horizontal").grid(row=7, column=0, columnspan=4, sticky="ew", padx=10, pady=(10, 4))

        # NEW: exponential response curve toggle + strength slider
        self.curve_enabled_var = tk.BooleanVar(value=self.config_obj.curve_enabled)
        ttk.Checkbutton(
            frm_axes, text="Exponential response curve (slow moves stay gentle, fast moves ramp to max)",
            variable=self.curve_enabled_var, command=self._on_curve_toggle
        ).grid(row=8, column=0, columnspan=4, sticky="w", **pad)

        self.curve_exponent_var = self._add_slider(
            frm_axes, 9, "Curve strength", 100, 400, self.config_obj.curve_exponent * 100,
            widget_attr="_curve_scale", fmt=lambda v: f"{v / 100:.1f}"
        )
        self._set_curve_slider_state()

        # ---- Tab 2: Button Mapping (visual gamepad diagram) ----
        self.hover_info_var = tk.StringVar(value=self._DEFAULT_HOVER_TEXT)
        ttk.Label(frm_buttons, textvariable=self.hover_info_var, foreground="#444").pack(
            side="bottom", fill="x", pady=(8, 0))
        ttk.Button(frm_buttons, text="Clear All Mappings", command=self._clear_all_mappings).pack(
            side="bottom", anchor="e", pady=(4, 0))

        self._build_gamepad_canvas(frm_buttons)

        # ---- Bottom Controls (Always Visible) ----
        frm_controls = ttk.Frame(self, padding=12)
        frm_controls.pack(fill="x", side="bottom")

        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(frm_controls, textvariable=self.status_var, foreground="#666").pack(side="left", padx=10)

        ttk.Button(frm_controls, text="Save settings", command=self._save).pack(side="right", padx=10)
        self.toggle_btn = ttk.Button(frm_controls, text="Start", command=self._on_toggle)
        self.toggle_btn.pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _add_slider(self, parent, row, label, lo, hi, initial, widget_attr=None, fmt=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=6)
        var = tk.DoubleVar(value=initial)
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", command=lambda _: self._push_live_config())
        scale.grid(row=row, column=1, columnspan=2, sticky="ew", padx=10, pady=6)
        val_lbl = ttk.Label(parent, width=5)
        val_lbl.grid(row=row, column=3, padx=10)

        fmt = fmt or (lambda v: f"{v:.0f}")

        def refresh(*_):
            val_lbl.config(text=fmt(var.get()))
        var.trace_add("write", refresh)
        refresh()

        if widget_attr:
            setattr(self, widget_attr, scale)
        return var

    # ---- response curve toggle -----------------------------------------------

    def _on_curve_toggle(self):
        self._set_curve_slider_state()
        self._push_live_config()

    def _set_curve_slider_state(self):
        """Grey out the strength slider when the curve is switched off, since
        it has no effect in that state (keeps the plain linear behavior)."""
        enabled = self.curve_enabled_var.get()
        self._curve_scale.state(["!disabled"] if enabled else ["disabled"])

    # ---- gamepad diagram ------------------------------------------------------

    def _build_gamepad_canvas(self, parent):
        BODY_FILL, BODY_OUTLINE = "#2e333b", "#16181c"
        SHADOW_FILL = "#000000"
        BTN_OUTLINE = "#181b1f"
        GLYPH_COLOR = "#f2f2f2"

        # Match the canvas background to the actual ttk theme instead of a
        # hardcoded light grey, so it doesn't look like a stray box pasted
        # onto a dark theme.
        style = ttk.Style(self)
        theme_bg = style.lookup("TFrame", "background") or self.cget("background")

        canvas = tk.Canvas(parent, width=560, height=245, highlightthickness=0, bg=theme_bg)
        canvas.pack(pady=(4, 8))
        self._gamepad_canvas = canvas

        # soft drop shadow behind the body for a bit of depth
        self._rounded_rect(canvas, 24, 56, 544, 236, 45, fill=SHADOW_FILL, outline="", stipple="gray25")

        # controller body silhouette (decorative)
        self._rounded_rect(canvas, 20, 50, 540, 230, 45, fill=BODY_FILL, outline=BODY_OUTLINE, width=2)
        canvas.create_oval(0, 130, 100, 230, fill=BODY_FILL, outline=BODY_OUTLINE, width=2)
        canvas.create_oval(460, 130, 560, 230, fill=BODY_FILL, outline=BODY_OUTLINE, width=2)

        # decorative D-pad (not mappable — axis movement already covers direction)
        canvas.create_rectangle(126, 172, 166, 188, fill="#181b1f", outline="")
        canvas.create_rectangle(138, 160, 154, 200, fill="#181b1f", outline="")

        self._canvas_shape_tags = {}
        self._canvas_colors = {}
        for entry in BUTTON_LAYOUT:
            code, glyph, shape, coords, name = (
                entry["code"], entry["glyph"], entry["shape"], entry["coords"], entry["name"]
            )
            base_color, hover_color = entry["color"], entry["hover"]
            tag = f"btn_{code}"
            x1, y1, x2, y2 = coords
            if shape == "oval":
                canvas.create_oval(x1, y1, x2, y2, fill=base_color, outline=BTN_OUTLINE, width=2, tags=(tag,))
                # small highlight arc for a subtle 3D "cap" look
                canvas.create_arc(x1 + 4, y1 + 3, x2 - 4, (y1 + y2) / 2, start=20, extent=140,
                                   style="arc", outline="#ffffff", width=1, outlinestipple="gray25", tags=(tag,))
            else:
                self._rounded_rect(canvas, x1, y1, x2, y2, 6, fill=base_color, outline=BTN_OUTLINE, width=2, tags=(tag,))
            canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=glyph, fill=GLYPH_COLOR,
                                font=("TkDefaultFont", 9, "bold"), tags=(tag,))

            canvas.tag_bind(tag, "<Button-1>", lambda e, c=code: self._start_mapping(c))
            canvas.tag_bind(tag, "<Button-3>", lambda e, c=code: self._clear_mapping(c))
            canvas.tag_bind(tag, "<Enter>", lambda e, c=code, n=name, t=tag: self._on_button_hover(c, n, t))
            canvas.tag_bind(tag, "<Leave>", lambda e, t=tag: self._on_button_unhover(t))
            self._canvas_shape_tags[code] = tag
            self._canvas_colors[tag] = (base_color, hover_color)

    @staticmethod
    def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return canvas.create_polygon(points, smooth=True, **kwargs)

    def _on_button_hover(self, pad_code, pretty_name, tag):
        self._gamepad_canvas.config(cursor="hand2")
        _, hover_color = self._canvas_colors[tag]
        for item in self._gamepad_canvas.find_withtag(tag):
            if self._gamepad_canvas.type(item) in ("polygon", "oval"):
                self._gamepad_canvas.itemconfig(item, fill=hover_color)
        mapped = self.config_obj.button_map.get(pad_code, "Unmapped")
        self.hover_info_var.set(f"{pretty_name} -> {mapped}   (left-click to remap, right-click to clear)")

    def _on_button_unhover(self, tag):
        self._gamepad_canvas.config(cursor="")
        base_color, _ = self._canvas_colors[tag]
        for item in self._gamepad_canvas.find_withtag(tag):
            if self._gamepad_canvas.type(item) in ("polygon", "oval"):
                self._gamepad_canvas.itemconfig(item, fill=base_color)
        self.hover_info_var.set(self._DEFAULT_HOVER_TEXT)



    # ---- button mapping logic -----------------------------------------------

    def _start_mapping(self, pad_button):
        if self.engine and self.config_obj.grab_enabled:
            messagebox.showwarning("Engine Running", "Please stop the engine first! The mouse is exclusively grabbed.")
            return

        dev_path = self._current_config().device_path
        if not dev_path:
            messagebox.showerror("Error", "Please select a valid mouse device first.")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Mapping...")
        dlg.geometry("250x100")
        dlg.transient(self)
        ttk.Label(dlg, text=f"Press a mouse button for {pad_button}...").pack(expand=True)
        dlg.update_idletasks()
        dlg.wait_visibility()
        dlg.grab_set()

        self._mapping_active = True

        def listen_thread():
            try:
                dev = InputDevice(dev_path)
                for event in dev.read_loop():
                    if not self._mapping_active:
                        break
                    if event.type == ecodes.EV_KEY and event.value == 1:
                        mouse_btn = ecodes.bytype[ecodes.EV_KEY].get(event.code, f"BTN_{event.code}")
                        if isinstance(mouse_btn, list):
                            mouse_btn = mouse_btn[0]
                        self.after(0, lambda: _apply(mouse_btn))
                        break
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Read Error", str(e)))
                self.after(0, dlg.destroy)

        def _apply(btn_name):
            self._mapping_active = False
            dlg.destroy()
            if btn_name:
                self.config_obj.button_map[pad_button] = btn_name
                self.hover_info_var.set(f"Mapped {pad_button} -> {btn_name}")
                self._push_live_config()

        threading.Thread(target=listen_thread, daemon=True).start()

        def _cancel():
            self._mapping_active = False
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _cancel)

    def _clear_mapping(self, pad_button):
        self.config_obj.button_map.pop(pad_button, None)
        self.hover_info_var.set(f"Cleared {pad_button}")
        self._push_live_config()

    def _clear_all_mappings(self):
        self.config_obj.button_map.clear()
        self.hover_info_var.set(self._DEFAULT_HOVER_TEXT)
        self._push_live_config()

    # ---- device handling ----------------------------------------------------

    def _rescan_devices(self):
        candidates = find_candidate_mice()
        self._device_paths = [c[0] for c in candidates]
        labels = [f"{name}  ({path})" for path, name in candidates] or ["No mice found"]
        self.device_combo["values"] = labels
        if candidates:
            if self.config_obj.device_path in self._device_paths:
                idx = self._device_paths.index(self.config_obj.device_path)
            else:
                idx = 0
            self.device_combo.current(idx)

    # ---- config <-> UI sync --------------------------------------------------

    def _current_config(self) -> Config:
        idx = self.device_combo.current()
        device_path = self._device_paths[idx] if 0 <= idx < len(self._device_paths) else ""
        return Config(
            device_path=device_path,
            deadzone_offset=self.deadzone_var.get() / 100.0,
            sensitivity=self.sensitivity_var.get() / 1000.0,
            smoothing_alpha=self.smoothing_var.get() / 100.0,
            decay_rate=self.decay_var.get() / 10.0,
            grab_enabled=self.grab_var.get(),
            invert_x=self.invert_x_var.get(),
            invert_y=self.invert_y_var.get(),
            curve_enabled=self.curve_enabled_var.get(),
            curve_exponent=self.curve_exponent_var.get() / 100.0,
            button_map=self.config_obj.button_map.copy(),
        )

    def _push_live_config(self):
        self.config_obj = self._current_config()
        if self.engine:
            self.engine.config.deadzone_offset = self.config_obj.deadzone_offset
            self.engine.config.sensitivity = self.config_obj.sensitivity
            self.engine.config.smoothing_alpha = self.config_obj.smoothing_alpha
            self.engine.config.decay_rate = self.config_obj.decay_rate
            self.engine.config.invert_x = self.config_obj.invert_x
            self.engine.config.invert_y = self.config_obj.invert_y
            self.engine.config.curve_enabled = self.config_obj.curve_enabled
            self.engine.config.curve_exponent = self.config_obj.curve_exponent
            self.engine.config.button_map = self.config_obj.button_map
            self.engine.update_button_lut()

    def _on_grab_toggle(self):
        self.config_obj.grab_enabled = self.grab_var.get()
        if self.engine:
            self.engine.set_grab(self.grab_var.get())

    def _save(self):
        self._current_config().save()
        messagebox.showinfo("Saved", "Settings saved.")

    # ---- start/stop -----------------------------------------------------------

    def _on_toggle(self):
        if self.engine is None:
            self._start()
        else:
            self._stop()

    def _start(self):
        cfg = self._current_config()
        if not cfg.device_path:
            messagebox.showerror("No device", "No mouse device selected/found.")
            return
        self.engine = MouseToJoystick(cfg, on_error=self._show_error)
        try:
            self.engine.start()
        except Exception as e:
            messagebox.showerror("Failed to start", str(e))
            self.engine = None
            return
        self.status_var.set(f"Running on {cfg.device_path}")
        self.toggle_btn.config(text="Stop")

    def _stop(self):
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.status_var.set("Stopped")
        self.toggle_btn.config(text="Start")

    def _show_error(self, msg):
        self.after(0, lambda: (self.status_var.set(f"Error: {msg}"), self._stop()))

    def _on_close(self):
        self._stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
