import hashlib
import hmac as _hmac

def hex_md5(data):
    """Generate MD5 hash of data."""
    return hashlib.md5(data.encode()).hexdigest()


def hex_hmac_md5(key, data):
    """Generate HMAC-MD5 hash."""
    return _hmac.new(key.encode(), data.encode(), hashlib.md5).hexdigest()
