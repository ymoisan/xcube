# The MIT License (MIT)
# Copyright (c) 2022 by the xcube team and contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import collections.abc
import concurrent.futures
import copy
import os.path
from typing import (Optional, Dict, Any, Union,
                    Callable, Sequence, Awaitable, Tuple, Type, List, Mapping)

import jsonschema.exceptions

from xcube.constants import EXTENSION_POINT_SERVER_APIS
from xcube.constants import LOG
from xcube.util.assertions import assert_instance
from xcube.util.assertions import assert_subclass
from xcube.util.extension import ExtensionRegistry
from xcube.util.extension import get_extension_registry
from xcube.util.jsonschema import JsonObjectSchema
from xcube.version import version
from .api import Api
from .api import ApiContext
from .api import ApiContextT
from .api import ApiRoute
from .api import Context
from .api import ReturnT
from .api import ServerConfig
from .asyncexec import AsyncExecution
from .config import BASE_SERVER_CONFIG_SCHEMA
from .config import get_url_prefix
from .framework import Framework
from ..util.frozen import FrozenDict


class Server(AsyncExecution):
    """
    A REST server extendable by API extensions.

    APIs are registered using the extension point
    "xcube.server.api".

    TODO:
      * Allow server to serve generic static content,
        e.g. "http://localhost:8080/images/outside-cube/${ID}.jpg"
      * Allow server updates triggered by local file changes
        and changes in s3 buckets
      * Address common server configuration
        - Configure server types by meta-configuration,
          e.g. server name, server description, names of APIs served,
          aliases for common server config...
        - Why we need aliases for common server config:
          o Camel case vs snake case parameters names,
            e.g. "BaseDir" vs "base_dir"
          o First capital letter in parameter names,
            e.g. "Address" vs "address"
      * Use any given request JSON schema in openAPI
        to validate requests in HTTP methods

    :param framework: The web server framework to be used
    :param config: The server configuration.
    :param extension_registry: Optional extension registry.
        Defaults to xcube's default extension registry.
    """

    def __init__(
            self,
            framework: Framework,
            config: Mapping[str, Any],
            extension_registry: Optional[ExtensionRegistry] = None,
    ):
        assert_instance(framework, Framework)
        assert_instance(config, collections.abc.Mapping)
        apis = self.load_apis(config,
                              extension_registry=extension_registry)
        for api in apis:
            LOG.info(f'Loaded service API {api.name!r}')
        static_routes = self.collect_static_routes(config)
        routes = self.collect_api_routes(apis)
        url_prefix = get_url_prefix(config)
        framework.add_static_routes(static_routes, url_prefix)
        framework.add_routes(routes, url_prefix)
        self._framework = framework
        self._apis = apis
        self._config_schema = self.get_effective_config_schema(
            framework,
            apis
        )
        ctx = self._new_ctx(config)
        ctx.on_update(None)
        self._set_ctx(ctx)

    @property
    def framework(self) -> Framework:
        """The web server framework use by this server."""
        return self._framework

    @property
    def apis(self) -> Tuple[Api]:
        """The APIs supported by this server."""
        return self._apis

    @property
    def config_schema(self) -> JsonObjectSchema:
        """The effective JSON schema for the server configuration."""
        return self._config_schema

    @property
    def ctx(self) -> "ServerContext":
        """The current server context."""
        return self._ctx

    def _set_ctx(self, ctx: "ServerContext"):
        self._ctx = ctx
        self._framework.update(ctx)

    def _new_ctx(self, config: collections.abc.Mapping) -> "ServerContext":
        config = dict(config)
        for key in tuple(config.keys()):
            if key not in self._config_schema.properties:
                LOG.warning(f'Configuration setting {key!r} ignored,'
                            f' because there is no schema describing it.')
                config.pop(key)
        try:
            validated_config = self._config_schema.from_instance(config)
        except jsonschema.exceptions.ValidationError as e:
            raise ValueError(f"Invalid server configuration:\n{e}") from e
        return ServerContext(self, validated_config)

    def start(self):
        """Start this server."""
        LOG.info(f'Starting service...')
        for api in self._apis:
            api.on_start(self.ctx)
        self._framework.start(self.ctx)

    def stop(self):
        """Stop this server."""
        LOG.info(f'Stopping service...')
        self._framework.stop(self.ctx)
        for api in self._apis:
            api.on_stop(self.ctx)
        self._ctx.on_dispose()

    def update(self, config: Mapping[str, Any]):
        """Update this server with given server configuration."""
        ctx = self._new_ctx(config)
        ctx.on_update(prev_ctx=self._ctx)
        self._set_ctx(ctx)

    def call_later(self,
                   delay: Union[int, float],
                   callback: Callable,
                   *args,
                   **kwargs):
        """
        Executes the given callable *callback* after *delay* seconds.

        :param delay: Delay in seconds.
        :param callback: Callback to be called.
        :param args: Positional arguments passed to *callback*.
        :param kwargs: Keyword arguments passed to *callback*.
        """
        return self._framework.call_later(
            delay, callback, *args, **kwargs
        )

    def run_in_executor(
            self,
            executor: Optional[concurrent.futures.Executor],
            function: Callable[..., ReturnT],
            *args: Any,
            **kwargs: Any
    ) -> Awaitable[ReturnT]:
        """
        Concurrently runs a *function* in a ``concurrent.futures.Executor``.
        If *executor* is ``None``, the framework's default
        executor will be used.

        :param executor: An optional executor.
        :param function: The function to be run concurrently.
        :param args: Positional arguments passed to *function*.
        :param kwargs: Keyword arguments passed to *function*.
        :return: The awaitable return value of *function*.
        """
        return self._framework.run_in_executor(
            executor, function, *args, **kwargs
        )

    @classmethod
    def load_apis(
            cls,
            config: collections.abc.Mapping,
            extension_registry: Optional[ExtensionRegistry] = None
    ) -> Tuple[Api]:
        # Collect all registered API extensions
        extension_registry = extension_registry \
                             or get_extension_registry()
        api_extensions = extension_registry.find_extensions(
            EXTENSION_POINT_SERVER_APIS
        )

        # Get APIs specification
        api_spec = config.get("api_spec", {})
        incl_api_names = api_spec.get("includes",
                                      [ext.name for ext in api_extensions])
        excl_api_names = api_spec.get("excludes",
                                      [])

        # Collect effective APIs
        api_names = set(incl_api_names).difference(set(excl_api_names))
        apis: List[Api] = [ext.component
                           for ext in api_extensions
                           if ext.name in api_names]

        api_lookup = {api.name: api for api in apis}

        def assert_required_apis_available():
            # Assert that required APIs are available.
            for api in apis:
                for req_api_name in api.required_apis:
                    if req_api_name not in api_lookup:
                        raise ValueError(f'API {api.name!r}: missing API'
                                         f' dependency {req_api_name!r}')

        assert_required_apis_available()

        def count_api_refs(api: Api) -> int:
            # Count the number of times the given API is referenced.
            dep_sum = 0
            for req_api_name in api.required_apis:
                dep_sum += count_api_refs(api_lookup[req_api_name]) + 1
            for opt_api_name in api.optional_apis:
                if opt_api_name in api_lookup:
                    dep_sum += count_api_refs(api_lookup[opt_api_name]) + 1
            return dep_sum

        # Count the number of times each API is referenced.
        api_ref_counts = {
            api.name: count_api_refs(api)
            for api in apis
        }

        # Return an ordered dict sorted by an API's reference count
        return tuple(sorted(apis,
                            key=lambda api: api_ref_counts[api.name]))

    @classmethod
    def collect_static_routes(cls, config: collections.abc.Mapping) \
            -> Sequence[Tuple[str, str]]:
        static_routes = config.get('static_routes', [])
        base_dir = config.get('base_dir', os.path.abspath(""))
        return [
            (
                url_path,
                local_path if os.path.isabs(local_path)
                else os.path.join(base_dir, local_path)
            )
            for url_path, local_path in static_routes
        ]

    @classmethod
    def collect_api_routes(cls, apis: Sequence[Api]) -> Sequence[ApiRoute]:
        handlers = []
        for api in apis:
            handlers.extend(api.routes)
        return handlers

    @classmethod
    def get_effective_config_schema(
            cls,
            framework: Framework,
            apis: Sequence[Api]
    ) -> JsonObjectSchema:
        effective_config_schema = copy.deepcopy(BASE_SERVER_CONFIG_SCHEMA)
        framework_config_schema = framework.config_schema
        if framework_config_schema is not None:
            cls._update_config_schema(effective_config_schema,
                                      framework_config_schema,
                                      f'Server')
        for api in apis:
            api_config_schema = api.config_schema
            if api_config_schema is not None:
                cls._update_config_schema(effective_config_schema,
                                          api_config_schema,
                                          f'API {api.name!r}')
        return effective_config_schema

    @classmethod
    def _update_config_schema(cls,
                              config_schema: JsonObjectSchema,
                              config_schema_update: JsonObjectSchema,
                              schema_name: str):
        assert isinstance(config_schema, JsonObjectSchema)
        assert isinstance(config_schema_update, JsonObjectSchema)
        for k, v in config_schema_update.properties.items():
            if k in config_schema.properties:
                raise ValueError(f'{schema_name}:'
                                 f' configuration parameter {k!r}'
                                 f' is already defined.')
            config_schema.properties[k] = v
        if config_schema_update.required:
            config_schema.required.update(
                config_schema_update.required
            )

    @property
    def open_api_doc(self) -> Dict[str, Any]:
        """Get the OpenAPI JSON document for this server."""
        error_schema = {
            "type": "object",
            "properties": {
                "status_code": {
                    "type": "integer",
                    "minimum": 200,
                },
                "message": {
                    "type": "string",
                },
                "reason": {
                    "type": "string",
                },
                "exception": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                }
            },
            "additionalProperties": True,
            "required": ["status_code", "message"],
        }

        schema_components = {
            "Error": {
                "type": "object",
                "properties": {
                    "error": error_schema,
                },
                "additionalProperties": True,
                "required": ["error"],
            }
        }

        response_components = {
            "UnexpectedError": {
                "description": "Unexpected error.",
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": "#/components/schemas/Error"
                        }
                    }
                }
            }
        }

        default_responses = {
            "200": {
                "description": "On success.",
            },
            "default": {
                "$ref": "#/components/responses/UnexpectedError"
            }
        }

        url_prefix = get_url_prefix(self.ctx.config)

        tags = []
        paths = {}
        for other_api in self.ctx.apis:
            if not other_api.routes:
                # Only include APIs with endpoints
                continue
            tags.append({
                "name": other_api.name,
                "description": other_api.description or ""
            })
            for route in other_api.routes:
                path = dict(
                    description=getattr(
                        route.handler_cls, "__doc__", ""
                    ) or ""
                )
                for method in ("head",
                               "get",
                               "post",
                               "put",
                               "delete",
                               "options"):
                    fn = getattr(route.handler_cls, method, None)
                    fn_openapi = getattr(fn, '__openapi__', None)
                    if fn_openapi is not None:
                        fn_openapi = dict(**fn_openapi)
                        if 'tags' not in fn_openapi:
                            fn_openapi['tags'] = [other_api.name]
                        if 'description' not in fn_openapi:
                            fn_openapi['description'] = \
                                getattr(fn, "__doc__", None) or ""
                        if 'responses' not in fn_openapi:
                            fn_openapi['responses'] = default_responses
                        path[method] = dict(**fn_openapi)
                paths[route.path] = path

        return {
            "openapi": "3.0.0",
            "info": {
                "title": "xcube Server",
                "description": "xcube Server API",
                "version": version,
            },
            "servers": [
                {
                    # TODO (forman): the following URL must be adjusted
                    #   e.g. pass request.url_for_path('') as url into
                    #   this method, or even pass the list of servers.
                    "url": f"http://localhost:8080{url_prefix}",
                    "description": "Local development server."
                },
            ],
            "tags": tags,
            "paths": paths,
            "components": {
                "schemas": schema_components,
                "responses": response_components
            }
        }


