import json
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from evdev import InputDevice, UInput, ecodes, list_devices, AbsInfo

CONFIG_DIR = Path.home() / ".config" / "mouse2joystick"
CONFIG_PATH = CONFIG_DIR / "config.json"

AXIS_MIN = -32768
AXIS_MAX = 32767


@dataclass
class Config:
    device_path: str = ""          # "" = auto-detect on start
    deadzone_offset: float = 0.25  # stick "jumps" to this % tilt as soon as you move it
    sensitivity: float = 0.02      # stick position moved per pixel of mouse delta
    smoothing_alpha: float = 0.35  # low-pass filter on raw delta
    decay_rate: float = 4.0        # spring-back speed toward center
    update_hz: float = 250.0       # internal tick rate
    grab_enabled: bool = True      # exclusively grab the mouse
    invert_x: bool = False
    invert_y: bool = False
    curve_enabled: bool = False    # NEW: exponential response curve toggle
    curve_exponent: float = 2.0    # NEW: >1 = slow/small moves stay gentle, fast moves ramp to max quicker
    button_map: dict = field(default_factory=dict) # Maps Pad Buttons to Mouse Buttons

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                return cls(**{**asdict(cls()), **data})
            except Exception:
                pass
        return cls()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))


def find_candidate_mice():
    candidates = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities()
        rel = caps.get(ecodes.EV_REL, [])
        keys = caps.get(ecodes.EV_KEY, [])
        has_rel_motion = ecodes.REL_X in rel and ecodes.REL_Y in rel
        has_mouse_button = ecodes.BTN_LEFT in keys
        looks_virtual = any(
            tag in dev.name.lower()
            for tag in ("uinput", "anti-deadzone", "virtual", "joystick", "gamepad", "input-remapper")
        )
        if has_rel_motion and has_mouse_button and not looks_virtual:
            candidates.append((path, dev.name))
        dev.close()
    return candidates


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _step_toward_zero(value: float, step: float) -> float:
    if value > 0:
        return max(0.0, value - step)
    if value < 0:
        return min(0.0, value + step)
    return 0.0


def _apply_response_curve(pos: float, exponent: float) -> float:
    """
    Reshape the -1..1 spring position magnitude exponentially.

    exponent > 1: small/slow-building positions get suppressed further
    (finer control near center), while positions that build up quickly
    (fast mouse movement) ramp toward full deflection faster than linear.
    exponent == 1 is a no-op (identical to the old linear behavior).
    """
    if abs(pos) < 1e-9:
        return 0.0
    sign = 1.0 if pos > 0 else -1.0
    return sign * (abs(pos) ** exponent)


def _apply_deadzone_offset(pos: float, offset: float) -> float:
    if abs(pos) < 1e-6:
        return 0.0
    sign = 1.0 if pos > 0 else -1.0
    return sign * (offset + (1.0 - offset) * abs(pos))


