import pkg_resources
import logging

__version__ = pkg_resources.get_distribution(__package__).version
logger = logging.getLogger('circus-web')
