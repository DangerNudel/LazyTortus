#!/usr/bin/env python3
"""
Aircraft Traffic Simulator for dump1090-fa

This script generates realistic aircraft traffic and sends it to dump1090-fa
via the Beast binary protocol on port 30001 (TCP).

NO EXTERNAL DEPENDENCIES - Uses only Python standard library!

Usage:
    python3 aircraft_simulator.py

The script will prompt for:
    - Center latitude (e.g., 33.7490 for Atlanta)
    - Center longitude (e.g., -84.3880 for Atlanta)
    - Number of planes to simulate

Author: Claude
License: MIT
"""

import socket
import struct
import time
import math
import random
import sys
import signal
from datetime import datetime


class ADSBEncoder:
    """Manual ADS-B message encoder (DF17) - No external dependencies!"""
    
    # CRC polynomial for Mode-S (ICAO Annex 10)
    GENERATOR = 0xFFFA0480900
    
    @staticmethod
    def crc(msg_hex, encode=True):
        """
        Calculate CRC for Mode-S message using the ICAO polynomial
        """
        # Mode-S generator polynomial (25 bits): x^24 + x^23 + ... (standard ICAO)
        GENERATOR = 0xFFFA0480900  # 25-bit generator
        
        msg_bin = bin(int(msg_hex, 16))[2:].zfill(len(msg_hex) * 4)
        
        if encode:
            msg_bin = msg_bin + '0' * 24  # Append 24 zeros for CRC
        
        # Convert to integer for faster calculation
        msg_int = int(msg_bin, 2)
        
        # Perform polynomial division
        for i in range(len(msg_bin) - 24):
            if (msg_int >> (len(msg_bin) - 1 - i)) & 1:
                msg_int ^= (GENERATOR >> 1) << (len(msg_bin) - 25 - i)
        
        # Return last 24 bits
        return msg_int & 0xFFFFFF
    
    @staticmethod
    def encode_callsign(icao, callsign):
        """Encode aircraft identification message (TC 1-4)"""
        charset = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"
        
        df = 17  # Downlink Format
        ca = 5   # Capability
        tc = 4   # Type Code for aircraft ID
        
        callsign = (callsign + "        ")[:8].upper()
        
        encoded_chars = []
        for char in callsign:
            idx = charset.find(char)
            encoded_chars.append(idx if idx >= 0 else 0)
        
        msg_bin = ''
        msg_bin += format(df, '05b')
        msg_bin += format(ca, '03b')
        msg_bin += format(int(icao, 16), '024b')
        msg_bin += format(tc, '05b')
        msg_bin += format(0, '03b')  # Category
        
        for char_code in encoded_chars:
            msg_bin += format(char_code, '06b')
        
        msg_hex = format(int(msg_bin, 2), '016X')
        crc = ADSBEncoder.crc(msg_hex, encode=True)
        
        return msg_hex + format(crc, '06X')
    
    @staticmethod
    def encode_position(icao, lat, lon, alt, time_bit):
        """Encode airborne position message (TC 9-18)"""
        df = 17
        ca = 5
        tc = 11
        
        # Encode altitude (25ft resolution, Q-bit encoding)
        alt_code = int((alt + 1000) / 25)
        # The altitude is 11 bits + Q-bit (bit 4 from right)
        # Q=1 for 25ft resolution
        # Format: D2 D4 D5 A1 A2 A4 B1 Q B2 B4 C1 C2 C4
        # We'll use simplified: top 8 bits, Q=1, bottom 3 bits
        alt_bin = format(alt_code, '011b')  # 11 bits for altitude
        alt_encoded = alt_bin[:8] + '1' + alt_bin[8:11]  # Insert Q-bit to make 12 bits total
        
        # CPR encoding
        nb = 17
        nz = 60 if time_bit == 0 else 59
        
        dlat = 360.0 / nz
        yz = lat / dlat
        lat_cpr = int((yz - int(yz)) * (2 ** nb)) & ((2 ** nb) - 1)
        
        lat_rad = lat * math.pi / 180
        nl = ADSBEncoder.nl(lat_rad)
        n = max(nl if time_bit == 0 else nl - 1, 1)
        
        dlon = 360.0 / n
        xz = lon / dlon
        lon_cpr = int((xz - int(xz)) * (2 ** nb)) & ((2 ** nb) - 1)
        
        msg_bin = ''
        msg_bin += format(df, '05b')
        msg_bin += format(ca, '03b')
        msg_bin += format(int(icao, 16), '024b')
        msg_bin += format(tc, '05b')
        msg_bin += format(0, '02b')  # Surveillance status
        msg_bin += format(0, '01b')  # NICsb  
        msg_bin += alt_encoded  # 12 bits
        msg_bin += format(0, '01b')  # Time
        msg_bin += format(time_bit, '01b')  # CPR format
        msg_bin += format(lat_cpr, '017b')
        msg_bin += format(lon_cpr, '017b')
        
        # Total should be 88 bits
        assert len(msg_bin) == 88, f"Expected 88 bits, got {len(msg_bin)}"
        
        msg_hex = format(int(msg_bin, 2), '022X')  # 88 bits = 22 hex chars
        crc = ADSBEncoder.crc(msg_hex, encode=True)
        
        return msg_hex + format(crc, '06X')  # Total 28 hex chars
    
    @staticmethod
    def nl(lat):
        """Calculate NL (number of longitude zones)"""
        if lat == 0:
            return 59
        if abs(lat) >= math.pi / 2:
            return 1
        
        nz = 15
        a = 1 - math.cos(math.pi / (2 * nz))
        b = math.cos(lat) ** 2
        
        if (1 - a / b) <= 0:
            return 1
        
        return int(math.floor(2 * math.pi / math.acos(1 - a / b)))
    
    @staticmethod
    def encode_velocity(icao, speed, heading, vr=0):
        """Encode airborne velocity message (TC 19)"""
        df = 17
        ca = 5
        tc = 19
        st = 1  # Subtype 1 (ground speed)
        
        heading_rad = math.radians(heading)
        v_ew = speed * math.sin(heading_rad)
        v_ns = speed * math.cos(heading_rad)
        
        v_ew_sign = 0 if v_ew >= 0 else 1
        v_ns_sign = 0 if v_ns >= 0 else 1
        v_ew_encoded = min(int(abs(v_ew)) + 1, 1023)
        v_ns_encoded = min(int(abs(v_ns)) + 1, 1023)
        
        vr_sign = 0 if vr >= 0 else 1
        vr_encoded = min(int(abs(vr) / 64) + 1, 511) if vr != 0 else 0
        
        msg_bin = ''
        msg_bin += format(df, '05b')
        msg_bin += format(ca, '03b')
        msg_bin += format(int(icao, 16), '024b')
        msg_bin += format(tc, '05b')
        msg_bin += format(st, '03b')
        msg_bin += format(0, '01b')  # Intent change
        msg_bin += format(0, '01b')  # IFR
        msg_bin += format(0, '03b')  # NUCv
        msg_bin += format(v_ew_sign, '01b')
        msg_bin += format(v_ew_encoded, '010b')
        msg_bin += format(v_ns_sign, '01b')
        msg_bin += format(v_ns_encoded, '010b')
        msg_bin += format(0, '01b')  # VrSrc
        msg_bin += format(vr_sign, '01b')
        msg_bin += format(vr_encoded, '09b')
        msg_bin += format(0, '02b')  # Reserved
        msg_bin += format(0, '01b')  # Diff from baro
        
        # Pad to 88 bits if needed
        while len(msg_bin) < 88:
            msg_bin += '0'
        
        assert len(msg_bin) == 88, f"Expected 88 bits, got {len(msg_bin)}"
        
        msg_hex = format(int(msg_bin, 2), '022X')  # 88 bits = 22 hex chars
        crc = ADSBEncoder.crc(msg_hex, encode=True)
        
        return msg_hex + format(crc, '06X')  # Total 28 hex chars