class MouseToJoystick:
    def __init__(self, config: Config, on_error=None):
        self.config = config
        self.on_error = on_error or (lambda msg: print(f"[mouse2joystick] {msg}"))

        self._mouse: Optional[InputDevice] = None
        self._uinput: Optional[UInput] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._tick_thread: Optional[threading.Thread] = None
        self._running = threading.Event()

        self._lock = threading.Lock()
        self._raw_dx = 0.0
        self._raw_dy = 0.0
        self._smoothed_dx = 0.0
        self._smoothed_dy = 0.0
        self._pos_x = 0.0
        self._pos_y = 0.0

        self._button_lut = {}
        self.update_button_lut()

    def update_button_lut(self):
        """Builds a fast integer-to-integer lookup for mouse to gamepad clicks."""
        lut = {}
        for pad_str, mouse_str in self.config.button_map.items():
            if isinstance(pad_str, (list, tuple)):
                pad_str = pad_str[0]
            if isinstance(mouse_str, (list, tuple)):
                mouse_str = mouse_str[0]

            pad_code = getattr(ecodes, pad_str, None)
            mouse_code = getattr(ecodes, mouse_str, None)

            if pad_code is not None and mouse_code is not None:
                lut[mouse_code] = pad_code
        self._button_lut = lut

    def _open_mouse(self) -> InputDevice:
        path = self.config.device_path
        if not path:
            candidates = find_candidate_mice()
            if not candidates:
                raise RuntimeError("No real mouse device found.")
            path = candidates[0][0]
        dev = InputDevice(path)
        if self.config.grab_enabled:
            try:
                dev.grab()
            except OSError as e:
                self.on_error(f"Could not grab {path} exclusively ({e}); continuing ungrabbed.")
        return dev

    def _create_virtual_gamepad(self) -> UInput:
        abs_info = AbsInfo(value=0, min=AXIS_MIN, max=AXIS_MAX, fuzz=0, flat=0, resolution=0)
        capabilities = {
            ecodes.EV_ABS: [
                (ecodes.ABS_X, abs_info),
                (ecodes.ABS_Y, abs_info),
            ],
            ecodes.EV_KEY: [
                ecodes.BTN_A, ecodes.BTN_B, ecodes.BTN_X, ecodes.BTN_Y,
                ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_TL2, ecodes.BTN_TR2,
                ecodes.BTN_THUMBL, ecodes.BTN_THUMBR, ecodes.BTN_START, ecodes.BTN_SELECT
            ],
        }
        return UInput(capabilities, name="Mouse2Joystick Virtual Pad", vendor=0x1234, product=0x5678)

    def start(self):
        if self._running.is_set():
            return
        self._mouse = self._open_mouse()
        self._uinput = self._create_virtual_gamepad()
        self._running.set()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._reader_thread.start()
        self._tick_thread.start()

    def stop(self):
        self._running.clear()
        if self._reader_thread:
            self._reader_thread.join(timeout=1)
        if self._tick_thread:
            self._tick_thread.join(timeout=1)
        if self._mouse:
            try:
                if self.config.grab_enabled:
                    self._mouse.ungrab()
            except OSError:
                pass
            self._mouse.close()
        if self._uinput:
            self._uinput.close()
        self._mouse = None
        self._uinput = None

    def set_grab(self, enabled: bool):
        self.config.grab_enabled = enabled
        if not self._mouse:
            return
        try:
            if enabled:
                self._mouse.grab()
            else:
                self._mouse.ungrab()
        except OSError as e:
            self.on_error(f"Grab toggle failed: {e}")

    def _read_loop(self):
        assert self._mouse is not None
        try:
            for event in self._mouse.read_loop():
                if not self._running.is_set():
                    break
                if event.type == ecodes.EV_REL:
                    with self._lock:
                        if event.code == ecodes.REL_X:
                            self._raw_dx += event.value
                        elif event.code == ecodes.REL_Y:
                            self._raw_dy += event.value
                elif event.type == ecodes.EV_KEY:
                    pad_code = self._button_lut.get(event.code)
                    if pad_code is not None:
                        self._uinput.write(ecodes.EV_KEY, pad_code, event.value)
                        self._uinput.syn()
        except OSError:
            if self._running.is_set():
                self.on_error("Mouse device disconnected.")
                self._running.clear()

    def _tick_loop(self):
        dt = 1.0 / self.config.update_hz
        last_ax = last_ay = None
        while self._running.is_set():
            t0 = time.monotonic()
            cfg = self.config

            with self._lock:
                dx, dy = self._raw_dx, self._raw_dy
                self._raw_dx = 0.0
                self._raw_dy = 0.0

            a = cfg.smoothing_alpha
            self._smoothed_dx = a * dx + (1 - a) * self._smoothed_dx
            self._smoothed_dy = a * dy + (1 - a) * self._smoothed_dy

            self._pos_x = _clamp(self._pos_x + self._smoothed_dx * cfg.sensitivity, -1.0, 1.0)
            self._pos_y = _clamp(self._pos_y + self._smoothed_dy * cfg.sensitivity, -1.0, 1.0)

            decay = cfg.decay_rate * dt
            if dx == 0:
                self._pos_x = _step_toward_zero(self._pos_x, decay)
            if dy == 0:
                self._pos_y = _step_toward_zero(self._pos_y, decay)

            # NEW: optional exponential response curve, applied to the spring
            # position before the deadzone-offset remap. Off by default so
            # the original linear feel is unchanged unless you opt in.
            mag_x, mag_y = self._pos_x, self._pos_y
            if cfg.curve_enabled:
                mag_x = _apply_response_curve(mag_x, cfg.curve_exponent)
                mag_y = _apply_response_curve(mag_y, cfg.curve_exponent)

            ax = _apply_deadzone_offset(mag_x, cfg.deadzone_offset)
            ay = _apply_deadzone_offset(mag_y, cfg.deadzone_offset)
            if cfg.invert_x:
                ax = -ax
            if cfg.invert_y:
                ay = -ay

            out_x = int(ax * AXIS_MAX)
            out_y = int(ay * AXIS_MAX)

            if out_x != last_ax or out_y != last_ay:
                self._uinput.write(ecodes.EV_ABS, ecodes.ABS_X, out_x)
                self._uinput.write(ecodes.EV_ABS, ecodes.ABS_Y, out_y)
                self._uinput.syn()
                last_ax, last_ay = out_x, out_y

            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, dt - elapsed))
