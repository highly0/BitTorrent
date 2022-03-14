import asyncio
import struct
import random
import string
import os
import math
import time

from hashlib import sha1
from typing import List
from torrent import Torrent 
from collections import namedtuple, defaultdict
from concurrent.futures import CancelledError
from MessageType import Unchoke, Choke, Interested, NotInterested, Have, Bitfield, KeepAlive, Cancel, Piece, Request
REQUEST_SIZE = 2**14 # 16384
"""
Diffulty coming up with a algorithm to request what piece --> what to request first
"""


class Block:
    """
    The block is a partial piece, this is what is requested and transferred
    between peers.
    """
    Missing = 0
    Pending = 1
    Retrieved = 2

    def __init__(self, piece: int, offset: int, length: int):
        self.piece = piece
        self.offset = offset
        self.length = length
        self.status = Block.Missing
        self.data = None


class Piece:
    def __init__(self, index: int, blocks: [], hash_value):
        self.index = index
        self.blocks = blocks
        self.hash = hash_value

    def reset(self):
        """
        Reset all blocks to Missing regardless of current state.
        """
        for block in self.blocks:
            block.status = Block.Missing

    def next_request(self) -> Block:
        """
        Get the next Block to be requested
        """
        missing = [b for b in self.blocks if b.status is Block.Missing]
        if missing:
            missing[0].status = Block.Pending
            return missing[0]
        return None

    def block_received(self, offset: int, data: bytes):
        """
        Update block information that the given block is now received
        :param offset: The block offset (within the piece)
        :param data: The block data
        """
        matches = [b for b in self.blocks if b.offset == offset]
        block = matches[0] if matches else None
        if block:
            block.status = Block.Retrieved
            block.data = data
        else:
            print('Trying to complete a non-existing block {offset}'
                            .format(offset=offset))

    def is_complete(self) -> bool:
        """
        Checks if all blocks for this piece is retrieved (regardless of SHA1)
        :return: True or False
        """
        blocks = [b for b in self.blocks if b.status is not Block.Retrieved]
        return len(blocks) is 0

    def is_hash_matching(self):
        """
        Check if a SHA1 hash for all the received blocks match the piece hash
        from the torrent meta-info.
        :return: True or False
        """
        piece_hash = sha1(self.data).digest()
        return self.hash == piece_hash

    @property
    def data(self):
        """
        Return the data for this piece (by concatenating all blocks in order)
        NOTE: This method does not control that all blocks are valid or even
        existing!
        """
        retrieved = sorted(self.blocks, key=lambda b: b.offset)
        blocks_data = [b.data for b in retrieved]
        return b''.join(blocks_data)

# The type used for keeping track of pending request that can be re-issued
PendingRequest = namedtuple('PendingRequest', ['block', 'added'])
File = namedtuple('File', ['file_length', 'file_pieces', 'pieces_written', 'blocks_written', 'current_idx'])

