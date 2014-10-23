import argparse
import os
import os.path
import sys
import json
from base64 import b64encode, b64decode
from zmq.eventloop import ioloop
import socket

# Install zmq.eventloop to replace tornado.ioloop
ioloop.install()

try:
    import tornado.httpserver
    import tornado.ioloop
    import tornado.web

    from tornado import gen
    from tornado.escape import json_decode, json_encode  # NOQA
    from tornado.options import define, options  # NOQA
    from tornado.web import URLSpec

    import tornadio2

    from mako import exceptions
    from tomako import MakoTemplateLoader
except ImportError, e:
    reqs = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                        'web-requirements.txt')
    raise ImportError('You need to install dependencies to run the webui. '
                      'You can do so by using "pip install -r '
                      '%s"\nInitial error: %s' % (reqs, str(e)))


from circus.exc import CallError
from circus.util import configure_logger, LOG_LEVELS

from circusweb.namespace import SocketIOConnection
from circusweb import __version__, logger
from circusweb.util import (run_command, AutoDiscovery)
from circusweb.session import (
    connect_to_circus, disconnect_from_circus, get_controller, SessionManager)

from uuid import uuid4
from functools import wraps


CURDIR = os.path.dirname(__file__)
TMPLDIR = os.path.join(CURDIR, 'templates')
STATIC_PATH = os.path.join(CURDIR, 'media')


session_opts = {
    'session.type': 'file',
    'session.cookie_expires': 300,
    'session.data_dir': './data',
    'session.auto': True
}


def require_logged_user(func):

    @wraps(func)
    def wrapped(self, *args, **kwargs):
        controller = get_controller()

        if not self.session.connected or not controller:
            self.clean_user_session()
            return self.redirect(self.application.reverse_url('connect'))

        return func(self, *args, **kwargs)

    return wrapped


class BaseHandler(tornado.web.RequestHandler):

    def prepare(self):
        session_id = self.get_secure_cookie('session_id')
        if not session_id or not SessionManager.get(session_id):
            session_id = uuid4().hex
            session = SessionManager.new(session_id)
            self.set_secure_cookie('session_id', session_id)
        else:
            session = SessionManager.get(session_id)
        self.session = session
        self.session_id = session_id

    def render_template(self, template_path, **data):
        namespace = self.get_template_namespace()
        if self.session.messages:
            messages = self.session.messages
            self.session.messages = []
        else:
            messages = []
        server = '%s://%s/' % (self.request.protocol, self.request.host)
        namespace.update({'controller': get_controller(),
                          'version': __version__,
                          'b64encode': b64encode,
                          'dumps': json.dumps,
                          'session': self.session, 'messages': messages,
                          'SERVER': server})

        # Stats endpoints
        controller = get_controller()
        endpoints = {}
        if self.session.endpoints and controller:
            for endpoint in self.session.endpoints:
                client = controller.get_client(endpoint)
                if client and client.stats_endpoint:
                    endpoints[endpoint] = client.stats_endpoint
                else:
                    endpoints[endpoint] = None
            namespace.update({
                'endpoints_list': app.auto_discovery.get_endpoints(),
                'endpoints': endpoints})

        namespace.update(data)

        try:
            template = app.loader.load(template_path)
            return template.generate(**namespace)
        except Exception:
            print exceptions.text_error_template().render()

    def clean_user_session(self):
        """Disconnect the endpoint the user was logged on + Remove cookies."""
        for endpoint in self.session.endpoints:
            disconnect_from_circus(endpoint)
        self.session.endpoints = set()

    def run_command(self, *args, **kwargs):
        """Run a command in a gen.Task, include current session"""
        kwargs['session'] = self.session
        return gen.Task(run_command, *args, **kwargs)


class IndexHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self):
        controller = get_controller()
        self.finish(self.render_template('index.html', controller=controller))


