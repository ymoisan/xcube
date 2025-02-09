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

import functools
import io
import json
from typing import Dict, Tuple, List, Set, Optional, Any, Callable, Mapping

import matplotlib.colorbar
import matplotlib.colors
import matplotlib.figure
import numpy as np
import pyproj
import xarray as xr

from xcube.constants import LOG
from xcube.core.geom import get_dataset_bounds
from xcube.core.geom import get_dataset_geometry
from xcube.core.normalize import DatasetIsNotACubeError
from xcube.core.store import DataStoreError
from xcube.core.tilingscheme import TilingScheme
from xcube.core.timecoord import timestamp_to_iso_string
from xcube.server.api import ApiError
from .authutil import READ_ALL_DATASETS_SCOPE
from .authutil import READ_ALL_VARIABLES_SCOPE
from .authutil import assert_scopes
from .authutil import check_scopes
from .context import DatasetConfig
from .context import DatasetsContext
from ..places.controllers import GeoJsonFeatureCollection
from ..places.controllers import find_places

_CRS84 = pyproj.CRS.from_string('CRS84')


# TODO (forman): Is this really still in use?
class CubeIsNotDisplayable(ValueError):
    pass


def find_dataset_places(ctx: DatasetsContext,
                        place_group_id: str,
                        ds_id: str,
                        base_url: str,
                        query_expr: Any = None,
                        comb_op: str = "and") -> GeoJsonFeatureCollection:
    dataset = ctx.get_dataset(ds_id)
    query_geometry = get_dataset_geometry(dataset)
    return find_places(ctx.places_ctx,
                       place_group_id,
                       base_url,
                       query_geometry=query_geometry,
                       query_expr=query_expr,
                       comb_op=comb_op)


def get_datasets(ctx: DatasetsContext,
                 details: bool = False,
                 point: Optional[Tuple[float, float]] = None,
                 base_url: Optional[str] = None,
                 granted_scopes: Optional[Set[str]] = None) -> Dict:
    can_authenticate = ctx.can_authenticate
    # If True, we can shorten scope checking
    if granted_scopes is None:
        can_read_all_datasets = False
    else:
        can_read_all_datasets = READ_ALL_DATASETS_SCOPE in granted_scopes

    LOG.info(f'Collecting datasets for granted scopes {granted_scopes!r}')

    dataset_configs = list(ctx.get_dataset_configs())

    dataset_dicts = list()
    for dataset_config in dataset_configs:

        ds_id = dataset_config['Identifier']

        if dataset_config.get('Hidden'):
            continue

        if can_authenticate \
                and not can_read_all_datasets \
                and not _allow_dataset(ctx,
                                       dataset_config,
                                       granted_scopes,
                                       check_scopes):
            LOG.info(
                f'Rejected dataset {ds_id!r} due to missing permission'
            )
            continue

        dataset_dict = dict(id=ds_id)

        dataset_dict['title'] = ds_id
        if 'Title' in dataset_config:
            ds_title = dataset_config['Title']
            if ds_title and isinstance(ds_title, str):
                dataset_dict['title'] = ds_title

        if 'BoundingBox' in dataset_config:
            ds_bbox = dataset_config['BoundingBox']
            if ds_bbox \
                    and len(ds_bbox) == 4 \
                    and all(map(lambda c: isinstance(c, (float, int)),
                                ds_bbox)):
                dataset_dict['bbox'] = ds_bbox

        LOG.info(f'Collected dataset {ds_id}')
        dataset_dicts.append(dataset_dict)

    # Important note:
    # the "point" parameter is used by
    # the CyanoAlert app only

    if details or point:
        filtered_dataset_dicts = []
        for dataset_dict in dataset_dicts:
            ds_id = dataset_dict["id"]
            try:
                if point:
                    ds = ctx.get_dataset(ds_id)
                    if "bbox" not in dataset_dict:
                        dataset_dict["bbox"] = list(get_dataset_bounds(ds))
                if details:
                    LOG.info(f'Loading details for dataset {ds_id}')
                    dataset_dict.update(
                        get_dataset(ctx, ds_id,
                                    base_url,
                                    granted_scopes=granted_scopes)
                    )
                filtered_dataset_dicts.append(dataset_dict)
            except (DatasetIsNotACubeError, CubeIsNotDisplayable) as e:
                LOG.warning(f'Skipping dataset {ds_id}: {e}')
        dataset_dicts = filtered_dataset_dicts

    if point:
        is_point_in_dataset_bbox = functools.partial(
            _is_point_in_dataset_bbox, point
        )
        # noinspection PyTypeChecker
        dataset_dicts = list(filter(is_point_in_dataset_bbox, dataset_dicts))

    if not dataset_dicts:
        LOG.warn(f'No datasets provided for current user.')

    return dict(datasets=dataset_dicts)