class Piecetracker:
    """
    The PieceManager is responsible for keeping track of all available
    pieces for the connected peers and what pieces we need to request next
    """
    def __init__(self, torrent: Torrent) -> None:
        self.torrent = torrent
        self.peers = {}
        self.pending_blocks = []
        self.missing_pieces = []
        self.ongoing_pieces = []
        self.have_pieces = [] # array of pieces
        self.max_pending_time = 300 * 1000  # 5 minutes

        #create an array for all of our missin gpieces
        self.missing_pieces = self.fetch_pieces()

        # create an array that have all of our pieces in the torrent
        self.pieces_list = self.fetch_pieces()
        self.total_pieces = len(torrent.pieces)

        # an array that holds our file information for writing purposes
        self.file_info = {}
        self.file_info = self.populate_file_info()

        # to keep track of what file we are currently on
        self.curr_file_idx = 0

        # array that keeps track of all pieces when finished
        self.result = []
    
        self.fd = []
        for dest in self.torrent.files:
            if type(dest.name) is list:
                tmp = "result/" + (dest.name[0].decode("utf-8"))
            elif type(dest.name) is bytes:
                tmp = "result/" + (dest.name[0]).decode("utf-8") 
            else:
                tmp = "result/" + (dest.name)
            curr = os.open(tmp, os.O_RDWR | os.O_CREAT)
            self.fd.append(curr)
        
    def fetch_pieces(self) -> List[Piece]:
        """
        return the list of missing pieces. And for each piece, construct its blocks.
        """
        torrent = self.torrent
        pieces = []
        total_pieces = len(torrent.pieces)
        blocks_per_piece = math.ceil(torrent.piece_length / REQUEST_SIZE)
        for i, hash_value in enumerate(torrent.pieces):
            # Number of blocks for each piece is calculated as
            # CEIL(piece length / request size)
            # Note: final piece could have fewer blocks as well as 
            # the final block might be smaller than the rest.
            if i < (total_pieces - 1): # if not last piece
                blocks = [Block(i, offset * REQUEST_SIZE, REQUEST_SIZE)
                            for offset in range(blocks_per_piece)]
            else:
                # account for (likely) smaller size of last piece
                last_piece_len = torrent.total_size % torrent.piece_length
                num_blocks = math.ceil(last_piece_len / REQUEST_SIZE)
                
                blocks = [Block(i, offset * REQUEST_SIZE, REQUEST_SIZE)
                            for offset in range(num_blocks)]

                if last_piece_len % REQUEST_SIZE > 0:
                    # account for (likely) smaller size of last block
                    last_block = blocks[-1]
                    last_block.length = last_piece_len % REQUEST_SIZE
                    blocks[-1] = last_block

            # construct Piece and add to list of missing pieces
            pieces.append(Piece(i, blocks, hash_value))
        return pieces

    # generate a bitfield message to indiciate what we have and what we don't
    def return_bitfield(self):
        data = ''
        for piece in self.torrent.pieces:
            if piece in self.have_pieces:
                data += '1'
            else:
                data += '0'
        return int(data, 2)
        
    

    def close(self):
        """
        Close open output file
        """
        if self.fd:
            os.close(self.fd)

    @property
    def complete(self) -> bool:
        """
        return true if all pieces have been downloaded, false otherwise
        """
        return len(self.have_pieces) == self.total_pieces


    def add_peer(self, peer_id, bitfield):
        """
        Adds a peer and its bitfield to dict of peers.
        """   
        self.peers[peer_id] = bitfield

    def update_peer(self, peer_id, i: int):
        """
        Updates bitfield of a peer for have messages
        """
        if peer_id in self.peers:
            if self.peers[peer_id][i] == 0:
                self.peers[peer_id][i] = 1 # set bit to indicate peer has piece i

    def next_request(self, peer_id) -> Block:
        """
        return the next block to be requested
        """
        if peer_id not in self.peers:
            return None

        block = self._expired_requests(peer_id)
        if not block:
            block = self._next_ongoing(peer_id)
            if not block:
                tmp = self._get_rarest_piece(peer_id)
                if tmp is None:
                    return None
                else:
                    block = tmp.next_request()
                #block = self._get_rarest_piece(peer_id).next_request()
        return block

    def block_received(self, peer_id, piece_index, block_offset, data, writer):
        """
        when a block is received from a peer. If the hash succeeds the partial piece is written to
        disk and the piece is indicated as Have.
        """
        print('Received block {block_offset} for piece {piece_index} '
                      'from peer {peer_id}: '.format(block_offset=block_offset,
                                                     piece_index=piece_index,
                                                     peer_id=peer_id))

        # Remove from pending requests
        for index, request in enumerate(self.pending_blocks):
            if request.block.piece == piece_index and \
               request.block.offset == block_offset:
                del self.pending_blocks[index]
                break

        pieces = [p for p in self.ongoing_pieces if p.index == piece_index]
        piece = pieces[0] if pieces else None
        if piece:
            piece.block_received(block_offset, data)
            if piece.is_complete():
                if piece.is_hash_matching():
                    self.result.append(piece)
                    self.ongoing_pieces.remove(piece)
                    self.have_pieces.append(piece)

                    # every time we receive a piece, announce to our peers
                    # that we have this piece
                    """
                    writer.write(Have.encode(piece))
                    await writer.drain()
                    """
                else:
                    print('Discarding corrupt piece {index}'
                                 .format(index=piece.index))
                    piece.reset()
        else:
            print('Trying to update piece that is not ongoing!')

                            

    def _expired_requests(self, peer_id) -> Block:
        """
        Go through previously requested blocks, if any one have been in the
        requested state for longer than `MAX_PENDING_TIME` return the block to
        be re-requested.
        If no pending blocks exist, None is returned
        """
        current = int(round(time.time() * 1000))
        for request in self.pending_blocks:
            if self.peers[peer_id][request.block.piece]:
                if request.added + self.max_pending_time < current:
                    print('Re-requesting block {block} for '
                                 'piece {piece}'.format(
                                    block=request.block.offset,
                                    piece=request.block.piece))
                    # Reset expiration timer
                    request.added = current
                    return request.block
        return None

    def _next_ongoing(self, peer_id) -> Block:
        """
        Go through the ongoing pieces and return the next block to be
        requested or None if no block is left to be requested.
        """
        for piece in self.ongoing_pieces:
            if self.peers[peer_id][piece.index]:
                # Is there any blocks left to request in this piece?
                block = piece.next_request()
                if block:
                    self.pending_blocks.append(
                        PendingRequest(block, int(round(time.time() * 1000))))
                    return block
        return None

    def _get_rarest_piece(self, peer_id):
        """
        go through our missing pieces and find the 
        rarest one -> the one that the fewest peer have
        """
        piece_count = defaultdict(int)
        for piece in self.missing_pieces:
            if not self.peers[peer_id][piece.index]:
                continue
            for p in self.peers:
                if self.peers[p][piece.index]:
                    piece_count[piece] += 1
        
        if len(piece_count) == 0:
            return None
        else:
            rarest_piece = min(piece_count, key=lambda p: piece_count[p])
            self.missing_pieces.remove(rarest_piece)
            self.ongoing_pieces.append(rarest_piece)
            return rarest_piece

    def _next_missing(self, peer_id) -> Block:
        """
        Go through the missing pieces and return the next block to request
        or None if no block is left to be requested, also change missing state to ongoing
        """
        for index, piece in enumerate(self.missing_pieces):
            if self.peers[peer_id][piece.index]:
                # Move this piece from missing to ongoing
                piece = self.missing_pieces.pop(index)
                self.ongoing_pieces.append(piece)
                # The missing pieces does not have any previously requested
                # blocks (then it is ongoing).
                return piece.next_request()
        return None

    def populate_file_info(self) -> {}:
        idx = 0
        result = {}
        """
        length:             file_info[curr_file_idx][0] 
        num pieces:         file_info[curr_file_idx][1] 
        num pieces written: file_info[curr_file_idx][2] 
        num blocks written: file_info[curr_file_idx][3]
        num bytes written:  file_info[curr_file_idx][4]
        """
        for file in self.torrent.files:
            file_pieces = (file.length / self.torrent.piece_length)
            result[idx] = [file.length, file_pieces, 0, 0, 0]
            idx += 1
            #result.append(File(file.length, file_pieces, 0, 0, 0))
            #print('this file {} has length: {}, and {} pieces'.format(file.name, file.length, file_pieces))
        return result


    def _write(self):
        """
        Write the given piece to disk
        """
        print('Finished downloading pieces, writing them to our file!')
        file_info = self.file_info
        torrent = self.torrent
        result = self.result
        piece_length = self.torrent.piece_length
        total_pieces = self.total_pieces
        left_over_data = None
        left_over_len = None
        print('\n')

        # single file
        if len(file_info) == 1:
            for piece in self.result:
                pos = piece.index * self.torrent.piece_length
                os.lseek(self.fd[0], pos, os.SEEK_SET)
                os.write(self.fd[0], piece.data)
        # multi-file
        else:      
            piece_idx = 0
            block_idx = 0
            blocks_per_piece = torrent.piece_length / REQUEST_SIZE
            
            for file, info in file_info.items():
                curr_file_idx = self.curr_file_idx
                curr_file_length = info[0]
                curr_file_pieces = info[1]
                pieces_written = info[2]
                blocks_written = info[3]
                bytes_written = info[4]

                #length left initially is the entire file length
                length_left = curr_file_length
                print('initial length: {}'.format(length_left))

                if left_over_data is not None and left_over_len is not None:
                    if length_left < left_over_len:
                        print('consuming left over (less) of {}'.format(length_left))
                        os.lseek(self.fd[curr_file_idx], 0, os.SEEK_SET) 
                        os.write(self.fd[curr_file_idx], left_over_data[0:length_left])
                        info[4] += length_left
                        
                        left_over_data = left_over_data[length_left:]
                        left_over_len = left_over_len - length_left
                        length_left = 0     
                    else:
                        pos = 0
                        print('consuming left over of {}'.format(left_over_len))
                        os.lseek(self.fd[curr_file_idx], pos, os.SEEK_SET) 
                        os.write(self.fd[curr_file_idx], left_over_data)
                        pos += left_over_len
                        block_idx += 1

                        #updating the number of bytes written
                        info[4] = left_over_len
                        length_left -= left_over_len
                        
                   
                        # adding the rest of the piece
                        blocks_written = 0
                        current_blocks = result[piece_idx].blocks[block_idx:]
                        if length_left >= (len(current_blocks) * REQUEST_SIZE):
                            for block in current_blocks:
                                os.lseek(self.fd[curr_file_idx], pos, os.SEEK_SET) 
                                os.write(self.fd[curr_file_idx], block.data)
                                pos += REQUEST_SIZE
                                # updating the number of blocks written
                                blocks_written += 1
                                info[3] += 1
                            # done with the current piece  
                            block_idx = 0
                            print('consumed: {} blocks, info[3] is: {}'.format(blocks_written, info[3]))
                            length_left -= blocks_written * REQUEST_SIZE
                            # after finishing with the piece, move on to the next one
                            piece_idx += 1  
                        # consumed all of the byte for this block/piece
                        left_over_data = None
                        left_over_len = None 
                   

                while length_left > 0:
                    num_pieces = math.floor(length_left / piece_length)
                    print('num pieces to consume: {}'.format(num_pieces))

                    print('current pieces: {}, current blocks {}, current bytes: {}'.format(info[2], info[3], info[4]))
                    position = (info[2] * piece_length) + (info[3] * REQUEST_SIZE) + info[4]
                    print('starting postion: {}'.format(position))
                    print('length left now is {}'.format(length_left))
                    #writing all of the possible pieces for this file
                    pieces_written = num_pieces
                    if num_pieces > 0:
                        while num_pieces > 0:
                            os.lseek(self.fd[curr_file_idx], position, os.SEEK_SET) 
                            os.write(self.fd[curr_file_idx], result[piece_idx].data)
                            # increment the pieces written
                            info[2] += 1
                            piece_idx += 1
                            num_pieces -= 1
                            position += piece_length
                        block_idx = 0
                        #update the length
                        length_left -= pieces_written * piece_length
                        print()
                        print('length left now is {}'.format(length_left))


                    num_blocks = math.floor(length_left / REQUEST_SIZE)
                    print('num blocks to consume: {}'.format(num_blocks))
                    #writing all of the possible blocks for this file
                    blocks_written = num_blocks
                    if num_blocks > 0:
                        print('current block idx: {}'.format(block_idx))
                        while num_blocks > 0:
                            os.lseek(self.fd[curr_file_idx], position, os.SEEK_SET)
                            curr_block = result[piece_idx].blocks[block_idx]
                            os.write(self.fd[curr_file_idx], curr_block.data)
                            info[3] += 1
                            block_idx += 1
                            num_blocks -=1
                            position += REQUEST_SIZE
                        #update the length
                        length_left -= blocks_written * REQUEST_SIZE
                        print('length left now is {}'.format(length_left))

                    num_bytes = length_left
                    if num_bytes > 0:
                        curr_block = result[piece_idx].blocks[block_idx]
                        os.lseek(self.fd[curr_file_idx], position, os.SEEK_SET)
                        os.write(self.fd[curr_file_idx], curr_block.data[0:num_bytes])
                        info[4] += num_bytes
                        left_over_data = curr_block.data[num_bytes:]  
                        left_over_len = curr_block.length - num_bytes

                        #left_over_len = result[piece_idx][block_idx].length - num_bytes 
                        # done with this file 
                        length_left = 0
                        print('num bytes left: {}'.format(left_over_len)) 
                        print('length left now is {}'.format(length_left))    

                    print('the end, file {} has length: {}'.format(curr_file_idx, length_left))
                    print('\n')
                
                if self.curr_file_idx < len(file_info) - 2:
                    print('incrementing')
                    self.curr_file_idx += 1

