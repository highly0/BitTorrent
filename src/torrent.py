from typing import List, NamedTuple
from bencoding import Decoder, Encoder
from collections import namedtuple
from hashlib import sha1

# represents a file within the metainfo's info dict
TorrentFile = namedtuple('TorrentFile', ['name', 'length'])

class Torrent:
    """
    This class is a wrapper around the information found inside a 
    .torrent file.
    """
    def __init__(self, filename) -> None:
        
        self.filename = filename # torrent filename
        self.files = []

        # parse metadata inside torrent file
        with open(self.filename, 'rb') as f:
            metainfo = f.read()
            self.metainfo = Decoder(metainfo).decode()
            self.info_hash = self._hash_info()
            self._extract_file()

    def _hash_info(self) -> bytes:
        """
        Creates a urlencoded 20-byte SHA1 hash of the value of the info 
        key from the torrent file. The info value will be a bencoded 
        dictionary.
        Returns:
            20-byte info hash
        """
        info = Encoder(self.metainfo[b'info']).encode()
        info_hash = sha1(info).digest()
        return info_hash

    def _extract_file(self):
        """
        This function identifies and extracts the file(s) of the torrent from the 
        info field of the metainfo.
        """
        if self.multi_file:
            # multi-file torrent
            self.files = [ TorrentFile(file[b'path'], file[b'length']) for file in self.metainfo[b'info'][b'files']]
        else:
            # single-file torrent
            name = self.metainfo[b'info'][b'name'].decode('utf-8') # filename
            length = self.metainfo[b'info'][b'length'] # length of file in bytes
            self.files.append(TorrentFile(name, length))

    @property
    def multi_file(self) -> bool:
        """
        Checks if torrent is mutli-file
        Returns:
            bool: if torrent contains mutliple files returns
            true; otherwise, false.
        """
        return b'files' in self.metainfo[b'info']

    @property
    def total_size(self) -> int:
        """
        This function gets the total size of the torrent's file(s)
        Returns:
            int: total size of file in bytes
        """
        length = 0
        if self.multi_file:
            for file in self.files:
                length += file.length
        else:
            length = self.files[0].length
        return length

    @property
    def output_file(self) -> str:
        """
        This function returns the recommended filename or directory of the output.
        Returns:
            str: filename or directory
        """
        return self.metainfo[b'info'][b'name'].decode('utf-8')

    @property
    def announce(self) -> str:
        """
        This function decodes the url to the tracker
        Returns:
            str: the announce URL of the tracker
        """
        return self.metainfo[b'announce'].decode('utf-8')
    
    @property
    def piece_length(self) -> int:
        """
        This function decodes the number of bytes in each piece
        Returns:
            int: length in bytes of each piece
        """
        return self.metainfo[b'info'][b'piece length']

    @property
    def pieces(self) -> List:
        """
        This function decodes the str of all 20-byte SHA1 hash values
        Returns:
            List: SHA1 hash of each piece
        """
        data = self.metainfo[b'info'][b'pieces']
        pieces = []
        offset = 0
        length = len(data)

        while offset < length:
            pieces.append(data[offset:offset+20])
            offset += 20
        return pieces