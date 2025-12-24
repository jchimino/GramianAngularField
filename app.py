from flask import Flask, render_template, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import numpy as np
from collections import deque
import threading
import time
import json
from datetime import datetime
from coinbase.websocket import WSClient
import base64
import os

# Your Coinbase API credentials
API_KEY = "organizations/4770a22f-d60a-4216-92b2-8a228f8cfe8b/apiKeys/a2cff221-b06e-498f-8c6f-dd77f8a3b0e3"
API_SECRET = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEICzoIl8T+X4E3NvpA6KGdYqt47Ol8arOxRbXoEJj+DFSoAoGCCqGSM49\nAwEHoUQDQgAE7y9gaoj4p2T/SFb7QgVpba+GNkZgfJDFycuiro5np4Wgn4Rb0pKS\nX6r3m4ojJAev4T6SZTV8C0SyaQ3vynBbmA==\n-----END EC PRIVATE KEY-----\n"

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

class BTCDataManager:
    def __init__(self, window_size=60, update_interval=0.5):
        self.window_size = window_size
        self.update_interval = update_interval
        self.price_data = deque(maxlen=window_size)
        self.volume_data = deque(maxlen=window_size)  # ADD THIS LINE
        self.timestamps = deque(maxlen=window_size)
        self.gasf_matrix = None
        self.gadf_matrix = None
        self.lock = threading.Lock()
        self.is_streaming = False
        self.update_thread = None
        self.base_price = None
        self.current_price = None
        self.ws_client = None
        self.ws_connected = False
        self.initialize_historical_data()
        self.start_websocket()

    def on_message(self, msg):
        try:
            data = json.loads(msg)
            if data.get("channel") == "ticker" and data.get("events"):
                for event in data["events"]:
                    if "tickers" in event:
                        for ticker in event["tickers"]:
                            if ticker.get("product_id") == "BTC-USD":
                                latest_price = float(ticker.get("price", 0))
                                latest_volume = float(ticker.get("volume_24h", 0))  # ADD THIS
                            
                            if latest_price > 0:
                                with self.lock:
                                    self.current_price = latest_price
                                    self.price_data.append(latest_price)
                                    self.volume_data.append(latest_volume)  # ADD THIS
                                    self.timestamps.append(datetime.now().isoformat())
                                    if self.base_price is None:
                                        self.base_price = latest_price
                                print(f"[SUCCESS] BTC Price: ${latest_price:.2f}, Volume: {latest_volume:.2f}")
        except Exception as e:
            print(f"[ERROR] Processing WebSocket message: {e}")

    def on_open(self):
        print("WebSocket connection opened")
        self.ws_connected = True
        
    def on_close(self):
        print("WebSocket connection closed")
        self.ws_connected = False
            
    def start_websocket(self):
        try:
            if self.ws_client and self.ws_connected:
                print("[INFO] WebSocket is already connected.")
                return
            
            print("[INFO] Starting WebSocket connection...")
            self.ws_client = WSClient(
                api_key=API_KEY, 
                api_secret=API_SECRET, 
                on_message=self.on_message,
                on_open=self.on_open,
                on_close=self.on_close,
                verbose=True
            )
            self.ws_client_thread = threading.Thread(target=self.ws_client.open)
            self.ws_client_thread.daemon = True
            self.ws_client_thread.start()
            
            time.sleep(3)
            self.ws_client.subscribe(product_ids=["BTC-USD"], channels=["ticker"])
            time.sleep(2)
            if not self.ws_connected:
                print("[WARNING] WebSocket may not be fully connected, using fallback data")
        except Exception as e:
            print(f"[ERROR] Starting WebSocket client: {e}")
            self.ws_connected = False
            
    def initialize_historical_data(self):
        with self.lock:
            self.base_price = 95000 + np.random.uniform(-2000, 2000)
            self.current_price = self.base_price
            base_volume = 50000000000  # Typical BTC 24h volume
            for _ in range(30):
                change = np.random.randn() * 100
                self.current_price += change
                volume = base_volume * (1 + np.random.uniform(-0.1, 0.1))  # ADD THIS
                self.price_data.append(self.current_price)
                self.volume_data.append(volume)  # ADD THIS
                self.timestamps.append(datetime.now().isoformat())
        print(f"Initialized with {len(self.price_data)} data points. Base price: ${self.base_price:.2f}")

    def normalize_ts(self, ts):
        ts = np.array(ts)
        ts_min, ts_max = np.min(ts), np.max(ts)
        if ts_max == ts_min:
            return np.zeros_like(ts)
        return 2 * (ts - ts_min) / (ts_max - ts_min) - 1
    
    def ts_to_gasf(self, ts):
        normalized_ts = self.normalize_ts(ts)
        phi = np.arccos(np.clip(normalized_ts, -1, 1))
        return np.cos(phi[:, None] + phi[None, :])
    
    def ts_to_gadf(self, ts):
        normalized_ts = self.normalize_ts(ts)
        phi = np.arccos(np.clip(normalized_ts, -1, 1))
        return np.sin(phi[:, None] - phi[None, :])

    def compute_matrices(self):
        with self.lock:
            if len(self.price_data) >= self.window_size:
                price_array = np.array(list(self.price_data))
                self.gasf_matrix = self.ts_to_gasf(price_array)
                self.gadf_matrix = self.ts_to_gadf(price_array)
            else:
                self.gasf_matrix = None
                self.gadf_matrix = None
    
    def streaming_loop(self):
        while self.is_streaming:
            self.compute_matrices()
            state = self.get_current_state()
            socketio.emit('state_update', state) 
            time.sleep(self.update_interval)
            
    def start_streaming(self):
        if not self.is_streaming:
            self.is_streaming = True
            self.update_thread = threading.Thread(target=self.streaming_loop)
            self.update_thread.daemon = True
            self.update_thread.start()
            return True
        return False
    
    def stop_streaming(self):
        self.is_streaming = False
        if self.update_thread:
            self.update_thread.join(timeout=2)
        return True
    
    def reset_data(self):
        self.stop_streaming()
        with self.lock:
            self.price_data.clear()
            self.volume_data.clear()  # ADD THIS
            self.timestamps.clear()
            self.gasf_matrix = None
            self.gadf_matrix = None
            self.base_price = None
            self.current_price = None
        self.initialize_historical_data()
        self.start_streaming()
        return True

    def update_window_size(self, new_size):
        if 30 <= new_size <= 120:
            with self.lock:
                self.window_size = new_size
                old_prices = list(self.price_data)
                old_timestamps = list(self.timestamps)
                self.price_data = deque(old_prices[-new_size:], maxlen=new_size)
                self.timestamps = deque(old_timestamps[-new_size:], maxlen=new_size)
            return True
        return False
        
    def update_update_interval(self, new_interval):
        if 0.1 <= new_interval <= 2:
            self.update_interval = new_interval / 1000.0  # Convert ms to seconds
            return True
        return False

    def get_current_state(self):
        with self.lock:
            state = {
                'prices': list(self.price_data),
                'volumes': list(self.volume_data),  # ADD THIS
                'timestamps': list(self.timestamps),
                'current_price': self.current_price,
                'base_price': self.base_price,
                'window_size': self.window_size,
                'data_points': len(self.price_data),
                'is_streaming': self.is_streaming,
                'ws_connected': self.ws_connected,
                'price_change': 0
        }
            if self.base_price and self.current_price:
                state['price_change'] = ((self.current_price - self.base_price) / self.base_price) * 100
            if self.gasf_matrix is not None:
                state['gasf'] = self.gasf_matrix.tolist()
            if self.gadf_matrix is not None:
                state['gadf'] = self.gadf_matrix.tolist()
            return state

