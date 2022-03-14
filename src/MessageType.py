import struct

REQUEST_SIZE = 2**14

# diffrent message ID
CHOKE = 0
UNCHOKE = 1
INTERESTED = 2
NOTINTERESTED = 3
HAVE = 4
BITFIELD = 5
REQUEST = 6
PIECE = 7
CANCEL = 8
PORT = 9

class Unchoke:
    """
    Message format: <len=0001><id=1>
    Peer is allowed to request for pieces
    """
    def __str__(self):
        return 'Unchoke'

class Choke:
    """
    Message format: <len=0001><id=0>
    Peer can't request for pieces
    """
    def __str__(self):
        return 'Choke'


class Interested:
    """
    Message format: <len=0001><id=2>
    """
    def encode(self) -> bytes:
        return struct.pack('>Ib', 1, INTERESTED)

    def __str__(self):
        return 'Interested'

class NotInterested:
    """
    Message format: <len=0001><id=3>
    """
    def __str__(self):
        return 'NotInterested'

class KeepAlive:
    """
    Message format <len = 0000>
    """
    def __str__(self):
        return 'KeepAlive'

class Bitfield:
    """
    Message format: <len=0001+X><id=5><bitfield>
    what pieces the peer have, and what they don't
    """
    def __init__(self, data):
        temp = ''.join(format(byte, '08b') for byte in data) 
        #turning out string into a byte array
        self.bitfield = bytearray(temp, 'utf-8')

    @classmethod
    def decode(cls, data: bytes) -> bytes:
        """
        Args: data (bytes): raw bytes of the bitfield message
        """
        return cls(data[1:])

    def encode(data) -> bytes:
        return struct.pack('>Ib' + str(len(data)) + 's',
                           1 + len(data),
                           BITFIELD,
                           data)


    def __str__(self) -> str:
        return 'Bitfield'
    
class Request:
    """
    Message format: <id=6><index><begin><length>
    Request for a piece --> request size is 2^14 bytes
    """
    def __init__(self, index: int, begin: int, length: int = REQUEST_SIZE) -> None:
        """
        Constructs a Request messages
        Args:
            index (int): zero-based piece index
            begin (int): zero-based offset within a piece
            length (int, optional): requested length of data. Defaults to REQUEST_SIZE.
        """
        self.index = index
        self.begin = begin
        self.length = length

    """
    encoding a request for a certain piece
    """
    def encode(data):
        return struct.pack('>IbIII',
            13,
            REQUEST,
            data.index,
            data.begin,
            data.length)

    @classmethod
    def decode(cls, data: bytes) -> tuple:
        """
        Args: data (bytes): raw bytes of incoming request msg
        """
        parts = struct.unpack('>bIII', data)
        print('decoding request: index: {}, begin: {}, length: {}'.format(part[1], part[2], part[3]))
        return cls(parts[1], parts[2], parts[3])

    def __str__(self) -> str:
        return 'Request'

class Have:
    """
    Represents a piece successfully downloaded by the remote peer. The piece
    is a zero based index of the torrents pieces
    """
    def __init__(self, index: int):
        self.index = index

    @classmethod
    def decode(cls, data: bytes):
        """
        data format: <id><payload>
        since length is already consumed by client.py
        """
        index = struct.unpack('>I', data[1:])[0]
        return cls(index)

    def encode(self, piece):
        return struct.pack('>IbI',
            5,  # Message length
            HAVE,
            piece.index)

    def __str__(self):
        return 'Have'


class Cancel:
    def __str__(self):
        return 'Cancel'


class Piece:
    """
    Message format:
        <length prefix><message ID><index><begin><block>
    """

    def __init__(self, index: int, begin: int, block: bytes):
        self.index = index
        self.begin = begin
        self.block = block


    def encode(data):
        message_length = 9 + len(self.block)
        return struct.pack('>IbII' + str(len(self.block)) + 's',
                            message_length,
                            PIECE,
                            self.index,
                            self.begin,
                            data)

    @classmethod
    def decode(cls, data: bytes):
        # The Piece message length without the block data is 9

        """
        data format: <message_id><piece_index><begin_index><piece_data>
        """

        piece_index = struct.unpack('>I', data[1:5])[0]
        begin_index = struct.unpack('>I', data[5:9])[0]
        piece_data = data[9:]

        return cls(piece_index, begin_index, piece_data)

    def __str__(self):
        return 'Piece'