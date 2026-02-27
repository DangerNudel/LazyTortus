#!/usr/bin/env python3
"""
US Army ATC System - Debian 9.3 Compatible
Compatible with Python 3.5+
"""

import socket
import threading
import json
import time
import random
import math
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

# Global state
aircraft_list = []
received_aircraft = {}
aircraft_lock = threading.Lock()
center_latitude = 33.7490
center_longitude = -84.3880
message_count = 0
AIRCRAFT_TIMEOUT = 30

class Aircraft:
    """Simulated aircraft with circular flight pattern"""
    
    def __init__(self, icao, callsign, center_lat, center_lon, altitude, speed, ac_type='civilian'):
        self.icao = icao
        self.callsign = callsign
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.altitude = altitude
        self.speed_knots = speed
        self.ac_type = ac_type
        
        # Circular pattern parameters
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius = random.uniform(0.15, 0.4)
        self.angular_velocity = 0.0005
    
    def update(self, dt):
        """Update aircraft position"""
        self.angle += self.angular_velocity * dt
        self.angle = self.angle % (2 * math.pi)
    
    def get_position(self):
        """Get current lat/lon"""
        lat = self.center_lat + self.radius * math.sin(self.angle)
        lon = self.center_lon + self.radius * math.cos(self.angle)
        return lat, lon
    
    def get_heading(self):
        """Get current heading"""
        return (math.degrees(self.angle) + 90) % 360