data_manager = BTCDataManager()

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

# --- WebSocket Handlers ---
@socketio.on('connect')
def handle_connect():
    print('Client connected to WebSocket!')
    state = data_manager.get_current_state()
    emit('state_update', state)

@socketio.on('start_stream')
def handle_start_stream():
    data_manager.start_streaming()

@socketio.on('stop_stream')
def handle_stop_stream():
    data_manager.stop_streaming()

@socketio.on('reset_data')
def handle_reset_data():
    data_manager.reset_data()
    state = data_manager.get_current_state()
    emit('state_update', state)

@socketio.on('update_window')
def handle_update_window(data):
    size = data.get('size')
    if size:
        data_manager.update_window_size(size)

@socketio.on('update_speed')
def handle_update_speed(data):
    speed = data.get('speed')
    if speed:
        data_manager.update_update_interval(speed)

if __name__ == '__main__':
    print("\n" + "="*60)
    print("Bitcoin Gramian Angular Field Visualizer")
    print("Using Coinbase WebSocket for Real-Time Data")
    print("="*60)
    print("Starting Flask server...")
    print("Open http://localhost:5000 in your browser")
    print("="*60 + "\n")
    data_manager.start_streaming()
    socketio.run(app, debug=True, port=5000, host='0.0.0.0', use_reloader=False)
