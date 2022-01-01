"""
Documentation for chunk store data structures:

A Chunk is a contiguous piece of a file (a file may be one or more chunks). A Chunk represents an atomic retyrable piece of data to make
state management simpler. It is identified by a globally unique ID for an entire transfer. The ChunkStore maintains state (ChunkState)
for each Chunk in addition to the path through the overlay that the Chunk will follow. As a ChunkRequest makes its way through the overlay,
a ChunkRequestHop is popped off the path and the ChunkState is updated. ChunkState is maintained separately from the ChunkRequest in the
ChunkStore so that ChunkRequests can be passed between Gateways.

ChunkRequest:
    chunk:
        key: str
        chunk_id: int
        file_offset_bytes: int
        chunk_len_bytes: int
    path: List[ChunkRequestHop]
        hop_cloud_region: str
        hop_ip_address: str
        chunk_location_type: str
        src_object_store_region: str
        src_object_store_bucket: str
        dst_object_store_region: str
        dst_object_store_bucket: str

As compared to a ChunkRequest, the WireProtocolHeader is solely used to manage transfers over network sockets. It identifies the ID and
length of the upcoming stream of data (contents of the Chunk) on the socket. An end_of_stream flag is used to indicate that this is the
last transfer over a socket while a magic int (SKY_LARK) as well as the protocol version are used to enable wire protocol upgrades.

WireProtocolHeader:
    magic: int
    protocol_version: int
    chunk_id: int
    chunk_len: int
    end_of_stream: bool
"""

import socket
from dataclasses import asdict, dataclass
from enum import Enum, auto
from typing import Dict, List


@dataclass
class Chunk:
    """A Chunk is a contiguous piece of a file (a file may be one or more chunks)."""

    key: str  # human readable path where object is stored
    chunk_id: int
    file_offset_bytes: int
    chunk_length_bytes: int

    def to_wire_header(self, end_of_stream: bool = False):
        return WireProtocolHeader(chunk_id=self.chunk_id, chunk_len=self.chunk_length_bytes, end_of_stream=end_of_stream)

    def as_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict):
        return Chunk(**d)


@dataclass
class ChunkRequestHop:
    """A ChunkRequestHop represents metadata needed by the Gateway to route a ChunkRequest through the overlay."""

    hop_cloud_region: str  # format is provider:region
    hop_ip_address: str
    chunk_location_type: str  # enum of {"src_object_store", "dst_object_store", "relay"}

    # if chunk_location_type == "src_object_store":
    # src_object_store_region: str = None  # format is provider:region
    # src_object_store_bucket: str = None

    # if chunk_location_type == "dst_object_store":
    # dst_object_store_region: str = None  # format is provider:region
    # dst_object_store_bucket: str = None

    def as_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(src_dict: Dict):
        return ChunkRequestHop(**src_dict)


@dataclass
class ChunkRequest:
    """A ChunkRequest stores all local state in the Gateway pertaining to a ChunkRequest."""

    chunk: Chunk
    path: List[ChunkRequestHop]

    # todo: flags for compression, encryption, logging api, etc.

    def as_dict(self):
        out = {"chunk": self.chunk.as_dict(), "path": [hop.as_dict() for hop in self.path]}
        return out

    @staticmethod
    def from_dict(in_dict: Dict):
        return ChunkRequest(chunk=Chunk.from_dict(in_dict["chunk"]), path=[ChunkRequestHop.from_dict(hop) for hop in in_dict["path"]])


class ChunkState(Enum):
    registered = auto()
    download_in_progress = auto()
    downloaded = auto()
    upload_in_progress = auto()
    upload_complete = auto()
    failed = auto()

    @staticmethod
    def from_str(s: str):
        return ChunkState[s.lower()]


@dataclass
class WireProtocolHeader:
    """Lightweight wire protocol header for chunk transfers along socket."""

    chunk_id: int  # unsigned long
    chunk_len: int  # unsigned long
    end_of_stream: bool = False  # false by default, but true if this is the last chunk

    @staticmethod
    def magic_hex():
        return 0x534B595F4C41524B  # "SKY_LARK"

    @staticmethod
    def protocol_version():
        return 1

    @staticmethod
    def length_bytes():
        # magic (8) + protocol_version (4) + chunk_id (8) + chunk_len (8) + end_of_stream (1)
        return 8 + 4 + 8 + 8 + 1

    @staticmethod
    def from_bytes(data: bytes):
        assert len(data) == WireProtocolHeader.length_bytes(), f"{len(data)} != {WireProtocolHeader.length_bytes()}"
        magic = int.from_bytes(data[:8], byteorder="big")
        if magic != WireProtocolHeader.magic_hex():
            raise ValueError("Invalid magic number")
        version = int.from_bytes(data[8:12], byteorder="big")
        if version != WireProtocolHeader.protocol_version():
            raise ValueError("Invalid protocol version")
        chunk_id = int.from_bytes(data[12:20], byteorder="big")
        chunk_len = int.from_bytes(data[20:28], byteorder="big")
        end_of_stream = bool(data[28])
        return WireProtocolHeader(chunk_id=chunk_id, chunk_len=chunk_len, end_of_stream=end_of_stream)

    def to_bytes(self):
        out_bytes = b""
        out_bytes += self.magic_hex().to_bytes(8, byteorder="big")
        out_bytes += self.protocol_version().to_bytes(4, byteorder="big")
        out_bytes += self.chunk_id.to_bytes(8, byteorder="big")
        out_bytes += self.chunk_len.to_bytes(8, byteorder="big")
        out_bytes += bytes([int(self.end_of_stream)])
        assert len(out_bytes) == WireProtocolHeader.length_bytes(), f"{len(out_bytes)} != {WireProtocolHeader.length_bytes()}"
        return out_bytes

    @staticmethod
    def from_socket(sock: socket.socket):
        num_bytes = WireProtocolHeader.length_bytes()
        header_bytes = sock.recv(num_bytes)
        assert len(header_bytes) == num_bytes, f"{len(header_bytes)} != {num_bytes}"
        return WireProtocolHeader.from_bytes(header_bytes)

    def to_socket(self, sock: socket.socket):
        assert sock.sendall(self.to_bytes()) is None