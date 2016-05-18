from __future__ import unicode_literals, absolute_import
from sockjs.tornado import SockJSConnection
from tornado import gen
from collections import defaultdict
import json
# from base64 import b64encode, b64decode


def chucktify(name, *args, **kwargs):
    """Format for chuckt SockJS event client."""
    return json.dumps({'chuckt': {'event': name, 'args': [kwargs, args]}})


class SocketConnection(SockJSConnection):

    participants = defaultdict(set)

    def __init__(self, *args, **kwargs):
        super(SocketConnection, self).__init__(*args, **kwargs)
        self.stats_endpoints = []

    def on_close(self):
        from circusweb.session import get_controller  # Circular import
        controller = get_controller()
        for endpoint in self.stats_endpoints:
            self.participants[endpoint].discard(self)
            controller.disconnect_stats_endpoint(endpoint)

    @gen.coroutine
    def on_message(self, data):
        data = json.loads(data)
        if data['name'] != "get_stats":
            return
        return self.get_stats(**data)

    def get_stats(self, watchers=[], watchersWithPids=[],
                  endpoints=[], stats_endpoints=[], **kwargs):
        from circusweb.session import get_controller  # Circular import
        from .circushttpd import b64decode
        controller = get_controller()

        for watcher_tuple in watchersWithPids:
            watcher, encoded_endpoint = watcher_tuple
            endpoint = b64decode(encoded_endpoint)

            if watcher == "sockets":
                sockets = yield gen.Task(controller.get_sockets,
                                         endpoint=endpoint)
                fds = [s['fd'] for s in sockets]
                self.send(chucktify('socket-stats-fds-{endpoint}'.format(
                    endpoint=encoded_endpoint), fds=fds))
            else:
                pids = yield gen.Task(controller.get_pids, watcher, endpoint)
                pids = [int(pid) for pid in pids]
                channel = 'stats-{watcher}-pids-{endpoint}'.format(
                    watcher=watcher, endpoint=encoded_endpoint)
                self.send(chucktify(channel, pids=pids))
        self.watchers = watchers

        # Dirty fix
        self.watchersWithPids = [x[0] for x in watchersWithPids]
        self.stats_endpoints = stats_endpoints

        for endpoint in stats_endpoints:
            controller.connect_to_stats_endpoint(endpoint)
            self.participants[endpoint].add(self)

    @classmethod
    def consume_stats(cls, watcher, pid, stat, stat_endpoint):
        from .circushttpd import b64encode
        stat_endpoint_b64 = b64encode(stat_endpoint).decode('utf-8')
        for p in cls.participants[stat_endpoint]:
            cls.send_stats(p, watcher, pid, stat, stat_endpoint_b64)

    @classmethod
    def send_stats(cls, p, watcher, pid, stat, stat_endpoint):
        if watcher == 'sockets':
            # if we get information about sockets and we explicitely
            # requested them, send back the information.
            if 'sockets' in p.watchersWithPids and 'fd' in stat:
                event = 'socket-stats-{fd}-{endpoint}'.format(
                    fd=stat['fd'], endpoint=stat_endpoint)
                p.send(chucktify(event, **stat))
            elif 'sockets' in p.watchers and 'addresses' in stat:
                event = 'socket-stats-{endpoint}'.format(endpoint=stat_endpoint)  # noqa
                keys_to_send = ('reads', 'addresses',)
                p.send(chucktify(event, **dict((k, stat[k]) for k in keys_to_send)))  # noqa
        else:
            available_watchers = p.watchers + p.watchersWithPids + ['circus']
            # these are not sockets but normal watchers
            if watcher in available_watchers:
                keys_to_send = ('mem', 'cpu', 'age')
                if (watcher == 'circus' and stat.get(
                        'name', None) in available_watchers):
                    event = 'stats-{watcher}-{endpoint}'.format(
                        watcher=stat['name'], endpoint=stat_endpoint)
                    p.send(chucktify(event, **dict((k, stat[k]) for k in keys_to_send)))  # noqa
                else:
                    if pid is None:  # means that it's the aggregation
                        event = 'stats-{watcher}-{endpoint}'.format(
                            watcher=watcher, endpoint=stat_endpoint)
                        p.send(chucktify(event, **dict((k, stat[k]) for k in keys_to_send)))  # noqa
                    else:
                        if watcher in p.watchersWithPids:
                            event = 'stats-{watcher}-{pid}-{endpoint}'.format(  # noqa
                                watcher=watcher, pid=pid, endpoint=stat_endpoint)  # noqa
                            p.send(chucktify(event, **dict((k, stat[k]) for k in keys_to_send)))  # noqa
