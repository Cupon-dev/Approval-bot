#!/usr/bin/env python3
"""
Check if all required dependencies are installed
"""
import importlib

required_packages = [
    'telegram',
    'python_dotenv',
    'urllib3'
]

print("🔍 Checking dependencies...")
all_ok = True

for package in required_packages:
    try:
        importlib.import_module(package)
        print(f"✅ {package}")
    except ImportError:
        print(f"❌ {package}")
        all_ok = False

if all_ok:
    print("\n🎉 All dependencies are installed correctly!")
else:
    print("\n❌ Some dependencies are missing.")
    print("Run: pip install -r requirements.txt")
