import asyncio
import struct
import random
import string
import os

from hashlib import sha1
from typing import List
from torrent import Torrent 
from concurrent.futures import CancelledError
from MessageType import Unchoke, Choke, Interested, NotInterested, Have, Bitfield, KeepAlive, Cancel, Piece, Request
from Piecetracker import Piece as piece, Block, Piecetracker
# 2**14 = 10 * 1024 bytes
REQUEST_SIZE = 10*1024

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

class Client:
    # instantiate 
    #piece tracker is for tracking all of our pieces, what each peers have in terms of pieces, and what we need
    def __init__(self, torrent, pieceTracker, peer_id, remote_id, ip, port):
        self.torrent = torrent
        self.pieceTracker = pieceTracker
        self.ip = ip
        self.port = port
        self.peer_id = peer_id
        self.remote_id = remote_id
        self.str_id = str(self.ip) + ':' + str(self.port)

    # constructing the handshake
    def handshakeBuf(self):
        #if its a string
        if isinstance(self.torrent.info_hash, str):
            self.torrent = self.torrent.info_hash.encode('utf-8')
        return struct.pack(
            '>B19s8x20s20s',
            19,
            b'BitTorrent protocol',
            self.torrent.info_hash,
            self.peer_id.encode('utf-8')
        )

    def decode_handshake(self, data: bytes):
        """
        Decodes the given BitTorrent message into a handshake message, if not
        a valid message, None is returned.
        """
        print('Decoding Handshake of length: {length}'.format(
            length=len(data)))
        if len(data) < 68:
            return None
        parts = struct.unpack('>B19s8x20s20s', data)
        return parts
    
    async def _handshake(self, reader, writer):
        #sending the handshake
        writer.write(self.handshakeBuf())
        await writer.drain()

        #reading back the handshake
        """
        pstrlen = 19
        pstr = "BitTorrent protocol".
        49 + len(pstr) = 68 bytes long."""

        data = b''
        tries = 1
        data = await reader.read(68)

        while len(data) < 68:
            extra = await reader.read(68 - len(data))
            data += extra

        # validating the handshake
        res = self.decode_handshake(data)
        if res is None:
            print('Unable receive and parse a handshake')
            exit(1)
        if not res[2] == self.torrent.info_hash:
            print('Handshake with invalid info_hash')
            exit(1)

    async def _request_piece(self, writer) -> bool:
        block = self.pieceTracker.next_request(self.remote_id)
        if block:
            message = Request(block.piece, block.offset, block.length).encode()

            print('Requesting block {block} for piece {piece} '
                            'of {length} bytes from peer {peer}'.format(
                            piece=block.piece,
                            block=block.offset,
                            length=block.length,
                            peer=self.remote_id))

            writer.write(message)
            await writer.drain()
            return 1
        else:
            return 0

    async def start(self):
        reader = None
        writer = None
        try:
            print("connecting to: " + str(self.ip)+ " "+ str(self.port))
            #wait for at most 10 seconds
            reader, writer = await asyncio.open_connection(self.ip, self.port)
            print('succesfully made connection with Peer {}:{}'.format(self.ip, self.port))

            #handshake
            await self._handshake(reader, writer)
            print('Succesfull handshake with Peer {}:{}'.format(self.ip, self.port))

            # our initial state is choked
            isChoke = True

            """
            #sending our peers the bitfield message
            bitfield = self.pieceTracker.return_bitfield()
            writer.write(Bitfield.encode(data=bitfield))
            await writer.drain()
            print('sent a bitfield message')

            #sending unchoke messages back to peers
            unchoke = struct.pack('>Ib', 1, UNCHOKE)
            writer.write(unchoke)
            await writer.drain()
            print('sent unchoke message')
            """

            # sending back interest message -> let peer know we want to download
            msg = struct.pack('>Ib', 1, 2)
            writer.write(msg)
            await writer.drain()
            isInterested = True

            received_piece = False
            """
            Each message's structure: <message ID><payload>
            The `message ID` is a decimal byte
            The `payload` is the value of `length prefix`
            """

            buf = b''
            async for message in PeerStream(reader, buf):
                if type(message) is Choke:
                    isChoke = True
                elif type(message) is Unchoke:
                    isChoke = False
                elif type(message) is Interested:
                    isInterested = True
                elif type(message) is NotInterested:
                    isInterested = False
                elif type(message) is Piece:
                    received_piece = False
                    self.pieceTracker.block_received(
                        peer_id=self.remote_id, piece_index=message.index,
                        block_offset=message.begin, data=message.block, writer=writer)
                    
                    # every time we recieve a piece, send a have message to this piece

                elif type(message) is Request:
                    """
                    print('sending back request index: {index}, begin:{begin}, '
                                    'length: {length}'.format(
                                    index=message.index,
                                    begin=message.begin,
                                    length=message.length))
                    piece_requested = self.pieceTracker.have_pieces[index][begin]
                    writer.write(Piece.encode(piece_requested))
                    await writer.drain()
                    """
                    pass
                elif type(message) is Bitfield:
                    self.pieceTracker.add_peer(self.remote_id,
                                                    message.bitfield)
                elif type(message) is Have:
                    self.pieceTracker.update_peer(self.remote_id,
                                                    message.index)
                elif type(message) is KeepAlive:
                    pass
                elif type(message) is Cancel:
                    pass    
                # Requesting this current peer
                if isChoke == False:
                    if isInterested == True:
                        if received_piece == False:
                            if len(self.pieceTracker.have_pieces) == self.pieceTracker.total_pieces:
                                print('here before write')
                                self.pieceTracker._write()
                                print('Torrent sucessfully done downloading!')
                                break
                            else:
                                # only send request if not done
                                print('sending a request to peer {}:{}'.format(self.ip, self.port))
                                received_piece = True
                                #self.my_state.append('received_piece')             
                                if await self._request_piece(writer) == 0:
                                    break
        except ConnectionError:
            print('Failed to connect to Peer {}:{}'.format(self.ip, self.port))
        except (ConnectionRefusedError, TimeoutError):
            print('Unable to connect to peer due to time out')
        except (ConnectionResetError, CancelledError):
            print('Connection closed')
        except Exception as e:
            print('An error occurred')
            #self.cancel(writer)
            print(e)
            raise e
        self.cancel(writer)
    
    def cancel(self, writer):
        """
        Sends the cancel message to the remote peer and closes the connection.
        """
        print('Closing peer {id}'.format(id=self.str_id))
        if writer:
            writer.close()
        

