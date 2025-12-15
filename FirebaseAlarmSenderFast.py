import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from firebase_admin import messaging
import time
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---

# IMPORTANT: Ensure this file is in the same directory as this script.
CREDENTIALS_FILENAME = 'serviceAccountKey_trackaship-live-marine-traffic-firebase-adminsdk-r6k95-642eb778c2.json' 
CREDENTIALS_PATH = f'./{CREDENTIALS_FILENAME}'

# Define the push notification payload TEMPLATE
# The title and body will now be formatted dynamically with alert data.
NOTIFICATION_TITLE_TEMPLATE = "🚨 Ship Alert: {alertName} Detected!"
NOTIFICATION_BODY_TEMPLATE = "Vessel MMSI: {vesselMMSI}. This is a critical alert for the vessel you are tracking."
NOTIFICATION_DATA_TEMPLATE = {
    # These fields are required for the client application to handle the navigation/action
    "vesselMMSI": "", 
    "alertName": "",
    "timestamp": str(int(time.time()))
}

# Define the Firestore collection structure
FULL_USERS_COLLECTION_PATH = 'users' 
ALERTS_SUBCOLLECTION = 'alarms'

# --- FIELD NAMES FROM FIRESTORE DOCUMENT ---
FCM_TOKEN_FIELD = 'FCMDeviceToken' # Field containing the device token
MMSI_FIELD = 'vesselMMSI'          # Field containing the vessel MMSI
SHIP_NAME_FIELD = 'name'           # Field containing the alert name/ship name
MODE = 'mode'                     # Field containing the alert mode, outside_radius pr inside_radius
RADIUS_METERS = 'radiusMeters'  # Field containing the radius in meters
# Need lat and lon fields

# DIAGNOSTIC CONFIGURATION (Adjust these to match your current test user/alarm)
TEST_USER_ID = 'PRFzKRIJGbSsrwC60ic9ifU9qsC3' 
TEST_ALARM_ID = '1u40ZCLzvSIDitkUYIM5' 

# Performance configuration
MAX_WORKERS = 10  # Number of parallel message sends

# --- INITIALIZATION ---

def initialize_firebase_app():
    """Initializes the Firebase Admin SDK by loading the service account key locally."""
    
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"CRITICAL ERROR: Credentials file not found at '{CREDENTIALS_PATH}'.")
        print(f"Please save your service account key as '{CREDENTIALS_FILENAME}' in this directory.")
        return None
        
    project_id = None
    try:
        # Load the JSON content to extract the project ID
        with open(CREDENTIALS_PATH, 'r') as f:
            cred_data = json.load(f)
            project_id = cred_data.get('project_id')
        
        print(f"Service account key loaded from: {CREDENTIALS_PATH}")
        print(f"Extracted Project ID: {project_id}")

    except Exception as e:
        print(f"CRITICAL ERROR: Failed to read or parse the JSON file. Details: {e}")
        return None

    # Check if the app is already initialized (important for notebooks/environments that persist state)
    default_app = None
    try:
        default_app = firebase_admin.get_app()
    except ValueError:
        pass # App is not initialized yet

    if default_app:
        print("--- Firebase Admin SDK already initialized. Reusing existing client. ---")
        db = firestore.client(app=default_app)
        return db

    try:
        cred = credentials.Certificate(CREDENTIALS_PATH)
        
        firebase_admin.initialize_app(cred, {'projectId': project_id})
        print("--- Firebase Admin SDK Initialized Successfully ---")
        
        # Explicitly target the default database instance
        db = firestore.client(app=firebase_admin.get_app())
        return db
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to initialize Firebase Admin SDK.")
        print(f"Details: {e}")
        return None
    
# --- DIAGNOSTIC CHECK ---

