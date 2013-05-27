import sys
from time import time
from gevent import sleep

from circusweb.util import AutoDiscoveryThread

from circus.tests.support import TestCircus
from circus.util import DEFAULT_ENDPOINT_MULTICAST, DEFAULT_ENDPOINT_DEALER


class TestAutoDiscovery(TestCircus):
    def setUp(self):
        TestCircus.setUp(self)

    def test_auto_discovery(self):
        self._stop_runners()

        dummy_process = 'circus.tests.support.run_process'
        self._run_circus(dummy_process)

        thread = AutoDiscoveryThread(DEFAULT_ENDPOINT_MULTICAST)
        thread.daemon = True
        thread.start()

        def oracle():
            self.assertIn(DEFAULT_ENDPOINT_DEALER, thread.get_endpoints())
        retry_timeout(oracle, 10)


def retry_timeout(oracle, timeout=5, step=0.1):
    """ For a given oracle, a callable, try to execute it.

    If it doesn't raises an AssertionError, oracle is green, return and
    continue execution.
    Else, if we doesn't reach the timeout, sleep for step seconds and try
    again.
    If we reach the timeout, raise an AssertionError.
    """

    assert timeout > 0, "Timeout should be > 0"

    begin_time = time()

    etype = value = traceback = None
    while (time() - begin_time) < timeout:
        try:
            oracle()
            break
        except AssertionError:
            etype, value, traceback = sys.exc_info()
        sleep(step)
    else:
        if not value or not etype or not traceback:
            raise AssertionError('Oracle has not been executed')

        value = "%s\n\nOracle failed during %s seconds." % (value, timeout)
        raise etype, value, traceback
