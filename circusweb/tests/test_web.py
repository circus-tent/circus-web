import subprocess
import os
import sys

from webtest import TestApp
import gevent

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
        try:
            with gevent.Timeout(1):
                while self.p.poll() is None:
                    gevent.sleep(0.1)
        except gevent.Timeout:
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

        # let's go back to the watcher page now
        # and check the number of watchers
        watcher_page = self.app.get('/watchers/sleeper')
        self.assertTrue('<span class="num_process">3</span>' in
                        watcher_page.body)
