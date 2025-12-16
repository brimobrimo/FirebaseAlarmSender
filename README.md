# Firebase Alarm Sender

A high-performance Python application that monitors vessel positions and sends push notifications via Firebase Cloud Messaging (FCM) when vessels meet specific alarm conditions.

## Overview

This system integrates Firebase Firestore for alarm configuration storage with a MariaDB database containing real-time vessel position data (AIS). It processes user-defined alarms in parallel and sends notifications to mobile devices when vessels enter or exit defined radius zones.

## Features

- **Parallel Processing**: Handles multiple alarms simultaneously using ThreadPoolExecutor
- **Database Connection Pooling**: Efficient MariaDB connection management for high-throughput operations
- **Firebase Integration**:
  - Firestore for alarm configuration storage
  - FCM for push notifications to iOS/Android devices
- **Radius-based Alerts**: Support for both "inside_radius" and "outside_radius" alarm modes
- **Real-time Position Checking**: Uses Haversine formula for accurate distance calculations
- **Automatic Retries**: Connection pool handles failed connections gracefully

## Architecture

```
┌─────────────────┐
│   Firebase      │
│   Firestore     │◄─── Read alarm configurations
│  (users/alarms) │
└─────────────────┘
         │
         ▼
┌─────────────────┐       ┌──────────────────┐
│ FirebaseAlarm   │◄─────►│ MariaDB          │
│ SenderFast.py   │       │ (Vessel AIS Data)│
└─────────────────┘       └──────────────────┘
         │
         ▼
┌─────────────────┐
│   Firebase      │
│   Cloud         │◄─── Send notifications
│   Messaging     │
└─────────────────┘
```

## Prerequisites

- Python 3.7+
- MariaDB/MySQL database with AIS vessel data
- Firebase project with:
  - Firestore database enabled
  - FCM configured for mobile apps
  - Service account key (JSON file)

## Installation

1. Install required Python packages:

```bash
pip install firebase-admin pymysql
```

2. Place your Firebase service account key in the project directory:
   - File should be named: `serviceAccountKey_trackaship-live-marine-traffic-firebase-adminsdk-r6k95-642eb778c2.json`
   - Or update `CREDENTIALS_FILENAME` in the script

3. Configure database credentials in `alarmChecker.py`:

```python
DB_HOST = "127.0.0.1"
DB_USER = "your_db_user"
DB_PASSWORD = "your_db_password"
DB_NAME = "vesselinfo"
```

## Configuration

### Performance Settings

```python
MAX_WORKERS = 10      # Number of parallel message sends
DB_POOL_SIZE = 20     # Database connection pool size (should be >= MAX_WORKERS)
```

### Firestore Document Structure

Each alarm document should contain:

```javascript
{
  "vesselMMSI": "210387000",           // Required: Vessel identifier
  "name": "Alert for RIX MELODY",      // Required: Alert name
  "FCMDeviceToken": "device_token...", // Required: FCM token for notifications
  "mode": "inside_radius",             // Required: "inside_radius" or "outside_radius"
  "center": {                          // Required: Geographic center point
    "lat": 55.690240870687866,
    "lon": 12.709938422375608
  },
  "radiusMeters": 5000.0               // Required: Radius in meters
}
```

### Firestore Path Structure

```
users/{userId}/alarms/{alarmId}
```

## Usage

### Run the script:

```bash
python FirebaseAlarmSenderFast.py
```

### Diagnostic Mode

For testing, configure specific user/alarm IDs:

```python
TEST_USER_ID = 'PRFzKRIJGbSsrwC60ic9ifU9qsC3'
TEST_ALARM_ID = 'JMYvcXgjTZUOdKcx6OUU'
```

The script will process this user first and verify all fields are correctly configured.

## How It Works

### 1. Initialization Phase
- Creates database connection pool (20 connections by default)
- Initializes Firebase Admin SDK
- Connects to Firestore

### 2. Diagnostic Phase
- Reads test alarm document
- Verifies all required fields are present
- Tests database connection and vessel position lookup
- Checks if vessel is within/outside specified radius

### 3. Processing Phase
- Scans all users in Firestore
- Collects all alarms for each user
- For "inside_radius" alarms:
  - Queries MariaDB for latest vessel position
  - Calculates distance using Haversine formula
  - Determines if alarm condition is met
- Batches notifications for parallel sending

### 4. Notification Phase
- Sends FCM notifications in parallel (10 workers by default)
- Handles token validation errors
- Reports success/failure statistics

### 5. Cleanup Phase
- Closes all database connections
- Releases resources

