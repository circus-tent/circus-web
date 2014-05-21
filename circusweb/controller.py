from itertools import chain
from circus.commands import get_commands
from circusweb.client import AsynchronousCircusClient
from circusweb.stats_client import AsynchronousStatsConsumer
from circusweb.namespace import SocketIOConnection

from tornado import gen

cmds = get_commands()


class Controller(object):
    def __init__(self, loop, ssh_server=None):
        self.clients = {}
        self.stats_clients = {}
        self.loop = loop
        self.ssh_server = ssh_server

    @gen.coroutine
    def connect(self, endpoint):
        endpoint = str(endpoint)
        if endpoint not in self.clients:
            client = AsynchronousCircusClient(self.loop, endpoint,
                                              ssh_server=self.ssh_server)
            yield gen.Task(client.update_watchers)
        else:
            client = self.get_client(endpoint)
        client.count += 1
        self.clients[endpoint] = client

    def disconnect(self, endpoint):
        endpoint = str(endpoint)
        if not endpoint in self.clients:
            return
        self.clients[endpoint].count -= 1

        if self.clients[endpoint].count <= 0:
            del self.clients[endpoint]

    def connect_to_stats_endpoint(self, stats_endpoint):
        stats_endpoint = str(stats_endpoint)
        if stats_endpoint in self.stats_clients:
            return

        stats_client = AsynchronousStatsConsumer(
            ['stat.'], self.loop,
            SocketIOConnection.consume_stats, endpoint=stats_endpoint,
            ssh_server=self.ssh_server)

        stats_client.count += 1
        self.stats_clients[stats_endpoint] = stats_client

    def disconnect_stats_endpoint(self, stats_endpoint):
        stats_endpoint = str(stats_endpoint)
        if not stats_endpoint in self.stats_clients:
            return
        self.stats_clients[stats_endpoint].count -= 1

        if self.stats_clients[stats_endpoint].count <= 0:
            del self.stats_clients[stats_endpoint]

    def get_client(self, endpoint):
        return self.clients.get(endpoint)

    @gen.coroutine
    def killproc(self, name, pid, endpoint):
        client = self.get_client(endpoint)
        res = yield gen.Task(client.send_message, 'signal', name=name,
                             pid=int(pid), signum=9, recursive=True)
        yield gen.Task(client.update_watchers)  # will do better later
        raise gen.Return(res)

    def get_option(self, name, option, endpoint):
        client = self.get_client(endpoint)
        watchers = dict(client.watchers)
        return watchers[name][option]

    @gen.coroutine
    def get_global_options(self, endpoint):
        client = self.get_client(endpoint)
        res = yield gen.Task(client.send_message, 'globaloptions')
        raise gen.Return(res['options'])

    def get_options(self, name, endpoint):
        client = self.get_client(endpoint)
        watchers = dict(client.watchers)
        return watchers[name].items()

    @gen.coroutine
    def incrproc(self, name, endpoint):
        client = self.get_client(endpoint)
        res = yield gen.Task(client.send_message, 'incr', name=name)
        yield gen.Task(client.update_watchers)  # will do better later
        raise gen.Return(res)

    @gen.coroutine
    def decrproc(self, name, endpoint):
        client = self.get_client(endpoint)
        res = yield gen.Task(client.send_message, 'decr', name=name)
        yield gen.Task(client.update_watchers)  # will do better later
        raise gen.Return(res)

    def get_stats(self, name, start=0, end=-1):
        return self.stats[name][start:end]

    def get_dstats(self, field, start=0, end=-1):
        stats = self.dstats[start:end]
        res = []
        for stat in stats:
            res.append(stat[field])
        return res

    @gen.coroutine
    def get_pids(self, name, endpoint):
        tasks = []
        client = self.get_client(endpoint)
        res = yield gen.Task(client.send_message, 'list', name=name)
        print "Res", res
        raise gen.Return(res['pids'])

    @gen.coroutine
    def get_sockets(self, endpoint, force_reload=False):
        client = self.get_client(endpoint)
        if not client.sockets or force_reload:
            res = yield gen.Task(client.send_message, 'listsockets')
            client.sockets = res['sockets']
        raise gen.Return(client.sockets)

    def get_status(self, name, endpoint):
        client = self.get_client(endpoint)
        res = client.send_message('status', name=name)
        return res['status']

    @gen.coroutine
    def switch_status(self, name, endpoint):
        msg = cmds['status'].make_message(name=name)
        client = self.get_client(endpoint)
        res = yield gen.Task(client.call, msg)
        status = res['status']
        if status == 'active':
            # stopping the watcher
            msg = cmds['stop'].make_message(name=name)
        else:
            msg = cmds['start'].make_message(name=name)
        res = yield gen.Task(self.client.call, msg)

        raise gen.Return(res)

    @gen.coroutine
    def add_watcher(self, name, endpoint, cmd, **kw):
        client = self.get_client(endpoint)
        res = yield gen.Task(client.send_message, 'add', name=name, cmd=cmd)
        if res['status'] == 'ok':
            # now configuring the options
            options = {}
            options['numprocesses'] = int(kw.get('numprocesses', '5'))
            options['working_dir'] = kw.get('working_dir')
            options['shell'] = kw.get('shell', 'off') == 'on'
            res = yield gen.Task(client.send_message, 'set',
                                 name=name, options=options)
            yield gen.Task(client.update_watchers)
        raise gen.Return(res)
