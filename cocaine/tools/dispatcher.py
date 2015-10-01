#
# Copyright (c) 2013+ Anton Tyurin <noxiouz@yandex.ru>
# Copyright (c) 2013+ Evgeny Safronov <division494@gmail.com>
# Copyright (c) 2011-2014 Other contributors as noted in the AUTHORS file.
#
# This file is part of Cocaine-tools.
#
# Cocaine is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Cocaine is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import collections
import logging
import os
import sys

from opster import Dispatcher

from cocaine.services import Locator, Service
from cocaine.tools import ColoredFormatter, interactiveEmit
from cocaine.tools.cli import Executor
from cocaine.tools.error import ToolsError

__author__ = 'Evgeny Safronov <division494@gmail.com>'


DESCRIPTION = ''
DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 10053


class Global(object):
    options = [
        ('h', 'host', DEFAULT_HOST, 'hostname'),
        ('p', 'port', DEFAULT_PORT, 'port'),
        ('', 'timeout', 20.0, 'timeout, s'),
        ('', 'color', True, 'enable colored output'),
        ('', 'debug', ('disable', 'tools', 'all'), 'enable debug mode'),
    ]

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, color=False, timeout=False, debug=False):
        self.host = host
        self.port = port
        self.endpoints = [(self.host, self.port)]
        self.timeout = timeout
        self.executor = Executor(timeout)
        self._locator = None
        self.configureLog(debug=debug, color=color)

    @staticmethod
    def configureLog(debug='disable', color=True, logNames=None):
        if not logNames:
            logNames = ['cocaine.tools']
        message = '%(message)s'
        level = logging.INFO
        if debug != 'disable':
            message = '[%(asctime)s] %(module)s %(name)s:%(lineno)d %(levelname)-8s: %(message)s'
            level = logging.DEBUG

        ch = logging.StreamHandler()
        if debug == 'disable':
            setattr(logging.StreamHandler, logging.StreamHandler.emit.__name__, interactiveEmit)
        ch.fileno = ch.stream.fileno
        ch.setLevel(level)
        formatter = ColoredFormatter(message, colored=color and sys.stdin.isatty())
        ch.setFormatter(formatter)

        if debug == 'all':
            logNames.append('cocaine')

        for logName in logNames:
            log = logging.getLogger(logName)
            log.setLevel(logging.DEBUG)
            log.propagate = False
            log.addHandler(ch)

    @property
    def locator(self):
        if self._locator:
            return self._locator
        else:
            try:
                locator = Locator(endpoints=self.endpoints)
                self._locator = locator
                return locator
            except Exception as err:
                raise ToolsError(err)

    def getService(self, name):
        try:
            service = Service(name, endpoints=self.endpoints)
            return service
        except Exception as err:
            raise ToolsError(err)


def middleware(func):
    def extract_dict(source, *keys):
        dest = {}
        for k in keys:
            dest[k] = source.pop(k, None)
        return dest

    def inner(*args, **kwargs):
        opts = extract_dict(kwargs, 'host', 'port', 'color', 'timeout', 'debug')
        if func.__name__ == 'help_inner':
            return func(*args, **kwargs)
        locator = Global(**opts)
        return func(locator, *args, **kwargs)
    return inner


d = Dispatcher(globaloptions=Global.options, middleware=middleware)
appDispatcher = Dispatcher(globaloptions=Global.options, middleware=middleware)


class dispatcher:
    group = Dispatcher(globaloptions=Global.options, middleware=middleware)


profileDispatcher = Dispatcher(globaloptions=Global.options, middleware=middleware)
runlistDispatcher = Dispatcher(globaloptions=Global.options, middleware=middleware)
crashlogDispatcher = Dispatcher(globaloptions=Global.options, middleware=middleware)
# proxyDispatcher = Dispatcher()


@d.command(name='locate', usage='[--name=NAME]')
def locate(options,
           name=('n', '', 'service name')):
    """Show information about requested service"""
    options.executor.executeAction('locate', **{
        'name': name,
        'locator': options.locator,
    })


@d.command(name='routing', usage='[--name=NAME]')
def routing(options,
            name=('n', '', 'group name')):
    """Show information about the requested routing group"""
    options.executor.executeAction('routing', **{
        'name': name,
        'locator': options.locator,
    })


