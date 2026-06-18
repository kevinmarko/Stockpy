import sys
import os

def diagnose_conflict():
    print("--- DIAGNOSTIC START ---")
    print(f"Current Working Directory: {os.getcwd()}")
    
    # 1. Check for the specific file 'random.py' in the current folder
    local_random = os.path.join(os.getcwd(), "random.py")
    if os.path.exists(local_random):
        print("\n[CRITICAL FOUND]: A file named 'random.py' exists in your folder.")
        print(f"Location: {local_random}")
        print("SOLUTION: Rename this file to something else (e.g., 'my_random.py').")
        return

    # 2. Attempt to import random and see where it comes from
    try:
        import random
        print(f"\nImported 'random' module from: {getattr(random, '__file__', 'Unknown')}")
        
        # Check if the imported random lacks 'randbits' (which causes your error)
        if not hasattr(random, 'randbits'):
            print("\n[PROBLEM IDENTIFIED]: The imported 'random' module is missing 'randbits'.")
            print("This confirms you are loading a fake/local 'random' file instead of the real Python library.")
        else:
            print("The 'random' module seems correct. The issue might be a corrupted NumPy installation.")

    except Exception as e:
        print(f"\nError during import check: {e}")

    # 3. Check for lingering compiled files
    pycache_path = os.path.join(os.getcwd(), "__pycache__")
    if os.path.exists(pycache_path):
        print(f"\n[NOTE]: A '__pycache__' directory exists at {pycache_path}.")
        print("If you renamed a file recently, you must delete this folder to clear the memory.")
    
    print("\n--- DIAGNOSTIC END ---")

if __name__ == "__main__":
    diagnose_conflict()