def test_read_access(db):
    """
    Tests if the Admin SDK can read a known alarm document and check for the required fields.
    """
    if not TEST_USER_ID or not TEST_ALARM_ID:
        print("\n--- Skipping direct read test: TEST IDs are missing. ---")
        return True
        
    print(f"\n--- Running Diagnostic Read Test ---")
    doc_path = f"{FULL_USERS_COLLECTION_PATH}/{TEST_USER_ID}/{ALERTS_SUBCOLLECTION}/{TEST_ALARM_ID}"
    doc_ref = db.document(doc_path)
    
    try:
        doc = doc_ref.get()
        if doc.exists:
            print(f"SUCCESS: Found document at '{doc_path}'. Read access confirmed.")
            
            data = doc.to_dict()
            mmsi = data.get(MMSI_FIELD)
            alert_name = data.get(SHIP_NAME_FIELD)
            token = data.get(FCM_TOKEN_FIELD)
            mode = data.get(MODE)
            
            if mmsi and alert_name and token:
                print(f"  > Required fields found: MMSI='{mmsi}', Name='{alert_name}', Token present.")
                return True
            else:
                 print(f"FAILURE: Document found, but missing required fields ('{MMSI_FIELD}', '{SHIP_NAME_FIELD}', or '{FCM_TOKEN_FIELD}').")
                 return False
        else:
            print(f"FAILURE: Cannot find document at known path '{doc_path}'.")
            return False
    except Exception as e:
        print(f"CRITICAL FAILURE: Error during read attempt: {e}")
        print("  This is usually due to an Admin SDK permission issue (e.g., service account role is too restrictive).")
        return False


# --- MESSAGE SENDING LOGIC (PARALLEL) ---

def send_single_fcm_message(message_data):
    """Send a single FCM message. Designed to be called in parallel."""
    token, mmsi, alert_name, alert_id = message_data
    
    # Format the dynamic content
    title = NOTIFICATION_TITLE_TEMPLATE.format(alertName=alert_name)
    body = NOTIFICATION_BODY_TEMPLATE.format(vesselMMSI=mmsi)
    
    # Populate the data payload
    data_payload = NOTIFICATION_DATA_TEMPLATE.copy()
    data_payload["vesselMMSI"] = str(mmsi)
    data_payload["alertName"] = alert_name
    data_payload["timestamp"] = str(int(time.time()))

    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        data=data_payload,
        token=token,
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(
                        title=title,
                        body=body
                    ),
                    sound="default",
                    badge=1,
                    content_available=True, 
                ),
            ),
        ),
    )

    try:
        response = messaging.send(message)
        return {'success': True, 'alert_name': alert_name, 'mmsi': mmsi, 'response': response}
    except Exception as e:
        error_msg = str(e)
        is_invalid_token = 'not registered' in error_msg.lower() or 'invalid registration' in error_msg.lower()
        return {
            'success': False, 
            'alert_name': alert_name, 
            'mmsi': mmsi, 
            'error': error_msg,
            'invalid_token': is_invalid_token
        }

