DEFAULT_CONFIG = {
    width: 290,
    height: 79,
    delay: 10,
    dataSize: 25,
    colors: {
        mem: 'rgb(93, 170, 204)',
        cpu: 'rgb(122, 185, 76)',
        reads: 'rgb(203, 81, 58)'
    }
};

function hookGraph(socket, watcher, graph_id, metrics, prefix, capValues, config) {
    if (config === undefined) {
        config = DEFAULT_CONFIG;
    }
    if (metrics === undefined) {
        metrics = ['cpu', 'mem'];
    }
    if (prefix === undefined) {
        prefix = 'stats-';
    }
    if (capValues === undefined) {
        capValues = true;
    }

    var series = [];
    metrics.forEach(function(metric) {
        series.push({
            name: metric,
            color: config.colors[metric]
        });
    });

    var graph = new Rickshaw.Graph({
        element: document.getElementById(graph_id),
        min: 0,
        max: 100,
        width: config.width,
        height: config.height,
        renderer: 'line',
        interpolation: 'basis',
        series: new Rickshaw.Series.FixedDuration(
            series, undefined, {
                timeInterval: config.delay,
                maxDataPoints: 25,
                timeBase: new Date().getTime() / 1000
            })
    });

    socket.on(prefix + graph_id, function(received) {
        var data = {};

        // cap to 100
        metrics.forEach(function(metric) {
            if (received[metric] > 100) {
                data[metric] = 100;
            } else {
                data[metric] = received[metric];
            }
            var value = data[metric].toFixed(1);
            if (metric != 'reads') {
                value += '%';
            }

            // JQuery doesn't seem to like base 64
            text_dom = document.getElementById(graph_id + '_last_' + metric);
            $(text_dom).text(value);
        });

        if (received.hasOwnProperty("age")) {
            var val = '(' + Math.round(received.age) + 's)';
            var node = document.getElementById(graph_id + '_last_age');
            node.innerHTML = val;
        }

        graph.series.addData(data);
        graph.render();
    });
}


function supervise(socket, watchers, watchersWithPids, endpoints, stats_endpoints, config) {
    if (watchersWithPids === undefined) {
        watchersWithPids = [];
    }
    if (config === undefined) {
        config = DEFAULT_CONFIG;
    }
    watchers_to_send = [];

    watchers.forEach(function(watcher_tuple) {
        watcher = watcher_tuple[0];
        watcher_endpoint = watcher_tuple[1];

        // only the aggregation is sent here
        if (watcher == 'sockets') {
            hookGraph(socket, 'socket-stats', 'socket-stats' + '-' + watcher_endpoint, ['reads'], '', false, config);
        } else {
            hookGraph(socket, watcher, watcher + '-' + watcher_endpoint, ['cpu', 'mem'], 'stats-', true, config);
        }
        watchers_to_send.push(watcher);
    });

    watchers_with_pid_to_send = [];

    watchersWithPids.forEach(function(watcher_tuple) {
        var watcher = watcher_tuple[0];
        var watcher_stats_endpoint = watcher_tuple[1];
        var watcher_endpoint = watcher_tuple[2];
        if (watcher == 'sockets') {
            socket.on('socket-stats-fds-' + watcher_endpoint, function(data) {
                data.fds.forEach(function(fd) {
                    var id = 'socket-stats-' + fd;
                    var graph_id = 'socket-stats-' + fd + '-' + watcher_stats_endpoint;
                    hookGraph(socket, id, graph_id, ['reads'], '', false, config);
                });
            });
        } else {
            // get the list of processes for this watcher from the server
            socket.on('stats-' + watcher + '-pids-' + watcher_endpoint, function(data) {
                data.pids.forEach(function(pid) {
                    var id = watcher + '-' + pid;
                    var graph_id = watcher + '-' + pid + '-' + watcher_stats_endpoint;
                    hookGraph(socket, id, graph_id, ['cpu', 'mem'], 'stats-', false, config);
                });
            });
        }

        tuple = [];
        tuple.push(watcher);
        tuple.push(watcher_endpoint);
        watchers_with_pid_to_send.push(tuple);
    });

    // start the streaming of data, once the callbacks in place.
    socket.socket.onopen = function(event) {
        socket.socket.send(JSON.stringify({
            name: 'get_stats',
            watchers: watchers_to_send,
            watchersWithPids: watchers_with_pid_to_send,
            endpoints: endpoints,
            stats_endpoints: stats_endpoints
        }));
    };

}

$(document).ready(function() {
    $('.add_watcher').click(function() {
        $('#overlay form').attr('action', $(this).attr('data-add-url'));
        $('#overlay').show();
        return false;
    });

    $('#cancel_watcher_btn').click(function() {
        $('#overlay').hide();
        return false;
    });

    $('a.stopped, a.active').click(function(e) {
        return confirm('Are you sure you want to change the status ?');
    });

});