"""
async iterator continuously reads from
the given stream reader and tries to parse valid BitTorrent messages from
off that stream of bytes.
"""
class PeerStream:
    def __init__(self, reader, buf: bytes=None):
        self.reader = reader
        self.buffer = buf


    async def __aiter__(self):
        return self

    async def __anext__(self):
        header_length = 4
        while True:
            msg_len = await self.reader.read(4)

            while(len(msg_len) < 4):
                left = await self.reader.read(4 - len(msg_len))
                msg_len += left

            #converting to integer
            converted = struct.unpack('>I', msg_len[0:4])[0]

            if converted == 0:
                return KeepAlive()
            
            res = await self.reader.read(converted)
            #keep readin until we get the full packet
            while len(res) < converted:
                extra = await self.reader.read(converted - len(res))
                res += extra

            if res is None:
                raise StopAsyncIteration()
            
            # Payload format: <id><payload>
            self.buffer = self.buffer + res
            message_id = struct.unpack('>b', self.buffer[0:1])[0]

            def consume(buf):
                """Consume the current message from the read buffer"""
                return self.buffer[converted:]
            
            if message_id == CHOKE:
                self.buffer = consume(self.buffer)
                return Choke()
            elif message_id == UNCHOKE:
                self.buffer = consume(self.buffer)
                return Unchoke()
            elif message_id == INTERESTED:
                self.buffer = consume(self.buffer)
                return Interested()
            elif message_id == NOTINTERESTED:
                self.buffer = cconsume(self.buffer)
                return NotInterested()
            elif message_id == HAVE:
                result = Have.decode(self.buffer)
                self.buffer = consume(self.buffer)
                return result
            elif message_id == BITFIELD:
                result = Bitfield.decode(self.buffer)
                self.buffer = consume(self.buffer)
                return result
            elif message_id == REQUEST:
                result = Request.decode(self.buffer)
                self.buffer = consume(self.buffer)
                return result
            elif message_id == PIECE:
                result = Piece.decode(self.buffer)
                self.buffer = consume(self.buffer)
                return result
            elif message_id == CANCEL:
                self.buffer = consume(self.buffer)
                return Cancel()
            else:
                print("Unsupported message with message_id: {}".format(message_id))
                exit(1)