def send_messages_parallel(messages_to_send):
    """Send multiple messages in parallel using ThreadPoolExecutor."""
    if not messages_to_send:
        return 0, 0
    
    success_count = 0
    failure_count = 0
    invalid_token_count = 0
    
    print(f"  >> Sending {len(messages_to_send)} messages in parallel (max {MAX_WORKERS} workers)...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        future_to_message = {executor.submit(send_single_fcm_message, msg): msg for msg in messages_to_send}
        
        # Process results as they complete
        for future in as_completed(future_to_message):
            result = future.result()
            
            if result['success']:
                success_count += 1
            else:
                failure_count += 1
                if result.get('invalid_token'):
                    invalid_token_count += 1
                else:
                    print(f"     [FAILURE] {result['alert_name']} (MMSI: {result['mmsi']}): {result['error']}")
    
    print(f"  [RESULT] Sent: {success_count}, Failed: {failure_count} (Invalid tokens: {invalid_token_count})")
    return success_count, failure_count

def process_user_alerts_collect(db, user_id, messages_to_send, stats):
    """Helper function to collect alerts for a single user."""
    alerts_ref = db.collection(FULL_USERS_COLLECTION_PATH).document(user_id).collection(ALERTS_SUBCOLLECTION)
    alerts_found_for_user = 0
    
    try:
        alerts_stream = alerts_ref.stream()
    except Exception as e:
        print(f"  WARNING: Could not access alerts subcollection for user {user_id}. Details: {e}")
        return messages_to_send, stats

    for alert_doc in alerts_stream:
        alert_id = alert_doc.id
        alert_data = alert_doc.to_dict()
        stats['total_alerts_checked'] += 1
        alerts_found_for_user += 1
        
        fcm_token = alert_data.get(FCM_TOKEN_FIELD)
        mmsi = alert_data.get(MMSI_FIELD)
        alert_name = alert_data.get(SHIP_NAME_FIELD)
        
        if fcm_token and mmsi and alert_name:
            # Add to messages list (token, mmsi, alert_name, alert_id)
            messages_to_send.append((fcm_token, mmsi, alert_name, alert_id))
        else:
            missing = []
            if not fcm_token: missing.append(FCM_TOKEN_FIELD)
            if not mmsi: missing.append(MMSI_FIELD)
            if not alert_name: missing.append(SHIP_NAME_FIELD)
            stats['skipped_invalid'] += 1

    if alerts_found_for_user > 0:
        print(f"  > User {user_id}: Found {alerts_found_for_user} alert(s)")
    
    return messages_to_send, stats

def process_all_alerts(db):
    """
    Iterates through all users and all of their alerts, sending notifications in parallel.
    """
    print(f"\n--- Starting Parallel Alert Processing ---")
    print(f"Max parallel workers: {MAX_WORKERS}")
    
    stats = {
        'total_users_processed': 0,
        'total_alerts_checked': 0,
        'total_sent': 0,
        'total_failed': 0,
        'skipped_invalid': 0,
    }
    
    messages_to_send = []
    processed_user_ids = set()

    # --- Step 1: Process the test user first (if configured) ---
    if TEST_USER_ID:
        print(f"\nProcessing Target User (Diagnostic): {TEST_USER_ID}")
        messages_to_send, stats = process_user_alerts_collect(
            db, TEST_USER_ID, messages_to_send, stats
        )
        stats['total_users_processed'] += 1
        processed_user_ids.add(TEST_USER_ID)

    # --- Step 2: Scan all other users ---
    users_ref = db.collection(FULL_USERS_COLLECTION_PATH)
    
    try:
        users_stream = users_ref.stream()
    except Exception as e:
        print(f"CRITICAL ERROR: Could not access '{FULL_USERS_COLLECTION_PATH}' collection.")
        print(f"Details: {e}")
        return

    for user_doc in users_stream:
        user_id = user_doc.id
        
        # Skip if already processed
        if user_id in processed_user_ids:
            continue
            
        stats['total_users_processed'] += 1
        print(f"\nProcessing User: {user_id}")
        
        messages_to_send, stats = process_user_alerts_collect(
            db, user_id, messages_to_send, stats
        )

    # --- Step 3: Send all collected messages in parallel ---
    if messages_to_send:
        print(f"\n{'='*60}")
        success, failure = send_messages_parallel(messages_to_send)
        stats['total_sent'] = success
        stats['total_failed'] = failure

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"--- Processing Complete ---")
    print(f"{'='*60}")
    print(f"Users processed:        {stats['total_users_processed']}")
    print(f"Alerts checked:         {stats['total_alerts_checked']}")
    print(f"Messages sent:          {stats['total_sent']}")
    print(f"Messages failed:        {stats['total_failed']}")
    print(f"Skipped (invalid data): {stats['skipped_invalid']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    
    # 1. Run initialization and get the Firestore client
    firestore_client = initialize_firebase_app()
    
    if firestore_client:
        # 2. Run diagnostic check
        if test_read_access(firestore_client):
            # 3. Process all data and send notifications in parallel
            start_time = time.time()
            process_all_alerts(firestore_client)
            elapsed_time = time.time() - start_time
            print(f"\nTotal execution time: {elapsed_time:.2f} seconds")
        else:
            print("\nFATAL ERROR: Access test failed. Cannot proceed with alert processing.")
    
    print("\nScript finished execution.")