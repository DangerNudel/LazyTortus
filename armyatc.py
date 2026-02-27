#!/usr/bin/env python3
"""
Donovian Military ATC Simulator

"""

import socket
import threading
import json
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
import math
import random
import sys
import signal
from datetime import datetime

# Import our encoder
sys.path.insert(0, '.')
try:
    from aircraft_simulator import ADSBEncoder, Aircraft
except:
    print("Error: Make sure aircraft_simulator.py is in the same directory")
    sys.exit(1)


# Global aircraft storage
aircraft_list = []
aircraft_lock = threading.Lock()
message_count = 0


class IntegratedSimulator:
    """Simulator that updates aircraft list directly"""
    
    def __init__(self, center_lat, center_lon, num_aircraft):
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.num_aircraft = num_aircraft
        self.running = False
        
        signal.signal(signal.SIGINT, self.signal_handler)
        
        print(f"\nGenerating {num_aircraft} aircraft around {center_lat:.4f}, {center_lon:.4f}")
        self.generate_aircraft()
    
    def signal_handler(self, signum, frame):
        print("\n\nShutting down...")
        self.running = False
        sys.exit(0)
    
    def generate_aircraft(self):
        """Generate mix of civilian and military aircraft"""
        global aircraft_list
        
        civilian_airlines = ['AAL', 'DAL', 'UAL', 'SWA', 'JBU', 'ASA', 'SKW', 'FFT']
        military_callsigns = ['SNAKE', 'VIPER', 'EAGLE', 'RAVEN', 'GHOST', 'SABER', 'TALON', 'HAWK']
        
        with aircraft_lock:
            aircraft_list.clear()
            
            for i in range(self.num_aircraft):
                icao = f"{random.randint(0, 0xFFFFFF):06X}"
                
                # 60% civilian, 40% military
                if random.random() < 0.6:
                    # Civilian
                    airline = random.choice(civilian_airlines)
                    flight_num = random.randint(1, 9999)
                    callsign = f"{airline}{flight_num:04d}"
                    ac_type = 'civilian'
                else:
                    # Military
                    callsign = f"{random.choice(military_callsigns)}{random.randint(1,99):02d}"
                    ac_type = 'military'
                
                aircraft = Aircraft(icao, callsign, self.center_lat, self.center_lon)
                aircraft.ac_type = ac_type  # Add aircraft type
                aircraft_list.append(aircraft)
                
                type_icon = "CIV" if ac_type == 'civilian' else "MIL"
                print(f"  [{type_icon}] {callsign:10s} ({icao}) - Alt: {aircraft.altitude}ft, Speed: {aircraft.speed_knots:.0f}kts")
    
    def run(self):
        """Run simulation loop"""
        global message_count
        
        self.running = True
        last_time = time.time()
        start_time = time.time()
        
        print(f"\n{'='*70}")
        print("TACTICAL AIR CONTROL - SYSTEM ACTIVE")
        print(f"{'='*70}\n")
        
        try:
            while self.running:
                current_time = time.time()
                dt = current_time - last_time
                last_time = current_time
                
                with aircraft_lock:
                    for aircraft in aircraft_list:
                        aircraft.update(dt)
                        message_count += 1
                
                elapsed = current_time - start_time
                if int(elapsed) % 5 == 0 and dt < 0.2:
                    msg_rate = message_count / elapsed if elapsed > 0 else 0
                    print(f"[{datetime.now().strftime('%H:%M:%S')}Z] "
                          f"TRACKING: {len(aircraft_list)} ACFT | "
                          f"UPDATES: {message_count} | RATE: {msg_rate:.1f}/s")
                
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            self.signal_handler(None, None)


