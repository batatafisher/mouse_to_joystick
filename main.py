import argparse
import signal
import sys

from core import Config, MouseToJoystick


def run_headless():
    cfg = Config.load()
    engine = MouseToJoystick(cfg)
    print(f"Starting on {cfg.device_path or '(auto-detected mouse)'} ...")
    engine.start()
    print("Running. Press Ctrl+C to stop.")

    def _stop(*_):
        print("\nStopping...")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    signal.pause()


def run_gui():
    from gui import App
    App().mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mouse to virtual joystick")
    parser.add_argument("--headless", action="store_true", help="run without GUI, using saved config")
    args = parser.parse_args()

    if args.headless:
        run_headless()
    else:
        run_gui()
