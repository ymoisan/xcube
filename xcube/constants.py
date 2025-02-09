# The MIT License (MIT)
# Copyright (c) 2019 by the xcube development team and contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import logging
import math

PLUGIN_ENTRY_POINT_GROUP_NAME = 'xcube_plugins'
PLUGIN_MODULE_PREFIX = 'xcube_'
PLUGIN_MODULE_NAME = 'plugin'
PLUGIN_MODULE_FUNCTION_NAME = 'init_plugin'
PLUGIN_LOAD_TIME_WARN_LIMIT = 100  # milliseconds
PLUGIN_INIT_TIME__WARN_LIMIT = 100  # milliseconds

#: The extension point identifier for input processor extensions
EXTENSION_POINT_INPUT_PROCESSORS = 'xcube.core.gen.iproc'
#: The extension point identifier for dataset I/O extensions
EXTENSION_POINT_DATASET_IOS = 'xcube.core.dsio'
#: The extension point identifier for CLI command extensions
EXTENSION_POINT_CLI_COMMANDS = 'xcube.cli'
#: The extension point identifier for data stores
EXTENSION_POINT_DATA_STORES = 'xcube.core.store'
#: The extension point identifier for data openers
EXTENSION_POINT_DATA_OPENERS = 'xcube.core.store.opener'
#: The extension point identifier for data writers
EXTENSION_POINT_DATA_WRITERS = 'xcube.core.store.writer'
#: The extension point identifier for server APIs
EXTENSION_POINT_SERVER_APIS = 'xcube.server.api'
#: The extension point identifier for server frameworks such as Tornado
EXTENSION_POINT_SERVER_FRAMEWORKS = 'xcube.server.framework'

GLOBAL_GEO_EXTENT = -180., -90., 180., 90.

WGS84_ELLIPSOID_SEMI_MAJOR_AXIS = 6378137.
EARTH_EQUATORIAL_PERIMETER = 2. * math.pi * WGS84_ELLIPSOID_SEMI_MAJOR_AXIS

CRS_WKT_EPSG_4326 = """
GEOGCS["WGS 84",
    DATUM["WGS_1984",
        SPHEROID["WGS 84",6378137,298.257223563,
            AUTHORITY["EPSG","7030"]],
        AUTHORITY["EPSG","6326"]],
    PRIMEM["Greenwich",0,
        AUTHORITY["EPSG","8901"]],
    UNIT["degree",0.01745329251994328,
        AUTHORITY["EPSG","9122"]],
    AUTHORITY["EPSG","4326"]]
"""

FORMAT_NAME_ZARR = "zarr"
FORMAT_NAME_NETCDF4 = "netcdf4"
FORMAT_NAME_CSV = "csv"
FORMAT_NAME_MEM = "mem"
FORMAT_NAME_LEVELS = "levels"
FORMAT_NAME_SCRIPT = "script"
FORMAT_NAME_EXCEL = "excel"

# Note: this list must be kept in-sync with xcube.core.reproject:NAME_TO_GDAL_RESAMPLE_ALG
RESAMPLING_METHOD_NAMES = {
    # Up-sampling
    'Nearest',
    'Bilinear',
    'Cubic',
    'CubicSpline',
    'Lanczos',

    # Down-sampling
    'Average',
    'Min',
    'Max',
    'Median',
    'Mode',
    'Q1',
    'Q3',
}

LOG_LEVEL_OFF_NAME = 'OFF'
LOG_LEVEL_OFF = logging.CRITICAL + 5

LOG_LEVEL_DETAIL_NAME = 'DETAIL'
LOG_LEVEL_DETAIL = logging.DEBUG + 5

LOG_LEVEL_TRACE_NAME = 'TRACE'
LOG_LEVEL_TRACE = logging.DEBUG - 5

logging.addLevelName(LOG_LEVEL_DETAIL, LOG_LEVEL_DETAIL_NAME)
logging.addLevelName(LOG_LEVEL_TRACE, LOG_LEVEL_TRACE_NAME)

LOG_LEVELS = [
    LOG_LEVEL_OFF_NAME,
    'CRITICAL',
    'ERROR',
    'WARNING',
    'INFO',
    LOG_LEVEL_DETAIL_NAME,
    'DEBUG',
    LOG_LEVEL_TRACE_NAME
]

DEFAULT_GENERAL_LOG_LEVEL = LOG_LEVEL_OFF
DEFAULT_XCUBE_LOG_LEVEL = logging.WARNING

GENERAL_LOG_FORMAT = '[%(levelname).1s %(asctime)s %(name)s] %(message)s'
XCUBE_LOG_FORMAT = '%(levelname)s: %(message)s'

# xcube logger
LOG = logging.getLogger('xcube')
LOG.setLevel(DEFAULT_XCUBE_LOG_LEVEL)

DEFAULT_SERVER_FRAMEWORK = "tornado"
DEFAULT_SERVER_PORT = 8080
DEFAULT_SERVER_ADDRESS = "0.0.0.0"

DEFAULT_COLOR_MAP_NAME = "viridis"
DEFAULT_COLOR_MAP_VALUE_RANGE = (0., 1.)
