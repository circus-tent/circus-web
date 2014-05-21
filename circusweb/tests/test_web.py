import time
import subprocess
import os
import sys
import re

from webtest import TestApp

from circusweb.circushttpd import app
from circus.tests.support import TestCircus
from circus.stream import QueueStream


cfg = os.path.join(os.path.dirname(__file__), 'test_web.ini')


class TestHttpd(TestCircus):
    def setUp(self):
        TestCircus.setUp(self)
        self.app = TestApp(app)
        self.stream = QueueStream()
        # let's run a circus
        cmd = [sys.executable, "-c",
               "from circus import circusd; circusd.main()", cfg]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)

    def tearDown(self):
        self.p.terminate()
        counter = 0
        while self.p.poll() is None and counter < 10:
            counter += 1
            time.sleep(0.1)
        if self.p.returncode is None:
            self.p.kill()

        TestCircus.tearDown(self)

    def test_index(self):
        # let's open the web app
        res = self.app.get('/')

        if res.status_code == 302:
            res = res.follow()

        # we have a form to connect to the current app
        res = res.form.submit()

        # that should be a 302, redirecting to the connected index
        # let's follow it
        res = res.follow()
        self.assertTrue('tcp://127.0.0.1:5557' in res.body)

    def test_watcher_page(self):
        # let's go to the watcher page now
        watcher_page = self.app.get('/watchers/sleeper')
        self.assertTrue('<span class="num_process">1</span>' in
                        watcher_page.body)

        # let's add two watchers
        self.app.get('/watchers/sleeper/process/incr')
        self.app.get('/watchers/sleeper/process/incr')
        self.app.get('/watchers/sleeper/process/decr')
        self.app.get('/watchers/sleeper/process/incr')

        # let's go back to the watcher page now
        # and check the number of watchers
        watcher_page = self.app.get('/watchers/sleeper')
        self.assertTrue('<span class="num_process">3</span>' in
                        watcher_page.body)

        # kill all processes
        pids = set(re.findall('Process #(\d+)<', watcher_page.body))
        for pid in pids:
            self.app.get('/watchers/sleeper/process/kill/%s' % pid)

        # wait a sec
        time.sleep(1.)

        # check all pids have changed
        watcher_page = self.app.get('/watchers/sleeper')
        new_pids = set(re.findall('Process #(\d+)<', watcher_page.body))
        self.assertTrue(new_pids.isdisjoint(pids))

    def test_watcher_status(self):
        # starting/stopping the watcher
        watcher_page = self.app.get('/watchers/sleeper')
        self.assertTrue('title="active"' in watcher_page.body)

        # stopping
        self.app.get('/watchers/sleeper/switch_status')
        watcher_page = self.app.get('/watchers/sleeper')
        self.assertFalse('title="active"' in watcher_page.body)
        self.assertTrue('class="stopped"' in watcher_page.body)

        # starting
        self.app.get('/watchers/sleeper/switch_status')
        watcher_page = self.app.get('/watchers/sleeper')
        self.assertTrue('title="active"' in watcher_page.body)
        self.assertFalse('class="stopped"' in watcher_page.body)

    def test_disconnect(self):
        self.assertFalse('Connect' in self.app.get('/').body)
        res = self.app.get('/disconnect')
        res = res.follow()
        self.assertTrue('Connect' in res.body)
