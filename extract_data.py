import re, json

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Extract the JSON from the script tag
match = re.search(r'<script id="opportunities-data" type="application/json">(.*?)</script>', content, re.DOTALL)
if not match:
    print("ERROR: Could not find opportunities-data script tag")
    exit(1)

raw_json = match.group(1).strip()

# Validate and pretty-print it
data = json.loads(raw_json)
print(f"Found {len(data)} opportunities")

with open('opportunities.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("Written opportunities.json")

# Remove the script tag from index.html
new_content = re.sub(
    r'\n?<script id="opportunities-data" type="application/json">.*?</script>\n?',
    '\n',
    content,
    flags=re.DOTALL
)

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(new_content)
print("Removed inline data from index.html")
