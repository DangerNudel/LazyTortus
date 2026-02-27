#!/usr/bin/env python3
"""
Cyberpunk Military ATC Simulator

Military-grade air traffic control interface with realistic aircraft icons.
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
aircraft_list = []  # Simulated aircraft
received_aircraft = {}  # Real ADS-B aircraft: {icao: {data, last_seen}}
aircraft_lock = threading.Lock()
message_count = 0

# ADS-B timeout - remove aircraft if not seen for this many seconds
AIRCRAFT_TIMEOUT = 60


class ADSBReceiver:
    """Receives and decodes ADS-B data on port 30001"""
    
    def __init__(self):
        self.running = False
        self.socket = None
        self.decoder = None
        
        # Try to import ADSBEncoder for decoding
        try:
            self.decoder = ADSBEncoder
        except:
            pass
    
    def start(self):
        """Start listening on port 30001"""
        self.running = True
        thread = threading.Thread(target=self._listen, daemon=True)
        thread.start()
        print("✓ ADS-B receiver listening on port 30001")
    
    def stop(self):
        """Stop the receiver"""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
    
    def _listen(self):
        """Listen for incoming ADS-B data"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('127.0.0.1', 30001))
            self.socket.listen(5)
            
            print("  Waiting for ADS-B data connections...")
            
            while self.running:
                try:
                    self.socket.settimeout(1.0)
                    client, addr = self.socket.accept()
                    print(f"  ADS-B data source connected from {addr}")
                    
                    # Handle this connection
                    thread = threading.Thread(target=self._handle_client, 
                                            args=(client,), daemon=True)
                    thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        print(f"  Error accepting connection: {e}")
                        
        except Exception as e:
            print(f"  Error starting ADS-B receiver: {e}")
    
    def _handle_client(self, client):
        """Handle incoming ADS-B data from a client"""
        buffer = b""
        msg_count = 0
        
        try:
            while self.running:
                data = client.recv(4096)
                if not data:
                    break
                
                buffer += data
                
                # Process different message formats
                # AVR format: *<hex>;\n
                # Beast format: <esc><type><timestamp><signal><message>
                
                # Try AVR format first (simpler)
                while b'*' in buffer and b';' in buffer:
                    start = buffer.find(b'*')
                    end = buffer.find(b';', start)
                    
                    if end > start:
                        msg = buffer[start+1:end].decode('ascii', errors='ignore').strip()
                        buffer = buffer[end+1:]
                        
                        if msg:
                            self._process_message(msg)
                            msg_count += 1
                            
                            if msg_count % 50 == 0:
                                print(f"  Received {msg_count} ADS-B messages")
                    else:
                        break
                
                # Try Beast format
                while len(buffer) >= 23:  # Minimum Beast message size
                    if buffer[0] == 0x1A:  # ESC
                        msg_type = buffer[1]
                        
                        if msg_type == 0x33:  # 14-byte Mode-S
                            if len(buffer) >= 23:
                                # Extract message (skip ESC, type, timestamp, signal)
                                msg_bytes = buffer[9:23]
                                msg_hex = msg_bytes.hex().upper()
                                self._process_message(msg_hex)
                                buffer = buffer[23:]
                                msg_count += 1
                                
                                if msg_count % 50 == 0:
                                    print(f"  Received {msg_count} ADS-B messages")
                            else:
                                break
                        else:
                            buffer = buffer[1:]
                    else:
                        buffer = buffer[1:]
                
                # Keep buffer size reasonable
                if len(buffer) > 10000:
                    buffer = buffer[-1000:]
                    
        except Exception as e:
            print(f"  Error handling ADS-B data: {e}")
        finally:
            client.close()
            print(f"  ADS-B data source disconnected ({msg_count} messages received)")
    
    def _process_message(self, msg_hex):
        """Process a decoded ADS-B message"""
        global received_aircraft
        
        try:
            # Message should be 28 hex characters (14 bytes)
            if len(msg_hex) != 28:
                return
            
            # Extract ICAO address (bytes 1-3)
            icao = msg_hex[2:8]
            
            # Get downlink format
            first_byte = int(msg_hex[0:2], 16)
            df = first_byte >> 3
            
            # We only care about DF17 (ADS-B)
            if df != 17:
                return
            
            # Get message type
            me_byte = int(msg_hex[8:10], 16)
            tc = me_byte >> 3
            
            current_time = time.time()
            
            with aircraft_lock:
                if icao not in received_aircraft:
                    received_aircraft[icao] = {
                        'icao': icao,
                        'callsign': '',
                        'lat': None,
                        'lon': None,
                        'altitude': 0,
                        'speed': 0,
                        'heading': 0,
                        'last_seen': current_time,
                        'position_even': None,
                        'position_odd': None,
                        'type': 'civilian'  # Default to civilian for received
                    }
                    print(f"  New aircraft detected: {icao}")
                
                ac = received_aircraft[icao]
                ac['last_seen'] = current_time
                
                # Decode based on type code
                if 1 <= tc <= 4:
                    # Callsign
                    callsign = self._decode_callsign(msg_hex)
                    if callsign:
                        ac['callsign'] = callsign
                        print(f"  {icao}: Callsign = {callsign}")
                        
                elif 9 <= tc <= 18:
                    # Position
                    lat, lon, alt = self._decode_position(msg_hex, ac)
                    if lat is not None and lon is not None:
                        ac['lat'] = lat
                        ac['lon'] = lon
                        ac['altitude'] = alt
                        print(f"  {icao}: Position = {lat:.4f}, {lon:.4f}, {alt}ft")
                        
                elif tc == 19:
                    # Velocity
                    speed, heading = self._decode_velocity(msg_hex)
                    if speed is not None:
                        ac['speed'] = speed
                    if heading is not None:
                        ac['heading'] = heading
                    print(f"  {icao}: Velocity = {speed:.0f}kts, {heading:.0f}°")
                        
        except Exception as e:
            # Silently ignore decode errors
            pass
    
    def _decode_callsign(self, msg_hex):
        """Decode callsign from ADS-B message"""
        try:
            # Callsign is in bytes 5-10 (characters 10-22)
            charset = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"
            
            callsign = ""
            for i in range(8):
                idx = i * 6 + 40  # Starting bit position
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
        """Decode position from ADS-B message (simplified for local airspace)"""
        try:
            # Extract altitude from ME field properly
            # ME starts at position 8, altitude is bits 8-19 within ME (12 bits)
            me_hex = msg_hex[8:22]  # ME field is 56 bits = 14 hex chars
            me_int = int(me_hex, 16)
            me_bin = format(me_int, '056b')
            
            # Extract altitude (bits 8-19 of ME)
            alt_bin = me_bin[8:20]  # 12 bits
            alt_int = int(alt_bin, 2)
            
            # Check Q-bit (bit 4 from right, 0-indexed)
            q_bit = (alt_int >> 4) & 1
            
            if q_bit == 1:
                # Q=1: 25ft resolution
                # Format: [7 top bits][Q=1][4 bottom bits]
                top_bits = (alt_int >> 5) & 0x7F
                bottom_bits = alt_int & 0x0F
                alt_code = (top_bits << 4) | bottom_bits
                altitude = alt_code * 25 - 1000
            else:
                # Q=0: Gillham code (not commonly used, default to 0)
                altitude = 0
            
            # Extract CPR encoded position
            lat_bin = me_bin[22:39]  # 17 bits
            lon_bin = me_bin[39:56]  # 17 bits
            cpr_lat = int(lat_bin, 2)
            cpr_lon = int(lon_bin, 2)
            
            # Time/Format bit (F bit, position 21 in ME)
            time_bit = int(me_bin[21])
            
            # Store frame with timestamp
            if time_bit:
                ac['position_odd'] = (cpr_lat, cpr_lon, time.time())
            else:
                ac['position_even'] = (cpr_lat, cpr_lon, time.time())
            
            # Try to decode when we have both frames
            if ac['position_even'] and ac['position_odd']:
                lat_even_cpr, lon_even_cpr, time_even = ac['position_even']
                lat_odd_cpr, lon_odd_cpr, time_odd = ac['position_odd']
                
                # Only decode if frames are recent (within 10 seconds)
                if abs(time_even - time_odd) > 10:
                    return None, None, altitude
                
                # Simplified CPR decode for local airspace
                # This works within ~180nm and is much simpler than full CPR
                
                # CPR parameters
                NZ = 15  # Number of latitude zones
                dlat_even = 360.0 / (4 * NZ)  # 6 degrees
                dlat_odd = 360.0 / (4 * NZ - 1)  # ~6.1 degrees
                
                # Calculate latitude
                j = int(((59 * lat_even_cpr - 60 * lat_odd_cpr) / 131072.0) + 0.5)
                
                lat_even = dlat_even * ((j % 60) + lat_even_cpr / 131072.0)
                lat_odd = dlat_odd * ((j % 59) + lat_odd_cpr / 131072.0)
                
                # Normalize to -90 to 90
                if lat_even >= 270:
                    lat_even -= 360
                if lat_odd >= 270:
                    lat_odd -= 360
                    
                # Use most recent frame for latitude
                if time_even > time_odd:
                    lat = lat_even
                    use_even = True
                else:
                    lat = lat_odd
                    use_even = False
                
                # Calculate longitude using proper CPR global decoding
                nl = self._calculate_nl(lat)
                
                if use_even:
                    # Using even frame
                    ni = max(nl, 1)
                    dlon = 360.0 / ni
                    
                    # Calculate m with proper handling of negative values
                    m_raw = (lon_even_cpr * (nl - 1) - lon_odd_cpr * nl) / 131072.0
                    m = int(math.floor(m_raw + 0.5))
                    
                    # Decode longitude
                    lon_raw = dlon * (m + lon_even_cpr / 131072.0)
                    
                    # Normalize to -180 to 180
                    lon = lon_raw
                    while lon >= 180:
                        lon -= 360
                    while lon < -180:
                        lon += 360
                else:
                    # Using odd frame
                    ni = max(nl - 1, 1)
                    dlon = 360.0 / ni
                    
                    # Calculate m with proper handling of negative values
                    m_raw = (lon_even_cpr * (nl - 1) - lon_odd_cpr * nl) / 131072.0
                    m = int(math.floor(m_raw + 0.5))
                    
                    # Decode longitude
                    lon_raw = dlon * (m + lon_odd_cpr / 131072.0)
                    
                    # Normalize to -180 to 180
                    lon = lon_raw
                    while lon >= 180:
                        lon -= 360
                    while lon < -180:
                        lon += 360
                
                # Normalize to -180 to 180
                if lon >= 180:
                    lon -= 360
                if lon < -180:
                    lon += 360
                
                # Sanity check
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon, altitude
            
            return None, None, altitude
            
        except Exception as e:
            import traceback
            print(f"  Position decode error: {e}")
            traceback.print_exc()
            return None, None, 0
    
    def _calculate_nl(self, lat):
        """Calculate NL (number of longitude zones) for a given latitude using correct formula"""
        import math
        
        if abs(lat) >= 87.0:
            return 1
        
        # Correct NL calculation formula
        nz = 15.0
        a = 1.0 - math.cos(math.pi / (2.0 * nz))
        b = math.cos(math.pi * abs(lat) / 180.0) ** 2
        
        if 1.0 - a / b <= 0:
            return 1
            
        nl = 2.0 * math.pi / (math.acos(1.0 - a / b))
        return int(nl)
    
    def _decode_velocity(self, msg_hex):
        """Decode velocity from ADS-B message"""
        try:
            # Extract ME field properly
            me_hex = msg_hex[8:22]  # ME field is 56 bits = 14 hex chars
            me_int = int(me_hex, 16)
            me_bin = format(me_int, '056b')
            
            # ME format for velocity (TC 19, ST 1):
            # TC(5) ST(3) IC(1) IFR(1) NUC(3) EW_sign(1) EW_vel(10) NS_sign(1) NS_vel(10) VR_src(1) VR_sign(1) VR(9) Reserved(2) GNSS_sign(1) GNSS_diff(7)
            
            tc = int(me_bin[0:5], 2)
            if tc != 19:
                return None, None
            
            # EW velocity: bit 13 (sign) + bits 14-23 (value)
            ew_sign = int(me_bin[13])
            ew_vel_raw = int(me_bin[14:24], 2)
            ew_vel = ew_vel_raw - 1  # Subtract 1 offset
            if ew_sign:
                ew_vel = -ew_vel
            
            # NS velocity: bit 24 (sign) + bits 25-34 (value)
            ns_sign = int(me_bin[24])
            ns_vel_raw = int(me_bin[25:35], 2)
            ns_vel = ns_vel_raw - 1  # Subtract 1 offset
            if ns_sign:
                ns_vel = -ns_vel
            
            # Calculate speed and heading
            speed = math.sqrt(ew_vel**2 + ns_vel**2)
            heading = math.degrees(math.atan2(ew_vel, ns_vel)) % 360
            
            return speed, heading
            
        except Exception as e:
            return None, None


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
        """Serve aircraft data as JSON (simulated + received ADS-B)"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
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
                    "type": getattr(ac, 'ac_type', 'civilian'),
                    "messages": message_count,
                    "seen": 0,
                    "source": "simulated"
                })
            
            # Add received ADS-B aircraft
            # Remove stale aircraft first
            stale = [icao for icao, data in received_aircraft.items() 
                    if current_time - data['last_seen'] > AIRCRAFT_TIMEOUT]
            for icao in stale:
                del received_aircraft[icao]
            
            # Add active received aircraft
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
        
        self.wfile.write(json.dumps(response).encode())
    
    def serve_map_page(self):
        """Serve cyberpunk ATC interface"""
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        html = """<!DOCTYPE html>
<html>
<head>
    <title>US ARMY ATC - TACTICAL AIR CONTROL</title>
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
        TACTICAL AIR CONTROL SYSTEM v2.4 | AUTHORIZED PERSONNEL ONLY
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
                    console.error('Error updating aircraft:', e);
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
    print(" US ARMY TACTICAL AIR CONTROL SYSTEM")
    print(" UNCLASSIFIED // FOR TRAINING USE ONLY")
    print("="*70)
    print()
    
    while True:
        try:
            lat = float(input("Enter center latitude: ").strip())
            if -90 <= lat <= 90: break
            print("Invalid latitude")
        except ValueError:
            print("Invalid input")
    
    while True:
        try:
            lon = float(input("Enter center longitude: ").strip())
            if -180 <= lon <= 180: break
            print("Invalid longitude")
        except ValueError:
            print("Invalid input")
    
    while True:
        try:
            num = int(input("Enter number of aircraft: ").strip())
            if 1 <= num <= 100: break
            print("Must be 1-100")
        except ValueError:
            print("Invalid input")
    
    return lat, lon, num


def main():
    """Main entry point"""
    try:
        center_lat, center_lon, num_aircraft = get_user_input()
        
        simulator = IntegratedSimulator(center_lat, center_lon, num_aircraft)
        
        # Start ADS-B receiver
        print("\nStarting ADS-B receiver...")
        adsb_receiver = ADSBReceiver()
        adsb_receiver.start()
        
        print("\nStarting US Army ATC system on port 8888...")
        web_server = HTTPServer(('127.0.0.1', 8888), WebHandler)
        web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
        web_thread.start()
        print("✓ SYSTEM ONLINE")
        
        print("\n" + "="*70)
        print(" US ARMY TACTICAL AIR CONTROL - READY")
        print("="*70)
        print()
        print("ATC Terminal: http://localhost:8888")
        print("ADS-B Input: Port 30001 (AVR or Beast format)")
        print("Classification: UNCLASSIFIED")
        print()
        print("Students can send ADS-B data to port 30001")
        print("Aircraft will appear on the display in real-time")
        print()
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
