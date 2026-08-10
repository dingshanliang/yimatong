import ipaddress
import re
from urllib.parse import urlsplit

_MANAGED_PUBLIC_PATH = re.compile(
    r"^/api/v1/files/public/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*)*$"
)


def normalize_public_url(value: str) -> str:
    """Return a canonical consumer-safe public URL or raise ``ValueError``.

    Trust-master data is rendered directly by the consumer H5. Keep the
    persisted contract intentionally narrow: managed public-file paths or
    public HTTPS URLs without embedded credentials.
    """

    if not isinstance(value, str):
        raise ValueError("Public URL must be a string")

    normalized = value.strip()
    if _MANAGED_PUBLIC_PATH.fullmatch(normalized):
        return normalized

    parsed = urlsplit(normalized)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Public URL must use HTTPS or a managed public-file path")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Public URL must not contain credentials")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or "." not in host:
        raise ValueError("Public URL host must be publicly routable")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Public URL host must be publicly routable")
    return normalized