class ADSBReceiver:
    """Receives ADS-B messages on TCP port"""
    
    def __init__(self, port=30001):
        self.port = port
        self.running = False
    
    def start(self):
        """Start receiver thread"""
        self.running = True
        thread = threading.Thread(target=self._receive_loop, daemon=True)
        thread.start()
    
    def _receive_loop(self):
        """Main receiver loop"""
        global message_count
        
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('0.0.0.0', self.port))
        server_socket.listen(5)
        
        print("[ADS-B] Receiver listening on port {}".format(self.port))
        
        while self.running:
            try:
                client_socket, address = server_socket.accept()
                print("[ADS-B] Connection from {}".format(address))
                thread = threading.Thread(target=self._handle_client, 
                                        args=(client_socket,), daemon=True)
                thread.start()
            except Exception as e:
                if self.running:
                    print("[ADS-B] Accept error: {}".format(e))
    
    def _handle_client(self, client_socket):
        """Handle connected client"""
        global message_count, received_aircraft
        
        buffer = ""
        
        try:
            while self.running:
                data = client_socket.recv(4096)
                if not data:
                    break
                
                try:
                    buffer += data.decode('ascii', errors='ignore')
                except:
                    continue
                
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    
                    if line.startswith('*') and line.endswith(';'):
                        msg_hex = line[1:-1]
                        
                        if len(msg_hex) == 28:
                            message_count += 1
                            self._process_message(msg_hex)
        
        except Exception as e:
            pass
        finally:
            try:
                client_socket.close()
            except:
                pass
    
    def _process_message(self, msg_hex):
        """Process ADS-B message"""
        global received_aircraft
        
        try:
            # Extract ICAO
            icao = msg_hex[2:8]
            
            # Get or create aircraft entry
            with aircraft_lock:
                if icao not in received_aircraft:
                    received_aircraft[icao] = {
                        'icao': icao,
                        'callsign': None,
                        'lat': None,
                        'lon': None,
                        'altitude': None,
                        'speed': None,
                        'heading': None,
                        'type': 'unknown',
                        'last_seen': time.time(),
                        'lat_even_cpr': None,
                        'lon_even_cpr': None,
                        'lat_odd_cpr': None,
                        'lon_odd_cpr': None
                    }
                
                ac = received_aircraft[icao]
                ac['last_seen'] = time.time()
            
            # Extract ME field
            me_hex = msg_hex[8:22]
            me_int = int(me_hex, 16)
            me_bin = format(me_int, '056b')
            
            # Type Code
            tc = int(me_bin[0:5], 2)
            
            # Callsign (TC 1-4)
            if 1 <= tc <= 4:
                callsign = self._decode_callsign(msg_hex)
                if callsign:
                    with aircraft_lock:
                        ac['callsign'] = callsign
            
            # Position (TC 9-18)
            elif 9 <= tc <= 18:
                lat, lon, alt = self._decode_position(msg_hex, ac)
                with aircraft_lock:
                    if lat is not None and lon is not None:
                        ac['lat'] = lat
                        ac['lon'] = lon
                    if alt is not None:
                        ac['altitude'] = alt
            
            # Velocity (TC 19)
            elif tc == 19:
                speed, heading = self._decode_velocity(msg_hex)
                with aircraft_lock:
                    if speed is not None:
                        ac['speed'] = speed
                    if heading is not None:
                        ac['heading'] = heading
        
        except Exception as e:
            pass
    
    def _decode_callsign(self, msg_hex):
        """Decode callsign from message"""
        try:
            charset = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"
            
            callsign = ""
            for i in range(8):
                idx = i * 6 + 40
                byte_idx = idx // 4
                bit_offset = idx % 4
                
                if byte_idx + 2 < len(msg_hex):
                    bits = int(msg_hex[byte_idx:byte_idx+2], 16)
                    bits = (bits >> (2 - bit_offset)) & 0x3F
                    callsign += charset[bits]
            
            return callsign.strip().replace('#', '')
        except:
            return None
    
    def _decode_position(self, msg_hex, ac):
        """Decode position from message"""
        try:
            me_hex = msg_hex[8:22]
            me_int = int(me_hex, 16)
            me_bin = format(me_int, '056b')
            
            # Altitude
            alt_bin = me_bin[8:20]
            alt_int = int(alt_bin, 2)
            q_bit = (alt_int >> 4) & 1
            
            if q_bit == 1:
                top_bits = (alt_int >> 5) & 0x7F
                bottom_bits = alt_int & 0x0F
                alt_code = (top_bits << 4) | bottom_bits
                altitude = alt_code * 25 - 1000
            else:
                altitude = None
            
            # CPR
            time_bit = int(me_bin[20])
            lat_cpr = int(me_bin[22:39], 2)
            lon_cpr = int(me_bin[39:56], 2)
            
            if time_bit == 0:
                ac['lat_even_cpr'] = lat_cpr
                ac['lon_even_cpr'] = lon_cpr
            else:
                ac['lat_odd_cpr'] = lat_cpr
                ac['lon_odd_cpr'] = lon_cpr
            
            # Decode if we have both frames
            if (ac['lat_even_cpr'] is not None and ac['lon_even_cpr'] is not None and
                ac['lat_odd_cpr'] is not None and ac['lon_odd_cpr'] is not None):
                
                lat_even_cpr = ac['lat_even_cpr']
                lon_even_cpr = ac['lon_even_cpr']
                lat_odd_cpr = ac['lat_odd_cpr']
                lon_odd_cpr = ac['lon_odd_cpr']
                
                # Decode latitude
                dlat_even = 360.0 / 60
                dlat_odd = 360.0 / 59
                j = int((59 * lat_even_cpr - 60 * lat_odd_cpr) / 131072.0 + 0.5)
                lat_even = dlat_even * ((j % 60) + lat_even_cpr / 131072.0)
                lat_odd = dlat_odd * ((j % 59) + lat_odd_cpr / 131072.0)
                
                if lat_even >= 270:
                    lat_even -= 360
                if lat_odd >= 270:
                    lat_odd -= 360
                
                lat = lat_even if time_bit == 0 else lat_odd
                
                # NL function
                nl = self._calculate_nl(lat)
                
                if time_bit == 0:
                    ni = max(nl, 1)
                else:
                    ni = max(nl - 1, 1)
                
                dlon = 360.0 / ni
                
                # Decode longitude
                m_raw = (lon_even_cpr * (nl - 1) - lon_odd_cpr * nl) / 131072.0
                m = int(math.floor(m_raw + 0.5))
                
                lon_raw = dlon * (m + (lon_even_cpr if time_bit == 0 else lon_odd_cpr) / 131072.0)
                
                lon = lon_raw
                while lon >= 180:
                    lon -= 360
                while lon < -180:
                    lon += 360
                
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon, altitude
            
            return None, None, altitude
        
        except Exception as e:
            return None, None, None
    
    def _calculate_nl(self, lat):
        """Calculate NL function for CPR"""
        if abs(lat) >= 87.0:
            return 1
        
        nz = 15.0
        a = 1.0 - math.cos(math.pi / (2.0 * nz))
        b = math.cos(math.pi * abs(lat) / 180.0) ** 2
        
        if 1.0 - a / b <= 0:
            return 1
        
        nl = 2.0 * math.pi / (math.acos(1.0 - a / b))
        return int(nl)
    
    def _decode_velocity(self, msg_hex):
        """Decode velocity from message"""
        try:
            me_hex = msg_hex[8:22]
            me_int = int(me_hex, 16)
            me_bin = format(me_int, '056b')
            
            tc = int(me_bin[0:5], 2)
            if tc != 19:
                return None, None
            
            ew_sign = int(me_bin[13])
            ew_vel_raw = int(me_bin[14:24], 2)
            ew_vel = ew_vel_raw - 1
            if ew_sign:
                ew_vel = -ew_vel
            
            ns_sign = int(me_bin[24])
            ns_vel_raw = int(me_bin[25:35], 2)
            ns_vel = ns_vel_raw - 1
            if ns_sign:
                ns_vel = -ns_vel
            
            speed = math.sqrt(ew_vel**2 + ns_vel**2)
            heading = math.degrees(math.atan2(ew_vel, ns_vel)) % 360
            
            return speed, heading
        
        except Exception as e:
            return None, None