class WebHandler(SimpleHTTPRequestHandler):
    """HTTP handler for web interface"""
    
    def do_GET(self):
        """Handle GET requests"""
        if self.path == '/data/aircraft.json':
            self.serve_aircraft_json()
        elif self.path == '/' or self.path == '/index.html':
            self.serve_map_page()
        else:
            self.send_error(404)
    
    def serve_aircraft_json(self):
        """Serve aircraft data as JSON"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        with aircraft_lock:
            aircraft_data = []
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
                    "type": getattr(ac, 'ac_type', 'civilian'),
                    "messages": message_count,
                    "seen": 0
                })
        
        response = {
            "now": time.time(),
            "messages": message_count,
            "aircraft": aircraft_data
        }
        
        self.wfile.write(json.dumps(response).encode())
    
    def serve_map_page(self):
        """Serve ATC interface"""
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        html = """<!DOCTYPE html>
<html>
<head>
    <title>DONOVIAN ATC - TACTICAL AIR DEFENSE CONTROL SYSTEM</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            font-family: 'Share Tech Mono', monospace;
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
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
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
            letter-spacing: 2px;
            margin-bottom: 3px;
            text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.5);
        }
        
        .header .subtitle {
            color: #8b7355;
            font-size: 11px;
            letter-spacing: 3px;
            font-weight: 700;
        }
        
        .status-bar {
            background: #2a2a2a;
            padding: 12px 15px;
            border-bottom: 2px solid #4a4a4a;
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 15px;
            font-size: 11px;
        }
        
        .status-item {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .status-label {
            color: #888;
            font-size: 9px;
            margin-bottom: 4px;
            letter-spacing: 1px;
        }
        
        .status-value {
            color: #00ff00;
            font-weight: 700;
            font-size: 16px;
            text-shadow: 0 0 5px rgba(0, 255, 0, 0.5);
        }
        
        .classification {
            background: #8b7355;
            color: #000;
            text-align: center;
            padding: 8px;
            font-weight: 700;
            font-size: 12px;
            letter-spacing: 3px;
            border-bottom: 2px solid #6a5535;
        }
        
        .aircraft-list {
            padding: 15px 10px;
        }
        
        .aircraft-item {
            background: linear-gradient(135deg, #2a2a2a 0%, #1f1f1f 100%);
            border: 2px solid #4a4a4a;
            border-radius: 3px;
            padding: 12px;
            margin-bottom: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .aircraft-item:hover {
            border-color: #6a6a6a;
            background: linear-gradient(135deg, #333 0%, #262626 100%);
            transform: translateX(-3px);
            box-shadow: 3px 0 8px rgba(0, 0, 0, 0.3);
        }
        
        .aircraft-item.military {
            border-left: 4px solid #8b7355;
            background: linear-gradient(135deg, #2a2520 0%, #1f1f1f 100%);
        }
        
        .aircraft-item.military:hover {
            border-left-color: #b8935c;
            box-shadow: 0 0 10px rgba(139, 115, 85, 0.3);
        }
        
        .aircraft-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid #3a3a3a;
        }
        
        .callsign {
            font-size: 16px;
            font-weight: 700;
            color: #d4d4d4;
            letter-spacing: 1px;
        }
        
        .aircraft-item.military .callsign {
            color: #b8935c;
        }
        
        .aircraft-type {
            background: #4a4a4a;
            color: #d4d4d4;
            padding: 3px 10px;
            border-radius: 2px;
            font-size: 9px;
            font-weight: 700;
            letter-spacing: 1px;
        }
        
        .aircraft-item.military .aircraft-type {
            background: #8b7355;
            color: #000;
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
        
        ::-webkit-scrollbar-thumb:hover {
            background: #6a6a6a;
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
        TACTICAL AIR DEFENSE SYSTEM v2.4 | AUTHORIZED PERSONNEL ONLY
    </div>
    <div id="map"></div>
    
    <div class="sidebar">
        <div class="classification">
            UNCLASSIFIED // FOR TRAINING USE ONLY
        </div>
        
        <div class="header">
            <div class="army-star">★</div>
            <h1>DONOVIAN ARMY ATC</h1>
            <div class="subtitle">TACTICAL AIR DEFENSE CONTROL</div>
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
        
        <div class="aircraft-list" id="aircraft-list">
            <div style="text-align: center; color: #666; padding: 50px 20px; font-size: 11px;">
                SCANNING AIRSPACE...<br>
                NO CONTACTS DETECTED
            </div>
        </div>
    </div>

    <script>
        const map = L.map('map', {
            zoomControl: false
        }).setView([33.7490, -84.3880], 9);
        
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap'
        }).addTo(map);
        
        L.control.zoom({
            position: 'topleft'
        }).addTo(map);

        const markers = {};
        let centered = false;

        // Realistic aircraft SVG icons
        function createCivilianIcon(heading) {
            // Civilian plane SVG needs +90 degree adjustment (different orientation than military)
            const rotation = heading;
            const svg = `
                <svg width="40" height="40" viewBox="0 -3.43 122.88 122.88" xmlns="http://www.w3.org/2000/svg" 
                     style="transform: rotate(${rotation}deg); filter: drop-shadow(0 0 4px rgba(100, 150, 100, 0.6));">
                    <path fill="#6B8E23" fill-rule="evenodd" clip-rule="evenodd" d="M38.14,115.91c0-10.58,5.81-15.56,13.46-21.3l0-27.68L1.37,89.25c0-19.32-6.57-17.9,9.05-27.72l0.15-0.09 V49.37h11.22v5.08l8.24-5.13V35.8h11.22v6.54l10.36-6.45V7.3c0-4.02,4.37-7.3,9.7-7.3l0,0c5.34,0,9.7,3.29,9.7,7.3v28.58 l10.47,6.52V35.8l11.22,0v13.59l8.24,5.13v-5.15l11.21,0v12.14c15.56,9.67,9.61,7.78,9.61,27.74L71.01,66.91v27.58 c8.14,5.43,13.46,9.6,13.46,21.43l-12.81,0.11c-2.93-2.3-4.96-4.05-6.52-5.26c-1.18,0.39-2.48,0.6-3.83,0.6h0 c-1.53,0-2.99-0.27-4.28-0.76c-1.68,1.22-3.9,3.04-7.21,5.42L38.14,115.91L38.14,115.91L38.14,115.91z"/>
                </svg>
            `;
            
            return L.divIcon({
                html: svg,
                iconSize: [40, 40],
                iconAnchor: [20, 20],
                className: ''
            });
        }

        function createMilitaryIcon(heading) {
            // Adjust heading: SVG points right (90°), so we need to subtract 90°
            const rotation = heading - 90;
            const svg = `
                <svg width="40" height="32" viewBox="0 0 640 512" xmlns="http://www.w3.org/2000/svg" 
                     style="transform: rotate(${rotation}deg); filter: drop-shadow(0 0 4px rgba(139, 115, 85, 0.6));">
                    <path fill="#8b7355" d="M544 224l-128-16-48-16h-24L227.158 44h39.509C278.333 44 288 41.375 288 38s-9.667-6-21.333-6H152v12h16v164h-48l-66.667-80H18.667L8 138.667V208h8v16h48v2.666l-64 8v42.667l64 8V288H16v16H8v69.333L18.667 384h34.667L120 304h48v164h-16v12h114.667c11.667 0 21.333-2.625 21.333-6s-9.667-6-21.333-6h-39.509L344 320h24l48-16 128-16c96-21.333 96-26.583 96-32 0-5.417 0-10.667-96-32z"/>
                </svg>
            `;
            
            return L.divIcon({
                html: svg,
                iconSize: [40, 32],
                iconAnchor: [20, 16],
                className: ''
            });
        }

        function updateAircraft() {
            fetch('/data/aircraft.json')
                .then(r => r.json())
                .then(data => {
                    const aircraft = data.aircraft || [];
                    
                    // Update status bar
                    document.getElementById('aircraft-count').textContent = aircraft.length;
                    document.getElementById('time').textContent = new Date().toLocaleTimeString();
                    
                    // Update aircraft list
                    updateAircraftList(aircraft);
                    
                    // Update map markers
                    const current = new Set();
                    
                    aircraft.forEach(ac => {
                        current.add(ac.hex);
                        if (!ac.lat || !ac.lon) return;
                        
                        const pos = [ac.lat, ac.lon];
                        const heading = ac.track || 0;
                        const isMilitary = ac.type === 'military';
                        
                        const icon = isMilitary ? 
                            createMilitaryIcon(heading) : 
                            createCivilianIcon(heading);
                        
                        if (markers[ac.hex]) {
                            markers[ac.hex].setLatLng(pos);
                            markers[ac.hex].setIcon(icon);
                        } else {
                            const m = L.marker(pos, {icon: icon})
                                .addTo(map)
                                .on('click', () => highlightAircraft(ac.hex));
                            markers[ac.hex] = m;
                            
                            if (!centered) {
                                map.setView(pos, 10);
                                centered = true;
                            }
                        }
                    });
                    
                    Object.keys(markers).forEach(hex => {
                        if (!current.has(hex)) {
                            map.removeLayer(markers[hex]);
                            delete markers[hex];
                        }
                    });
                })
                .catch(e => {
                    document.getElementById('status').textContent = 'ERROR';
                });
        }

        function updateAircraftList(aircraft) {
            const listDiv = document.getElementById('aircraft-list');
            
            if (aircraft.length === 0) {
                listDiv.innerHTML = `
                    <div style="text-align: center; color: #888; padding: 50px 20px;">
                        NO AIRCRAFT DETECTED<br>
                        <span class="blink">█</span>
                    </div>
                `;
                return;
            }
            
            // Sort: military first, then by callsign
            aircraft.sort((a, b) => {
                if (a.type !== b.type) {
                    return a.type === 'military' ? -1 : 1;
                }
                return a.flight.localeCompare(b.flight);
            });
            
            listDiv.innerHTML = aircraft.map(ac => {
                const isMilitary = ac.type === 'military';
                const typeLabel = isMilitary ? 'MILITARY' : 'CIVILIAN';
                const className = isMilitary ? 'aircraft-item military' : 'aircraft-item';
                
                return `
                    <div class="${className}" id="ac-${ac.hex}" 
                         onclick="focusAircraft(${ac.lat}, ${ac.lon}, '${ac.hex}')">
                        <div class="aircraft-header">
                            <span class="callsign">${ac.flight}</span>
                            <span class="aircraft-type">${typeLabel}</span>
                        </div>
                        <div class="aircraft-data">
                            <div class="data-item">
                                <span class="data-label">ICAO:</span>
                                <span class="data-value">${ac.hex}</span>
                            </div>
                            <div class="data-item">
                                <span class="data-label">ALT:</span>
                                <span class="data-value">${ac.altitude.toLocaleString()} FT</span>
                            </div>
                            <div class="data-item">
                                <span class="data-label">SPD:</span>
                                <span class="data-value">${Math.round(ac.speed)} KTS</span>
                            </div>
                            <div class="data-item">
                                <span class="data-label">HDG:</span>
                                <span class="data-value">${Math.round(ac.track)}°</span>
                            </div>
                            <div class="data-item">
                                <span class="data-label">LAT:</span>
                                <span class="data-value">${ac.lat.toFixed(4)}</span>
                            </div>
                            <div class="data-item">
                                <span class="data-label">LON:</span>
                                <span class="data-value">${ac.lon.toFixed(4)}</span>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function focusAircraft(lat, lon, hex) {
            map.setView([lat, lon], 13);
            highlightAircraft(hex);
        }

        function highlightAircraft(hex) {
            // Remove previous highlights
            document.querySelectorAll('.aircraft-item').forEach(el => {
                el.style.background = '';
            });
            
            // Highlight selected
            const element = document.getElementById('ac-' + hex);
            if (element) {
                element.style.background = 'linear-gradient(135deg, #2a2a4e 0%, #1a1a3a 100%)';
                element.scrollIntoView({behavior: 'smooth', block: 'nearest'});
            }
        }

        updateAircraft();
        setInterval(updateAircraft, 1000);
    </script>
</body>
</html>"""
        
        self.wfile.write(html.encode())
    
    def log_message(self, format, *args):
        pass


def get_user_input():
    """Get simulation parameters"""
    print("="*70)
    print(" DONOVIAN ARMY TACTICAL AIR DEFENSE CONTROL SYSTEM")
    print(" UNCLASSIFIED // FOR TRAINING USE ONLY")
    print("="*70)
    print()
    
    while True:
        try:
            lat = float(input("Enter center latitude(Fort Gordon is 32.4290): ").strip())
            if -90 <= lat <= 90: break
            print("Invalid latitude")
        except ValueError:
            print("Invalid input")
    
    while True:
        try:
            lon = float(input("Enter center longitude(Fort Gordon is -82.1442): ").strip())
            if -180 <= lon <= 180: break
            print("Invalid longitude")
        except ValueError:
            print("Invalid input")
    
    while True:
        try:
            num = int(input("Enter number of aircraft to simulate: ").strip())
            if 1 <= num <= 100000: break
            print("Must be 1-100000")
        except ValueError:
            print("Invalid input")
    
    return lat, lon, num


def main():
    """Main entry point"""
    try:
        center_lat, center_lon, num_aircraft = get_user_input()
        
        simulator = IntegratedSimulator(center_lat, center_lon, num_aircraft)
        
        print("\nStarting Donovian Army Air Defense system on http://localhost:8888...")
        web_server = HTTPServer(('127.0.0.1', 8888), WebHandler)
        web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
        web_thread.start()
        print("✓ SYSTEM ONLINE")
        
        print("\n" + "="*70)
        print(" DONOVIAN ARMY TACTICAL AIR DEFENSE CONTROL - READY")
        print("="*70)
        print()
        print("ATC Terminal: http://localhost:8888")
        print("Classification: UNCLASSIFIED")
        print("Press Ctrl+C to shutdown system")
        print()
        
        simulator.run()
        
    except KeyboardInterrupt:
        print("\n\nSYSTEM SHUTDOWN")
    except Exception as e:
        print(f"\nSYSTEM ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