@d.command(name='cluster')
def cluster(options,
            resolve=('r', False, 'show IPs instead of hostnames')):
    """Show cluster info"""
    options.executor.executeAction('cluster', **{
        'locator': options.getService('locator'),
        # Actually we have IPs and we need not do anything
        # to resolve them to IPs.
        # So the default behavior fits better to this option name.
        'resolve': resolve,
    })


@d.command(name='info', usage='[--name=NAME] [-pmb]')
def info(options,
         name=('n', '', 'application name'),
         profile=('p', False, 'expand profile'),
         manifest=('m', False, 'expand manifest'),
         brief=('b', False, 'show brief info only (disable -p and -m)'),
         no_wildcard=('w', True, 'do not use wildcard to match app name')):
    """Show information about cocaine runtime

    Return json-like string with information about cocaine-runtime.

    If the name option is not specified, shows information about all applications.
    Flags can be specified to set detalization of the output.
    """
    flag_brief = 0x00
    flag_verbose = 0x01
    flag_manifest = 0x02
    flag_profile = 0x04
    # bried disables all further flags
    flags = flag_brief if brief else flag_verbose

    if manifest:
        flags |= flag_manifest
    if profile:
        flags |= flag_profile

    options.executor.executeAction('info', **{
        'node': options.getService('node'),
        'locator': options.locator,
        'name': name,
        'flags': flags,
        'use_wildcard': no_wildcard,
    })


@appDispatcher.command(name='list')
def app_list(options):
    """Show installed applications list."""
    options.executor.executeAction('app:list', **{
        'storage': options.getService('storage')
    })


@appDispatcher.command(usage='--name=NAME', name='view')
def app_view(options,
             name=('n', '', 'application name')):
    """Show manifest context for application.

    If application is not uploaded, an error will be displayed.
    """
    options.executor.executeAction('app:view', **{
        'storage': options.getService('storage'),
        'name': name,
    })


@appDispatcher.command(name='upload', usage='[PATH] [--name=NAME] [--manifest=MANIFEST] [--package=PACKAGE]')
def app_upload(options,
               path=None,
               name=('n', '', 'application name'),
               manifest=('', '', 'manifest file name'),
               package=('', '', 'path to the application archive'),
               docker_address=('', '', 'docker address'),
               registry=('', '', 'registry address'),
               recipe=('', '', 'path to the recipe file'),
               manifest_only=('', False, 'upload manifest only')):
    """Upload application with its environment (directory) into the storage.

    Application directory or its subdirectories must contain valid manifest file named `manifest.json` or `manifest`
    otherwise you must specify it explicitly by setting `--manifest` option.

    You can specify application name. By default, leaf directory name is treated as application name.

    If you have already prepared application archive (*.tar.gz), you can explicitly specify path to it by setting
    `--package` option.

    You can control process of creating and uploading application by specifying `--debug=tools` option. This is helpful
    when some errors occurred.
    """
    TIMEOUT_THRESHOLD = 120.0
    if options.executor.timeout < TIMEOUT_THRESHOLD:
        logging.getLogger('cocaine.tools').info('Setting timeout to the %fs', TIMEOUT_THRESHOLD)
        options.executor.timeout = TIMEOUT_THRESHOLD
    MutexRecord = collections.namedtuple('MutexRecord', 'value, name')
    mutex = [
        (MutexRecord(path, 'PATH'), MutexRecord(package, '--package')),
        (MutexRecord(package, '--package'), MutexRecord(docker_address, '--docker')),
        (MutexRecord(package, '--package'), MutexRecord(registry, '--registry')),
    ]
    for (f, s) in mutex:
        if f.value and s.value:
            print('Wrong usage: option {0} and {1} are mutual exclusive, you can only force one'.format(f.name, s.name))
            exit(os.EX_USAGE)

    if manifest_only:
        options.executor.executeAction('app:upload-manual', **{
            'storage': options.getService('storage'),
            'name': name,
            'manifest': manifest,
            'package': None,
            'manifest_only': manifest_only,
        })
    elif package:
        options.executor.executeAction('app:upload-manual', **{
            'storage': options.getService('storage'),
            'name': name,
            'manifest': manifest,
            'package': package
        })
    elif docker_address:
        options.executor.executeAction('app:upload-docker', **{
            'storage': options.getService('storage'),
            'path': path,
            'name': name,
            'manifest': manifest,
            'address': docker_address,
            'registry': registry
        })
    else:
        options.executor.executeAction('app:upload', **{
            'storage': options.getService('storage'),
            'path': path,
            'name': name,
            'manifest': manifest
        })


