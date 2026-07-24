import secrets

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_pub_id(prefix: str) -> str:
    value = int.from_bytes(secrets.token_bytes(16), "big")
    encoded = "".join(CROCKFORD[(value >> (5 * index)) & 31] for index in range(25, -1, -1))
    return f"{prefix}_{encoded}"