def get_dataset(ctx: DatasetsContext,
                ds_id: str,
                base_url: Optional[str] = None,
                granted_scopes: Optional[Set[str]] = None) -> Dict:
    can_authenticate = ctx.can_authenticate
    # If True, we can shorten scope checking
    if granted_scopes is None:
        can_read_all_datasets = False
        can_read_all_variables = False
    else:
        can_read_all_datasets = READ_ALL_DATASETS_SCOPE in granted_scopes
        can_read_all_variables = READ_ALL_VARIABLES_SCOPE in granted_scopes

    dataset_config = ctx.get_dataset_config(ds_id)
    ds_id = dataset_config['Identifier']

    if can_authenticate and not can_read_all_datasets:
        _allow_dataset(ctx, dataset_config, granted_scopes, assert_scopes)

    try:
        ml_ds = ctx.get_ml_dataset(ds_id)
    except (ValueError, DataStoreError) as e:
        raise DatasetIsNotACubeError(f'could not open dataset: {e}') from e

    ds = ml_ds.get_dataset(0)

    try:
        ts_ds = ctx.get_time_series_dataset(ds_id)
    except (ValueError, DataStoreError):
        ts_ds = None

    x_name, y_name = ml_ds.grid_mapping.xy_dim_names

    ds_title = dataset_config.get('Title',
                                  ds.attrs.get('title',
                                               ds.attrs.get('name',
                                                            ds_id)))
    dataset_dict = dict(id=ds_id, title=ds_title)

    crs = ml_ds.grid_mapping.crs
    transformer = pyproj.Transformer.from_crs(crs, _CRS84, always_xy=True)
    dataset_bounds = get_dataset_bounds(ds)

    if "bbox" not in dataset_dict:
        if not crs.is_geographic:
            x1, y1, x2, y2 = dataset_bounds
            (x1, x2), (y1, y2) = transformer.transform((x1, x2), (y1, y2))
            dataset_dict["bbox"] = [x1, y1, x2, y2]
        else:
            dataset_dict["bbox"] = list(dataset_bounds)

    dataset_dict['geometry'] = get_bbox_geometry(dataset_bounds,
                                                 transformer)
    dataset_dict['spatialRef'] = crs.to_string()

    variable_dicts = []
    dim_names = set()

    tiling_scheme = ml_ds.derive_tiling_scheme(TilingScheme.GEOGRAPHIC)
    LOG.debug('Tile level range for dataset %s: %d to %d',
              ds_id,
              tiling_scheme.min_level,
              tiling_scheme.max_level)

    for var_name, var in ds.data_vars.items():
        var_name = str(var_name)
        dims = var.dims
        if len(dims) < 2 \
                or dims[-2] != y_name \
                or dims[-1] != x_name:
            continue

        if can_authenticate \
                and not can_read_all_variables \
                and not _allow_variable(ctx,
                                        dataset_config,
                                        var_name,
                                        granted_scopes):
            continue

        variable_dict = dict(
            id=f'{ds_id}.{var_name}',
            name=var_name,
            dims=list(dims),
            shape=list(var.shape),
            dtype=str(var.dtype),
            units=var.attrs.get('units', ''),
            title=var.attrs.get('title',
                                var.attrs.get('long_name',
                                              var_name)),
            timeChunkSize=get_time_chunk_size(ts_ds, var_name, ds_id)
        )

        tile_url = _get_dataset_tile_url2(ctx, ds_id, var_name, base_url)
        variable_dict["tileUrl"] = tile_url
        LOG.debug('Tile URL for variable %s: %s',
                  var_name, tile_url)

        variable_dict["tileLevelMin"] = tiling_scheme.min_level
        variable_dict["tileLevelMax"] = tiling_scheme.max_level

        cmap_name, (cmap_vmin, cmap_vmax) = ctx.get_color_mapping(ds_id,
                                                                  var_name)
        variable_dict["colorBarName"] = cmap_name
        variable_dict["colorBarMin"] = cmap_vmin
        variable_dict["colorBarMax"] = cmap_vmax

        if hasattr(var.data, '_repr_html_'):
            # noinspection PyProtectedMember
            variable_dict["htmlRepr"] = var.data._repr_html_()

        variable_dict["attrs"] = {
            key: ("NaN"
                  if isinstance(value, float) and np.isnan(value)
                  else value)
            for key, value in var.attrs.items()
        }

        variable_dicts.append(variable_dict)
        for dim_name in var.dims:
            dim_names.add(dim_name)

    dataset_dict["variables"] = variable_dicts

    if not variable_dicts:
        if not ds.data_vars:
            message = f'Dataset {ds_id!r} has no variables'
        else:
            message = f'Dataset {ds_id!r} has no published variables'
        raise DatasetIsNotACubeError(message)

    rgb_var_names, rgb_norm_ranges = ctx.get_rgb_color_mapping(ds_id)
    if any(rgb_var_names):
        rgb_tile_url = _get_dataset_tile_url2(ctx, ds_id, 'rgb', base_url)
        rgb_schema = {
            'varNames': rgb_var_names,
            'normRanges': rgb_norm_ranges,
            'tileUrl': rgb_tile_url,
            'tileLevelMin': tiling_scheme.min_level,
            'tileLevelMax': tiling_scheme.max_level,
        }

        dataset_dict["rgbSchema"] = rgb_schema

    dataset_dict["dimensions"] = [
        get_dataset_coordinates(ctx, ds_id, str(dim_name))
        for dim_name in dim_names
    ]

    dataset_dict["attrs"] = {
        key: ds.attrs[key]
        for key in sorted(list(map(str, ds.attrs.keys())))
    }

    dataset_attributions = dataset_config.get(
        'Attribution',
        ctx.config.get('DatasetAttribution')
    )
    if dataset_attributions is not None:
        if isinstance(dataset_attributions, str):
            dataset_attributions = [dataset_attributions]
        dataset_dict['attributions'] = dataset_attributions

    place_groups = ctx.get_dataset_place_groups(ds_id, base_url)
    if place_groups:
        dataset_dict["placeGroups"] = _filter_place_groups(place_groups,
                                                           del_features=True)

    return dataset_dict