class Aircraft:
    """Represents a simulated aircraft with realistic flight characteristics"""
    
    def __init__(self, icao, callsign, center_lat, center_lon):
        self.icao = icao
        self.callsign = callsign
        self.center_lat = center_lat
        self.center_lon = center_lon
        
        # Random flight parameters
        self.radius_nm = random.uniform(5, 50)  # Distance from center (5-50 nautical miles)
        self.altitude = random.randint(100, 400) * 100  # 10,000 - 40,000 feet
        self.speed_knots = random.uniform(150, 550)  # 150-550 knots
        self.clockwise = random.choice([True, False])
        
        # Initial position on the circle
        self.angle = random.uniform(0, 2 * math.pi)
        
        # Calculate angular velocity (radians per second)
        # circumference = 2 * pi * radius
        # time for full circle = circumference / speed
        # angular velocity = 2 * pi / time
        circumference_nm = 2 * math.pi * self.radius_nm
        hours_per_circle = circumference_nm / self.speed_knots
        seconds_per_circle = hours_per_circle * 3600
        self.angular_velocity = (2 * math.pi / seconds_per_circle)
        if not self.clockwise:
            self.angular_velocity = -self.angular_velocity
        
        # Last message times for rate limiting
        self.last_position_time = 0
        self.last_velocity_time = 0
        self.last_callsign_time = 0
        
        # Message sequence (alternating even/odd for CPR)
        self.position_frame_even = True
    
    def get_position(self):
        """Calculate current lat/lon based on circular flight path"""
        # Convert radius from nautical miles to degrees (approximately)
        # 1 nautical mile ≈ 1/60 degree of latitude
        radius_deg = self.radius_nm / 60.0
        
        # Calculate position
        lat = self.center_lat + radius_deg * math.sin(self.angle)
        lon = self.center_lon + radius_deg * math.cos(self.angle) / math.cos(math.radians(self.center_lat))
        
        return lat, lon
    
    def update(self, dt):
        """Update aircraft position based on time elapsed"""
        self.angle += self.angular_velocity * dt
        # Keep angle in 0-2π range
        self.angle = self.angle % (2 * math.pi)
    
    def get_heading(self):
        """
        Calculate current heading in degrees.
        
        Position: lat = center + r*sin(angle), lon = center + r*cos(angle)
        Velocity (tangent to circle):
          v_lat = r * angular_velocity * cos(angle)
          v_lon = -r * angular_velocity * sin(angle)
        
        Heading from velocity:
          heading = atan2(v_lon, v_lat)
          
        For clockwise (angular_velocity > 0):
          heading = atan2(-sin(angle), cos(angle)) = angle (in radians) converted
          
        For counter-clockwise (angular_velocity < 0):
          heading = atan2(sin(angle), -cos(angle)) = angle + π
        """
        # Calculate velocity direction (tangent to circle)
        if self.clockwise:
            # Clockwise: velocity perpendicular to radius, rotated 90° clockwise
            v_lat = math.cos(self.angle)
            v_lon = -math.sin(self.angle)
        else:
            # Counter-clockwise: velocity perpendicular to radius, rotated 90° counter-clockwise
            v_lat = -math.cos(self.angle)
            v_lon = math.sin(self.angle)
        
        # Calculate heading from velocity vector
        # atan2(lon, lat) gives bearing in radians
        heading = math.degrees(math.atan2(v_lon, v_lat)) % 360
        
        return heading


