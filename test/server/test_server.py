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

import unittest
from typing import Optional

from tornado import concurrent

from xcube.server.api import Api
from xcube.server.api import ApiContext
from xcube.server.api import Context
from xcube.server.server import Server
from xcube.server.server import ServerContext
from xcube.util.frozen import FrozenDict
from xcube.util.jsonschema import JsonArraySchema
from xcube.util.jsonschema import JsonObjectSchema
from xcube.util.jsonschema import JsonStringSchema
from .mocks import MockApiContext
from .mocks import MockFramework
from .mocks import mock_extension_registry
from .mocks import mock_server


class ServerTest(unittest.TestCase):
    def test_basic_props(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict(create_ctx=MockApiContext)),
        ])
        framework = MockFramework()
        server = Server(
            framework, {},
            extension_registry=extension_registry
        )
        self.assertIs(framework, server.framework)
        self.assertIsInstance(server.apis, tuple)
        self.assertIs(server.apis, server.apis)
        self.assertIsInstance(server.open_api_doc, dict)
        self.assertIsInstance(server.ctx, ServerContext)
        self.assertIs(server.ctx, server.ctx)

    def test_accepts_unknown_config_settings(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict(create_ctx=MockApiContext)),
        ])
        framework = MockFramework()
        server = Server(
            framework,
            {
                "i_am_an_unknown_setting": 137
            },
            extension_registry=extension_registry
        )
        self.assertNotIn("i_am_an_unknown_setting",
                         server.ctx.config)

    def test_framework_delegation(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict(create_ctx=MockApiContext)),
        ])
        framework = MockFramework()
        server = Server(
            framework, {},
            extension_registry=extension_registry
        )
        self.assertEqual(1, framework.add_routes_count)
        self.assertEqual(1, framework.update_count)
        self.assertEqual(0, framework.start_count)
        self.assertEqual(0, framework.stop_count)
        server.start()
        self.assertEqual(1, framework.start_count)
        self.assertEqual(0, framework.stop_count)
        server.stop()
        self.assertEqual(1, framework.start_count)
        self.assertEqual(1, framework.stop_count)

    def test_server_ctx(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict(create_ctx=MockApiContext)),
        ])
        server = Server(
            MockFramework(), {},
            extension_registry=extension_registry
        )
        self.assertIsInstance(server.ctx, ServerContext)
        datasets_api_ctx = server.ctx.get_api_ctx('datasets')
        self.assertIsInstance(datasets_api_ctx, MockApiContext)
        self.assertTrue(datasets_api_ctx.on_update_count)
        time_series_api_ctx = server.ctx.get_api_ctx('timeseries')
        self.assertIsNone(time_series_api_ctx)
        self.assertEqual({'address': '0.0.0.0',
                          'port': 8080},
                         server.ctx.config)

    def test_config_schema_effectively_merged(self):
        extension_registry = mock_extension_registry([
            (
                "datasets",
                dict(
                    config_schema=JsonObjectSchema(
                        properties=dict(
                            data_stores=JsonArraySchema(
                                items=JsonObjectSchema(
                                    additional_properties=True
                                )
                            )
                        ),
                        required=['data_stores'],
                        additional_properties=False
                    ))
            ),
        ])
        server = Server(
            MockFramework(),
            {
                "data_stores": []
            },
            extension_registry=extension_registry
        )
        self.assertEqual(
            {
                'type': 'object',
                'additionalProperties': True,
                'required': ['data_stores'],
                'properties': {
                    'api_spec': {
                        'type': 'object',
                        'title': 'API specification',
                        'additionalProperties': False,
                        'description': 'selected = (includes | ALL) - '
                                       '(excludes | NONE)',
                        'properties': {
                            'excludes': {
                                'type': 'array',
                                'items': {
                                    'type': 'string',
                                    'minLength': 1,
                                },
                            },
                            'includes': {
                                'type': 'array',
                                'items': {
                                    'type': 'string',
                                    'minLength': 1,
                                },
                            }
                        },
                    },
                    'base_dir': {
                        'type': 'string',
                        'title': 'Base directory used to resolve relative '
                                 'local paths.',
                    },
                    'address': {
                        'type': 'string',
                        'default': '0.0.0.0',
                        'title': 'Server address.',
                    },
                    'port': {
                        'type': 'integer',
                        'title': 'Server port.',
                        'default': 8080,
                    },
                    'url_prefix': {
                        'title': 'Prefix to be prepended to all URL '
                                 'route paths.',
                        'type': 'string'
                    },
                    'static_routes': {
                        'type': 'array',
                        'title': 'Static content routes',
                        'items': {
                            'type': 'array',
                            'items': [{'minLength': 1,
                                       'title': 'URL path',
                                       'type': 'string'},
                                      {'minLength': 1,
                                       'title': 'Local path',
                                       'type': 'string'}],
                        },
                    },
                    'trace_perf': {
                        'type': 'boolean',
                        'title': 'Output performance measures',
                    },
                    'data_stores': {
                        'type': 'array',
                        'items': {'additionalProperties': True,
                                  'type': 'object'},
                    },
                },
            },
            server.config_schema.to_dict()
        )
        self.assertIsInstance(server.config_schema, JsonObjectSchema)

    def test_config_schema_must_be_object(self):
        extension_registry = mock_extension_registry([
            (
                "datasets",
                dict(
                    config_schema=JsonObjectSchema(
                        properties=dict(address=JsonStringSchema())
                    )
                )
            ),
        ])
        with self.assertRaises(ValueError) as cm:
            Server(
                MockFramework(), {},
                extension_registry=extension_registry
            )
        self.assertEqual(f"API 'datasets':"
                         f" configuration parameter 'address'"
                         f" is already defined.",
                         f'{cm.exception}')

    def test_update_is_effective(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict(create_ctx=MockApiContext)),
            ("timeseries", dict(create_ctx=MockApiContext,
                                required_apis=["datasets"])),
        ])
        server = Server(
            MockFramework(), {},
            extension_registry=extension_registry
        )
        prev_ctx = server.ctx
        server.update({"port": 9090})
        self.assertEqual({'address': '0.0.0.0',
                          'port': 9090},
                         server.ctx.config)
        self.assertIsNot(prev_ctx, server.ctx)

    def test_update_disposes(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict(create_ctx=MockApiContext)),
        ])
        server = Server(
            MockFramework(), {},
            extension_registry=extension_registry
        )
        api_ctx = server.ctx.get_api_ctx("datasets")
        self.assertIsInstance(api_ctx, MockApiContext)
        self.assertEqual(1, api_ctx.on_update_count)
        self.assertEqual(0, api_ctx.on_dispose_count)
        server.update({})
        self.assertEqual(1, api_ctx.on_update_count)
        self.assertEqual(1, api_ctx.on_dispose_count)

    def test_call_later(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict()),
        ])
        framework = MockFramework()
        server = Server(
            framework, {},
            extension_registry=extension_registry
        )
        self.assertEqual(0, framework.call_later_count)
        result = server.call_later(0.01, lambda x: x)
        self.assertIsInstance(result, object)
        self.assertEqual(1, framework.call_later_count)

    def test_run_in_executor(self):
        extension_registry = mock_extension_registry([
            ("datasets", dict()),
        ])
        framework = MockFramework()
        server = Server(
            framework, {},
            extension_registry=extension_registry
        )
        self.assertEqual(0, framework.run_in_executor_count)
        result = server.run_in_executor(None, lambda x: x)
        self.assertIsInstance(result, concurrent.futures.Future)
        self.assertEqual(1, framework.run_in_executor_count)

    api_spec = [
        ("datasets", dict()),
        ("places", dict()),
        ("timeseries", dict()),
        ("stac", dict(required_apis=("datasets", "places"))),
        ("openeo", dict(required_apis=("datasets",
                                       "places", "timeseries"))),
        ("wcs", dict(required_apis=("datasets",))),
        ("wmts", dict(required_apis=("datasets",))),
    ]

    def test_apis_loaded_in_order(self):
        extension_registry = mock_extension_registry(self.api_spec)
        apis = Server.load_apis({}, extension_registry=extension_registry)
        self.assertIsInstance(apis, tuple)
        self.assertEqual(('datasets',
                          'places',
                          'timeseries',
                          'wcs',
                          'wmts',
                          'stac',
                          'openeo'),
                         tuple(api.name for api in apis))

    def test_api_spec_includes(self):
        extension_registry = mock_extension_registry(self.api_spec)
        apis = Server.load_apis(
            {
                "api_spec": {
                    "includes": ["datasets", "timeseries"],
                }
            },
            extension_registry=extension_registry
        )
        self.assertIsInstance(apis, tuple)
        self.assertEqual(('datasets',
                          'timeseries'),
                         tuple(api.name for api in apis))

    def test_api_spec_excludes(self):
        extension_registry = mock_extension_registry(self.api_spec)
        apis = Server.load_apis(
            {
                "api_spec": {
                    "excludes": ["stac", "openeo", "wcs"],
                }
            },
            extension_registry=extension_registry
        )
        self.assertIsInstance(apis, tuple)
        self.assertEqual(('datasets',
                          'places',
                          'timeseries',
                          'wmts'),
                         tuple(api.name for api in apis))

    def test_api_spec_incl_excl(self):
        extension_registry = mock_extension_registry(self.api_spec)
        apis = Server.load_apis(
            {
                "api_spec": {
                    "includes": ["datasets", "places", "timeseries", "wmts"],
                    "excludes": ["places", "wmts", "wcs", "openeo"],
                }
            },
            extension_registry=extension_registry
        )
        self.assertIsInstance(apis, tuple)
        self.assertEqual(('datasets',
                          'timeseries'),
                         tuple(api.name for api in apis))

    def test_illegal_api_context_detected(self):
        # noinspection PyUnusedLocal
        def create_ctx(server_ctx):
            return 42

        extension_registry = mock_extension_registry(
            [('datasets', dict(create_ctx=create_ctx))],
        )

        with self.assertRaises(TypeError) as cm:
            Server(MockFramework(),
                   {},
                   extension_registry=extension_registry)
        self.assertEqual("api_ctx (context of API 'datasets')"
                         " must be an instance of"
                         " <class 'xcube.server.api.ApiContext'>,"
                         " was <class 'int'>",
                         f'{cm.exception}')

    def test_missing_dependency_detected(self):
        extension_registry = mock_extension_registry(
            [('timeseries', dict(required_apis=('datasets',)))]
        )

        with self.assertRaises(ValueError) as cm:
            Server(MockFramework(),
                   {},
                   extension_registry=extension_registry)
        self.assertEqual("API 'timeseries':"
                         " missing API dependency 'datasets'",
                         f'{cm.exception}')


