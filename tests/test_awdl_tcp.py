"""Test the upload coalescer with valid and incompatible IPv6/TCP packets."""

from pathlib import Path
import ipaddress
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from awdl_tcp import MAX_HOLD_SECONDS, TCP_ACK, TCP_PSH, TcpReceiveBatch, coalesced_packet, with_tcp_checksum


def segment(sequence, data, *, flags=TCP_ACK, source_port=49200, dest_port=8771,
            ack=800, window=32768, options=b""):
    ip = struct.pack("!IHBB", 0x60000000, 20 + len(options) + len(data), 6, 64)
    ip += ipaddress.IPv6Address("fe80::10").packed + ipaddress.IPv6Address("fe80::20").packed
    tcp = struct.pack("!HHIIBBHHH", source_port, dest_port, sequence, ack,
                      ((20 + len(options)) // 4) << 4, flags, window, 0, 0)
    return with_tcp_checksum(ip + tcp + options + data)


def timestamps(value, echo=1234):
    return bytes.fromhex("0101080a") + struct.pack("!II", value, echo)


class AwdlTcpTest(unittest.TestCase):
    def setUp(self):
        self.batch = TcpReceiveBatch(8771)

    def test_contiguous_data_uses_first_sequence_last_timestamp_and_push(self):
        first = segment(0xfffffff8, b"12345678", options=timestamps(0xfffffffe))
        last = segment(0, b"abcdefg", flags=TCP_ACK | TCP_PSH, options=timestamps(1))
        self.assertEqual(self.batch.offer(first, 1), ())
        self.assertEqual(self.batch.offer(last, 1.0001), ((first, last),))
        packet = coalesced_packet((first, last))
        self.assertEqual(packet[72:], b"12345678abcdefg")
        self.assertEqual(packet[44:48], first[44:48])
        self.assertEqual(packet[53], TCP_ACK | TCP_PSH)
        self.assertEqual(packet[60:72], last[60:72])
        self.assertEqual(int.from_bytes(packet[4:6], "big"), len(packet) - 40)
        pseudo = packet[8:40] + struct.pack("!I3xB", len(packet) - 40, 6) + packet[40:] + b"\0"
        self.assertEqual(sum(word for (word,) in struct.iter_unpack("!H", pseudo)) % 65535, 0)

    def test_gaps_duplicates_and_header_changes_flush_in_delivery_order(self):
        first = segment(100, b"a", options=timestamps(8))
        for next_packet in (segment(102, b"c", options=timestamps(9)),
                            segment(100, b"a", options=timestamps(9)),
                            segment(101, b"b", options=timestamps(7)),
                            segment(101, b"b", options=timestamps(9, echo=777)),
                            segment(101, b"b", options=timestamps(9), ack=801),
                            segment(101, b"b", options=timestamps(9), window=16000),
                            segment(101, b"b", options=timestamps(9), source_port=50300)):
            with self.subTest(next_packet=next_packet):
                self.assertEqual(self.batch.offer(first, 1), ())
                self.assertEqual(self.batch.offer(next_packet, 1.0001), ((first,),))
                self.assertEqual(self.batch.flush(), ((next_packet,),))

    def test_pure_ack_fin_bad_checksum_and_unknown_options_pass_through(self):
        first = segment(100, b"a")
        valid = segment(101, b"b")
        bogus = valid[:-1] + b"c"
        for next_packet in (segment(101, b""), segment(101, b"b", flags=TCP_ACK | 0x01),
                            bogus, segment(101, b"b", options=b"\x01\x01\x00\x00"),
                            segment(101, b"b", dest_port=80)):
            with self.subTest(next_packet=next_packet):
                self.assertEqual(self.batch.offer(first, 1), ())
                self.assertEqual(self.batch.offer(next_packet, 1.0001), ((first,), (next_packet,)))
                self.assertEqual(self.batch.flush(), ())

    def test_batch_size_and_deadline_are_bounded(self):
        parts = tuple(segment(100 + n * 1160, bytes([n]) * 1160) for n in range(4))
        for packet in parts[:3]:
            self.assertEqual(self.batch.offer(packet, 1), ())
        self.assertEqual(self.batch.offer(parts[3], 1), (parts,))
        self.assertEqual(len(coalesced_packet(parts)), 4700)
        self.batch.offer(parts[0], 2)
        self.assertEqual(self.batch.flush_due(2 + MAX_HOLD_SECONDS / 2), ())
        self.assertEqual(self.batch.flush_due(2 + MAX_HOLD_SECONDS * 2), ((parts[0],),))

    def test_larger_batches_preserve_data_and_flush_control_packets(self):
        for size in (8, 12, 16):
            with self.subTest(segments=size):
                batch = TcpReceiveBatch(8771, size)
                parts = tuple(segment(100 + index * 1210, bytes([index]) * 1210) for index in range(size))
                for packet in parts[:-1]:
                    self.assertEqual(batch.offer(packet, 1), ())
                self.assertEqual(batch.offer(parts[-1], 1), (parts,))
                combined = coalesced_packet(parts)
                self.assertEqual(combined[60:], b"".join(packet[60:] for packet in parts))
                self.assertEqual(with_tcp_checksum(combined), combined)
                batch.offer(parts[0], 2)
                ack = segment(1310, b"")
                self.assertEqual(batch.offer(ack, 2.0001), ((parts[0],), (ack,)))
                self.assertEqual(batch.offer(parts[0], 3), ())
                self.assertEqual(batch.flush_due(3 + MAX_HOLD_SECONDS / 2), ())
                self.assertEqual(batch.flush_due(3 + MAX_HOLD_SECONDS), ((parts[0],),))


if __name__ == "__main__":
    unittest.main()