def get_bbox_geometry(dataset_bounds: Tuple[float, float, float, float],
                      transformer: pyproj.Transformer,
                      n: int = 6):
    x1, y1, x2, y2 = dataset_bounds
    x_coords = []
    y_coords = []
    x_coords.extend(np.full(n - 1, x1))
    y_coords.extend(np.linspace(y1, y2, n)[: n - 1])
    x_coords.extend(np.linspace(x1, x2, n)[: n - 1])
    y_coords.extend(np.full(n - 1, y2))
    x_coords.extend(np.full(n - 1, x2))
    y_coords.extend(np.linspace(y2, y1, n)[: n - 1])
    x_coords.extend(np.linspace(x2, x1, n)[: n - 1])
    y_coords.extend(np.full(n - 1, y1))
    x_coords, y_coords = transformer.transform(x_coords, y_coords)
    coordinates = list(map(list, zip(map(float, x_coords),
                                     map(float, y_coords))))
    coordinates.append(coordinates[0])
    geometry = dict(type="Polygon", coordinates=[coordinates])
    return geometry


def get_time_chunk_size(ts_ds: Optional[xr.Dataset],
                        var_name: str,
                        ds_id: str) -> Optional[int]:
    """
    Get the time chunk size for variable *var_name*
    in time-chunked dataset *ts_ds*.

    Internal function.

    :param ts_ds: time-chunked dataset
    :param var_name: variable name
    :param ds_id: original dataset identifier
    :return: the time chunk size (integer) or None
    """
    if ts_ds is not None:
        ts_var: Optional[xr.DataArray] = ts_ds.get(var_name)
        if ts_var is not None:
            chunks = ts_var.chunks
            if chunks is None:
                LOG.warning(f'variable {var_name!r}'
                            f' in time-chunked dataset {ds_id!r}'
                            f' is not chunked')
                return None
            try:
                time_index = ts_var.dims.index('time')
                time_chunks = chunks[time_index]
            except ValueError:
                time_chunks = None
            if not time_chunks:
                LOG.warning(f'no chunks found'
                            f' for dimension \'time\''
                            f' of variable {var_name!r}'
                            f' in time-chunked dataset {ds_id!r}')
                return None
            if len(time_chunks) == 1:
                return time_chunks[0]
            return max(*time_chunks)
        else:
            LOG.warning(f'variable {var_name!r} not'
                        f' found in time-chunked dataset {ds_id!r}')
    return None


