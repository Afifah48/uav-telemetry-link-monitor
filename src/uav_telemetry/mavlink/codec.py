from collections import OrderedDict
import struct
from pymavlink.dialects.v20 import common as mav


class Decoder:
    """Parser state per UDP peer, bounded to prevent unbounded peer allocation.

    One datagram can hold many frames; a frame may span consecutive datagrams.
    Fragments lost/reordered across datagrams cannot be reliably reconstructed.
    """
    def __init__(self, max_peers: int = 128):
        self.parsers: OrderedDict = OrderedDict()
        self.max_peers = max_peers
        self.bad_data = 0
        self.valid_messages = 0
        self.peer_evictions = 0

    def feed(self, payload: bytes, peer: tuple) -> list:
        if peer not in self.parsers:
            if len(self.parsers) >= self.max_peers:
                self.parsers.popitem(last=False)
                self.peer_evictions += 1
            parser = mav.MAVLink(None)
            parser.robust_parsing = True
            self.parsers[peer] = parser
        self.parsers.move_to_end(peer)
        parser = self.parsers[peer]
        try:
            messages = parser.parse_buffer(payload) or []
        except (mav.MAVError, ValueError, struct.error):
            self.bad_data += 1
            self.parsers.pop(peer, None)
            return []
        valid = []
        for message in messages:
            if message.get_type() == "BAD_DATA":
                self.bad_data += 1
            else:
                self.valid_messages += 1
                valid.append(message)
        return valid
