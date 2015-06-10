import json
import socket
import sys

from six.moves.urllib.parse import urlparse
from tornado import ioloop
from tornado import gen
from tornado.ioloop import PeriodicCallback

from circus.client import CallError
from circusweb import logger
from circusweb.session import get_controller


# def set_error(message):
#     return set_message("An error happened: %s" % message)


@gen.coroutine
def run_command(command, message, endpoint, redirect_url,
                redirect_on_error=None, args=None, kwargs=None, session=None):

    command = getattr(get_controller(), command)

    if redirect_on_error is None:
        redirect_on_error = redirect_url
    args = args or ()
    kwargs = kwargs or {}
    kwargs['endpoint'] = endpoint

    try:
        logger.debug('Running %r' % command)
        res = yield gen.Task(command, *args, **kwargs)
        logger.debug('Result : %r' % res)

        if res['status'] != 'ok':
            message = "An error happened: %s" % res['reason']
    except CallError as e:
        message = "An error happened: %s" % e
        redirect_url = redirect_on_error

    if message and session:
        session.messages.append(message)
    raise gen.Return(redirect_url)


class AutoDiscovery(object):

    def __init__(self, multicast_endpoint, loop, rediscover_timeout=10):
        super(AutoDiscovery, self).__init__()
        self.multicast_endpoint = multicast_endpoint
        self.discovered_endpoints = set()
        self.rediscover_timeout = rediscover_timeout
        self.loop = loop

        self.create_socket()
        self.callback = PeriodicCallback(self.rediscover(),
                                         rediscover_timeout * 1000)

        self.loop.add_handler(self.sock.fileno(), self.get_message,
                              ioloop.IOLoop.READ)

    def create_socket(self):
        any_addr = '0.0.0.0'

        parsed = urlparse(self.multicast_endpoint).netloc.split(':')
        self.multicast_addr, self.multicast_port = parsed[0], int(parsed[1])

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                  socket.IPPROTO_UDP)
        self.sock.bind((any_addr, 0))
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(0)

    def rediscover(self):
        major_python_version_number = sys.version_info[0]
        if major_python_version_number == 3:
            data = b'""'
        else:
            data = '""'
        self.sock.sendto(data, (self.multicast_addr, self.multicast_port))

    def get_message(self, fd_no, type):
        data, address = self.sock.recvfrom(1024)
        data = json.loads(data)
        endpoint = data.get('endpoint', '')
        if endpoint.startswith('tcp://'):
            # In case of multi interface binding i.e:
            # tcp://0.0.0.0:5557
            endpoint = endpoint.replace('0.0.0.0', address[0])

        self.discovered_endpoints.add(endpoint)

    def get_endpoints(self):
        return list(self.discovered_endpoints)