def _allow_dataset(
        ctx: DatasetsContext,
        dataset_config: DatasetConfig,
        granted_scopes: Optional[Set[str]],
        function: Callable[[Set, Optional[Set], bool], Any]
) -> Any:
    required_scopes = ctx.get_required_dataset_scopes(
        dataset_config
    )
    # noinspection PyArgumentList
    return function(required_scopes,
                    granted_scopes,
                    is_substitute=_is_substitute(dataset_config))


def _allow_variable(
        ctx: DatasetsContext,
        dataset_config: DatasetConfig,
        var_name: str,
        granted_scopes: Optional[Set[str]]
) -> bool:
    required_scopes = ctx.get_required_variable_scopes(
        dataset_config, var_name
    )
    # noinspection PyArgumentList
    return check_scopes(required_scopes,
                        granted_scopes,
                        is_substitute=_is_substitute(dataset_config))


def _is_substitute(dataset_config: DatasetConfig) -> bool:
    return dataset_config \
        .get('AccessControl', {}) \
        .get('IsSubstitute', False)


def get_dataset_place_groups(ctx: DatasetsContext,
                             ds_id: str,
                             base_url: str) -> List[GeoJsonFeatureCollection]:
    # Do not load or return features, just place group (metadata).
    place_groups = ctx.get_dataset_place_groups(ds_id, base_url,
                                                load_features=False)
    return _filter_place_groups(place_groups, del_features=True)


def get_dataset_place_group(ctx: DatasetsContext,
                            ds_id: str,
                            place_group_id: str,
                            base_url: str) -> GeoJsonFeatureCollection:
    # Load and return features for specific place group.
    place_group = ctx.get_dataset_place_group(ds_id, place_group_id, base_url,
                                              load_features=True)
    return _filter_place_group(place_group, del_features=False)


def get_dataset_coordinates(ctx: DatasetsContext,
                            ds_id: str,
                            dim_name: str) -> Dict:
    ds, var = ctx.get_dataset_and_coord_variable(ds_id, dim_name)
    values = list()
    if np.issubdtype(var.dtype, np.floating):
        converter = float
    elif np.issubdtype(var.dtype, np.integer):
        converter = int
    else:
        converter = timestamp_to_iso_string
    for value in var.values:
        values.append(converter(value))
    return dict(name=dim_name,
                size=len(values),
                dtype=str(var.dtype),
                coordinates=values)


# noinspection PyUnusedLocal
def get_color_bars(ctx: DatasetsContext,
                   mime_type: str) -> str:
    cmaps = ctx.colormap_registry.to_json()
    if mime_type == 'application/json':
        return json.dumps(cmaps, indent=2)
    elif mime_type == 'text/html':
        html_head = '<!DOCTYPE html>\n' + \
                    '<html lang="en">\n' + \
                    '<head>' + \
                    '<meta charset="UTF-8">' + \
                    '<title>xcube Server Colormaps</title>' + \
                    '</head>\n' + \
                    '<body style="padding: 0.2em; font-family: sans-serif">\n'
        html_body = ''
        html_foot = '</body>\n' \
                    '</html>\n'
        for cmap_cat, cmap_desc, cmap_bars in cmaps:
            html_body += '    <h2>%s</h2>\n' % cmap_cat
            html_body += '    <p>%s</p>\n' % cmap_desc
            html_body += '    <table style=border: 0">\n'
            for cmap_bar in cmap_bars:
                cmap_name, cmap_data = cmap_bar
                cmap_image = f'<img src="data:image/png;base64,{cmap_data}"' \
                             f' width="100%%"' \
                             f' height="24"/>'
                name_cell = f'<td style="width: 5em">' \
                            f'<code>{cmap_name}</code>' \
                            f'</td>'
                image_cell = f'<td style="width: 40em">{cmap_image}</td>'
                row = f'<tr>{name_cell}{image_cell}</tr>\n'
                html_body += f'        {row}\n'
            html_body += '    </table>\n'
        return html_head + html_body + html_foot
    raise ApiError.BadRequest(
        f'Format {mime_type!r} not supported for colormaps'
    )


