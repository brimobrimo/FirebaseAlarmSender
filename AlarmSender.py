import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from firebase_admin import messaging
import time
import os
import json

# --- CONFIGURATION ---

# IMPORTANT: Ensure this file is in the same directory as this script.
CREDENTIALS_FILENAME = 'serviceAccountKey.json' 
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

# DIAGNOSTIC CONFIGURATION (Adjust these to match your current test user/alarm)
TEST_USER_ID = 'PRFzKRIJGbSsrwC60ic9ifU9qsC3' 
TEST_ALARM_ID = '1u40ZCLzvSIDitkUYIM5' 

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


# --- MESSAGE SENDING LOGIC ---

def send_fcm_message(token, alert_id, mmsi, alert_name):
    """Constructs and sends a notification message with dynamic ship data."""

    # 1. Format the dynamic content using the extracted data
    title = NOTIFICATION_TITLE_TEMPLATE.format(alertName=alert_name)
    body = NOTIFICATION_BODY_TEMPLATE.format(vesselMMSI=mmsi)
    
    # 2. Populate the data payload for the client app
    data_payload = NOTIFICATION_DATA_TEMPLATE.copy()
    data_payload["vesselMMSI"] = str(mmsi) # MMSI should be a string in the data payload
    data_payload["alertName"] = alert_name

    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        data=data_payload,
        token=token,
        # APNs configuration specific to iOS:
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
        # Send the message
        response = messaging.send(message)
        print(f"  [SUCCESS] Alert {alert_id} (Name: {alert_name}, MMSI: {mmsi}) sent. Response: {response}")
        return True
    except Exception as e:
        # Check for invalid token errors (which are common in tests)
        if 'not registered' in str(e).lower() or 'invalid registration' in str(e).lower():
            print(f"  [WARNING] Token is likely stale or invalid for Alert {alert_id}. Error: {e}")
        else:
            print(f"  [FAILURE] Failed to send message for Alert {alert_id}. Token: {token}. Error: {e}")
        return False

def process_user_alerts(db, user_id, total_alerts_processed):
    """Helper function to process alerts for a single user."""
    alerts_ref = db.collection(FULL_USERS_COLLECTION_PATH).document(user_id).collection(ALERTS_SUBCOLLECTION)
    alerts_found_for_user = 0
    
    try:
        alerts_stream = alerts_ref.stream()
    except Exception as e:
        print(f"  WARNING: Could not access alerts subcollection for user {user_id}. Details: {e}. Moving to next user.")
        return total_alerts_processed, 0

    for alert_doc in alerts_stream:
        alert_id = alert_doc.id
        alert_data = alert_doc.to_dict()
        total_alerts_processed += 1
        alerts_found_for_user += 1
        
        fcm_token = alert_data.get(FCM_TOKEN_FIELD)
        mmsi = alert_data.get(MMSI_FIELD)
        alert_name = alert_data.get(SHIP_NAME_FIELD)
        
        if fcm_token and mmsi and alert_name:
            print(f"  > Found Alert {alert_id}. Name: {alert_name} (MMSI: {mmsi}). Sending FCM...")
            send_fcm_message(fcm_token, alert_id, mmsi, alert_name)
        else:
            missing = []
            if not fcm_token: missing.append(FCM_TOKEN_FIELD)
            if not mmsi: missing.append(MMSI_FIELD)
            if not alert_name: missing.append(SHIP_NAME_FIELD)
            print(f"  > Alert {alert_id} found but missing data. Skipping. Missing fields: {', '.join(missing)}")

    return total_alerts_processed, alerts_found_for_user

def process_all_alerts(db):
    """
    Iterates through all users and all of their alerts to send a test notification.
    """
    print(f"\n--- Starting Alert Processing (Scanning all users and subcollections) ---")
    
    total_alerts_processed = 0
    total_users_processed = 0
    processed_user_ids = set()

    # --- Step 1: Explicitly process the known test user ID first ---
    if TEST_USER_ID:
        print(f"\nProcessing Target User (Diagnostic Success): {TEST_USER_ID}")
        total_alerts_processed, alerts_found = process_user_alerts(db, TEST_USER_ID, total_alerts_processed)
        total_users_processed += 1
        processed_user_ids.add(TEST_USER_ID)
        
        if alerts_found == 0:
             print(f"  > No documents found in the '{ALERTS_SUBCOLLECTION}' subcollection for user {TEST_USER_ID} (checked explicitly).")

    # --- Step 2: Scan all other users ---
    users_ref = db.collection(FULL_USERS_COLLECTION_PATH)
    
    try:
        # This streams over all user documents 
        users_stream = users_ref.stream()
    except Exception as e:
        print(f"CRITICAL ERROR: Could not access the '{FULL_USERS_COLLECTION_PATH}' collection for full scan. Check security rules.")
        print(f"Details: {e}")
        return

    for user_doc in users_stream:
        user_id = user_doc.id
        
        # Skip the user we already processed explicitly
        if user_id in processed_user_ids:
            continue
            
        total_users_processed += 1
        print(f"\nProcessing Scanned User: {user_id}")
        
        total_alerts_processed, alerts_found = process_user_alerts(db, user_id, total_alerts_processed)
        
        if alerts_found == 0:
             print(f"  > No documents found in the '{ALERTS_SUBCOLLECTION}' subcollection for user {user_id}.")


    print(f"\n--- Processing Complete ---")
    print(f"Summary: Processed {total_users_processed} user(s) and {total_alerts_processed} total alert document(s) checked.")


if __name__ == "__main__":
    
    # 1. Run initialization and get the Firestore client
    firestore_client = initialize_firebase_app()
    
    if firestore_client:
        # 1.5 Run diagnostic check
        if test_read_access(firestore_client):
            # 2. Process all data and send notifications
            process_all_alerts(firestore_client)
        else:
            print("\nFATAL ERROR: Access test failed. Cannot proceed with alert processing.")
    
    # In local execution, we don't clean up the file, as it's a permanent asset.
    print("\nLocal script finished execution.")