@appDispatcher.command(name='import', usage='[PATH] [--name=NAME] [--manifest=MANIFEST] [--package=PACKAGE]')
def app_import(options,
               path=None,
               name=('n', '', 'application name'),
               manifest=('', '', 'manifest file name'),
               container_url=('', '', 'docker container url'),
               docker_address=('', '', 'docker address'),
               registry=('', '', 'registry address'),
               recipe=('', '', 'path to the recipe file'),
               manifest_only=('', False, 'upload manifest only')):
    """Import application's docker container

    You can control process of creating and uploading application by specifying `--debug=tools` option. This is helpful
    when some errors occurred.
    """
    TIMEOUT_THRESHOLD = 120.0
    if options.executor.timeout < TIMEOUT_THRESHOLD:
        logging.getLogger('cocaine.tools').info('Setting timeout to the %fs', TIMEOUT_THRESHOLD)
        options.executor.timeout = TIMEOUT_THRESHOLD

    if container_url and docker_address:
        options.executor.executeAction('app:import-docker', **{
            'storage': options.getService('storage'),
            'path': path,
            'name': name,
            'manifest': manifest,
            'container': container_url,
            'address': docker_address,
            'registry': registry
        })
    else:
        print "wrong usage"
        exit(os.EX_USAGE)


@appDispatcher.command(name='remove')
def app_remove(options,
               name=('n', '', 'application name')):
    """Remove application from storage.

    No error messages will display if specified application is not uploaded.
    """
    options.executor.executeAction('app:remove', **{
        'storage': options.getService('storage'),
        'name': name
    })


@appDispatcher.command(name='start')
def app_start(options,
              name=('n', '', 'application name'),
              profile=('r', '', 'profile name')):
    """Start application with specified profile.

    Does nothing if application is already running.
    """
    options.executor.executeAction('app:start', **{
        'node': options.getService('node'),
        'name': name,
        'profile': profile
    })


@appDispatcher.command(name='pause')
def app_pause(options,
              name=('n', '', 'application name')):
    """Stop application.

    This command is alias for ```cocaine-tool app stop```.
    """
    options.executor.executeAction('app:pause', **{
        'node': options.getService('node'),
        'name': name
    })


@appDispatcher.command(name='stop')
def app_stop(options,
             name=('n', '', 'application name')):
    """Stop application."""
    options.executor.executeAction('app:stop', **{
        'node': options.getService('node'),
        'name': name
    })


@appDispatcher.command(name='restart')
def app_restart(options,
                name=('n', '', 'application name'),
                profile=('r', '', 'profile name')):
    """Restart application.

    Executes ```cocaine-tool app pause``` and ```cocaine-tool app start``` sequentially.

    It can be used to quickly change application profile.
    """
    options.executor.executeAction('app:restart', **{
        'node': options.getService('node'),
        'locator': options.locator,
        'name': name,
        'profile': profile
    })


@appDispatcher.command()
def check(options,
          name=('n', '', 'application name')):
    """Checks application status."""
    options.executor.executeAction('app:check', **{
        'node': options.getService('node'),
        'storage': options.getService('storage'),
        'locator': options.locator,
        'name': name,
    })


@profileDispatcher.command(name='list')
def profile_list(options):
    """Show installed profiles."""
    options.executor.executeAction('profile:list', **{
        'storage': options.getService('storage')
    })


@profileDispatcher.command(name='view')
def profile_view(options,
                 name=('n', '', 'profile name')):
    """Show profile configuration context."""
    options.executor.executeAction('profile:view', **{
        'storage': options.getService('storage'),
        'name': name
    })


@profileDispatcher.command(name='upload')
def profile_upload(options,
                   name=('n', '', 'profile name'),
                   profile=('', '', 'path to profile file')):
    """Upload profile into the storage."""
    options.executor.executeAction('profile:upload', **{
        'storage': options.getService('storage'),
        'name': name,
        'profile': profile
    })


