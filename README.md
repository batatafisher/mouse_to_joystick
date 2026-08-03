# mouse2joystick

Turns a real mouse into a virtual analog gamepad on Linux — with a proper
spring-back center, a configurable deadzone-start offset, smoothing, an
optional exponential response curve, and mouse-button-to-gamepad-button
mapping via a clickable controller diagram. No input-remapper in the
pipeline: it talks to your mouse and to `/dev/uinput` directly, so there's
only one transform stage between "you move the mouse" and "the game sees a
stick tilt."

## Why this exists

A real analog stick has two properties a mouse doesn't:

1. It reports **absolute position**, not relative motion.
2. It **springs back to center** the instant you let go.

This tool fakes both: it accumulates mouse deltas into an internal position
in the range `-1..1` per axis, decays that position toward `0` over time
(the "spring-back speed"), and low-pass filters the raw deltas first so
mouse jitter doesn't make the stick twitch. On top of that:

- **Deadzone-start offset** — any nonzero movement immediately reads as at
  least X% tilt, instead of ramping up from 0%.
- **Exponential response curve** (optional) — reshapes the spring position
  so slow/small movements stay gentle and precise, while a fast flick ramps
  to full deflection quicker than linear.
- **Button mapping** — mouse clicks/side-buttons can be mapped to any of the
  12 buttons the virtual gamepad exposes (A/B/X/Y, bumpers, triggers, stick
  clicks, Start/Select), passed straight through with no extra processing.

## Install

```bash
sudo apt install python3-evdev python3-tk   # Debian/Ubuntu
# or: pip install evdev --break-system-packages   (tk is stdlib, usually preinstalled)
```

## Permissions (one-time setup)

You need read access to `/dev/input/event*` and write access to `/dev/uinput`.

```bash
# Let your user read input devices
sudo usermod -aG input $USER

# Let your user (via the input group) write to uinput
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Log out and back in (group membership only applies to new sessions)
```

If `/dev/uinput` doesn't exist at all, load the kernel module once (and add
it to `/etc/modules-load.d/` if you want it to persist across reboots):

```bash
sudo modprobe uinput
```

## Run

```bash
python3 main.py            # GUI
python3 main.py --headless # no GUI, uses last-saved settings from
                            # ~/.config/mouse2joystick/config.json
```

## GUI overview

### Motion & Axes tab

- **Mouse device** — auto-detected dropdown (filtered to exclude virtual /
  joystick-named devices). Hit **Rescan** if you plug in a different mouse.
- **Deadzone start (%)** — minimum tilt reported the instant you move at all.
- **Sensitivity** — how much stick position moves per pixel of mouse motion.
- **Smoothing** — low-pass filter strength on raw mouse deltas.
- **Spring-back speed** — how fast the stick returns to center once you stop.
- **Grab mouse exclusively** — when on, the tool exclusively grabs the real
  mouse so it stops also moving your desktop cursor while gaming. Turn it
  off if you want to tune settings while still using the mouse normally.
- **Invert X / Invert Y** — flip either axis.
- **Exponential response curve** — toggle + strength slider (1.0–4.0). Off
  by default (pure linear response, identical to the original behavior).
  Higher strength = gentler near center, snappier at full deflection.

All sliders/toggles apply live — no restart needed to feel a change.

### Button Mapping tab

A clickable gamepad diagram:

- **Left-click** any button on the diagram to map it — a dialog opens
  listening for the next mouse button press, which becomes that mapping.
- **Right-click** a button to clear its mapping.
- **Hover** over a button to see its current mapping in the status line at
  the bottom.
- **Clear All Mappings** wipes every button mapping at once.

Covers all 12 buttons the virtual gamepad advertises: A/B/X/Y, L/R
(bumpers), ZL/ZR (triggers), L3/R3 (stick clicks), Start/Select. The D-pad
shown is decorative only — directional input already comes from the stick
axes, not digital buttons.

**Note:** button mapping listens on the real mouse device directly, so if
"Grab mouse exclusively" is on, stop the engine first before mapping a new
button (the dialog will warn you if you forget).

### Bottom bar

- **Start / Stop** — toggles the engine.
- **Save settings** — persists current values to
  `~/.config/mouse2joystick/config.json` for use with `--headless` later
  (e.g. a systemd user service or an autostart entry).

## Tuning guide

| Setting | What it does | Symptom if wrong |
|---|---|---|
| Deadzone start | Minimum tilt % reported the instant you move at all | Too low: stick feels dead near center. Too high: tiny nudges snap to a big tilt. |
| Sensitivity | Stick position moved per pixel of mouse motion | Too low: have to move the mouse a mile to reach full tilt. Too high: any twitch maxes it out. |
| Smoothing | Low-pass filter strength on raw motion | Too low: stick is jittery/noisy. Too high: stick feels laggy/rubbery. |
| Spring-back speed | How fast the stick returns to center once you stop | Too low: stick drifts and stays off-center. Too high: snaps back so hard it feels twitchy. |
| Exponential curve | Reshapes response by movement speed | Too weak: barely different from linear. Too strong: feels dead until you flick hard. |

## Troubleshooting

**Window doesn't center on screen.** Many tiling window managers (i3, sway,
Hyprland, etc.) ignore an app's requested window position entirely and
place windows themselves — no in-app fix can override that. You'd need a
WM-side rule instead, e.g. for i3:

```
for_window [title="Mouse to Joystick"] floating enable, move position center
```

**"Could not grab device exclusively."** Something else already has an
exclusive grab on the mouse (often another instance of this tool, or
input-remapper still running against it — make sure that mapping is
disabled/removed since this tool replaces it entirely).

**No mice found in the dropdown.** The detector filters by capability
(relative motion + a left-click button) and excludes anything with
"virtual", "joystick", "gamepad", or "input-remapper" in its name. If your
mouse still isn't showing up, hit Rescan after reconnecting it, or check
`sudo libinput list-devices` to confirm the kernel sees it as a mouse.

**Game doesn't see the virtual gamepad.** Confirm `/dev/uinput` permissions
(see above), and check the device actually appears with
`ls /dev/input/by-id/` or `jstest` after hitting Start.

## Config file

Saved at `~/.config/mouse2joystick/config.json`. Delete it to reset to
defaults.

## Extending

- Only 12 buttons are mapped currently; the axes (`ABS_X`/`ABS_Y`) are
  fixed to the spring-back model. Adding a second virtual stick or D-pad
  buttons would mean extending the `EV_ABS`/`EV_KEY` capabilities in
  `core.py`'s `_create_virtual_gamepad()` and wiring new input.
- The virtual device advertises a unique name/vendor/product ID
  (`Mouse2Joystick Virtual Pad`), so SDL/games won't dedupe or ignore it
  against your real mouse.
