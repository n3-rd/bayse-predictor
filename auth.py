import time
import hmac
import hashlib
import base64

def generate_signature(secret_key: str, method: str, path: str, timestamp: int, body_str: str = "") -> str:
    """
    Generates the HMAC-SHA256 signature for a Bayse API request.
    Payload structure: {timestamp}.{METHOD}.{path}.{bodyHash}
    """
    # Calculate body hash (SHA256 hex digest)
    if body_str:
        body_bytes = body_str.encode("utf-8")
        body_hash = hashlib.sha256(body_bytes).hexdigest()
    else:
        body_hash = ""
        
    # Construct signature payload string
    payload = f"{timestamp}.{method.upper()}.{path}.{body_hash}"
    
    # Compute HMAC-SHA256 using secret key
    secret_bytes = secret_key.encode("utf-8")
    payload_bytes = payload.encode("utf-8")
    
    mac = hmac.new(secret_bytes, payload_bytes, hashlib.sha256)
    signature_bytes = mac.digest()
    
    # Base64 encode signature
    signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")
    return signature_b64

def get_auth_headers(public_key: str, secret_key: str, method: str, path: str, body_str: str = "", timestamp: int = None) -> dict:
    """
    Constructs the dictionary of authentication headers required for authenticated write requests.
    """
    if timestamp is None:
        timestamp = int(time.time())
    signature = generate_signature(secret_key, method, path, timestamp, body_str)
    
    return {
        "X-Timestamp": str(timestamp),
        "X-Public-Key": public_key,
        "X-Signature": signature,
        "Content-Type": "application/json"
    }
