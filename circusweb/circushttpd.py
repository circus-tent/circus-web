import argparse
import os
import os.path
import sys
import json
from base64 import b64encode, b64decode
from zmq.eventloop import ioloop

# Install zmq.eventloop to replace tornado.ioloop
ioloop.install()

try:
    import tornado.httpserver
    import tornado.ioloop
    import tornado.web

    from tornado import gen
    from tornado.escape import json_decode, json_encode
    from tornado.options import define, options
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
    connect_to_circus, disconnect_from_circus, get_controller)

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

        user = self.get_current_user()
        controller = get_controller()

        if not user or not controller:
            self.clean_user_session()
            return self.redirect(self.application.reverse_url('connect'))

        return func(self, *args, **kwargs)

    return wrapped


class BaseHandler(tornado.web.RequestHandler):

    def render_template(self, template_path, **data):
        namespace = self.get_template_namespace()
        user = self.get_current_user()
        server = '%s://%s/' % (self.request.protocol, self.request.host)
        namespace.update({'controller': get_controller(),
                          'version': __version__,
                          'b64encode': b64encode,
                          'dumps': json.dumps,
                          'user': user, 'SERVER': server})

        # Stats endpoints
        controller = get_controller()
        if user and controller:
            endpoints = user['endpoints']
            stats_endpoints = []
            for endpoint in endpoints:
                client = controller.get_client(endpoint)
                if client and client.stats_endpoint:
                    stats_endpoints.append(client.stats_endpoint)
            namespace.update({'stats_endpoints': stats_endpoints,
                              'endpoints': user['endpoints']})

        namespace.update(data)

        try:
            template = app.loader.load(template_path)
            return template.generate(**namespace)
        except Exception:
            print exceptions.text_error_template().render()

    def get_current_user(self):
        user = self.get_secure_cookie("user")
        if not user:
            return None
        else:
            return json_decode(user)

    def clean_user_session(self):
        """Disconnect the endpoint the user was logged on + Remove cookies."""
        user = self.get_current_user()
        if user:
            user = user
            endpoints = user['endpoints']
            for endpoint in endpoints:
                disconnect_from_circus(endpoint)
        self.clear_all_cookies()


class IndexHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self):
        controller = get_controller()
        user = self.get_current_user()
        endpoints = user['endpoints']
        goptions = yield gen.Task(controller.get_global_options, endpoints[0])
        self.finish(self.render_template('index.html', goptions=goptions))


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
        endpoint = self.get_argument('endpoint', None)

        if not endpoint:
            self.finish(self.show_form())

        endpoint_select = self.get_argument('endpoint_select', None)
        if endpoint_select:
            endpoint = endpoint_select

        try:
            yield gen.Task(connect_to_circus, tornado.ioloop.IOLoop.instance(),
                           endpoint)
        except CallError:
            # TODO SHOW MESSAGE
            self.redirect(self.reverse_url('connect'))
        else:
            self.set_secure_cookie("user",
                                   json_encode({'endpoints': [endpoint]}))
            self.redirect(self.reverse_url('index'))


class DisconnectHandler(BaseHandler):

    @require_logged_user
    def get(self):
        self.clean_user_session()
        self.redirect(self.reverse_url('index'))


class WatcherAddHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def post(self, endpoint):
        url = yield gen.Task(
            run_command, 'add_watcher',
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
    def get(self, name):
        controller = get_controller()
        user = self.get_current_user()
        endpoints = user['endpoints']
        pids = yield gen.Task(controller.get_pids, name, endpoints)
        self.finish(self.render_template('watcher.html', pids=pids, name=name))


class WatcherSwitchStatusHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name):
        url = yield gen.Task(run_command, command='switch_status',
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
        url = yield gen.Task(run_command, command='killproc',
                             message=msg.format(
                                 pid=pid),
                             endpoint=b64decode(endpoint),
                             args=(name, pid),
                             redirect_url=self.reverse_url('watcher', name))
        self.redirect(url)


class DecrProcHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name):
        msg = 'removed one process from the {watcher} pool'
        url = yield gen.Task(run_command, command='decrproc',
                             message=msg.format(watcher=name),
                             endpoint=b64decode(endpoint),
                             args=(name,),
                             redirect_url=self.reverse_url('watcher', name))
        self.redirect(url)


class IncrProcHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self, endpoint, name):
        msg = 'added one process to the {watcher} pool'
        url = yield gen.Task(run_command, command='incrproc',
                             message=msg.format(watcher=name),
                             endpoint=b64decode(endpoint),
                             args=(name,),
                             redirect_url=self.reverse_url('watcher', name))
        self.redirect(url)


class SocketsHandler(BaseHandler):

    @require_logged_user
    @tornado.web.asynchronous
    @gen.coroutine
    def get(self):
        user = self.get_current_user()
        controller = get_controller()
        endpoints = user['endpoints']
        sockets = yield gen.Task(controller.get_sockets, endpoint=endpoints[0])
        self.finish(
            self.render_template('sockets.html', sockets=sockets))


class Application(tornado.web.Application):

    def __init__(self):
        handlers = [
            URLSpec(r'/',
                    IndexHandler, name="index"),
            URLSpec(r'/connect/',
                    ConnectHandler, name="connect"),
            URLSpec(r'/disconnect/',
                    DisconnectHandler, name="disconnect"),
            URLSpec(r'/watcher/([^/]+)/',
                    WatcherHandler, name="watcher"),
            URLSpec(r'/([^/]+)/add_watcher/',
                    WatcherAddHandler, name="add_watcher"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/switch_status/',
                    WatcherSwitchStatusHandler, name="switch_status"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/process/kill/([^/]+)/',
                    KillProcessHandler, name="kill_process"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/process/decr/',
                    DecrProcHandler, name="decr_proc"),
            URLSpec(r'/([^/]+)/watcher/([^/]+)/process/incr/',
                    IncrProcHandler, name="incr_proc"),
            URLSpec(r'/sockets/',
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

    parser.add_argument('--fd', help='FD', default=None)
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

    if args.endpoint is not None:
        connect_to_circus(args.endpoint, args.ssh)

    options.parse_command_line()

    # Get the tornado ioloop singleton
    loop = tornado.ioloop.IOLoop.instance()

    app.auto_discovery = AutoDiscovery(args.multicast, loop)

    http_server = tornado.httpserver.HTTPServer(app)
    http_server.listen(options.port, "0.0.0.0")
    logger.info("Starting circus web ui on port %s" % (options.port))
    loop.start()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