@profileDispatcher.command(name='edit')
def profile_edit(options,
                 name=('n', '', 'profile name')):
    """Edit profile in interactive editor."""
    options.executor.timeout = None
    options.executor.executeAction('profile:edit', **{
        'storage': options.getService('storage'),
        'name': name,
    })


@profileDispatcher.command(name='remove')
def profile_remove(options,
                   name=('n', '', 'profile name')):
    """Remove profile from the storage."""
    options.executor.executeAction('profile:remove', **{
        'storage': options.getService('storage'),
        'name': name
    })


@profileDispatcher.command(name='copy')
def profile_copy(options,
                 name=('n', '', 'profile name'),
                 copyname=('c', '', 'new profile name')):
    """Copy a profile"""
    options.executor.executeAction('profile:copy', **{
        'storage': options.getService('storage'),
        'name': name,
        'copyname': copyname,
    })


@profileDispatcher.command(name='rename')
def profile_rename(options,
                   name=('n', '', 'profile name'),
                   copyname=('c', '', 'new profile name')):
    """Raname a profile"""
    options.executor.executeAction('profile:rename', **{
        'storage': options.getService('storage'),
        'name': name,
        'copyname': copyname,
    })


@runlistDispatcher.command(name='list')
def runlist_list(options):
    """Show uploaded runlists."""
    options.executor.executeAction('runlist:list', **{
        'storage': options.getService('storage')
    })


@runlistDispatcher.command(name='view')
def runlist_view(options,
                 name=('n', '', 'name')):
    """Show configuration context for runlist."""
    options.executor.executeAction('runlist:view', **{
        'storage': options.getService('storage'),
        'name': name
    })


@runlistDispatcher.command(name='edit', usage='NAME')
def runlist_edit(options,
                 name=('n', '', 'runlist name')):
    """Edit runlist interactively."""
    options.executor.timeout = None
    options.executor.executeAction('runlist:edit', **{
        'storage': options.getService('storage'),
        'name': name
    })


@runlistDispatcher.command(name='upload')
def runlist_upload(options,
                   name=('n', '', 'name'),
                   runlist=('', '', 'path to the runlist configuration json file')):
    """Upload runlist with context into the storage."""
    options.executor.executeAction('runlist:upload', **{
        'storage': options.getService('storage'),
        'name': name,
        'runlist': runlist
    })


@runlistDispatcher.command(name='create')
def runlist_create(options,
                   name=('n', '', 'name')):
    """Create runlist and upload it into the storage."""
    options.executor.executeAction('runlist:create', **{
        'storage': options.getService('storage'),
        'name': name
    })


@runlistDispatcher.command(name='copy', usage='--name=NAME --copyname=NEWNAME')
def runlist_copy(options,
                 name=('n', '', 'name'),
                 copyname=('c', '', 'copyname')):
    """Copy runlist."""
    options.executor.executeAction('runlist:copy', **{
        'storage': options.getService('storage'),
        'name': name,
        'copyname': copyname,
    })


@runlistDispatcher.command(name='remove')
def runlist_remove(options,
                   name=('n', '', 'name')):
    """Remove runlist from the storage."""
    options.executor.executeAction('runlist:remove', **{
        'storage': options.getService('storage'),
        'name': name
    })


@runlistDispatcher.command(name='rename', usage='--name=NAME --copyname=NEWNAME')
def runlist_rename(options,
                   name=('n', '', 'name'),
                   copyname=('c', '', 'copyname')):
    """Rename runlist."""
    options.executor.executeAction('runlist:rename', **{
        'storage': options.getService('storage'),
        'name': name,
        'copyname': copyname,
    })


@runlistDispatcher.command(name='add-app')
def runlist_add_app(options,
                    name=('n', '', 'runlist name'),
                    app=('', '', 'application name'),
                    profile=('', '', 'suggested profile'),
                    force=('', False, 'create runlist if it is not exist')):
    """Add specified application with profile to the runlist.

    Existence of application or profile is not checked.
    """
    options.executor.executeAction('runlist:add-app', **{
        'storage': options.getService('storage'),
        'name': name,
        'app': app,
        'profile': profile,
        'force': force
    })


