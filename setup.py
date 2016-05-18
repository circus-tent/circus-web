import sys
from setuptools import setup, find_packages
from circusweb import __version__

if not hasattr(sys, 'version_info') or sys.version_info < (2, 6, 0, 'final'):
    raise SystemExit("Circus requires Python 2.6 or later.")


install_requires = ['Mako', 'MarkupSafe', 'anyjson', 'six',
                    'pyzmq', 'circus', 'tornado', 'sockjs-tornado', 'tomako']

try:
    import argparse     # NOQA
except ImportError:
    install_requires.append('argparse')

with open("README.rst") as f:
    README = f.read()

with open("CHANGES.rst") as f:
    CHANGES = f.read()


setup(name='circus-web',
      version=__version__,
      packages=find_packages(),
      description="Circus Web Dashboard",
      long_description=README,
      author="Mozilla Foundation & contributors",
      author_email="services-dev@lists.mozila.org",
      include_package_data=True,
      zip_safe=False,
      classifiers=[
          "Programming Language :: Python",
          "Programming Language :: Python :: 2.6",
          "Programming Language :: Python :: 2.7",
          "License :: OSI Approved :: Apache Software License",
          "Development Status :: 3 - Alpha"],
      install_requires=install_requires,
      tests_require=['webtest', 'unittest2'],
      test_suite='circusweb.tests',
      entry_points="""
      [console_scripts]
      circushttpd = circusweb.circushttpd:main
      """)
