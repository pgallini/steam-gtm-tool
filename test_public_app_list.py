import requests

url = "https://api.steampowered.com/ISteamApps/GetAppList/v0002/"

response = requests.get(
    url,
    params={"format": "json"},
    timeout=30,
)

if not response.ok:
    print("Status:", response.status_code)
    print("URL:", response.url)
    print("Response:", response.text[:1000])
    response.raise_for_status()

data = response.json()
apps = data["applist"]["apps"]

print(f"Fetched {len(apps)} apps")
print("Sample:")
for app in apps[:10]:
    print(app)
