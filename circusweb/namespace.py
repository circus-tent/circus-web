import tornadio2
from tornado import gen
from collections import defaultdict
from base64 import b64encode, b64decode


class SocketIOConnection(tornadio2.SocketConnection):

    participants = defaultdict(set)

    def __init__(self, *args, **kwargs):
        super(SocketIOConnection, self).__init__(*args, **kwargs)
        self.stats_endpoints = []

    def on_close(self):
        from circusweb.session import get_controller  # Circular import
        controller = get_controller()
        for endpoint in self.stats_endpoints:
            self.participants[endpoint].discard(self)
            controller.disconnect_stats_endpoint(endpoint)

    @tornadio2.event
    @gen.coroutine
    def get_stats(self, watchers=[], watchersWithPids=[],
                  endpoints=[], stats_endpoints=[]):
        from circusweb.session import get_controller  # Circular import
        controller = get_controller()

        for watcher_tuple in watchersWithPids:
            watcher, encoded_endpoint = watcher_tuple
            endpoint = b64decode(encoded_endpoint).decode("utf-8")
            if watcher == "sockets":
                sockets = yield gen.Task(controller.get_sockets,
                                         endpoint=endpoint)
                fds = [s['fd'] for s in sockets]
                self.emit('socket-stats-fds-{endpoint}'.format(
                    endpoint=encoded_endpoint), fds=fds)
            else:
                pids = yield gen.Task(controller.get_pids, watcher, endpoint)
                pids = [int(pid) for pid in pids]
                channel = 'stats-{watcher}-pids-{endpoint}'.format(
                    watcher=watcher, endpoint=encoded_endpoint)
                self.emit(channel, pids=pids)

        self.watchers = watchers

        # Dirty fix
        self.watchersWithPids = [x[0] for x in watchersWithPids]
        self.stats_endpoints = stats_endpoints

        for endpoint in stats_endpoints:
            controller.connect_to_stats_endpoint(endpoint)
            self.participants[endpoint].add(self)

    @classmethod
    def consume_stats(cls, watcher, pid, stat, stat_endpoint):
        stat_endpoint_b64 = b64encode(stat_endpoint.encode('utf-8'))
        for p in cls.participants[stat_endpoint]:
            if watcher == 'sockets':
                # if we get information about sockets and we explicitely
                # requested them, send back the information.
                if 'sockets' in p.watchersWithPids and 'fd' in stat:
                    p.emit('socket-stats-{fd}-{endpoint}'.format(
                        fd=stat['fd'], endpoint=stat_endpoint_b64.decode('utf-8')),
                        **stat)
                elif 'sockets' in p.watchers and 'addresses' in stat:
                    p.emit('socket-stats-{endpoint}'.format(
                        endpoint=stat_endpoint_b64.decode('utf-8')), reads=stat['reads'],
                        adresses=stat['addresses'])
            else:
                available_watchers = p.watchers + p.watchersWithPids + \
                    ['circus']
                # these are not sockets but normal watchers
                if watcher in available_watchers:
                    if (watcher == 'circus' and
                            stat.get('name', None) in available_watchers):
                        p.emit('stats-{watcher}-{endpoint}'.format(
                            watcher=stat['name'], endpoint=stat_endpoint_b64.decode('utf-8')),
                            mem=stat['mem'], cpu=stat['cpu'], age=stat['age'])
                    else:
                        if pid is None:  # means that it's the aggregation
                            p.emit('stats-{watcher}-{endpoint}'.format(
                                watcher=watcher, endpoint=stat_endpoint_b64.decode('utf-8')),
                                mem=stat['mem'], cpu=stat['cpu'],
                                age=stat['age'])
                        else:
                            if watcher in p.watchersWithPids:
                                p.emit(
                                    'stats-{watcher}-{pid}-{endpoint}'.format(
                                        watcher=watcher, pid=pid,
                                        endpoint=stat_endpoint_b64.decode('utf-8')),
                                    mem=stat['mem'],
                                    cpu=stat['cpu'],
                                    age=stat['age'])
