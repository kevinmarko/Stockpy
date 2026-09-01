import re

with open('tests/test_config.py', 'r') as f:
    content = f.read()

content = content.replace('"sector", "shortName", "Market Cap",', '"sector", "shortName", "Market Cap",\n        "Google_Trends_LSTM_Forecast", "Google_Trends_ASVI",')
content = content.replace('assert len(unmapped) == 80', 'assert len(unmapped) == 82')
content = content.replace('== 115', '== 117')

with open('tests/test_config.py', 'w') as f:
    f.write(content)