## Database Connection Pool

The connection pool provides:

- **Pre-initialized Connections**: 20 connections ready at startup
- **Thread Safety**: Multiple workers can safely acquire/release connections
- **Health Checks**: Validates connections before reuse with `ping()`
- **Auto-recovery**: Creates new connections if pool is exhausted
- **Proper Cleanup**: Closes all connections on shutdown

Example usage:

```python
# Get connection from pool
db_conn = db_pool.get_connection()
try:
    cursor = db_conn.cursor()
    # Use cursor for queries
    cursor.close()
finally:
    # Always return connection to pool
    db_pool.return_connection(db_conn)
```

## MariaDB Schema

The script expects an `aivdm` table with vessel positions:

```sql
CREATE TABLE aivdm (
    mmsi VARCHAR(20),
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    unix_time BIGINT,
    INDEX idx_mmsi_time (mmsi, unix_time)
);
```

## Notification Payload

FCM messages include:

**Notification:**
- Title: "🚨 Ship Alert: {alertName} Detected!"
- Body: "Vessel MMSI: {vesselMMSI}. This is a critical alert for the vessel you are tracking."

**Data Payload:**
```json
{
  "vesselMMSI": "210387000",
  "alertName": "Alert for RIX MELODY",
  "timestamp": "1702741234"
}
```

**iOS APNS Configuration:**
- Sound: "default"
- Badge: 1
- Content Available: true

## Error Handling

The script handles:

- Missing Firestore documents
- Invalid FCM tokens
- Database connection failures
- Missing required fields in alarm documents
- Vessel position not found in database

## Output Example

```
Initializing database connection pool (size: 20)...
Database connection pool initialized with 20 connections.
--- Firebase Admin SDK Initialized Successfully ---

--- Running Diagnostic Read Test ---
SUCCESS: Found document at 'users/PRFzKRIJGbSsrwC60ic9ifU9qsC3/alarms/JMYvcXgjTZUOdKcx6OUU'
  > Required fields found: MMSI='210387000', Name='Alert for RIX MELODY', Token present
  > Mode='inside_radius', Center=({'lat': 55.69, 'lon': 12.71}), Radius=5000.0 meters
  > Alert mode is 'inside_radius'. Checking if ship is within radius...
  > Distance for MMSI 210387000: 3245.67 meters
  > Ship with MMSI 210387000 IS within the radius of 5000 meters.

--- Starting Parallel Alert Processing ---
Max parallel workers: 10

Processing Target User: PRFzKRIJGbSsrwC60ic9ifU9qsC3
  > User PRFzKRIJGbSsrwC60ic9ifU9qsC3: Found 3 alert(s)

============================================================
  >> Sending 3 messages in parallel (max 10 workers)...
  [RESULT] Sent: 3, Failed: 0 (Invalid tokens: 0)

============================================================
--- Processing Complete ---
============================================================
Users processed:        1
Alerts checked:         3
Messages sent:          3
Messages failed:        0
Skipped (invalid data): 0
============================================================

Total execution time: 1.23 seconds
```

## Performance Considerations

- **Connection Pool Size**: Set `DB_POOL_SIZE` to at least equal `MAX_WORKERS`
- **Worker Count**: Adjust `MAX_WORKERS` based on FCM rate limits and server capacity
- **Database Indexes**: Ensure `aivdm` table has index on `(mmsi, unix_time)`
- **Firestore Reads**: Each alarm requires 1 read operation

## Files

- **FirebaseAlarmSenderFast.py**: Main application with parallel processing and connection pooling
- **alarmChecker.py**: Database connection and distance calculation utilities
  - `get_connection()`: Creates MariaDB connection
  - `haversine()`: Calculates distance between two coordinates
  - `is_ship_within_radius()`: Checks if vessel is within specified radius

## Troubleshooting

**"CRITICAL ERROR: Credentials file not found"**
- Ensure Firebase service account key is in the correct location
- Check filename matches `CREDENTIALS_FILENAME` variable

**"No position found for MMSI"**
- Verify vessel MMSI exists in MariaDB `aivdm` table
- Check database connection is working

**"Pool exhausted, creating new connection"**
- Increase `DB_POOL_SIZE` if this warning appears frequently
- Current load exceeds pool capacity

**FCM token errors**
- Invalid tokens are counted and reported
- Users may need to re-register their devices

## Security Notes

- Keep Firebase service account key secure (never commit to git)
- Use environment variables for database credentials in production
- Implement proper authentication for production deployments
- Consider rate limiting to prevent abuse

## License

This project is proprietary software.
