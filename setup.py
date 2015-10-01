#!/usr/bin/env python
# encoding: utf-8
#
#    Copyright (c) 2011-2012 Andrey Sibiryov <me@kobology.ru>
#    Copyright (c) 2011-2015 Anton Tyurin <noxiouz@yandex.ru>
#    Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
#    Copyright (c) 2011-2013 Other contributors as noted in the AUTHORS file.
#
#    This file is part of Cocaine.
#
#    Cocaine is free software; you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published by
#    the Free Software Foundation; either version 3 of the License, or
#    (at your option) any later version.
#
#    Cocaine is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import os

from setuptools import setup


if 'DEB_BUILD_GNU_TYPE' in os.environ:
    tools_data = [
        ('/etc/bash_completion.d/', ["scripts/bash_completion.d/cocaine-tool"])
    ]
else:
    tools_data = []


setup(
    name="cocaine-tools",
    version="0.12.5.3",
    author="Anton Tyurin",
    author_email="noxiouz@yandex.ru",
    maintainer='Evgeny Safronov',
    maintainer_email='division494@gmail.com',
    url="https://github.com/cocaine/cocaine-tools",
    description="Cocaine Tools for Cocaine Application Cloud.",
    long_description="Tools for deploying and managing applications in the cloud",
    license="LGPLv3+",
    platforms=["Linux", "BSD", "MacOS"],
    namespace_packages=['cocaine'],
    include_package_data=True,
    zip_safe=False,
    packages=[
        "cocaine",
        "cocaine.proxy",
        "cocaine.tools",
        "cocaine.tools.actions",
        "cocaine.tools.helpers",
        "cocaine.tools.interactive",
    ],
    entry_points={
        'console_scripts': [
            'cocaine-tool = cocaine.tools.cocaine_tool:main',
            'cocaine-tornado-proxy = cocaine.proxy.proxy:main',
        ]},
    install_requires=open('./requirements.txt').read(),
    tests_require=open('./tests/requirements.txt').read(),
    test_suite='nose.collector',
    classifiers=[
        'Programming Language :: Python',
        # 'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        # 'Programming Language :: Python :: 3.2',
        # 'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        "Programming Language :: Python :: Implementation :: CPython",
        # 'Development Status :: 1 - Planning',
        # 'Development Status :: 2 - Pre-Alpha',
        # 'Development Status :: 3 - Alpha',
        'Development Status :: 4 - Beta',
        # 'Development Status :: 5 - Production/Stable',
        # 'Development Status :: 6 - Mature',
        # 'Development Status :: 7 - Inactive',
    ],
)
