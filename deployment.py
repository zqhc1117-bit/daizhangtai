"""Read the optional address of this workbench installation."""

from ipaddress import IPv4Address


def configured_listen_address(data_dir):
    setting = data_dir / "listen-address.txt"
    try:
        value = setting.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return None
    try:
        address = IPv4Address(value)
        if address.is_unspecified or address.is_multicast or address == IPv4Address("255.255.255.255"):
            raise ValueError(value)
    except ValueError as exc:
        raise ValueError(f"{setting} 需要填写本机的单个 IPv4 地址。") from exc
    return str(address)


def local_service_url(listen_address, port):
    return f"http://{listen_address or '127.0.0.1'}:{port}/"