def _is_point_in_dataset_bbox(point: Tuple[float, float], dataset_dict: Dict):
    if 'bbox' not in dataset_dict:
        return False
    x, y = point
    x_min, y_min, x_max, y_max = dataset_dict['bbox']
    if not (y_min <= y <= y_max):
        return False
    if x_min < x_max:
        return x_min <= x <= x_max
    else:
        # Bounding box crosses anti-meridian
        return x_min <= x <= 180.0 or -180.0 <= x <= x_max


def _filter_place_group(place_group: Dict,
                        del_features: bool = False) -> Dict:
    place_group = dict(place_group)
    del place_group['sourcePaths']
    del place_group['sourceEncoding']
    if del_features:
        del place_group['features']
    return place_group


def _filter_place_groups(place_groups, del_features: bool = False) \
        -> List[Dict]:
    if del_features:
        def __filter_place_group(place_group):
            return _filter_place_group(place_group, del_features=True)
    else:
        def __filter_place_group(place_group):
            return _filter_place_group(place_group, del_features=False)

    return list(map(__filter_place_group, place_groups))


def _get_dataset_tile_url2(ctx: DatasetsContext,
                           ds_id: str,
                           var_name: str,
                           base_url: str):
    import urllib.parse
    return ctx.get_service_url(base_url,
                               'datasets',
                               urllib.parse.quote_plus(ds_id),
                               'vars',
                               urllib.parse.quote_plus(var_name),
                               'tiles2',
                               '{z}/{y}/{x}')


def get_legend(ctx: DatasetsContext,
               ds_id: str,
               var_name: str,
               params: Mapping[str, str]):
    default_cmap_cbar, (default_cmap_vmin, default_cmap_vmax) = \
        ctx.get_color_mapping(ds_id, var_name)

    try:
        cmap_name = params.get('cbar', params.get('cmap', default_cmap_cbar))
        cmap_vmin = float(params.get('vmin', str(default_cmap_vmin)))
        cmap_vmax = float(params.get('vmax', str(default_cmap_vmax)))
        cmap_w = int(params.get('width', '256'))
        cmap_h = int(params.get('height', '16'))
    except (ValueError, TypeError):
        raise ApiError.BadRequest("Invalid color legend parameter(s)")

    cmap_name, cmap = ctx.colormap_registry.get_cmap(cmap_name)
    colormap = ctx.colormap_registry.colormaps.get(cmap_name)
    assert colormap is not None

    norm = colormap.norm
    if norm is None:
        norm = matplotlib.colors.Normalize(vmin=cmap_vmin, vmax=cmap_vmax)

    try:
        _, cmap = ctx.colormap_registry.get_cmap(cmap_name)
    except ValueError:
        raise ApiError.NotFound(f"Colormap {cmap_name!r} not found")

    fig = matplotlib.figure.Figure(figsize=(cmap_w, cmap_h))
    ax1 = fig.add_subplot(1, 1, 1)
    image_legend = matplotlib.colorbar.ColorbarBase(ax1,
                                                    format='%.1f',
                                                    ticks=None,
                                                    cmap=cmap,
                                                    norm=norm,
                                                    orientation='vertical')

    image_legend_label = ctx.get_legend_label(ds_id, var_name)
    if image_legend_label is not None:
        image_legend.set_label(image_legend_label)

    fig.patch.set_facecolor('white')
    fig.patch.set_alpha(0.0)
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format='png')

    return buffer.getvalue()
