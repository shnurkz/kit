import sys
import os

def main():
    # Handle PyInstaller frozen directory vs normal script path
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
        exe_dir = os.path.dirname(sys.executable)
        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        exe_dir = base_dir

    # Point Playwright to bundled ms-playwright folder if present
    bundled_browsers = os.path.join(exe_dir, "ms-playwright")
    if os.path.exists(bundled_browsers):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bundled_browsers

    script_path = os.path.join(base_dir, "main.py")

    import streamlit.web.cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        script_path,
        "--global.developmentMode=false",
        "--server.headless=false",
        "--browser.gatherUsageStats=false"
    ]
    sys.exit(stcli.main())

if __name__ == '__main__':
    main()
