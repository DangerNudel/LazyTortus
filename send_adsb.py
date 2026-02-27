#!/usr/bin/env python3
"""
Standalone ADS-B Aircraft Generator

Send custom aircraft to any ADS-B receiver (like US Army ATC system).
No external dependencies required - all encoding built-in.

Usage: python3 send_test_adsb.py
"""

import socket
import time
import random
import math


class ADSBEncoder:
    """Standalone ADS-B message encoder"""
    
    @staticmethod
    def crc(msg, encode=False):
        """Calculate CRC for ADS-B message"""
        generator = 0xFFFA0480  # CRC polynomial for Mode-S
        
        msg_bin = bin(int(msg, 16))[2:].zfill(len(msg) * 4)
        
        if encode:
            msg_bin += '0' * 24
        
        msg_int = int(msg_bin, 2)
        
        for i in range(len(msg_bin) - 24):
            if (msg_int >> (len(msg_bin) - i - 1)) & 1:
                msg_int ^= generator >> i
        
        return msg_int & 0xFFFFFF
    
    @staticmethod
    def nl(lat_rad):
        """Calculate NL (number of longitude zones) for CPR encoding"""
        if abs(lat_rad) >= 1.5707963:  # pi/2
            return 1
        
        nz = 15.0
        a = 1.0 - math.cos(math.pi / (2.0 * nz))
        b = math.cos(lat_rad) ** 2
        
        if 1.0 - a / b <= 0:
            return 1
        
        return int(2.0 * math.pi / (math.acos(1.0 - a / b)))
    
    @staticmethod
    def encode_callsign(icao, callsign):
        """Encode aircraft identification message (TC 1-4)"""
        df = 17
        ca = 5
        tc = 4
        
        # Pad callsign to 8 characters
        callsign = (callsign + '        ')[:8]
        
        # Character encoding table
        charset = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"
        
        encoded_chars = []
        for char in callsign:
            char = char.upper()
            if char in charset:
                encoded_chars.append(charset.index(char))
            else:
                encoded_chars.append(0)
        
        # Build message
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
        # 12-bit format: [7 top bits][Q=1][4 bottom bits]
        top_bits = (alt_code >> 4) & 0x7F
        bottom_bits = alt_code & 0x0F
        alt_encoded_int = (top_bits << 5) | (1 << 4) | bottom_bits
        alt_encoded = format(alt_encoded_int, '012b')
        
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
        
        # Build message
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
        
        # Total: 88 bits
        msg_hex = format(int(msg_bin, 2), '022X')
        crc = ADSBEncoder.crc(msg_hex, encode=True)
        
        return msg_hex + format(crc, '06X')
    
    @staticmethod
    def encode_velocity(icao, speed, heading, vr):
        """Encode airborne velocity message (TC 19)"""
        df = 17
        ca = 5
        tc = 19
        st = 1  # Subtype 1: ground speed
        
        # Calculate velocity components
        heading_rad = heading * math.pi / 180
        v_ew = speed * math.sin(heading_rad)
        v_ns = speed * math.cos(heading_rad)
        
        # Encode velocities
        ew_sign = 1 if v_ew < 0 else 0
        ns_sign = 1 if v_ns < 0 else 0
        
        ew_vel = int(abs(v_ew)) + 1
        ns_vel = int(abs(v_ns)) + 1
        
        # Encode vertical rate
        vr_sign = 1 if vr < 0 else 0
        vr_val = int(abs(vr) / 64) + 1
        
        # Build message
        msg_bin = ''
        msg_bin += format(df, '05b')
        msg_bin += format(ca, '03b')
        msg_bin += format(int(icao, 16), '024b')
        msg_bin += format(tc, '05b')
        msg_bin += format(st, '03b')
        msg_bin += format(0, '01b')  # Intent change
        msg_bin += format(0, '01b')  # IFR capability (reserved)
        msg_bin += format(0, '03b')  # Navigation uncertainty
        msg_bin += format(ew_sign, '01b')
        msg_bin += format(ew_vel, '010b')
        msg_bin += format(ns_sign, '01b')
        msg_bin += format(ns_vel, '010b')
        msg_bin += format(0, '01b')  # Vertical rate source
        msg_bin += format(vr_sign, '01b')
        msg_bin += format(vr_val, '09b')
        msg_bin += format(0, '02b')  # Reserved
        msg_bin += format(0, '01b')  # GNSS sign
        msg_bin += format(0, '07b')  # GNSS height difference
        
        # Total should be 88 bits
        assert len(msg_bin) == 88, "Expected 88 bits, got {}".format(len(msg_bin))
        
        msg_hex = format(int(msg_bin, 2), '022X')
        crc = ADSBEncoder.crc(msg_hex, encode=True)
        
        return msg_hex + format(crc, '06X')