@runlistDispatcher.command(name='remove-app')
def runlist_remove_app(options,
                       name=('n', '', 'runlist name'),
                       app=('', '', 'application name')):
    """Remove specified application from the runlist.
    """
    options.executor.executeAction('runlist:remove-app', **{
        'storage': options.getService('storage'),
        'name': name,
        'app': app
    })


@crashlogDispatcher.command(name='status')
def crashlog_status(options):
    """Show crashlog status.
    """
    options.executor.executeAction('crashlog:status', **{
        'storage': options.getService('storage'),
    })


@crashlogDispatcher.command(name='list')
def crashlog_list(options,
                  name=('n', '', 'name'),
                  day=('d', '', 'day')):
    """Show crashlogs list for application.

    Prints crashlog list in timestamp - uuid format.
    """
    options.executor.executeAction('crashlog:list', **{
        'storage': options.getService('storage'),
        'name': name,
        'day_string': day,
    })


@crashlogDispatcher.command(name='view')
def crashlog_view(options,
                  name=('n', '', 'name'),
                  timestamp=('t', '', 'timestamp')):
    """Show crashlog for application with specified timestamp."""
    options.executor.executeAction('crashlog:view', **{
        'storage': options.getService('storage'),
        'name': name,
        'timestamp': timestamp
    })


@crashlogDispatcher.command(name='remove')
def crashlog_remove(options,
                    name=('n', '', 'name'),
                    timestamp=('t', '', 'timestamp')):
    """Remove crashlog for application with specified timestamp from the storage."""
    options.executor.executeAction('crashlog:remove', **{
        'storage': options.getService('storage'),
        'name': name,
        'timestamp': timestamp
    })


@crashlogDispatcher.command(name='removeall')
def crashlog_removeall(options,
                       name=('n', '', 'name')):
    """Remove all crashlogs for application from the storage."""
    options.executor.executeAction('crashlog:removeall', **{
        'storage': options.getService('storage'),
        'name': name,
    })


@crashlogDispatcher.command(name='clean')
def crashlog_clean(options,
                   name=('n', '', 'name'),
                   timestamp=('t', '', 'timestamp'),
                   size=('s', 1000, 'size')):
    """For application [NAME] leave [SIZE] crashlogs or remove all crashlogs with timestamp > [TIMESTAMP]."""
    options.executor.executeAction('crashlog:clean', **{
        'storage': options.getService('storage'),
        'name': name,
        'size': size,
        'timestamp': timestamp
    })


@dispatcher.group.command(name='list', usage='')
def group_list(options):
    """Show routing groups.
    """
    options.executor.executeAction('group:list', **{
        'storage': options.getService('storage')
    })


@dispatcher.group.command(name='view', usage='-n NAME')
def group_view(options,
               name=('n', '', 'group name')):
    """Show specified routing group.
    """
    options.executor.executeAction('group:view', **{
        'storage': options.getService('storage'),
        'name': name
    })


@dispatcher.group.command(name='create', usage='-n NAME -c CONTENT')
def group_create(options,
                 name=('n', '', 'group name'),
                 content=('c', '', 'group content')):
    """Create routing group.
    You can optionally specify content for created routing group. It can be both direct json expression in single
    quotes, or path to the json file with settings. The settings itself must be key-value list, where `key` represents
    application name, and `value` represents its weight. For example:

    cocaine-tool group create -n new_group -c '{
        "app": 1,
        "another_app": 2
    }'.

    Warning: all application weights must be positive integers,
             total weight must be positive.
    """
    options.executor.executeAction('group:create', **{
        'storage': options.getService('storage'),
        'name': name,
        'content': content
    })


@dispatcher.group.command(name='remove', usage='--name=NAME')
def group_remove(options,
                 name=('n', '', 'group name')):
    """Remove routing group"""
    options.executor.executeAction('group:remove', **{
        'storage': options.getService('storage'),
        'name': name
    })


@dispatcher.group.command(name='copy', usage='--name=NAME --copyname=NEWNAME')
def group_copy(options,
               name=('n', '', 'group name'),
               copyname=('c', '', 'new group name')):
    """Copy routing group."""
    options.executor.executeAction('group:copy', **{
        'storage': options.getService('storage'),
        'name': name,
        'copyname': copyname,
    })


