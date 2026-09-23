import logging
import unittest

import bot


class LoggingSecurityTests(unittest.TestCase):
    def test_http_client_info_logs_are_disabled_to_protect_bot_token(self):
        logger = logging.getLogger('httpx')
        self.assertGreaterEqual(logger.level, logging.WARNING)


if __name__ == '__main__':
    unittest.main()
