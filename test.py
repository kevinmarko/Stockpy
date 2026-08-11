import requests
from bs4 import BeautifulSoup

url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
resp = requests.get(url, headers=headers, timeout=15)
soup = BeautifulSoup(resp.text, 'html.parser')
for a in soup.find_all('a'):
    if a.text and ('historical' in a.text.lower() or 'changes' in a.text.lower() or 'component' in a.text.lower()):
        print(f"{a.text}: {a.get('href', '')}")
