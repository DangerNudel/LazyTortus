#!/usr/bin/env python3
"""
Map Tile Downloader for Offline Use
Downloads OpenStreetMap tiles for a specified area
"""

import os
import sys
import time
import urllib.request
import math

def latlon_to_tile(lat, lon, zoom):
    """Convert lat/lon to tile coordinates"""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def download_tiles(center_lat, center_lon, zoom_levels, radius_tiles=5):
    """
    Download map tiles for offline use
    
    Args:
        center_lat: Center latitude
        center_lon: Center longitude
        zoom_levels: List of zoom levels to download (e.g., [8, 9, 10, 11, 12])
        radius_tiles: Number of tiles to download around center (default 5)
    """
    
    base_dir = "offline_maps"
    os.makedirs(base_dir, exist_ok=True)
    
    total_tiles = 0
    downloaded = 0
    skipped = 0
    
    print("="*70)
    print(" MAP TILE DOWNLOADER FOR OFFLINE USE")
    print("="*70)
    print()
    print(f"Center: {center_lat}, {center_lon}")
    print(f"Zoom levels: {zoom_levels}")
    print(f"Radius: {radius_tiles} tiles")
    print()
    
    # Calculate total tiles
    for zoom in zoom_levels:
        tiles = (2 * radius_tiles + 1) ** 2
        total_tiles += tiles
    
    print(f"Total tiles to download: {total_tiles}")
    print()
    print("Downloading... (this may take a while)")
    print("Press Ctrl+C to stop")
    print()
    
    try:
        for zoom in zoom_levels:
            center_x, center_y = latlon_to_tile(center_lat, center_lon, zoom)
            
            zoom_dir = os.path.join(base_dir, str(zoom))
            os.makedirs(zoom_dir, exist_ok=True)
            
            print(f"Zoom level {zoom}:")
            
            for dx in range(-radius_tiles, radius_tiles + 1):
                x = center_x + dx
                x_dir = os.path.join(zoom_dir, str(x))
                os.makedirs(x_dir, exist_ok=True)
                
                for dy in range(-radius_tiles, radius_tiles + 1):
                    y = center_y + dy
                    
                    # File path
                    tile_file = os.path.join(x_dir, f"{y}.png")
                    
                    if os.path.exists(tile_file):
                        skipped += 1
                        continue
                    
                    # Download tile
                    url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
                    
                    try:
                        # Add delay to respect rate limits
                        time.sleep(0.1)
                        
                        headers = {
                            'User-Agent': 'OfflineMapDownloader/1.0 (Educational/Training Use)'
                        }
                        
                        request = urllib.request.Request(url, headers=headers)
                        with urllib.request.urlopen(request, timeout=10) as response:
                            with open(tile_file, 'wb') as f:
                                f.write(response.read())
                        
                        downloaded += 1
                        
                        # Progress
                        if downloaded % 10 == 0:
                            progress = (downloaded + skipped) / total_tiles * 100
                            print(f"  Progress: {downloaded + skipped}/{total_tiles} ({progress:.1f}%) "
                                  f"[Downloaded: {downloaded}, Skipped: {skipped}]")
                    
                    except Exception as e:
                        print(f"  Error downloading {zoom}/{x}/{y}: {e}")
            
            print(f"  Completed zoom level {zoom}")
            print()
    
    except KeyboardInterrupt:
        print("\n\nDownload interrupted by user")
    
    print()
    print("="*70)
    print(" DOWNLOAD COMPLETE")
    print("="*70)
    print()
    print(f"Total tiles downloaded: {downloaded}")
    print(f"Total tiles skipped (already exist): {skipped}")
    print(f"Total tiles: {downloaded + skipped}")
    print()
    print(f"Map tiles saved to: {os.path.abspath(base_dir)}")
    print()
    print("Next steps:")
    print("1. Copy the 'offline_maps' folder to your offline system")
    print("2. Place it in the same directory as atc_army.py")
    print("3. Run the modified atc_army.py (will serve tiles locally)")
    print()

def main():
    """Main function"""
    
    print("="*70)
    print(" MAP TILE DOWNLOADER")
    print(" Prepare Maps for Offline Use")
    print("="*70)
    print()
    print("This script will download OpenStreetMap tiles for offline use.")
    print("Please use responsibly and respect OpenStreetMap's tile usage policy.")
    print()
    
    # Get parameters
    try:
        lat_input = input("Enter center latitude [33.7490]: ").strip()
        center_lat = float(lat_input) if lat_input else 33.7490
        
        lon_input = input("Enter center longitude [-84.3880]: ").strip()
        center_lon = float(lon_input) if lon_input else -84.3880
        
        print()
        print("Zoom levels determine detail and area covered:")
        print("  8  = Very wide area (state level)")
        print("  9  = Wide area (multiple cities)")
        print("  10 = Large area (city level)")
        print("  11 = Medium area (city district)")
        print("  12 = Detailed area (neighborhood)")
        print("  13 = Very detailed (street level)")
        print()
        
        zoom_input = input("Enter zoom levels (comma-separated) [10,11,12]: ").strip()
        if zoom_input:
            zoom_levels = [int(z.strip()) for z in zoom_input.split(',')]
        else:
            zoom_levels = [10, 11, 12]
        
        radius_input = input("Enter radius in tiles [5]: ").strip()
        radius_tiles = int(radius_input) if radius_input else 5
        
        print()
        print(f"Configuration:")
        print(f"  Center: {center_lat}, {center_lon}")
        print(f"  Zoom levels: {zoom_levels}")
        print(f"  Radius: {radius_tiles} tiles")
        print()
        
        # Calculate coverage
        for zoom in zoom_levels:
            tiles = (2 * radius_tiles + 1) ** 2
            print(f"  Zoom {zoom}: {tiles} tiles (~{tiles * 20 / 1024:.1f} MB)")
        
        print()
        confirm = input("Continue with download? [y/n]: ").strip().lower()
        
        if confirm != 'y':
            print("Download cancelled.")
            return
        
        print()
        download_tiles(center_lat, center_lon, zoom_levels, radius_tiles)
    
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