@dispatcher.group.command(name='rename', usage='--name=NAME --copyname=NEWNAME')
def group_rename(options,
                 name=('n', '', 'group name'),
                 copyname=('c', '', 'new group name')):
    """Rename routing group."""
    options.executor.executeAction('group:rename', **{
        'storage': options.getService('storage'),
        'name': name,
        'copyname': copyname,
    })


@dispatcher.group.command(name='refresh', usage='[NAME]')
def group_refresh(options,
                  name=None):
    """Refresh routing group.

    If group name is empty this command will refresh all groups.
    """
    options.executor.executeAction('group:refresh', **{
        'locator': options.locator,
        'storage': options.getService('storage'),
        'name': name
    })


@dispatcher.group.command(name='push', usage='-n=NAME --app=APP -w=WEIGHT')
def group_push(options,
               name=('n', '', 'group name'),
               app=('', '', 'app name'),
               weight=('w', '', 'weight')):
    """Add application with its weight into the routing group.

    Warning: application weight must be positive integer.
    """
    options.executor.executeAction('group:app:add', **{
        'storage': options.getService('storage'),
        'name': name,
        'app': app,
        'weight': weight
    })


@dispatcher.group.command(name='pop', usage='-n NAME --app=APP')
def group_pop(options,
              name=('n', '', 'group name'),
              app=('', '', 'app name')):
    """Remove application from routing group.
    """
    options.executor.executeAction('group:app:remove', **{
        'storage': options.getService('storage'),
        'name': name,
        'app': app
    })


@dispatcher.group.command(name='edit', usage='-n NAME')
def group_edit(options,
               name=('n', '', 'group name')):
    """Edit group in an interactive editor"""
    options.executor.timeout = None
    options.executor.executeAction('group:edit', **{
        'storage': options.getService('storage'),
        'name': name,
    })


# @proxyDispatcher.command()
# def start(port=('', 8080, 'server port'),
#           count=('', 0, 'server subprocess count (0 means optimal for current CPU count)'),
#           config=('', '/etc/cocaine/cocaine-tornado-proxy.conf', 'path to the configuration file'),
#           daemon=('', False, 'run as daemon'),
#           pidfile=('', DEFAULT_COCAINE_PROXY_PID_FILE, 'pidfile')):
#     """Start embedded cocaine proxy.
#     """
#     Global.configureLog(logNames=['cocaine.tools', 'cocaine.proxy'])
#     try:
#         proxy.Start(**{
#             'port': port,
#             'daemon': daemon,
#             'count': count,
#             'config': config,
#             'pidfile': pidfile,
#         }).execute()
#     except proxy.Error as err:
#         logging.getLogger('cocaine.tools').error('Cocaine tool error - %s', err)


# @proxyDispatcher.command()
# def stop(pidfile=('', DEFAULT_COCAINE_PROXY_PID_FILE, 'pidfile')):
#     """Stop embedded cocaine proxy.
#     """
#     Global.configureLog(logNames=['cocaine.tools', 'cocaine.proxy'])
#     try:
#         proxy.Stop(**{
#             'pidfile': pidfile,
#         }).execute()
#     except proxy.Error as err:
#         logging.getLogger('cocaine.tools').error('Cocaine tool error - %s', err)


# @proxyDispatcher.command()
# def status(pidfile=('', DEFAULT_COCAINE_PROXY_PID_FILE, 'pidfile')):
#     """Show embedded cocaine proxy status.
#     """
#     Global.configureLog(logNames=['cocaine.tools', 'cocaine.proxy'])
#     try:
#         proxy.Status(**{
#             'pidfile': pidfile,
#         }).execute()
#     except proxy.Error as err:
#         logging.getLogger('cocaine.tools').error('Cocaine tool error - %s', err)


d.nest('app', appDispatcher, 'application commands')
d.nest('profile', profileDispatcher, 'profile commands')
d.nest('runlist', runlistDispatcher, 'runlist commands')
d.nest('crashlog', crashlogDispatcher, 'crashlog commands')
d.nest('group', dispatcher.group, 'routing group commands')
# d.nest('proxy', proxyDispatcher, 'cocaine proxy commands')