class WebHandler(BaseHTTPRequestHandler):
    """HTTP handler for web interface"""
    
    def log_message(self, format_str, *args):
        """Log only errors, suppress normal requests"""
        if '404' in format_str or '500' in format_str or 'error' in format_str.lower():
            print("[HTTP] {}".format(format_str % args))
    
    def do_GET(self):
        """Handle GET requests"""
        # Debug output
        if '/data/' in self.path or '/aircraft' in self.path:
            print("[DEBUG] Request: {}".format(self.path))
        
        if self.path == '/data/aircraft.json':
            self.serve_aircraft_json()
        elif self.path == '/test':
            # Simple test endpoint
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            html = "<html><body><h1>Server OK</h1><p>Aircraft: {}</p><p>Received: {}</p></body></html>".format(
                len(aircraft_list), len(received_aircraft))
            self.wfile.write(html.encode('utf-8'))
        elif self.path == '/' or self.path == '/index.html':
            self.serve_map_page()
        elif self.path.startswith('/tiles/'):
            self.serve_map_tile()
        elif self.path == '/leaflet.css':
            self.serve_static_file('leaflet/leaflet.css', 'text/css')
        elif self.path == '/leaflet.js':
            self.serve_static_file('leaflet/leaflet.js', 'application/javascript')
        elif self.path.startswith('/images/'):
            image_file = self.path[1:]
            self.serve_static_file('leaflet/{}'.format(image_file), 'image/png')
        else:
            self.send_error(404)
    
    def serve_map_tile(self):
        """Serve map tiles from local offline_maps directory"""
        try:
            # Path format: /tiles/{z}/{x}/{y}.png
            parts = self.path.split('/')
            z = parts[2]
            x = parts[3]
            y = parts[4].replace('.png', '')
            
            tile_path = os.path.join('offline_maps', z, x, '{}.png'.format(y))
            
            if os.path.exists(tile_path):
                self.send_response(200)
                self.send_header('Content-type', 'image/png')
                self.send_header('Cache-Control', 'max-age=86400')
                self.end_headers()
                
                with open(tile_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                # Serve blank tile
                self.send_response(200)
                self.send_header('Content-type', 'image/png')
                self.end_headers()
                # 1x1 transparent PNG
                self.wfile.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
        except Exception as e:
            self.send_error(404)
    
    def serve_static_file(self, filepath, content_type):
        """Serve static files (CSS, JS, images)"""
        try:
            if os.path.exists(filepath):
                self.send_response(200)
                self.send_header('Content-type', content_type)
                self.send_header('Cache-Control', 'max-age=86400')
                self.end_headers()
                
                mode = 'rb' if 'image' in content_type else 'r'
                encoding = None if mode == 'rb' else 'utf-8'
                
                with open(filepath, mode, encoding=encoding) as f:
                    content = f.read()
                    if isinstance(content, str):
                        content = content.encode('utf-8')
                    self.wfile.write(content)
            else:
                self.send_error(404)
        except Exception as e:
            print("Error serving {}: {}".format(filepath, e))
            self.send_error(500)
    
    def serve_aircraft_json(self):
        """Serve aircraft data as JSON"""
        global aircraft_list, received_aircraft, message_count
        
        try:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            
            current_time = time.time()
            
            with aircraft_lock:
                aircraft_data = []
                
                # Add simulated aircraft
                for ac in aircraft_list:
                    lat, lon = ac.get_position()
                    aircraft_data.append({
                        "hex": ac.icao,
                        "flight": ac.callsign,
                        "lat": lat,
                        "lon": lon,
                        "altitude": ac.altitude,
                        "track": ac.get_heading(),
                        "speed": ac.speed_knots,
                        "type": ac.ac_type,
                        "messages": message_count,
                        "seen": 0,
                        "source": "simulated"
                    })
                
                # Remove stale aircraft
                stale = [icao for icao, data in received_aircraft.items() 
                        if current_time - data['last_seen'] > AIRCRAFT_TIMEOUT]
                for icao in stale:
                    del received_aircraft[icao]
                
                # Add received aircraft
                for icao, ac in received_aircraft.items():
                    if ac['lat'] is not None and ac['lon'] is not None:
                        aircraft_data.append({
                            "hex": ac['icao'],
                            "flight": ac['callsign'] or icao,
                            "lat": ac['lat'],
                            "lon": ac['lon'],
                            "altitude": ac['altitude'],
                            "track": ac['heading'],
                            "speed": ac['speed'],
                            "type": ac['type'],
                            "messages": 0,
                            "seen": int(current_time - ac['last_seen']),
                            "source": "adsb"
                        })
            
            response = {
                "now": time.time(),
                "messages": message_count,
                "aircraft": aircraft_data
            }
            
            self.wfile.write(json.dumps(response).encode('utf-8'))
        
        except Exception as e:
            print("Error in serve_aircraft_json: {}".format(e))
            import traceback
            traceback.print_exc()
            try:
                self.send_error(500)
            except:
                pass
    
    def serve_map_page(self):
        """Serve main HTML page"""
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        html = """<!DOCTYPE html>
<html>
<head>
    <title>US ARMY ATC - TACTICAL AIR CONTROL</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="/leaflet.css"/>
    <script src="/leaflet.js"></script>
    <script>
        // Check if Leaflet loaded
        if (typeof L === 'undefined') {
            console.error('Leaflet.js failed to load. Please ensure leaflet/ folder exists with leaflet.js and leaflet.css');
        }
    </script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            overflow: hidden;
        }
        
        #map { 
            position: absolute;
            top: 0;
            left: 0;
            right: 420px;
            bottom: 0;
            filter: saturate(0.8);
        }
        
        .sidebar {
            position: fixed;
            right: 0;
            top: 0;
            bottom: 0;
            width: 420px;
            background: linear-gradient(180deg, #2a2a2a 0%, #1a1a1a 100%);
            border-left: 3px solid #4a4a4a;
            box-shadow: -5px 0 15px rgba(0, 0, 0, 0.5);
            overflow-y: auto;
            z-index: 2000;
        }
        
        .header {
            background: linear-gradient(180deg, #3a3a3a 0%, #2a2a2a 100%);
            padding: 20px;
            text-align: center;
            border-bottom: 3px solid #4a4a4a;
        }
        
        .army-star {
            font-size: 32px;
            color: #8b7355;
            margin-bottom: 5px;
        }
        
        .header h1 {
            color: #d4d4d4;
            font-size: 18px;
            font-weight: 700;
            letter-spacing: 3px;
            margin-bottom: 5px;
        }
        
        .subtitle {
            color: #8b7355;
            font-size: 11px;
            letter-spacing: 2px;
        }
        
        .classification {
            background: #8b7355;
            color: #1a1a1a;
            padding: 8px;
            text-align: center;
            font-size: 9px;
            font-weight: bold;
            letter-spacing: 2px;
        }
        
        .status-bar {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 10px;
            padding: 15px;
            background: rgba(42, 42, 42, 0.5);
            border-bottom: 2px solid #4a4a4a;
        }
        
        .status-item {
            text-align: center;
        }
        
        .status-label {
            display: block;
            font-size: 9px;
            color: #888;
            margin-bottom: 5px;
            letter-spacing: 1px;
        }
        
        .status-value {
            display: block;
            font-size: 16px;
            color: #00ff00;
            font-weight: bold;
            text-shadow: 0 0 10px rgba(0, 255, 0, 0.5);
        }
        
        .aircraft-list {
            padding: 15px;
        }
        
        .aircraft-item {
            background: linear-gradient(90deg, rgba(42, 42, 42, 0.8) 0%, rgba(26, 26, 26, 0.6) 100%);
            border: 1px solid #4a4a4a;
            border-left: 3px solid #00ff00;
            padding: 12px;
            margin-bottom: 10px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .aircraft-item:hover {
            border-left-color: #8b7355;
            background: linear-gradient(90deg, rgba(52, 52, 52, 0.9) 0%, rgba(36, 36, 36, 0.8) 100%);
            transform: translateX(-3px);
        }
        
        .aircraft-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        
        .aircraft-callsign {
            font-size: 14px;
            font-weight: bold;
            color: #00ff00;
            text-shadow: 0 0 5px rgba(0, 255, 0, 0.3);
        }
        
        .aircraft-type {
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 2px;
            background: #8b7355;
            color: #1a1a1a;
            font-weight: bold;
            letter-spacing: 1px;
        }
        
        .aircraft-data {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            font-size: 11px;
        }
        
        .data-item {
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
        }
        
        .data-label {
            color: #888;
            font-weight: 400;
        }
        
        .data-value {
            color: #00ff00;
            font-weight: 700;
            text-shadow: 0 0 3px rgba(0, 255, 0, 0.3);
        }
        
        .grid-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 420px;
            bottom: 0;
            background: 
                repeating-linear-gradient(0deg, rgba(74,74,74,0.1) 0px, transparent 1px, transparent 40px),
                repeating-linear-gradient(90deg, rgba(74,74,74,0.1) 0px, transparent 1px, transparent 40px);
            pointer-events: none;
            z-index: 999;
        }
        
        ::-webkit-scrollbar {
            width: 10px;
        }
        
        ::-webkit-scrollbar-track {
            background: #1a1a1a;
        }
        
        ::-webkit-scrollbar-thumb {
            background: #4a4a4a;
            border-radius: 5px;
        }
        
        .tactical-notice {
            position: fixed;
            bottom: 10px;
            left: 10px;
            background: rgba(26, 26, 26, 0.9);
            border: 2px solid #8b7355;
            padding: 10px 15px;
            font-size: 10px;
            color: #8b7355;
            letter-spacing: 1px;
            z-index: 1000;
            border-radius: 3px;
        }
    </style>
</head>
<body>
    <div class="grid-overlay"></div>
    <div class="tactical-notice">
        TACTICAL AIR CONTROL SYSTEM v2.5 | DEBIAN 9.3 COMPATIBLE
    </div>
    <div id="map"></div>
    
    <div class="sidebar">
        <div class="classification">
            UNCLASSIFIED // FOR TRAINING USE ONLY
        </div>
        
        <div class="header">
            <div class="army-star">★</div>
            <h1>US ARMY ATC</h1>
            <div class="subtitle">TACTICAL AIR CONTROL</div>
        </div>
        
        <div class="status-bar">
            <div class="status-item">
                <span class="status-label">AIRCRAFT</span>
                <span class="status-value" id="aircraft-count">0</span>
            </div>
            <div class="status-item">
                <span class="status-label">SYSTEM</span>
                <span class="status-value" id="status">ACTIVE</span>
            </div>
            <div class="status-item">
                <span class="status-label">ZULU TIME</span>
                <span class="status-value" id="time">00:00:00</span>
            </div>
        </div>
        
        <div class="aircraft-list" id="aircraft-list"></div>
    </div>
    
    <script>
        var map = null;
        var aircraftMarkers = {};
        var mapInitialized = false;
        var centerLat = 33.7490;
        var centerLon = -84.3880;
        
        // Check if Leaflet is available
        if (typeof L === 'undefined') {
            document.getElementById('map').innerHTML = 
                '<div style="display: flex; align-items: center; justify-content: center; height: 100%; background: #1a1a1a; color: #ff6b6b; padding: 20px; text-align: center; font-family: monospace;">' +
                '<div>' +
                '<h2 style="color: #ff6b6b; margin-bottom: 20px;">⚠️ LEAFLET NOT FOUND</h2>' +
                '<p style="margin-bottom: 10px;">Leaflet.js library is not available.</p>' +
                '<p style="margin-bottom: 20px;">To use offline maps:</p>' +
                '<ol style="text-align: left; display: inline-block; margin-bottom: 20px;">' +
                '<li style="margin-bottom: 10px;">Download Leaflet:<br><code>mkdir -p leaflet && cd leaflet<br>curl -L https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o leaflet.css<br>curl -L https://unpkg.com/leaflet@1.9.4/dist/leaflet.js -o leaflet.js</code></li>' +
                '<li style="margin-bottom: 10px;">Download map tiles:<br><code>python3 download_map_tiles.py</code></li>' +
                '<li>Restart the ATC system</li>' +
                '</ol>' +
                '<p style="color: #888; font-size: 12px;">See OFFLINE_MAPS_SETUP.md for complete instructions</p>' +
                '</div>' +
                '</div>';
        }
        
        // Initialize map with default center
        function initMap() {
            if (map || typeof L === 'undefined') return;
            
            try {
                map = L.map('map').setView([centerLat, centerLon], 11);
                
                L.tileLayer('/tiles/{z}/{x}/{y}.png', {
                    attribution: 'OpenStreetMap (Offline)',
                    maxZoom: 18,
                    errorTileUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
                }).addTo(map);
                
                mapInitialized = true;
            } catch (e) {
                console.error('Error initializing map:', e);
                document.getElementById('map').innerHTML = 
                    '<div style="display: flex; align-items: center; justify-content: center; height: 100%; background: #1a1a1a; color: #ff6b6b; padding: 20px; text-align: center;">' +
                    '<div><h2>Map Initialization Error</h2><p>' + e.message + '</p></div></div>';
            }
        }
        
        var planeIcon = L.divIcon({
            html: '<div style="width: 24px; height: 24px; position: relative;"><svg width="24" height="24" viewBox="0 0 24 24"><path fill="#00ff00" stroke="#003300" stroke-width="1" d="M12 2 L12 10 L4 18 L8 18 L12 14 L16 18 L20 18 L12 10 Z"/></svg></div>',
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            className: ''
        });
        
        function updateAircraft() {
            if (!map) {
                console.log('Map not initialized yet');
                return;
            }
            
            fetch('/data/aircraft.json')
                .then(function(r) { 
                    console.log('Fetch response status:', r.status);
                    return r.json(); 
                })
                .then(function(data) {
                    console.log('Received data:', data);
                    console.log('Aircraft count:', data.aircraft.length);
                    
                    document.getElementById('aircraft-count').textContent = data.aircraft.length;
                    
                    var currentMarkers = {};
                    
                    // Update center if we have aircraft and haven't centered yet
                    if (data.aircraft.length > 0 && data.aircraft[0].lat && data.aircraft[0].lon) {
                        var firstAc = data.aircraft[0];
                        console.log('First aircraft:', firstAc);
                        if (Math.abs(centerLat - 33.7490) < 0.0001 && Math.abs(centerLon - -84.3880) < 0.0001) {
                            centerLat = firstAc.lat;
                            centerLon = firstAc.lon;
                            map.setView([centerLat, centerLon], 11);
                            console.log('Centered map on:', centerLat, centerLon);
                        }
                    }
                    
                    data.aircraft.forEach(function(ac) {
                        if (ac.lat && ac.lon) {
                            console.log('Processing aircraft:', ac.hex, ac.flight, ac.lat, ac.lon);
                            
                            if (aircraftMarkers[ac.hex]) {
                                aircraftMarkers[ac.hex].setLatLng([ac.lat, ac.lon]);
                                
                                var iconDiv = aircraftMarkers[ac.hex].getElement();
                                if (iconDiv && ac.track) {
                                    var svg = iconDiv.querySelector('svg');
                                    if (svg) {
                                        svg.style.transform = 'rotate(' + ac.track + 'deg)';
                                    }
                                }
                            } else {
                                console.log('Creating new marker for:', ac.hex);
                                var marker = L.marker([ac.lat, ac.lon], {
                                    icon: planeIcon,
                                    title: ac.flight || ac.hex
                                }).addTo(map);
                                
                                marker.bindPopup(
                                    '<b>' + (ac.flight || ac.hex) + '</b><br>' +
                                    'Alt: ' + (ac.altitude ? Math.round(ac.altitude) + ' ft' : 'N/A') + '<br>' +
                                    'Speed: ' + (ac.speed ? Math.round(ac.speed) + ' kts' : 'N/A') + '<br>' +
                                    'Track: ' + (ac.track ? Math.round(ac.track) + '°' : 'N/A')
                                );
                                
                                aircraftMarkers[ac.hex] = marker;
                                
                                if (ac.track) {
                                    var iconDiv = marker.getElement();
                                    if (iconDiv) {
                                        var svg = iconDiv.querySelector('svg');
                                        if (svg) {
                                            svg.style.transform = 'rotate(' + ac.track + 'deg)';
                                        }
                                    }
                                }
                            }
                            
                            currentMarkers[ac.hex] = true;
                        }
                    });
                    
                    Object.keys(aircraftMarkers).forEach(function(hex) {
                        if (!currentMarkers[hex]) {
                            map.removeLayer(aircraftMarkers[hex]);
                            delete aircraftMarkers[hex];
                        }
                    });
                    
                    var list = document.getElementById('aircraft-list');
                    list.innerHTML = '';
                    
                    data.aircraft.forEach(function(ac) {
                        var div = document.createElement('div');
                        div.className = 'aircraft-item';
                        
                        var typeLabel = ac.source === 'adsb' ? 'RCV' : 'SIM';
                        
                        div.innerHTML = 
                            '<div class="aircraft-header">' +
                                '<span class="aircraft-callsign">' + (ac.flight || ac.hex) + '</span>' +
                                '<span class="aircraft-type">' + typeLabel + '</span>' +
                            '</div>' +
                            '<div class="aircraft-data">' +
                                '<div class="data-item">' +
                                    '<span class="data-label">LAT</span>' +
                                    '<span class="data-value">' + (ac.lat ? ac.lat.toFixed(4) : 'N/A') + '</span>' +
                                '</div>' +
                                '<div class="data-item">' +
                                    '<span class="data-label">LON</span>' +
                                    '<span class="data-value">' + (ac.lon ? ac.lon.toFixed(4) : 'N/A') + '</span>' +
                                '</div>' +
                                '<div class="data-item">' +
                                    '<span class="data-label">ALT</span>' +
                                    '<span class="data-value">' + (ac.altitude ? Math.round(ac.altitude).toLocaleString() + ' ft' : 'N/A') + '</span>' +
                                '</div>' +
                                '<div class="data-item">' +
                                    '<span class="data-label">HDG</span>' +
                                    '<span class="data-value">' + (ac.track ? Math.round(ac.track) + '°' : 'N/A') + '</span>' +
                                '</div>' +
                                '<div class="data-item">' +
                                    '<span class="data-label">SPD</span>' +
                                    '<span class="data-value">' + (ac.speed ? Math.round(ac.speed) + ' kts' : 'N/A') + '</span>' +
                                '</div>' +
                                '<div class="data-item">' +
                                    '<span class="data-label">ICAO</span>' +
                                    '<span class="data-value">' + ac.hex + '</span>' +
                                '</div>' +
                            '</div>';
                        
                        list.appendChild(div);
                    });
                })
                .catch(function(err) {
                    console.error('Error fetching aircraft:', err);
                });
        }
        
        function updateTime() {
            var now = new Date();
            var hours = String(now.getUTCHours()).padStart(2, '0');
            var minutes = String(now.getUTCMinutes()).padStart(2, '0');
            var seconds = String(now.getUTCSeconds()).padStart(2, '0');
            document.getElementById('time').textContent = hours + ':' + minutes + ':' + seconds;
        }
        
        // Initialize map first
        initMap();
        
        // Then start updates
        updateAircraft();
        setInterval(updateAircraft, 1000);
        setInterval(updateTime, 1000);
        updateTime();
    </script>
</body>
</html>
"""
        
        self.wfile.write(html.encode('utf-8'))


def update_aircraft_loop():
    """Update simulated aircraft positions"""
    global aircraft_list
    
    last_time = time.time()
    
    while True:
        current_time = time.time()
        dt = current_time - last_time
        last_time = current_time
        
        with aircraft_lock:
            for ac in aircraft_list:
                ac.update(dt)
        
        time.sleep(0.1)


def main():
    """Main function"""
    global aircraft_list, center_latitude, center_longitude
    
    print("="*70)
    print(" US ARMY ATC SYSTEM - DEBIAN 9.3 COMPATIBLE")
    print(" Python 3.5+ Compatible")
    print("="*70)
    print()
    
    # Get parameters
    try:
        lat_input = input("Enter center latitude [33.7490]: ").strip()
        center_latitude = float(lat_input) if lat_input else 33.7490
    except:
        center_latitude = 33.7490
    
    try:
        lon_input = input("Enter center longitude [-84.3880]: ").strip()
        center_longitude = float(lon_input) if lon_input else -84.3880
    except:
        center_longitude = -84.3880
    
    try:
        num_input = input("Enter number of simulated aircraft [5]: ").strip()
        num_aircraft = int(num_input) if num_input else 5
    except:
        num_aircraft = 5
    
    print()
    print("Center: {}, {}".format(center_latitude, center_longitude))
    print("Simulated aircraft: {}".format(num_aircraft))
    print()
    
    # Generate simulated aircraft
    callsigns_mil = ["VIPER", "SNAKE", "EAGLE", "HAWK", "RAVEN", "GHOST", "SABER"]
    callsigns_civ = ["AAL", "DAL", "UAL", "SWA", "JBU"]
    
    for i in range(num_aircraft):
        is_mil = random.random() < 0.3
        
        if is_mil:
            callsign = "{}{}".format(random.choice(callsigns_mil), 
                                    str(random.randint(1, 99)).zfill(2))
            ac_type = "military"
        else:
            callsign = "{}{}".format(random.choice(callsigns_civ),
                                    random.randint(1000, 9999))
            ac_type = "civilian"
        
        icao = "{:06X}".format(random.randint(0xA00000, 0xAFFFFF))
        alt = random.randint(100, 400) * 100
        speed = random.randint(250, 550)
        
        aircraft_list.append(Aircraft(icao, callsign, center_latitude, 
                                     center_longitude, alt, speed, ac_type))
    
    # Start ADS-B receiver
    receiver = ADSBReceiver(30001)
    receiver.start()
    
    # Start aircraft update thread
    update_thread = threading.Thread(target=update_aircraft_loop, daemon=True)
    update_thread.start()
    
    # Start web server
    print("="*70)
    print(" SYSTEM ACTIVE")
    print("="*70)
    print()
    print(" Web Interface: http://localhost:8888")
    print(" ADS-B Port:    30001")
    print()
    print(" Press Ctrl+C to stop")
    print("="*70)
    print()
    
    try:
        server = HTTPServer(('0.0.0.0', 8888), WebHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down...")


if __name__ == "__main__":
    main()
