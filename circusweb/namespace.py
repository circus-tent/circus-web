import tornadio2
from tornado import gen
from collections import defaultdict


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

        for watcher in watchersWithPids:
            if watcher == "sockets":
                sockets = yield gen.Task(controller.get_sockets,
                                         endpoint=endpoints[0])
                fds = [s['fd'] for s in sockets]
                self.emit('socket-stats-fds', fds=fds)
            else:
                pids = yield gen.Task(controller.get_pids, watcher, endpoints)
                pids = [int(pid) for pid in pids]
                channel = 'stats-{watcher}-pids'.format(watcher=watcher)
                self.emit(channel, pids=pids)

        self.watchers = watchers

        self.watchersWithPids = watchersWithPids
        self.stats_endpoints = stats_endpoints

        for endpoint in stats_endpoints:
            controller.connect_to_stats_endpoint(endpoint)
            self.participants[endpoint].add(self)

    @classmethod
    def consume_stats(cls, watcher, pid, stat, endpoint):
        for p in cls.participants[endpoint]:
            if watcher == 'sockets':
                # if we get information about sockets and we explicitely
                # requested them, send back the information.
                if 'sockets' in p.watchersWithPids and 'fd' in stat:
                    p.emit('socket-stats-{fd}'.format(fd=stat['fd']),
                           **stat)
                elif 'sockets' in p.watchers and 'addresses' in stat:
                    p.emit('socket-stats', reads=stat['reads'],
                           adresses=stat['addresses'])
            else:
                available_watchers = p.watchers + p.watchersWithPids + \
                    ['circus']
                # these are not sockets but normal watchers
                if watcher in available_watchers:
                    if (watcher == 'circus'
                            and stat.get('name', None) in available_watchers):
                        p.emit(
                            'stats-{watcher}'.format(watcher=stat['name']),
                            mem=stat['mem'], cpu=stat['cpu'], age=stat['age'])
                    else:
                        if pid is None:  # means that it's the aggregation
                            p.emit(
                                'stats-{watcher}'.format(watcher=watcher),
                                mem=stat['mem'], cpu=stat['cpu'],
                                age=stat['age'])
                        else:
                            if watcher in p.watchersWithPids:
                                p.emit(
                                    'stats-{watcher}-{pid}'.format(
                                        watcher=watcher, pid=pid),
                                    mem=stat['mem'],
                                    cpu=stat['cpu'],
                                    age=stat['age'])
