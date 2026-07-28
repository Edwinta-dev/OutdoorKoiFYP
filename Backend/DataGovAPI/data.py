import os
from supabase import create_client, Client
from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS 
from datetime import datetime
import requests

load_dotenv()

app = Flask(__name__)
CORS(app) 

# Initialize Supabase
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICEROLE_KEY")
)

NEA_API_KEY=os.environ.get("NEA_API_KEY")


@app.route('/api/weatherforecast', methods=['GET'])
def getweatherforecast(datatype):
    api_url = "https://api-open.data.gov.sg/v2/real-time/api/"
    api_url+=datatype
    response = requests.get(api_url, params={"x-api-key": NEA_API_KEY})
    if response.status_code == 200:
        return jsonify(response.json())
    print(f"Failed to retrieve data: {datatype}")
    return jsonify({"error": "Failed to fetch data"}), response.status_code


if __name__ == '__main__':
    # host='0.0.0.0' makes the server accessible across your local Wi-Fi network
    app.run(host='0.0.0.0', port=5000, debug=True)