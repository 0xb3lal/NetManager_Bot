import hashlib
import hmac as _hmac


def hex_md5(data):
    return hashlib.md5(data.encode()).hexdigest()


def hex_hmac_md5(key, data):
    return _hmac.new(key.encode(), data.encode(), hashlib.md5).hexdigest()
