
import requests
from flask import jsonify
api_url = "http://192.168.1.100:5000//api/weatherforecast"
response = requests.get(api_url)
if response.status_code == 200:
    print(response.json())
else:
    print(f"Failed to retrieve data")
