import requests
import zipfile
import io
import pandas as pd

def test_ff():
    url = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip"
    print("Fetching Fama-French factors...")
    try:
        r = requests.get(url, timeout=10)
        print("Status code:", r.status_code)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        print("Files in zip:", z.namelist())
        with z.open(z.namelist()[0]) as f:
            lines = [line.decode('utf-8', errors='ignore') for line in f.readlines()[:10]]
            for i, l in enumerate(lines):
                print(f"{i}: {repr(l)}")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test_ff()