class ServerContextTest(unittest.TestCase):
    def test_basic_props(self):
        server = mock_server()
        config = {}
        server_ctx = ServerContext(server, config)
        self.assertIsInstance(server_ctx.config, FrozenDict)
        self.assertEqual(config, server_ctx.config)
        self.assertEqual((), server_ctx.apis)
        self.assertIsInstance(server_ctx.open_api_doc, dict)

    def test_on_update_and_on_dispose(self):
        server = mock_server()
        server_ctx = ServerContext(server, {})
        self.assertIs(None, server_ctx.on_update(None))
        self.assertIs(None, server_ctx.on_dispose())

    def test_async_exec(self):
        framework = MockFramework()
        server = mock_server(framework=framework)
        config = {}
        server_ctx = ServerContext(server, config)

        def my_func(a, b):
            return a + b

        self.assertEqual(0, framework.call_later_count)
        server_ctx.call_later(0.1, my_func, 40, 2)
        self.assertEqual(1, framework.call_later_count)

        self.assertEqual(0, framework.run_in_executor_count)
        server_ctx.run_in_executor(None, my_func, 40, 2)
        self.assertEqual(1, framework.run_in_executor_count)

    class DatasetsContext(ApiContext):

        def on_update(self, prev_ctx: Optional[Context]):
            pass

    class TimeSeriesContext(ApiContext):
        def __init__(self, server_ctx: Context):
            super().__init__(server_ctx)
            self.dataset_ctx = server_ctx.get_api_ctx("datasets")

        def on_update(self, prev_ctx: Optional[Context]):
            pass

    def test_on_update(self):
        api1 = Api("datasets",
                   create_ctx=self.DatasetsContext)
        api2 = Api("timeseries",
                   create_ctx=self.TimeSeriesContext,
                   required_apis=["datasets"])
        config = {}
        server_ctx = ServerContext(mock_server(api_specs=[api1, api2]),
                                   config)
        server_ctx.on_update(None)
        api1_ctx = server_ctx.get_api_ctx('datasets')
        api2_ctx = server_ctx.get_api_ctx('timeseries')

        self.assertIsInstance(api1_ctx, self.DatasetsContext)
        self.assertIsInstance(api2_ctx, self.TimeSeriesContext)

        self.assertIs(server_ctx, api1_ctx.server_ctx)
        self.assertIs(server_ctx, api2_ctx.server_ctx)

        self.assertIsInstance(api1_ctx.config, FrozenDict)
        self.assertIsInstance(api2_ctx.config, FrozenDict)
        self.assertEqual(config, api1_ctx.config)
        self.assertEqual(config, api2_ctx.config)

        api21_ctx = api2_ctx.get_api_ctx("datasets")
        self.assertIsInstance(api21_ctx, self.DatasetsContext)