class BeastEncoder:
    """Encodes Mode-S messages in Beast binary format"""
    
    ESC = 0x1A  # Escape character for Beast format
    
    @staticmethod
    def encode_message(msg_hex):
        """
        Encode a Mode-S message in Beast binary format
        
        Format: <esc> "3" <6-byte timestamp> <1-byte signal> <14-byte message>
        For short messages (7 bytes): <esc> "2" <6-byte timestamp> <1-byte signal> <7-byte message>
        """
        msg_bytes = bytes.fromhex(msg_hex)
        
        # Determine message type based on length
        if len(msg_bytes) == 7:
            msg_type = 0x32  # '2' for short messages
        elif len(msg_bytes) == 14:
            msg_type = 0x33  # '3' for long messages
        else:
            raise ValueError(f"Invalid message length: {len(msg_bytes)}")
        
        # Generate timestamp (48-bit, 12 MHz counter)
        # For simulation, we'll use current time in microseconds
        timestamp_us = int(time.time() * 1000000)
        timestamp_12mhz = (timestamp_us * 12) & 0xFFFFFFFFFFFF
        timestamp_bytes = timestamp_12mhz.to_bytes(6, byteorder='big')
        
        # Signal level (0-255, we'll use a typical value)
        signal_level = random.randint(150, 250)
        
        # Build the message
        encoded = bytearray()
        encoded.append(BeastEncoder.ESC)
        encoded.append(msg_type)
        
        # Add timestamp (escape any 0x1A bytes)
        for byte in timestamp_bytes:
            if byte == BeastEncoder.ESC:
                encoded.append(BeastEncoder.ESC)
            encoded.append(byte)
        
        # Add signal level (escape if needed)
        if signal_level == BeastEncoder.ESC:
            encoded.append(BeastEncoder.ESC)
        encoded.append(signal_level)
        
        # Add message bytes (escape any 0x1A bytes)
        for byte in msg_bytes:
            if byte == BeastEncoder.ESC:
                encoded.append(BeastEncoder.ESC)
            encoded.append(byte)
        
        return bytes(encoded)


