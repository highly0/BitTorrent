import http
import random
import socket
from http import client
from bencoding import Decoder
from torrent import Torrent
from urllib import request, parse
from collections import namedtuple
from struct import unpack

# represents a peer from the tracker's response
Peer = namedtuple('Peer', ['ip', 'port'])

class TrackerResponse:
    """
    The response from the tracker after a successful connection
    to the tracker's announce URL.
    """
    def __init__(self, response: dict) -> None:
        #print(response)
        self.response = response

    @property
    def failure(self):
        """
        If a failed response is sent, then this function
        captures it and returns the error message that 
        explains why the tracker request failed. Otherwise,
        None is returned
        """
        if b'failure reason' in self.response:
            return self.response[b'failure reason'].decode('utf-8')
        return None

    @property
    def interval(self) -> int:
        """
        Interval in seconds that the client should wait between sending
        regular requests to the tracker.
        """
        return self.response.get(b'interval', 0)

    @property
    def complete(self) -> int:
        """
        Number of peers with the entire file, i.e. seeders.
        """
        return self.response.get(b'complete', 0)

    @property
    def incomplete(self) -> int:
        """
        Number of non-seeder peers, aka "leechers".
        """
        return self.response.get(b'incomplete', 0)

    @property
    def peers(self):
        """
        A list of namedTuples for each peer structured as (ip, port)
        """
        #Peer = namedtuple('Peer', ['ip', 'port'])
        peers = self.response[b'peers']
        print(peers)
        if type(peers) == list:
            ip = None
            port = None
            result = []

            for i in peers:
                for k, v in i.items():
                    decoded = k.decode('utf-8')
                    if(decoded == "ip" or decoded == 'ip'):
                        if v is not None:
                            ip = v.decode('utf-8')
      
                    if(decoded == "port" or decoded == 'port'):
                        port = v

                    if ip and port:
                        result.append(Peer(ip, port))
            return result
        else:
            print('peers is a binary model')
            print(peers)
            # split string into pieces of length 6 bytes, where the
            # first 4 bytes is the IP addr and the last 2 is the port no.
            peers = [peers[i:i+6] for i in range(0, len(peers), 6)]

            # convert encoded address to a list of namedTuples
            return [ Peer(socket.inet_ntoa(peer[:4]), self._decode_port(peer[4:])) for peer in peers ]

    # private functions

    def _decode_port(self, port) -> int:
        """
        Converts a 32-bit packed binary port number to int
        Args:
            port ([byte]): [port number corresponding to peer in bytes]
        Returns:
            port number in int
        """
        return unpack("!H", port)[0]

class Tracker:
    """
    Represents the connection to a tracker for a given Torrent that is either
    under download or seeding state.
    """

    def __init__(self, torrent: Torrent):
        self.torrent = torrent
        self.peer_id = _calculate_peer_id()

    def connect(self, first: bool, uploaded: int = 0, downloaded: int = 0):
        """
        Makes the announce call to the tracker to update metrics on the 
        torrent and get a list of avaliable peers to connect to.

        Args:
            first (bool): Whether or not this is the first announce call
            uploaded (int, optional): The total number of bytes uploaded. Defaults to 0.
            downloaded (int, optional): The total number of bytes downloaded. Defaults to 0.
        """
        self.http_conn = client.HTTPConnection(self.torrent.announce, 6881)

        # create Tracker request
        params = {
            'info_hash': self.torrent.info_hash,
            'peer_id': self.peer_id.encode(),
            'port': 6881,
            'uploaded': uploaded,
            'downloaded': downloaded,
            'left': self.torrent.total_size - downloaded,
            'compact': 1
        }

        if first:
            params['event'] = 'started'

        # add params to tracker url
        url = self.torrent.announce + '?' + parse.urlencode(params)
        # send GET request
        with request.urlopen(url) as f:
            if not f.status == 200:
                raise ConnectionError(f'unable to connect to tracker: status code {f.status}')
            data = f.read() # read response
            return TrackerResponse(Decoder(data).decode())

    def close(self):
        """
        Closes the connection to the tracker server
        """
        self.http_conn.close()

    def raise_for_error(self, tracker_response):
        """
        A (hacky) fix to detect errors by tracker even when the response has a status code of 200  
        """
        try:
            # a tracker response containing an error will have a utf-8 message only.
            # see: https://wiki.theory.org/index.php/BitTorrentSpecification#Tracker_Response
            message = tracker_response.decode("utf-8")
            if "failure" in message:
                raise ConnectionError('Unable to connect to tracker: {}'.format(message))

        # a successful tracker response will have non-uncicode data, so it's a safe to bet ignore this exception.
        except UnicodeDecodeError:
            pass

    # def construct_tracker_parameters(self):
    #     """
    #     Constructs the URL parameters used when issuing the announce call
    #     to the tracker.
    #     """
    #     pass


def _calculate_peer_id():
    """
    Calculate and return a unique Peer ID.
    The `peer id` is a 20 byte long identifier. This implementation use the
    Azureus style `-RV0001-<random-characters>`.
    """
    return '-RV0001-' + ''.join(str(random.randint(0, 9)) for _ in range(12))

def decode_port(port):
    """
    Converts a 32-bit packed binary port number to int
    """
    pass