"""Pure-Python ed25519 signature verification (used for signed Horizon and launcher releases)."""

import hashlib


_ED_P = 2 ** 255 - 19
_ED_Q = 2 ** 252 + 27742317777372353535851937790883648493
_ED_D = -121665 * pow(121666, _ED_P - 2, _ED_P) % _ED_P
_ED_SQRT_M1 = pow(2, (_ED_P - 1) // 4, _ED_P)


def _ed_recover_x(y, sign):
    if y >= _ED_P:
        return None
    x2 = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_P - 2, _ED_P) % _ED_P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_ED_P + 3) // 8, _ED_P)
    if (x * x - x2) % _ED_P:
        x = x * _ED_SQRT_M1 % _ED_P
    if (x * x - x2) % _ED_P:
        return None
    return _ED_P - x if (x & 1) != sign else x


def _ed_add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _ED_P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _ED_P
    C = 2 * a[3] * b[3] * _ED_D % _ED_P
    D = 2 * a[2] * b[2] % _ED_P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _ED_P, G * H % _ED_P, F * G % _ED_P, E * H % _ED_P)


def _ed_mul(s, point):
    result = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            result = _ed_add(result, point)
        point = _ed_add(point, point)
        s >>= 1
    return result


def _ed_equal(a, b):
    return ((a[0] * b[2] - b[0] * a[2]) % _ED_P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _ED_P == 0)


def _ed_decompress(data):
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _ed_recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _ED_P)


_ED_GY = 4 * pow(5, _ED_P - 2, _ED_P) % _ED_P
_ED_G = (_ed_recover_x(_ED_GY, 0), _ED_GY, 1, _ed_recover_x(_ED_GY, 0) * _ED_GY % _ED_P)


def horizon_ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A, R = _ed_decompress(public), _ed_decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if A is None or R is None or s >= _ED_Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _ED_Q
    return _ed_equal(_ed_mul(s, _ED_G), _ed_add(R, _ed_mul(h, A)))
