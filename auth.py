import os
import time
import secrets
import hashlib
import hmac
from datetime import datetime, timedelta

# ============================================================
# OFFLINE MULTI-LAYER AUTHENTICATION SYSTEM
# ============================================================

MAX_ATTEMPTS = 3
LOCK_TIME = 60
SESSION_TIME = 15 * 60


# ============================================================
# USER DATABASE
# ============================================================

users = {
    "manjunath": {
        "username": "manjunath",
        "password_hash": None,
        "pin_hash": None,
        "role": "user",
        "failed_attempts": 0,
        "locked_until": 0
    },

    "admin": {
        "username": "admin",
        "password_hash": None,
        "pin_hash": None,
        "role": "admin",
        "failed_attempts": 0,
        "locked_until": 0
    }
}


# ============================================================
# HASHING
# ============================================================

def hash_value(value):
    salt = os.urandom(16)

    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode(),
        salt,
        100000
    )

    return salt.hex() + ":" + hashed.hex()


def verify_value(value, stored_hash):

    try:
        salt_hex, hash_hex = stored_hash.split(":")

        salt = bytes.fromhex(salt_hex)

        new_hash = hashlib.pbkdf2_hmac(
            "sha256",
            value.encode(),
            salt,
            100000
        )

        return hmac.compare_digest(
            new_hash.hex(),
            hash_hex
        )

    except Exception:
        return False


# ============================================================
# INITIALIZE PASSWORDS
# ============================================================

users["manjunath"]["password_hash"] = hash_value(
    "password123"
)

users["manjunath"]["pin_hash"] = hash_value(
    "2468"
)

users["admin"]["password_hash"] = hash_value(
    "admin123"
)

users["admin"]["pin_hash"] = hash_value(
    "1357"
)


# ============================================================
# LOCKOUT SYSTEM
# ============================================================

def is_locked(user):

    current_time = time.time()

    if user["locked_until"] > current_time:

        remaining = int(
            user["locked_until"] - current_time
        )

        print(
            f"Account locked. Try again in {remaining} seconds."
        )

        return True

    return False


def register_failure(user):

    user["failed_attempts"] += 1

    print(
        "Authentication failed."
    )

    print(
        "Attempts:",
        user["failed_attempts"],
        "/",
        MAX_ATTEMPTS
    )

    if user["failed_attempts"] >= MAX_ATTEMPTS:

        user["locked_until"] = (
            time.time() + LOCK_TIME
        )

        user["failed_attempts"] = 0

        print(
            "Too many failures."
        )

        print(
            "Account temporarily locked."
        )


def reset_failures(user):

    user["failed_attempts"] = 0


# ============================================================
# LAYER 1
# USERNAME + PASSWORD
# ============================================================

def password_authentication():

    print("\n========== LAYER 1 ==========")
    print("Username + Password")

    username = input(
        "Username: "
    )

    user = users.get(username)

    if user is None:

        print(
            "User does not exist."
        )

        return None

    if is_locked(user):

        return None

    password = input(
        "Password: "
    )

    if verify_value(
        password,
        user["password_hash"]
    ):

        print(
            "Password authentication successful."
        )

        reset_failures(user)

        return user

    register_failure(user)

    return None


# ============================================================
# LAYER 2
# LOCAL PIN
# ============================================================

def pin_authentication(user):

    print("\n========== LAYER 2 ==========")
    print("Local PIN Authentication")

    pin = input(
        "Enter 4-digit PIN: "
    )

    if verify_value(
        pin,
        user["pin_hash"]
    ):

        print(
            "PIN authentication successful."
        )

        return True

    print(
        "Incorrect PIN."
    )

    return False


# ============================================================
# LAYER 3
# OFFLINE OTP
# ============================================================

def generate_otp():

    return str(
        secrets.randbelow(900000) + 100000
    )


def otp_authentication():

    print("\n========== LAYER 3 ==========")
    print("Offline One-Time Password")

    otp = generate_otp()

    # In a real offline application this could be
    # displayed through a secure local mechanism,
    # hardware token, authenticator device, etc.

    print(
        "DEBUG OTP:",
        otp
    )

    entered_otp = input(
        "Enter OTP: "
    )

    if hmac.compare_digest(
        otp,
        entered_otp
    ):

        print(
            "OTP authentication successful."
        )

        return True

    print(
        "Invalid OTP."
    )

    return False


# ============================================================
# LAYER 4
# DEVICE AUTHENTICATION
# ============================================================