class ConnectHandler(BaseHandler):

    def show_form(self):
        self.finish(
            self.render_template('connect.html',
                                 endpoints=app.auto_discovery.get_endpoints()))

    def get(self):
        self.show_form()

    @tornado.web.asynchronous
    @gen.coroutine
    def post(self):
        endpoints_list = list(self.session.endpoints)
        endpoints = self.get_arguments('endpoint_list', [])

        # If no selection in list
        if not endpoints:
            endpoints = self.get_arguments('endpoint_direct', [])

        if not endpoints:
            self.redirect(self.reverse_url('disconnect'))
            raise StopIteration()

        for endpoint in endpoints:
            try:
                yield gen.Task(connect_to_circus,
                               tornado.ioloop.IOLoop.instance(),
                               endpoint)
            except CallError:
                self.session.messages.append("Impossible to connect to %s" %
                                             endpoint)
            else:
                if endpoint not in app.auto_discovery.get_endpoints():
                    app.auto_discovery.discovered_endpoints.add(endpoint)
                self.session.endpoints.add(endpoint)
        for endpoint in endpoints_list:
            if endpoint not in endpoints:
                self.session.endpoints.remove(endpoint)

        self.redirect(self.reverse_url('index'))


class DisconnectHandler(BaseHandler):

    @require_logged_user
    def get(self):
        self.clean_user_session()
        self.session.messages.append("You are now disconnected")
        self.redirect(self.reverse_url('index'))


class WatcherAddHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def post(self, endpoint):
        url = yield self.run_command(
            'add_watcher',
            kwargs=dict((k, v[0]) for k, v in
                        self.request.arguments.iteritems()),
            message='added a new watcher', endpoint=b64decode(endpoint),
            redirect_url=self.reverse_url('watcher',
                                          self.get_argument('name').lower()),
            redirect_on_error=self.reverse_url('index'))
        self.redirect(url)


class WatcherHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name):
        controller = get_controller()
        endpoint = b64decode(endpoint)
        pids = yield gen.Task(controller.get_pids, name, endpoint)
        self.finish(self.render_template('watcher.html', pids=pids, name=name,
                                         endpoint=endpoint))


class WatcherSwitchStatusHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name):
        url = yield self.run_command(command='switch_status',
                                     message='status switched',
                                     endpoint=b64decode(endpoint),
                                     args=(name,),
                                     redirect_url=self.reverse_url('index'))
        self.redirect(url)


class KillProcessHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name, pid):
        msg = 'process {pid} killed sucessfully'
        url = yield self.run_command(
            command='killproc',
            message=msg.format(pid=pid),
            endpoint=b64decode(endpoint),
            args=(name, pid),
            redirect_url=self.reverse_url('watcher', endpoint, name))
        self.redirect(url)


class DecrProcHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name):
        msg = 'removed one process from the {watcher} pool'
        url = yield self.run_command(command='decrproc',
                                     message=msg.format(watcher=name),
                                     endpoint=b64decode(endpoint),
                                     args=(name,),
                                     redirect_url=self.reverse_url('watcher',
                                                                   endpoint,
                                                                   name))
        self.redirect(url)


class IncrProcHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name):
        msg = 'added one process to the {watcher} pool'
        url = yield self.run_command(command='incrproc',
                                     message=msg.format(watcher=name),
                                     endpoint=b64decode(endpoint),
                                     args=(name,),
                                     redirect_url=self.reverse_url('watcher',
                                                                   endpoint,
                                                                   name))
        self.redirect(url)


class SocketsHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint=None):
        controller = get_controller()
        sockets = {}

        if endpoint:
            endpoint = b64decode(endpoint)
            sockets[endpoint] = yield gen.Task(controller.get_sockets,
                                               endpoint=endpoint)
        else:
            for endpoint in self.session.endpoints:
                # Ignore endpoints which doesn't uses sockets
                if controller.get_client(endpoint).use_sockets:
                    sockets[endpoint] = yield gen.Task(controller.get_sockets,
                                                       endpoint=endpoint)

        self.finish(
            self.render_template('sockets.html', sockets=sockets,
                                 controller=controller,
                                 endpoints=self.session.endpoints))


class ReloadconfigHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint):
        url = yield self.run_command(command='reloadconfig',
                                     message='reload the configuration',
                                     endpoint=b64decode(endpoint),
                                     args=[],
                                     redirect_url=self.reverse_url('index'))
        self.redirect(url)



class Application(tornado.web.Application):

    def __init__(self):
        handlers = [
            URLSpec(r'/',
                    IndexHandler, name="index"),
            URLSpec(r'/connect/',
                    ConnectHandler, name="connect"),
            URLSpec(r'/disconnect/',
                    DisconnectHandler, name="disconnect"),
            URLSpec(r'/([^/]+)/reloadconfig/',
                    ReloadconfigHandler, name="reloadconfig"),
            URLSpec(r'/([^/]+)/add_watcher/',
                    WatcherAddHandler, name="add_watcher"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/',
                    WatcherHandler, name="watcher"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/switch_status/',
                    WatcherSwitchStatusHandler, name="switch_status"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/process/kill/([^/]+)/',
                    KillProcessHandler, name="kill_process"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/process/decr/',
                    DecrProcHandler, name="decr_proc"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/process/incr/',
                    IncrProcHandler, name="incr_proc"),
            URLSpec(r'/sockets/',
                    SocketsHandler, name="all_sockets"),
            URLSpec(r'/([^/]+)/sockets/',
                    SocketsHandler, name="sockets"),
        ]

        self.loader = MakoTemplateLoader(TMPLDIR)
        self.router = tornadio2.TornadioRouter(SocketIOConnection)
        handlers += self.router.urls

        settings = {
            'template_loader': self.loader,
            'static_path': STATIC_PATH,
            'debug': True,
            'cookie_secret': 'fxCIK+cbRZe6zhwX8yIQDVS54LFfB0I+nQt0pGp3IY0='
        }

        tornado.web.Application.__init__(self, handlers, **settings)


app = Application()


def main():
    define("port", default=8080, type=int)
    parser = argparse.ArgumentParser(description='Run the Web Console')

    parser.add_argument('--fd', help='FD', default=None, type=int)
    parser.add_argument('--host', help='Host', default='0.0.0.0')
    parser.add_argument('--port', help='port', default=8080)
    parser.add_argument('--endpoint', default=None,
                        help='Circus Endpoint. If not specified, Circus will '
                             'ask you which system you want to connect to')
    parser.add_argument('--version', action='store_true', default=False,
                        help='Displays Circus version and exits.')
    parser.add_argument('--log-level', dest='loglevel', default='info',
                        choices=LOG_LEVELS.keys() + [key.upper() for key in
                                                     LOG_LEVELS.keys()],
                        help="log level")
    parser.add_argument('--log-output', dest='logoutput', default='-',
                        help="log output")
    parser.add_argument('--ssh', default=None, help='SSH Server')
    parser.add_argument('--multicast', dest="multicast",
                        default="udp://237.219.251.97:12027",
                        help="Multicast endpoint. If not specified, Circus "
                             "will use default one")

    args = parser.parse_args()

    if args.version:
        print(__version__)
        sys.exit(0)

    # configure the logger
    configure_logger(logger, args.loglevel, args.logoutput)

    # Get the tornado ioloop singleton
    loop = tornado.ioloop.IOLoop.instance()

    if args.endpoint is not None:
        connect_to_circus(loop, args.endpoint, args.ssh)

    app.auto_discovery = AutoDiscovery(args.multicast, loop)
    http_server = tornado.httpserver.HTTPServer(app, xheaders=True)

    if args.fd:
        sock = socket.fromfd(args.fd, socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)
        http_server.add_sockets([sock])
        logger.info("Starting circus web ui on fd %d" % args.fd)
    else:
        http_server.listen(args.port, args.host)
        logger.info("Starting circus web ui on %s:%s" % (args.host, args.port))

    loop.start()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
