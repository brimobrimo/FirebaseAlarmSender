#!/usr/bin/env python3
"""
MariaDB Database Connection Module for Alarm Checker
"""

import pymysql
import math

# Database credentials
DB_HOST = "127.0.0.1"
DB_USER = "iphone_user"
DB_PASSWORD = "shipaholic"
DB_NAME = "vesselinfo"


def get_connection():
    """Create and return a database connection."""
    try:
        connection = pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        return connection
    except pymysql.Error as e:
        print(f"Error connecting to MariaDB: {e}")
        raise


def haversine(lat1, lon1, lat2, lon2):
    # Earth radius in meters
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_ship_within_radius(cursor, mmsi, center_lat, center_lon, radius_m, closer=True):
    """
    Checks if a ship is within (closer=True) or outside (closer=False) a radius in meters from a point.
    """
    cursor.execute(
        "SELECT latitude, longitude FROM aivdm WHERE mmsi=%s ORDER BY unix_time DESC LIMIT 1",
        (mmsi,)
    )
    row = cursor.fetchone()
    if not row:
        print("No position found for MMSI", mmsi)
        return False
    ship_lat, ship_lon = row['latitude'], row['longitude']
    distance = haversine(center_lat, center_lon, ship_lat, ship_lon)
    print(f"Distance for MMSI {mmsi}: {distance} meters")
    return distance < radius_m if closer else distance > radius_m

def main():
    """Test the database connection."""
    try:
        conn = get_connection()
        print("Successfully connected to MariaDB!")
        
        with conn.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            result = cursor.fetchone()
            print(f"Database version: {result}")
        
        with conn.cursor() as cursor:
            try:
                with conn.cursor() as cursor:
                    result = is_ship_within_radius(cursor, 246571000, 55.757911, 12.453396, 13000)
                    if result:
                        print("Ship is within radius.")
                    else:
                        print("Ship is outside radius.")
            finally:
                conn.close()
                
        # conn.close()
        print("Connection closed.")
        
    except Exception as e:
        print(f"Failed to connect: {e}")


if __name__ == "__main__":
    main()
