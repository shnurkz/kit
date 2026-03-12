import sys
import subprocess

def main():
    print("=== Python Environment Check ===")
    print(f"Python Executable: {sys.executable}")
    print(f"Python Version:\n{sys.version}\n")
    
    print("=== Installed Packages ===")
    try:
        subprocess.run([sys.executable, "-m", "pip", "list"], check=True)
    except Exception as e:
        print(f"Failed to list packages: {e}")

if __name__ == "__main__":
    main()
