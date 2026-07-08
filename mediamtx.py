import subprocess
import time
import paho.mqtt.client as mqtt

# Configuration
MEDIAMTX_URL = "rtsp://localhost:8554/staircase_loop"
TAPO_URL = "rtsp://admin:password@192.168.86.21:554/stream1"
BLANK_IMAGE = "/config/www/black_frame.jpg" # A small static black image file

current_process = None

def play_pseudo_stream():
    global current_process
    kill_current_stream()
    # Continuous low-overhead loop of a static black image
    cmd = f"ffmpeg -re -loop 1 -i {BLANK_IMAGE} -c:v libx264 -pix_fmt yuv420p -f rtsp {MEDIAMTX_URL}"
    current_process = subprocess.Popen(cmd, shell=True)

def play_real_stream():
    global current_process
    kill_current_stream()
    # Wait 3 seconds for Tapo hardware to connect to Wi-Fi after Privacy Mode drops
    time.sleep(3) 
    # Pipe the raw Tapo stream straight through to MediaMTX with zero re-encoding (copy)
    cmd = f"ffmpeg -rtsp_transport tcp -i {TAPO_URL} -c copy -f rtsp {MEDIAMTX_URL}"
    current_process = subprocess.Popen(cmd, shell=True)

def kill_current_stream():
    global current_process
    if current_process:
        current_process.terminate()
        current_process.wait()

def on_message(client, userdata, message):
    payload = message.payload.decode("utf-8")
    if payload == "ON":
        play_real_stream()
    elif payload == "OFF":
        play_pseudo_stream()

# MQTT Setup listening to your staircase state topic
client = mqtt.Client()
client.on_message = on_message
client.connect("192.168.86.X", 1883)
client.subscribe("staircase/camera_injection/set")
play_pseudo_stream() # Start with the pseudo-stream on boot
client.loop_forever()