class ServerContext(Context):
    """
    The server context holds the current server configuration and
    the API context objects that depend on that specific configuration.

    A new server context is created for any new server configuration,
    which in turn will cause all API context objects to be updated.

    The constructor shall not be called directly.

    :param server: The server.
    :param config: The current server configuration.
    """

    def __init__(self,
                 server: Server,
                 config: collections.abc.Mapping):
        self._server = server
        self._config = FrozenDict.freeze(config)
        self._api_contexts: Dict[str, Context] = dict()

    @property
    def server(self) -> Server:
        return self._server

    @property
    def apis(self) -> Tuple[Api]:
        return self._server.apis

    @property
    def open_api_doc(self) -> Dict[str, Any]:
        return self._server.open_api_doc

    @property
    def config(self) -> ServerConfig:
        return self._config

    def get_api_ctx(self,
                    api_name: str,
                    cls: Optional[Type[ApiContextT]] = None) \
            -> Optional[ApiContextT]:
        api_ctx = self._api_contexts.get(api_name)
        if cls is not None:
            assert_subclass(cls, ApiContext, name='cls')
            assert_instance(api_ctx, cls,
                            name=f'api_ctx (context of API {api_name!r})')
        return api_ctx

    def _set_api_ctx(self, api_name: str, api_ctx: ApiContext):
        assert_instance(api_ctx, ApiContext,
                        name=f'api_ctx (context of API {api_name!r})')
        self._api_contexts[api_name] = api_ctx
        setattr(self, api_name, api_ctx)

    def call_later(self,
                   delay: Union[int, float],
                   callback: Callable,
                   *args,
                   **kwargs) -> object:
        return self._server.call_later(delay, callback,
                                       *args, **kwargs)

    def run_in_executor(self,
                        executor: Optional[concurrent.futures.Executor],
                        function: Callable[..., ReturnT],
                        *args: Any,
                        **kwargs: Any) -> Awaitable[ReturnT]:
        return self._server.run_in_executor(executor, function,
                                            *args, **kwargs)

    def on_update(self, prev_ctx: Optional["ServerContext"]):
        if prev_ctx is None:
            LOG.info(f'Applying initial configuration...')
        else:
            LOG.info(f'Applying configuration changes...')
        for api in self.apis:
            prev_api_ctx: Optional[ApiContext] = None
            if prev_ctx is not None:
                prev_api_ctx = prev_ctx.get_api_ctx(api.name)
                assert prev_api_ctx is not None
            for dep_api_name in api.required_apis:
                dep_api_ctx = self.get_api_ctx(dep_api_name)
                assert dep_api_ctx is not None
            next_api_ctx: Optional[ApiContext] = api.create_ctx(self)
            self._set_api_ctx(api.name, next_api_ctx)
            next_api_ctx.on_update(prev_api_ctx)
            if prev_api_ctx is not None \
                    and prev_api_ctx is not next_api_ctx:
                prev_api_ctx.on_dispose()

    def on_dispose(self):
        for api_name in reversed([api.name for api in self.apis]):
            api_ctx = self.get_api_ctx(api_name)
            if api_ctx is not None:
                api_ctx.on_dispose()
