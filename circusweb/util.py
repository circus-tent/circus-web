import os
import json
import socket
import select

from time import time
from threading import Thread, Lock
from urlparse import urlparse

from mako.lookup import TemplateLookup
from bottle import request, route as route_, url, redirect

from circusweb import logger, __version__
from circusweb.controller import CallError
from circusweb.session import get_session, connect_to_circus, get_client


def set_message(message):
    session = get_session()
    session['message'] = message
    session.save()


def set_error(message):
    return set_message("An error happened: %s" % message)


def run_command(func, message, redirect_url, redirect_on_error=None,
                args=None, kwargs=None):

    func = getattr(get_client(), func)

    if redirect_on_error is None:
        redirect_on_error = redirect_url
    args = args or ()
    kwargs = kwargs or {}

    try:
        logger.debug('Running %r' % func)
        res = func(*args, **kwargs)
        logger.debug('Result : %r' % res)

        if res['status'] != 'ok':
            message = "An error happened: %s" % res['reason']
    except CallError, e:
        message = "An error happened: %s" % e
        redirect_url = redirect_on_error

    if message:
        set_message(message)
    redirect(redirect_url)


CURDIR = os.path.dirname(__file__)
TMPLDIR = os.path.join(CURDIR, 'templates')
TMPLS = TemplateLookup(directories=[TMPLDIR])
MEDIADIR = os.path.join(CURDIR, 'media')


def render_template(template, **data):
    """Finds the given template and renders it with the given data.

    Also adds some data that can be useful to the template, even if not
    explicitely asked so.

    :param template: the template to render
    :param **data: the kwargs that will be passed when rendering the template
    """
    tmpl = TMPLS.get_template(template)
    client = get_client()

    # send the last message stored in the session in addition, in the "message"
    # attribute.
    server = '%s://%s' % (request.urlparts.scheme, request.urlparts.netloc)

    return tmpl.render(client=client, version=__version__,
                       session=get_session(), SERVER=server, **data)


def route(*args, **kwargs):
    """Replace the default bottle route decorator and redirect to the
    connection page if the client is not defined
    """
    ensure_client = kwargs.get('ensure_client', True)

    def wrapper(func):
        def client_or_redirect(*fargs, **fkwargs):
            if ensure_client:
                client = get_client()
                session = get_session()

                if client is None:
                    session = get_session()
                    if session.get('endpoint', None) is not None:
                        # XXX we need to pass SSH too here
                        connect_to_circus(session['endpoint'])
                    else:
                        return redirect(url('connect'))

            return func(*fargs, **fkwargs)
        return route_(*args, **kwargs)(client_or_redirect)
    return wrapper


class AutoDiscoveryThread(Thread):

    def __init__(self, multicast_endpoint, rediscover_timeout=30):
        super(AutoDiscoveryThread, self).__init__()
        self.multicast_endpoint = multicast_endpoint
        self.discovered_endpoints = []
        self.rediscover_timeout = rediscover_timeout
        self.lock = Lock()

    def run(self):
        any_addr = '0.0.0.0'

        multicast_addr, multicast_port = urlparse(self.multicast_endpoint) \
            .netloc.split(':')
        multicast_port = int(multicast_port)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                             socket.IPPROTO_UDP)
        sock.bind((any_addr, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)

        sock.sendto('""', (multicast_addr, multicast_port))

        timer = time()

        while True:
            # Wait for socket event
            ready = select.select([sock], [], [], self.rediscover_timeout)

            if ready[0]:
                data, address = sock.recvfrom(1024)
                data = json.loads(data)
                endpoint = data.get('endpoint', '')
                if endpoint.startswith('tcp://'):
                    # In case of multi interface binding i.e:
                    # tcp://0.0.0.0:5557
                    endpoint = endpoint.replace('0.0.0.0', address[0])

                with self.lock:
                    self.discovered_endpoints.append(endpoint)

            if time() - timer > self.rediscover_timeout * 60:
                # Rediscover every 30 seconds
                with self.lock:
                    self.discovered_endpoints = []
                timer = time()
                sock.sendto('""', (multicast_addr, multicast_port))

    def get_endpoints(self):
        with self.lock:
            return self.discovered_endpoints[:]