def main():
    """Main function"""
    
    print("="*70)
    print(" STANDALONE ADS-B AIRCRAFT GENERATOR")
    print(" Send Custom Aircraft to Any ADS-B Receiver")
    print("="*70)
    print()
    
    # Get ATC system IP address
    default_ip = "127.0.0.1"
    ip_input = input("Enter ATC system IP address [{}]: ".format(default_ip)).strip()
    atc_ip = ip_input if ip_input else default_ip
    
    # Get ATC system port
    default_port = "30001"
    port_input = input("Enter ATC system port [{}]: ".format(default_port)).strip()
    try:
        atc_port = int(port_input) if port_input else int(default_port)
    except ValueError:
        print("Invalid port, using {}".format(default_port))
        atc_port = int(default_port)
    
    print()
    
    # Get center position
    try:
        lat = float(input("Enter center latitude (e.g., 33.7490): ").strip())
        if not (-90 <= lat <= 90):
            print("Invalid latitude")
            return
    except ValueError:
        print("Invalid latitude")
        return
    
    try:
        lon = float(input("Enter center longitude (e.g., -84.3880): ").strip())
        if not (-180 <= lon <= 180):
            print("Invalid longitude")
            return
    except ValueError:
        print("Invalid longitude")
        return
    
    # Get number of aircraft
    try:
        num_aircraft = int(input("How many aircraft to generate (1-20): ").strip())
        if not (1 <= num_aircraft <= 20):
            print("Must be between 1 and 20")
            return
    except ValueError:
        print("Invalid number")
        return
    
    # Get aircraft type
    aircraft_type = input("Aircraft type (civilian/military/mixed) [mixed]: ").strip().lower()
    if not aircraft_type:
        aircraft_type = "mixed"
    if aircraft_type not in ['civilian', 'military', 'mixed']:
        print("Invalid type. Using 'mixed'")
        aircraft_type = "mixed"
    
    print()
    print("Generating {} {} aircraft...".format(num_aircraft, aircraft_type))
    print("Target: {}:{}".format(atc_ip, atc_port))
    print("-" * 70)
    print()
    
    # Connect to ATC
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((atc_ip, atc_port))
        print("✓ Connected to ATC system at {}:{}".format(atc_ip, atc_port))
        print()
    except Exception as e:
        print("✗ Could not connect to ATC system: {}".format(e))
        print("Make sure ATC system is running at {}:{}".format(atc_ip, atc_port))
        return
    
    # Generate aircraft data with unique parameters
    aircraft_data = []
    for i in range(num_aircraft):
        # Generate unique ICAO (valid hex)
        icao = "{:06X}".format(0xABC000 + i)
        
        # Determine type
        if aircraft_type == "civilian":
            is_military = False
        elif aircraft_type == "military":
            is_military = True
        else:  # mixed
            is_military = random.random() < 0.4
        
        # Generate UNIQUE callsign for each aircraft
        if is_military:
            callsigns = ["VIPER", "SNAKE", "EAGLE", "HAWK", "RAVEN", "GHOST", "SABER", "TALON", 
                        "COBRA", "FALCON", "JAGUAR", "PANTHER", "TIGER", "WOLF", "RAPTOR", "HORNET"]
            base = random.choice(callsigns)
            callsign = "{}{:02d}".format(base, random.randint(1, 99))
        else:
            airlines = ["AAL", "DAL", "UAL", "SWA", "JBU", "ASA", "FFT", "SKW", 
                       "NKS", "BAW", "AFR", "DLH", "ACA", "QFA", "EIN", "KLM"]
            airline = random.choice(airlines)
            callsign = "{}{}".format(airline, random.randint(1000, 9999))
        
        # Generate UNIQUE flight parameters for straight-line flight
        # Random starting position around center
        lat_offset = random.uniform(-0.5, 0.5)
        lon_offset = random.uniform(-0.5, 0.5)
        start_lat = lat + lat_offset
        start_lon = lon + lon_offset
        
        # Random heading (direction of flight)
        heading = random.uniform(0, 360)
        
        # Random speed
        speed = random.randint(250, 550)  # knots
        
        # Random altitude
        altitude = random.randint(100, 400) * 100  # 10,000-40,000 ft
        
        aircraft_data.append({
            'icao': icao,
            'callsign': callsign,
            'lat': start_lat,
            'lon': start_lon,
            'heading': heading,  # Fixed heading - fly straight
            'speed': speed,
            'altitude': altitude,
            'is_military': is_military,
            'position_frame_even': True  # For alternating frames
        })
        
        # Calculate initial position (already set above)
        aircraft_lat = start_lat
        aircraft_lon = start_lon
        
        # Send initial messages for this aircraft
        type_str = "MIL" if is_military else "CIV"
        print("  [{:2d}] {:8s} ({}) - {} - {:7.3f}, {:8.3f}, {:5d}ft, {:3d}kts, {:3.0f}°".format(
            i+1, callsign, icao, type_str, aircraft_lat, aircraft_lon, altitude, speed, heading))
        
        try:
            # Send callsign
            msg = ADSBEncoder.encode_callsign(icao, callsign)
            sock.sendall("*{};\n".format(msg).encode('ascii'))
            time.sleep(0.02)
            
            # Send position even
            msg = ADSBEncoder.encode_position(icao, aircraft_lat, aircraft_lon, altitude, 0)
            sock.sendall("*{};\n".format(msg).encode('ascii'))
            time.sleep(0.02)
            
            # Send position odd
            msg = ADSBEncoder.encode_position(icao, aircraft_lat, aircraft_lon, altitude, 1)
            sock.sendall("*{};\n".format(msg).encode('ascii'))
            time.sleep(0.02)
            
            # Send velocity
            msg = ADSBEncoder.encode_velocity(icao, speed, heading, 0)
            sock.sendall("*{};\n".format(msg).encode('ascii'))
            time.sleep(0.02)
            
        except Exception as e:
            print("  ✗ Error sending aircraft {}: {}".format(i+1, e))
    
    print()
    print("="*70)
    print(" {} Aircraft Sent - Starting Continuous Simulation".format(num_aircraft))
    print("="*70)
    print()
    print("Simulating realistic air traffic with moving aircraft")
    print("Press Ctrl+C to stop")
    print()
    
    # Start continuous simulation automatically
    try:
        start_time = time.time()
        last_update = start_time
        update_count = 0
        
        print("{:<8} {:<10} {:<12} {:<28} {:<8} {:<15}".format(
            'Time', 'Updates', 'Aircraft', 'Position', 'Alt', 'Course'))
        print("-" * 95)
        
        while True:
            current_time = time.time()
            dt = current_time - last_update
            last_update = current_time
            
            # Update each aircraft position
            for ac in aircraft_data:
                # Move aircraft in straight line based on heading and speed
                # Convert speed from knots to degrees per second (approximate)
                # 1 knot ≈ 1.852 km/h, 1 degree lat ≈ 111 km
                # So: degrees/sec = (knots * 1.852) / (111 * 3600)
                speed_deg_per_sec = (ac['speed'] * 1.852) / (111 * 3600)
                
                # Calculate distance traveled in this time step
                distance = speed_deg_per_sec * dt
                
                # Convert heading to radians
                heading_rad = math.radians(ac['heading'])
                
                # Update position (move in direction of heading)
                # Heading 0° = North, 90° = East, 180° = South, 270° = West
                ac['lat'] += distance * math.cos(heading_rad)  # North/South component
                ac['lon'] += distance * math.sin(heading_rad) / math.cos(math.radians(ac['lat']))  # East/West component (adjust for latitude)
                
                # Wrap around if aircraft goes off map (optional - creates continuous traffic)
                # If aircraft goes too far, respawn on opposite side
                if abs(ac['lat'] - lat) > 1.0:  # More than ~60 nm from center
                    # Respawn on opposite side
                    if ac['lat'] > lat:
                        ac['lat'] = lat - 0.9
                    else:
                        ac['lat'] = lat + 0.9
                
                if abs(ac['lon'] - lon) > 1.0:
                    if ac['lon'] > lon:
                        ac['lon'] = lon - 0.9
                    else:
                        ac['lon'] = lon + 0.9
                
                # Get current position
                aircraft_lat = ac['lat']
                aircraft_lon = ac['lon']
                heading = ac['heading']  # Heading stays constant for straight flight
                
                # Send callsign every 10 updates
                if update_count % 10 == 0:
                    msg = ADSBEncoder.encode_callsign(ac['icao'], ac['callsign'])
                    sock.sendall("*{};\n".format(msg).encode('ascii'))
                    time.sleep(0.01)
                
                # Send position (alternate even/odd frames) EVERY update
                time_bit = 0 if ac['position_frame_even'] else 1
                msg = ADSBEncoder.encode_position(ac['icao'], aircraft_lat, aircraft_lon, 
                                                 ac['altitude'], time_bit)
                sock.sendall("*{};\n".format(msg).encode('ascii'))
                time.sleep(0.01)
                ac['position_frame_even'] = not ac['position_frame_even']
                
                # Send velocity EVERY update (not just every 2)
                msg = ADSBEncoder.encode_velocity(ac['icao'], ac['speed'], heading, 0)
                sock.sendall("*{};\n".format(msg).encode('ascii'))
                time.sleep(0.01)
            
            update_count += 1
            
            # Print status every 5 updates - show DIFFERENT aircraft each time
            if update_count % 5 == 0:
                elapsed = int(current_time - start_time)
                minutes, seconds = divmod(elapsed, 60)
                
                # Cycle through different aircraft for display
                display_idx = (update_count // 5) % len(aircraft_data)
                ac = aircraft_data[display_idx]
                
                aircraft_lat = ac['lat']
                aircraft_lon = ac['lon']
                heading = ac['heading']
                
                print("{:02d}:{:02d}    {:<10} {:<12} {:7.3f}, {:8.3f}   {:<8} {:3d}kts → {:3.0f}°".format(
                    minutes, seconds, update_count, ac['callsign'], aircraft_lat, aircraft_lon, 
                    ac['altitude'], ac['speed'], heading))
            
            # Sleep for 1 second between updates
            time.sleep(1.0)
    
    except KeyboardInterrupt:
        print("\n")
        print("="*70)
        print(" SIMULATION STOPPED")
        print("="*70)
        elapsed = int(time.time() - start_time)
        minutes, seconds = divmod(elapsed, 60)
        print("Runtime: {:02d}:{:02d}".format(minutes, seconds))
        print("Updates sent: {}".format(update_count))
        print("Total messages: ~{}".format(update_count * num_aircraft * 2))
        print()
    except Exception as e:
        print("\nError: {}".format(e))
        import traceback
        traceback.print_exc()
    finally:
        try:
            sock.close()
        except:
            pass
    
    print("Done!")
    print()


if __name__ == "__main__":
    main()
