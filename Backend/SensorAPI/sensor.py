import os
from flask import Flask
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_PUBLISHABLE_KEY")
)

@app.route('/')
def index():
    response = supabase.table('SensorData').select("*").execute()
    sensordata = response.data

    html = '<h1>Sensor Data</h1><ul>'
    for data in sensordata:
        html += f'<li>{data["data1"]}</li>'
    html += '</ul>'

    return html

if __name__ == '__main__':
    app.run(debug=True)