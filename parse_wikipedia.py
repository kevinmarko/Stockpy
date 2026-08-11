import requests
import pandas as pd
import io
url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
resp = requests.get(url, headers=headers, timeout=15)
tables = pd.read_html(io.StringIO(resp.text))
print(f"Total tables: {len(tables)}")
for i, t in enumerate(tables):
    print(f"Table {i} cols: {list(t.columns)}")