class AircraftSimulator:
    """Main simulator class that manages aircraft and communications"""
    
    def __init__(self, center_lat, center_lon, num_aircraft):
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.num_aircraft = num_aircraft
        self.aircraft = []
        self.socket = None
        self.running = False
        
        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        
        print(f"\nGenerating {num_aircraft} aircraft around position {center_lat:.4f}, {center_lon:.4f}")
        self.generate_aircraft()
    
    def signal_handler(self, signum, frame):
        """Handle Ctrl+C gracefully"""
        print("\n\nShutting down simulator...")
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        sys.exit(0)
    
    def generate_aircraft(self):
        """Generate simulated aircraft with unique ICAOs and callsigns"""
        airlines = ['AAL', 'DAL', 'UAL', 'SWA', 'JBU', 'ASA', 'SKW', 'FFT', 'NKS', 'BAW', 
                   'DLH', 'AFR', 'KLM', 'ACA', 'UAE', 'QTR', 'SIA', 'CPA', 'JAL', 'ANA']
        
        for i in range(self.num_aircraft):
            # Generate unique ICAO address (24-bit)
            icao = f"{random.randint(0, 0xFFFFFF):06X}"
            
            # Generate callsign (8 characters max)
            if random.random() < 0.8:  # 80% airline flights
                airline = random.choice(airlines)
                flight_num = random.randint(1, 9999)
                callsign = f"{airline}{flight_num:04d}"
            else:  # 20% general aviation
                callsign = f"N{random.randint(1, 999):03d}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
            
            aircraft = Aircraft(icao, callsign, self.center_lat, self.center_lon)
            self.aircraft.append(aircraft)
            print(f"  Generated: {callsign:8s} ({icao}) - Alt: {aircraft.altitude:5d}ft, "
                  f"Speed: {aircraft.speed_knots:.0f}kts, Radius: {aircraft.radius_nm:.1f}nm")
    
    def connect_to_dump1090(self, host='127.0.0.1', port=30001, max_retries=5):
        """Connect to dump1090-fa's raw input port"""
        for attempt in range(max_retries):
            try:
                print(f"\nConnecting to dump1090-fa at {host}:{port}...")
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.connect((host, port))
                print("Connected successfully!")
                return True
            except ConnectionRefusedError:
                print(f"Connection refused (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    print("Is dump1090-fa running? Trying again in 2 seconds...")
                    time.sleep(2)
                else:
                    print("\nERROR: Could not connect to dump1090-fa")
                    print("Make sure dump1090-fa is running with network support enabled:")
                    print("  sudo systemctl start dump1090-fa")
                    print("  or: dump1090-fa --net")
                    return False
            except Exception as e:
                print(f"Connection error: {e}")
                return False
        
        return False
    
    def send_message(self, msg_hex):
        """Send a Mode-S message in Beast format"""
        try:
            encoded = BeastEncoder.encode_message(msg_hex)
            self.socket.sendall(encoded)
            return True
        except BrokenPipeError:
            print("\nConnection lost to dump1090-fa. Attempting to reconnect...")
            if self.connect_to_dump1090():
                return self.send_message(msg_hex)
            return False
        except Exception as e:
            print(f"Error sending message: {e}")
            return False
    
    def generate_callsign_message(self, aircraft):
        """Generate aircraft identification message (TC 1-4)"""
        try:
            msg = ADSBEncoder.encode_callsign(aircraft.icao, aircraft.callsign)
            return msg
        except Exception as e:
            print(f"Error generating callsign message: {e}")
            return None
    
    def generate_position_message(self, aircraft):
        """Generate airborne position message (TC 9-18)"""
        try:
            lat, lon = aircraft.get_position()
            alt = aircraft.altitude
            
            # Alternate between even and odd frames for CPR encoding
            time_bit = 0 if aircraft.position_frame_even else 1
            
            msg = ADSBEncoder.encode_position(
                icao=aircraft.icao,
                lat=lat,
                lon=lon,
                alt=alt,
                time_bit=time_bit
            )
            
            # Toggle frame for next message
            aircraft.position_frame_even = not aircraft.position_frame_even
            
            return msg
        except Exception as e:
            print(f"Error generating position message: {e}")
            return None
    
    def generate_velocity_message(self, aircraft):
        """Generate airborne velocity message (TC 19)"""
        try:
            heading = aircraft.get_heading()
            speed = aircraft.speed_knots
            
            msg = ADSBEncoder.encode_velocity(
                icao=aircraft.icao,
                speed=speed,
                heading=heading,
                vr=0  # Vertical rate (0 for level flight)
            )
            
            return msg
        except Exception as e:
            print(f"Error generating velocity message: {e}")
            return None
    
    def run(self):
        """Main simulation loop"""
        if not self.connect_to_dump1090():
            return
        
        self.running = True
        last_time = time.time()
        update_interval = 0.1  # Update every 100ms
        
        print(f"\n{'='*70}")
        print("Simulation started!")
        print(f"{'='*70}")
        print(f"Aircraft count: {self.num_aircraft}")
        print(f"Center position: {self.center_lat:.4f}, {self.center_lon:.4f}")
        print("Press Ctrl+C to stop")
        print(f"{'='*70}\n")
        
        message_count = 0
        start_time = time.time()
        
        try:
            while self.running:
                current_time = time.time()
                dt = current_time - last_time
                last_time = current_time
                
                # Update all aircraft positions
                for aircraft in self.aircraft:
                    aircraft.update(dt)
                    
                    # Send callsign every 10 seconds
                    if current_time - aircraft.last_callsign_time > 10.0:
                        msg = self.generate_callsign_message(aircraft)
                        if msg and self.send_message(msg):
                            aircraft.last_callsign_time = current_time
                            message_count += 1
                    
                    # Send position twice per second (alternating even/odd for CPR)
                    if current_time - aircraft.last_position_time > 0.5:
                        msg = self.generate_position_message(aircraft)
                        if msg and self.send_message(msg):
                            aircraft.last_position_time = current_time
                            message_count += 1
                    
                    # Send velocity every 2 seconds
                    if current_time - aircraft.last_velocity_time > 2.0:
                        msg = self.generate_velocity_message(aircraft)
                        if msg and self.send_message(msg):
                            aircraft.last_velocity_time = current_time
                            message_count += 1
                
                # Print status every 5 seconds
                elapsed = current_time - start_time
                if int(elapsed) % 5 == 0 and dt < 0.2:  # Avoid multiple prints in same second
                    msg_rate = message_count / elapsed if elapsed > 0 else 0
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                          f"Runtime: {elapsed:.0f}s | Messages sent: {message_count} | "
                          f"Rate: {msg_rate:.1f} msg/s")
                
                # Sleep to maintain update rate
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            self.signal_handler(None, None)
        finally:
            if self.socket:
                self.socket.close()


