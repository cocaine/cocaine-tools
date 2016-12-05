# coding=utf-8

import contextlib
import copy
import logging
import os

import click
import collections
import six
import yaml
from tornado import gen
from tornado.util import import_object

from cocaine.tools.cli import Executor

from cocaine.decorators import coroutine
from cocaine.exceptions import CocaineError
from cocaine.services import Locator, Service
from .plugins.secure.promiscuous import Promiscuous


CONFIG_GLOB = '/etc/cocaine/.cocaine/tools.yml'
CONFIG_USER = '~/.cocaine/tools.yml'
CONFIG_PATHS = [CONFIG_GLOB, CONFIG_USER]

log = logging.getLogger('cocaine.tools')


class SecureServiceError(CocaineError):
    pass


def set_verbosity(ctx, param, value):
    levels = [
        logging.NOTSET,
        logging.ERROR,
        logging.WARN,
        logging.INFO,
        logging.DEBUG
    ]

    loggers = ['cocaine.tools']

    # Enable cocaine-framework logging if there are more than maximum verbosity requested.
    if value > 4:
        value = 4
        loggers.append('cocaine')

    level = levels[value]

    formatter = logging.Formatter('%(levelname)-1.1s, %(asctime)s: %(message)s')

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(level)
    for name in loggers:
        lg = logging.getLogger(name)
        lg.propagate = False
        lg.addHandler(handler)
        lg.setLevel(level)
    return value


_global_options = [
    click.option('-v', count=True, callback=set_verbosity, help='Enable additional output.'),
    click.option('--host', metavar='', default='localhost', help='Locator hostname.'),
    click.option('--port', metavar='', default=10053, help='Locator port.'),
    click.option('--timeout', metavar='', default=20, help='Operation timeout.'),
]


@contextlib.contextmanager
def watch_progress(fmt, *args):
    message = (fmt % args).capitalize()
    try:
        click.echo('[ ] {}\033[1A'.format(message))
        yield
    except Exception:
        click.echo('[{}] {}'.format(click.style('✘', fg='red'), message))
        raise
    else:
        click.echo('[{}] {}'.format(click.style('✔', fg='green'), message))


def with_options(func):
    for option in reversed(_global_options):
        func = option(func)
    return func


class SecureService(object):
    def __init__(self, secure, wrapped):
        self._secure = secure
        self._wrapped = wrapped

    @coroutine
    def connect(self, traceid=None):
        yield self._wrapped.connect(traceid)

    def disconnect(self):
        return self._wrapped.disconnect()

    def __getattr__(self, name):
        @coroutine
        def wrapper(*args, **kwargs):
            try:
                kwargs['authorization'] = yield self._secure.fetch_token()
            except Exception as err:
                raise SecureServiceError('failed to fetch secure token: {}'.format(err))
            raise gen.Return((yield getattr(self._wrapped, name)(*args, **kwargs)))
        return wrapper


class ServiceFactory(object):
    def create_service(self, name):
        raise NotImplementedError

    def create_secure_service(self, name):
        raise NotImplementedError


class PooledServiceFactory(ServiceFactory):
    def __init__(self, endpoints):
        self._secure = None
        self._endpoints = endpoints
        self._cache = {}

    @property
    def secure(self):
        return self._secure

    @secure.setter
    def secure(self, value):
        self._secure = value

    def create_service(self, name):
        if name not in self._cache:
            if name == 'locator':
                service = Locator(endpoints=self._endpoints)
            else:
                service = Service(name, endpoints=self._endpoints)
            self._cache[name] = service
        return self._cache[name]

    def create_secure_service(self, name):
        return SecureService(self._secure, self.create_service(name))


