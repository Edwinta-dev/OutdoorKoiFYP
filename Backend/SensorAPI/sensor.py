import os
from supabase import create_client, Client
from dotenv import load_dotenv
from flask import Flask

load_dotenv()

app = Flask(__name__)

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_PUBLISHABLE_KEY")
)

# Notice the route usually starts with /api/ to distinguish it from web pages
@app.route('/api/current_status', methods=['GET'])
def get_current_status():
    # Fetch the latest readings for all sensors instantly
    response = supabase.table('latest_sensor_readings').select('*').execute()
    sensordata = response.data
    return format_sensor_data(sensordata)
@app.route('/api/temp', methods=['GET'])
def get_temp():
    # Fetch the latest readings for all sensors instantly
    response = (supabase.table('SensorData').select('*').eq("sensor_type", "temp").order("created_at", desc=True).limit(30).execute())
    sensordata = response.data
    return format_historic_data(sensordata)

@app.route('/api/ph', methods=['GET'])
def get_ph():
    # Fetch the latest readings for all sensors instantly
    response = (supabase.table('SensorData').select('*').eq("sensor_type", "pH").order("created_at", desc=True).limit(30).execute())
    sensordata = response.data
    return format_historic_data(sensordata)

@app.route('/api/tds', methods=['GET'])
def get_tds():
    # Fetch the latest readings for all sensors instantly
    response = (supabase.table('SensorData').select('*').eq("sensor_type", "TDS").order("created_at", desc=True).limit(30).execute())
    sensordata = response.data
    return format_historic_data(sensordata)

@app.route('/api/lux', methods=['GET'])
def get_lux():
    # Fetch the latest readings for all sensors instantly
    response = (supabase.table('SensorData').select('*').eq("sensor_type", "LUX").order("created_at", desc=True).limit(30).execute())
    sensordata = response.data
    return format_historic_data(sensordata)

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
        chart_data.append(data_point)
    return chart_data


if __name__ == '__main__':
    app.run(debug=True)