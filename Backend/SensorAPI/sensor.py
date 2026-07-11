import os
from supabase import create_client, Client
from dotenv import load_dotenv
from flask import Flask, jsonify
from flask_cors import CORS 
from datetime import datetime


load_dotenv()

app = Flask(__name__)
CORS(app) 

# Initialize Supabase
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_PUBLISHABLE_KEY")
)

@app.route('/api/current_status', methods=['GET'])
def get_current_status():
    try:
        response = supabase.table('latest_sensor_readings').select('*').execute()
        return jsonify(format_sensor_data(response.data)), 200 
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/temp', methods=['GET'])
def get_temp():
    try:
        response = (supabase.table('SensorData')
                    .select('*')
                    .eq("sensor_type", "temp")
                    .order("created_at", desc=True)
                    .limit(30).execute())
        return jsonify(format_historic_data(response.data)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ph', methods=['GET'])
def get_ph():
    try:
        response = (supabase.table('SensorData').select('*').eq("sensor_type", "pH").order("created_at", desc=True).limit(30).execute())
        return jsonify(format_historic_data(response.data)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tds', methods=['GET'])
def get_tds():
    try:
        response = (supabase.table('SensorData').select('*').eq("sensor_type", "TDS").order("created_at", desc=True).limit(30).execute())
        return jsonify(format_historic_data(response.data)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/lux', methods=['GET'])
def get_lux():
    try:
        response = (supabase.table('SensorData').select('*').eq("sensor_type", "LUX").order("created_at", desc=True).limit(30).execute())
        return jsonify(format_historic_data(response.data)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def format_sensor_data(sensordata):
    formatted_sensors = {}
    for sensor in sensordata:
        s_name = sensor.get("sensor_type")
        s_value = sensor.get("data1")
        formatted_sensors[s_name] = s_value
    return formatted_sensors

def format_historic_data(raw_sensordata_list):
    chart_data = []
    for sensor in raw_sensordata_list:
        data_point = {
            "time": sensor.get("created_at"),
            "value": sensor.get("data1")
        }
        # "Casting" it into a Python datetime object
        dt_object = datetime.fromisoformat(data_point["time"])
        date=dt_object.date()
        if date == datetime.now().date():
            chart_data.append(data_point)
    return chart_data

if __name__ == '__main__':
    # host='0.0.0.0' makes the server accessible across your local Wi-Fi network
    app.run(host='0.0.0.0', port=5000, debug=True)