class Configurator(object):
    def __init__(self):
        self._config = {}

    @property
    def config(self):
        return self._config

    def update(self, paths=None):
        if paths is None:
            paths = CONFIG_PATHS

        log.debug('scanning configs at %s', paths)
        configs = []
        for path in paths:
            if os.path.exists(os.path.expanduser(path)):
                content = yaml.safe_load(open(os.path.expanduser(path)).read())
                configs.append((path, content))
                log.debug('successfully read config from %s\n%s', path, content)
            else:
                log.debug('config %s was not found, skipping', path)

        self._config.clear()
        if len(configs) == 0:
            log.info('no config found - use default values')
        else:
            used = []
            for filename, config in configs:
                used.append(filename)
                self._config = Configurator._merge_dicts(self._config, config)
            log.info('loaded config(s) from %s', used)

    @staticmethod
    def _merge_dicts(src, d):
        key = None
        try:
            if src is None or isinstance(src, (six.string_types, float, six.integer_types)):
                # Border case for first run or if `src` is a primitive.
                src = d
            elif isinstance(src, list):
                # Lists can be only appended.
                if isinstance(d, list):
                    src.extend(d)
                else:
                    src.append(d)
            elif isinstance(src, dict):
                # Dicts must be merged.
                if isinstance(d, dict):
                    for key in d:
                        if key in src:
                            src[key] = Configurator._merge_dicts(src[key], d[key])
                        else:
                            src[key] = d[key]
                else:
                    raise RuntimeError('Cannot merge non-dict "%s" into dict "%s"' % (d, src))
            else:
                raise RuntimeError('NOT IMPLEMENTED "%s" into "%s"' % (d, src))
        except TypeError as e:
            raise RuntimeError(
                'TypeError "%s" in key "%s" when merging "%s" into "%s"' % (e, key, d, src))
        return src


class PluginLoader(object):
    def __init__(self):
        self._secure = Promiscuous(None)

    def load(self, config, repo):
        self._load_secure(config, repo)

    def _load_secure(self, config, repo):
        if 'secure' in config:
            ty = config['secure'].get('mod')
            if ty is None:
                return
            kwargs = copy.deepcopy(config['secure'])
            kwargs.pop('mod')

            if not ty.startswith('cocaine.tools.plugins.secure'):
                ty = 'cocaine.tools.plugins.secure.' + ty.lower() + '.' + ty
            self._secure = import_object(ty)(repo, **kwargs)
            log.info('imported "%s" secure plugin', ty)

    def secure(self):
        return self._secure


class Context(object):
    def __init__(self, host, port, timeout, **kwargs):
        self._endpoints = [(host, port)]
        self._timeout = timeout
        self._options = kwargs

        self._configurator = Configurator()
        self._configurator.update()

        self._repo = PooledServiceFactory(endpoints=self._endpoints)

        self._loader = PluginLoader()
        self._loader.load(self._configurator.config, self._repo)

        self._repo.secure = self._loader.secure()

        self._executor = Executor(timeout=timeout)

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value

    @property
    def repo(self):
        return self._repo

    @property
    def locator(self):
        return self._repo.create_service('locator')

    def execute_action(self, __name, **kwargs):
        return self._executor.execute_action(__name, **kwargs)


@click.group()
def tools():
    pass


@tools.group()
def app():
    """
    Application commands.
    """
    pass


@tools.group()
def profile():
    """
    Profile commands.
    """
    pass


@app.command(name='list')
@with_options
def app_list(**kwargs):
    """
    Show uploaded applications.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@app.command(name='view')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_view(name, **kwargs):
    """
    Show manifest content for an application.

    If application is not uploaded, an error will be displayed.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:view', **{
        'storage': ctx.repo.create_service('storage'),
        'name': name,
    })


