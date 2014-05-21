from time import time

from circusweb.util import AutoDiscovery

from circus.tests.support import TestCircus
from circus.util import DEFAULT_ENDPOINT_MULTICAST, DEFAULT_ENDPOINT_DEALER


class TestAutoDiscovery(TestCircus):
    def setUp(self):
        TestCircus.setUp(self)

    def test_auto_discovery(self):
        self._stop_runners()

        dummy_process = 'circus.tests.support.run_process'
        self._run_circus(dummy_process)

        auto_discovery = AutoDiscovery(DEFAULT_ENDPOINT_MULTICAST,
                                       self.io_loop)

        def oracle():
            self.assertIn(DEFAULT_ENDPOINT_DEALER,
                          auto_discovery.get_endpoints())
        self.retry_timeout(oracle, 10)

    def retry_timeout(self, oracle, timeout=5, step=0.1):
        """ For a given oracle, a callable, try to execute it.

        If it doesn't raises an AssertionError, oracle is green, return and
        continue execution.
        Else, if we doesn't reach the timeout, sleep for step seconds and try
        again.
        If we reach the timeout, raise an AssertionError.
        """

        assert timeout > 0, "Timeout should be > 0"
        begin_time = time()

        def test():
            global begin_time, oracle, timeout

            if time() - begin_time >= timeout:
                raise AssertionError("Timeout before oracle went true.")
            else:
                try:
                    oracle()
                    return True
                except AssertionError:
                    return False

        self.wait(test)
