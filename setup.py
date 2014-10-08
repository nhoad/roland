#!/usr/bin/env python2

from setuptools import setup

settings = {
    'name': 'roland',
    'version': '0.0.1',
    'description': "Minimal vim-like browser implemented in Python",
    'author': 'Nathan Hoad',
    'author_email': 'nathan@getoffmalawn.com',
    'url': 'https://github.com/nathan-hoad/roland',
    'license': 'BSD',
    'classifiers': (
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
    ),
    'zip_safe': False,
    'packages': ['roland'],
    'scripts': ['bin/roland'],
}

setup(**settings)