@app.command(name='upload')
@click.argument('path', type=click.Path(exists=True))
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('--manifest', metavar='', help='Manifest file name.')
@click.option('--package', metavar='', help='Path to the application archive.')
@click.option('--docker_address', metavar='', help='Docker address.')
@click.option('--registry', metavar='', help='Docker Registry address.')
@click.option('--manifest_only', metavar='', default=False, help='Upload only manifest.')
@with_options
def app_upload(path, name, manifest, package, docker_address, registry, manifest_only, **kwargs):
    """
    Upload application with its environment (directory) into the storage.

    Application directory or its subdirectories must contain valid manifest file
    named `manifest.json` or `manifest` otherwise you must specify it explicitly by
    setting `--manifest` option.

    You can specify application name. By default, leaf directory name is treated as application
    name.

    If you have already prepared application archive (*.tar.gz), you can explicitly specify path to
    it by setting `--package` option.

    Additional output can be turned on by passing `-vvvv` option.
    """
    lower_limit = 120.0

    ctx = Context(**kwargs)
    if ctx.timeout < lower_limit:
        ctx.timeout = lower_limit
        log.info('shifted timeout to the %.2fs', ctx.timeout)

    mutex_record = collections.namedtuple('mutex_record', 'value, name')
    mutex = [
        (mutex_record(path, 'PATH'), mutex_record(package, '--package')),
        (mutex_record(package, '--package'), mutex_record(docker_address, '--docker')),
        (mutex_record(package, '--package'), mutex_record(registry, '--registry')),
    ]
    for (f, s) in mutex:
        if f.value and s.value:
            click.echo('Wrong usage: option {} and {} are mutual exclusive, you can only use one'.
                       format(f.name, s.name))
            exit(os.EX_USAGE)

    if manifest_only:
        ctx.execute_action('app:upload-manual', **{
            'storage': ctx.repo.create_service('storage'),
            'name': name,
            'manifest': manifest,
            'package': None,
            'manifest_only': manifest_only,
        })
    elif package:
        ctx.execute_action('app:upload-manual', **{
            'storage': ctx.repo.create_service('storage'),
            'name': name,
            'manifest': manifest,
            'package': package
        })
    elif docker_address:
        ctx.execute_action('app:upload-docker', **{
            'storage': ctx.repo.create_service('storage'),
            'path': path,
            'name': name,
            'manifest': manifest,
            'address': docker_address,
            'registry': registry
        })
    else:
        ctx.execute_action('app:upload', **{
            'storage': ctx.repo.create_service('storage'),
            'path': path,
            'name': name,
            'manifest': manifest
        })


@app.command(name='remove')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_remove(name, **kwargs):
    """
    Remove application from storage.

    No error messages will display if specified application is not uploaded.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:remove', **{
        'node': ctx.repo.create_secure_service('storage'),
        'name': name,
    })


@app.command(name='start')
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('-r', '--profile', metavar='', help='Profile name.')
@with_options
def app_start(name, profile, **kwargs):
    """
    Start an application with specified profile.

    Does nothing if application is already running.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:start', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
        'profile': profile
    })


@app.command(name='pause')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_pause(name, **kwargs):
    """
    Stop application.

    This command is alias for ```cocaine-tool app stop```.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:stop', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
    })


@app.command(name='stop')
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def app_stop(name, **kwargs):
    """
    Stop application.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:stop', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
    })


@app.command(name='restart')
@click.option('-n', '--name', metavar='', help='Application name.')
@click.option('-r', '--profile', metavar='', help='Profile name.')
@with_options
def app_restart(name, prof, **kwargs):
    """
    Restart application.

    Executes ```cocaine-tool app pause``` and ```cocaine-tool app start``` sequentially.

    It can be used to quickly change application profile.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:restart', **{
        'node': ctx.repo.create_secure_service('node'),
        'locator': ctx.locator,
        'name': name,
        'profile': prof
    })


@app.command()
@click.option('-n', '--name', metavar='', help='Application name.')
@with_options
def check(name, **kwargs):
    """
    Check application status.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('app:check', **{
        'node': ctx.repo.create_secure_service('node'),
        'name': name,
    })


@profile.command(name='list')
@with_options
def profile_list(**kwargs):
    """
    Show uploaded profiles.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:list', **{
        'storage': ctx.repo.create_secure_service('storage'),
    })


@profile.command(name='view')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@with_options
def profile_view(name, **kwargs):
    """
    Show profile configuration content.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:view', **{
        'storage': ctx.repo.create_service('storage'),
        'name': name,
    })


@profile.command(name='upload')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@click.option('-r', '--profile', metavar='', required=True, help='Path to profile.')
@with_options
def profile_upload(name, prof, **kwargs):
    """
    Upload profile into the storage.
    """
    ctx = Context(**kwargs)
    ctx.execute_action('profile:upload', **{
        'storage': ctx.repo.create_service('storage'),
        'name': name,
        'profile': prof,
    })


@profile.command(name='edit')
@click.option('-n', '--name', metavar='', required=True, help='Profile name.')
@with_options
def profile_edit(name, **kwargs):
    """
    Edit profile using interactive editor.
    """
    ctx = Context(**kwargs)
    ctx.timeout = None
    ctx.execute_action('profile:edit', **{
        'storage': ctx.repo.create_service('storage'),
        'name': name,
    })


cli = click.CommandCollection(sources=[tools])