def get_device_fingerprint():

    computer_name = os.getenv(
        "COMPUTERNAME",
        os.getenv("HOSTNAME", "UNKNOWN")
    )

    system_info = (
        computer_name
        + "|"
        + os.name
    )

    fingerprint = hashlib.sha256(
        system_info.encode()
    ).hexdigest()

    return fingerprint


def device_authentication():

    print("\n========== LAYER 4 ==========")
    print("Device Verification")

    fingerprint = get_device_fingerprint()

    print(
        "Device fingerprint:",
        fingerprint[:16] + "..."
    )

    confirmation = input(
        "Is this trusted device? (yes/no): "
    )

    if confirmation.lower() == "yes":

        print(
            "Device verification successful."
        )

        return True

    print(
        "Device not trusted."
    )

    return False


# ============================================================
# SESSION TOKEN
# ============================================================

active_sessions = {}


def create_session(user):

    token = secrets.token_urlsafe(32)

    expiration = (
        datetime.now()
        + timedelta(
            seconds=SESSION_TIME
        )
    )

    active_sessions[token] = {
        "username": user["username"],
        "role": user["role"],
        "expires": expiration
    }

    return token


def validate_session(token):

    session = active_sessions.get(token)

    if session is None:

        return False

    if datetime.now() > session["expires"]:

        del active_sessions[token]

        return False

    return True


# ============================================================
# ROLE AUTHORIZATION
# ============================================================

def authorize_admin(token):

    session = active_sessions.get(token)

    if session is None:

        return False

    if session["role"] != "admin":

        print(
            "Admin privileges required."
        )

        return False

    return True


# ============================================================
# SECURE DATA
# ============================================================

def access_user_data(token):

    if not validate_session(token):

        print(
            "Invalid or expired session."
        )

        return

    session = active_sessions[token]

    print("\n========== SECURE DATA ==========")

    print(
        "Welcome:",
        session["username"]
    )

    print(
        "Role:",
        session["role"]
    )

    print(
        "Sensitive offline data accessed."
    )


# ============================================================
# ADMIN OPERATION
# ============================================================

def admin_operation(token):

    print(
        "\n========== ADMIN OPERATION =========="
    )

    if not authorize_admin(token):

        print(
            "Access denied."
        )

        return

    print(
        "Admin authentication successful."
    )

    print(
        "System configuration accessed."
    )


# ============================================================
# LOGGING
# ============================================================

def security_log(message):

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    log_entry = (
        f"[{timestamp}] {message}\n"
    )

    with open(
        "security.log",
        "a"
    ) as file:

        file.write(log_entry)


# ============================================================
# MAIN AUTHENTICATION PIPELINE
# ============================================================

def authenticate():

    print(
        "\n========================================"
    )

    print(
        "     OFFLINE SECURE LOGIN SYSTEM"
    )

    print(
        "========================================"
    )

    # Layer 1

    user = password_authentication()

    if user is None:

        security_log(
            "Layer 1 authentication failed"
        )

        return None

    security_log(
        f"Password authentication: {user['username']}"
    )

    # Layer 2

    if not pin_authentication(user):

        security_log(
            "PIN authentication failed"
        )

        return None

    security_log(
        "PIN authentication successful"
    )

    # Layer 3

    if not otp_authentication():

        security_log(
            "OTP authentication failed"
        )

        return None

    security_log(
        "OTP authentication successful"
    )

    # Layer 4

    if not device_authentication():

        security_log(
            "Device authentication failed"
        )

        return None

    security_log(
        "Device authentication successful"
    )

    # Create session

    token = create_session(user)

    security_log(
        f"Session created for {user['username']}"
    )

    print(
        "\n========================================"
    )

    print(
        "       AUTHENTICATION SUCCESSFUL"
    )

    print(
        "========================================"
    )

    return token


# ============================================================
# APPLICATION
# ============================================================

def application():

    token = authenticate()

    if token is None:

        print(
            "\nAccess denied."
        )

        return

    while True:

        print(
            "\n========== APPLICATION =========="
        )

        print("1. Access secure data")
        print("2. Admin operation")
        print("3. Logout")

        choice = input(
            "Choose option: "
        )

        if choice == "1":

            access_user_data(token)

        elif choice == "2":

            admin_operation(token)

        elif choice == "3":

            if token in active_sessions:

                del active_sessions[token]

            security_log(
                "User logged out"
            )

            print(
                "Logged out successfully."
            )

            break

        else:

            print(
                "Invalid option."
            )


# ============================================================
# PROGRAM START
# ============================================================

if __name__ == "__main__":

    application()
