"""Answer the USB-attached iPhone's AirDrop queries on the Surface WLAN."""

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
import ipaddress
from pathlib import Path
import secrets
import socket
import struct
import sys
import tempfile
import threading
import time

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.pcapd import PcapdService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/winawdl/airdrop"))
import mdns
import netif
import receiver


def ip_header(data):
    # en0 with Ethernet framing (including pcapd's synthetic Ethernet header).
    # IPv6 extension headers are not decoded by this probe.
    ip = data[14:]
    if len(ip) >= 40 and ip[0] >> 4 == 6:
        return ipaddress.ip_address(ip[8:24]), ip[6], ip[40:]
    if len(ip) >= 20 and ip[0] >> 4 == 4 and not (int.from_bytes(ip[6:8], "big") & 0x3fff):
        size = (ip[0] & 15) * 4
        if 20 <= size <= len(ip):
            return ipaddress.ip_address(ip[12:16]), ip[9], ip[size:]
    return None


class DiscoveryHandler(receiver.Handler):
    def do_POST(self):
        if self.path != "/Discover":
            self.reject(403, "discovery only")
            return
        super().do_POST()

    def identity_plist(self):
        print("Received /Discover from the phone", flush=True)
        return super().identity_plist()

    def log_message(self, *args):
        pass


class PhoneReceiver(receiver.Receiver):
    def verify_request(self, request, client_address):
        return (super().verify_request(request, client_address)
                and ipaddress.ip_address(client_address[0].split("%")[0]) in self.phone_addresses)


def publish_window(sock, service, index, deadline, stopping, counts):
    destination = (mdns.GROUP, mdns.PORT, 0, index)
    try:
        while not stopping.is_set():
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            sock.sendto(service.response(ttl=min(10, int(remaining))), destination)
            counts["multicast_sent"] += 1
            stopping.wait(min(remaining, mdns.ANNOUNCE_PERIOD_S))
    finally:
        sock.sendto(service.response(alive=False), destination)
        counts["goodbye_sent"] += 1


async def run(args):
    index = netif.interface_index(args.iface)
    address = netif.link_local_address(index) if index else None
    if not address:
        raise RuntimeError("No WLAN link-local address")
    with ExitStack() as stack:
        settings = receiver.parse_args(["--name", "Surface-Test", "--iface", args.iface,
                                       "--outdir", tempfile.gettempdir()])
        settings.interface_index = index
        server = stack.enter_context(PhoneReceiver(0, settings, bind_address=address))
        server.phone_addresses = set()
        server.RequestHandlerClass = DiscoveryHandler
        context = receiver.tls_context(stack.enter_context(tempfile.TemporaryDirectory()))
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        instance = secrets.token_hex(6)
        service = mdns.Service(receiver.SERVICE_TYPE, instance, instance + ".local", address,
                               server.server_port, f"flags={settings.flags}")
        sockets = {}
        for version, family, endpoint in [(4, socket.AF_INET, (args.ipv4, mdns.PORT)),
                                          (6, socket.AF_INET6, (address, mdns.PORT, 0, index))]:
            sock = stack.enter_context(socket.socket(family, socket.SOCK_DGRAM))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(endpoint)
            if version == 6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, 255)
            else:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 255)
            sockets[version] = sock
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.reset_sessions)
        stack.callback(server.shutdown)
        counts = Counter()
        last_reply = {}
        async with await create_using_usbmux(connection_type="USB") as phone, PcapdService(phone) as cap:
            stopping = threading.Event()
            publisher = None
            try:
                duration = 60 if args.multicast_60s else args.seconds
                async with asyncio.timeout(duration):
                    async for packet in cap.watch():
                        interface = packet.interface_name
                        if not interface[-1:].isdigit():
                            interface += str(packet.unit)
                        if interface not in ("en0", "awdl0"):
                            continue
                        counts[interface + "_packets"] += 1
                        if interface == "en0" and counts["en0_packets"] == 1:
                            if args.multicast_60s:
                                sock = sockets[6]
                                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, index)
                                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
                                publisher = threading.Thread(target=publish_window, args=(
                                    sock, service, index, time.monotonic() + duration, stopping, counts))
                                publisher.start()
                            print("Ready for the iPhone AirDrop picker", flush=True)
                        parsed = ip_header(packet.data)
                        if not parsed:
                            continue
                        source, protocol, payload = parsed
                        names = set()
                        if (packet.io == 1 and protocol == 17 and len(payload) >= 20
                                and struct.unpack_from("!H", payload, 2)[0] == mdns.PORT):
                            names = mdns.question_names(payload[8:])
                            if service.type_name in names:
                                counts[interface + "_airdrop_query"] += 1
                        if interface != "en0":
                            continue
                        if (packet.io == 16 and protocol == 17 and len(payload) >= 20
                                and struct.unpack_from("!HH", payload) == (mdns.PORT, mdns.PORT)):
                            if any(name == service.type_name and value == service.instance_name
                                   for name, _, _, value in mdns.response_records(payload[8:])):
                                counts["phone_observed_reply"] += 1
                        if packet.io != 1 or source.is_multicast or source.is_unspecified:
                            continue
                        if source.version == 6 and source.is_link_local:
                            server.phone_addresses.add(source)
                        if protocol == 6 and len(payload) >= 20:
                            port = struct.unpack_from("!H", payload, 2)[0]
                            # Count only SYNs to the advertised WLAN address and port.
                            if (source.version == 6 and packet.data[38:54] == service.address.packed
                                    and port == server.server_port and payload[13] & 0x12 == 0x02):
                                counts["phone_tcp_syn"] += 1
                        if protocol != 17 or len(payload) < 20:
                            continue
                        sport, dport = struct.unpack_from("!HH", payload)
                        if sport != mdns.PORT or dport != mdns.PORT:
                            continue
                        if not service.names().intersection(names):
                            continue
                        counts["phone_query"] += 1
                        if args.multicast_60s:
                            continue
                        if time.monotonic() - last_reply.get(source, 0) < mdns.MIN_REPLY_GAP_S:
                            continue
                        dest = (str(source), sport) if source.version == 4 else (str(source), sport, 0, index)
                        sockets[source.version].sendto(service.response(ttl=10), dest)
                        last_reply[source] = time.monotonic()
                        counts["unicast_reply_sent"] += 1
            except TimeoutError:
                pass
            else:
                raise RuntimeError("USB capture ended early")
            finally:
                if publisher is not None:
                    stopping.set()
                    publisher.join()
                print("Discovery counters:", dict(counts), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iface", default="WLAN")
    parser.add_argument("--ipv4", required=True, help="local IPv4 address of the selected WLAN")
    parser.add_argument("--seconds", type=int, choices=range(1, 181), default=90, metavar="1..180")
    parser.add_argument("--multicast-60s", action="store_true",
                        help="publish via WLAN IPv6 multicast for at most 60 seconds")
    asyncio.run(run(parser.parse_args()))