def get_user_input():
    """Get simulation parameters from user"""
    print("="*70)
    print(" Aircraft Traffic Simulator for dump1090-fa")
    print("="*70)
    print()
    
    # Get latitude
    while True:
        try:
            lat_str = input("Enter center latitude (e.g., 33.7490 for Atlanta): ").strip()
            center_lat = float(lat_str)
            if -90 <= center_lat <= 90:
                break
            else:
                print("Latitude must be between -90 and 90")
        except ValueError:
            print("Invalid input. Please enter a number.")
    
    # Get longitude
    while True:
        try:
            lon_str = input("Enter center longitude (e.g., -84.3880 for Atlanta): ").strip()
            center_lon = float(lon_str)
            if -180 <= center_lon <= 180:
                break
            else:
                print("Longitude must be between -180 and 180")
        except ValueError:
            print("Invalid input. Please enter a number.")
    
    # Get number of aircraft
    while True:
        try:
            num_str = input("Enter number of planes to simulate (1-100): ").strip()
            num_aircraft = int(num_str)
            if 1 <= num_aircraft <= 100:
                break
            else:
                print("Number of planes must be between 1 and 100")
        except ValueError:
            print("Invalid input. Please enter a whole number.")
    
    return center_lat, center_lon, num_aircraft


def main():
    """Main entry point"""
    try:
        center_lat, center_lon, num_aircraft = get_user_input()
        simulator = AircraftSimulator(center_lat, center_lon, num_aircraft)
        simulator.run()
    except KeyboardInterrupt:
        print("\n\nShutdown requested. Exiting...")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()