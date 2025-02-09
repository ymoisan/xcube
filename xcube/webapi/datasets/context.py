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


import fnmatch
import itertools
import os
import os.path
import warnings
from functools import cached_property
from typing import Any, Dict, List, Optional, Tuple, Callable, Collection, \
    Set, Mapping

import numpy as np
import pandas as pd
import pyproj
import xarray as xr

from xcube.constants import LOG
from xcube.core.mldataset import BaseMultiLevelDataset
from xcube.core.mldataset import MultiLevelDataset
from xcube.core.mldataset import augment_ml_dataset
from xcube.core.mldataset import open_ml_dataset_from_python_code
from xcube.core.store import DATASET_TYPE
from xcube.core.store import DataStoreConfig
from xcube.core.store import DataStorePool
from xcube.core.store import DatasetDescriptor
from xcube.core.store import MULTI_LEVEL_DATASET_TYPE
from xcube.core.tile import get_var_cmap_params
from xcube.core.tile import get_var_valid_range
from xcube.server.api import Context, ApiError
from xcube.server.api import ServerConfig
from xcube.util.cache import parse_mem_size
from xcube.util.cmaps import ColormapRegistry
from xcube.util.cmaps import load_custom_colormap
from xcube.webapi.common.context import ResourcesContext
from xcube.webapi.places import PlacesContext

COMPUTE_DATASET = 'compute_dataset'
COMPUTE_VARIABLES = 'compute_variables'

# We use tilde, because it is not a reserved URI characters
STORE_DS_ID_SEPARATOR = '~'
FS_TYPE_TO_PROTOCOL = {
    'local': 'file',
    'obs': 's3',
    'file': 'file',
    's3': 's3',
    'memory': 'memory'
}
NON_MEMORY_FILE_SYSTEMS = ['local', 'obs', 'file', 's3']

ALL_PLACES = "all"

MultiLevelDatasetOpener = Callable[
    ["DatasetsContext", ServerConfig],
    MultiLevelDataset
]

DatasetConfig = Mapping[str, Any]


class DatasetsContext(ResourcesContext):

    def __init__(self,
                 server_ctx: Context,
                 ml_dataset_openers=None):
        super().__init__(server_ctx)
        # noinspection PyTypeChecker
        self._places_ctx: PlacesContext = server_ctx.get_api_ctx("places")
        assert isinstance(self._places_ctx, PlacesContext)
        self._ml_dataset_openers = ml_dataset_openers
        # cache for all dataset configs
        # contains tuples of form (MultiLevelDataset, dataset_config)
        self._dataset_cache = dict()
        self._data_store_pool, self._dataset_configs = \
            self._process_dataset_configs(self.config, self.base_dir)
        self._cm_styles, self._colormap_registry = self._get_cm_styles()

    def on_dispose(self):
        with self.rlock:
            # Close all datasets
            for ml_dataset, _ in self._dataset_cache.values():
                # noinspection PyBroadException
                try:
                    ml_dataset.close()
                except Exception:
                    pass
            # Clear all caches
            if self._dataset_cache:
                self._dataset_cache.clear()
            if self._data_store_pool:
                self._data_store_pool.remove_all_store_configs()
            self._dataset_configs = None

    @property
    def places_ctx(self):
        return self._places_ctx

    @property
    def dataset_cache(self) \
            -> Dict[str, Tuple[MultiLevelDataset, DatasetConfig]]:
        return self._dataset_cache

    @cached_property
    def access_control(self) -> Dict[str, Any]:
        return self.config.get('AccessControl', {})

    @cached_property
    def required_scopes(self) -> List[str]:
        return self.access_control.get('RequiredScopes', [])

    @property
    def colormap_registry(self) -> ColormapRegistry:
        return self._colormap_registry

    def get_required_dataset_scopes(
            self,
            dataset_config: DatasetConfig
    ) -> Set[str]:
        return self._get_required_scopes(dataset_config,
                                         'read:dataset', 'Dataset',
                                         dataset_config['Identifier'])

    def get_required_variable_scopes(
            self,
            dataset_config: DatasetConfig,
            var_name: str
    ) -> Set[str]:
        return self._get_required_scopes(dataset_config,
                                         'read:variable', 'Variable',
                                         var_name)

    def _get_required_scopes(self,
                             dataset_config: DatasetConfig,
                             base_scope: str,
                             value_name: str,
                             value: str) -> Set[str]:
        required_global_scopes = set(self.required_scopes)
        required_dataset_scopes = set(
            dataset_config.get('AccessControl', {}).get('RequiredScopes', [])
        )
        if not required_global_scopes and not required_dataset_scopes:
            return required_global_scopes
        required_dataset_scopes = required_global_scopes \
            .union(required_dataset_scopes)
        base_scope_prefix = base_scope + ':'
        required_dataset_scopes = {
            scope
            for scope in required_dataset_scopes
            if scope == base_scope or scope.startswith(base_scope_prefix)
        }
        pattern_scope = base_scope_prefix + '{' + value_name + '}'
        if pattern_scope in required_dataset_scopes:
            # Replace "{base_scope}:{value_name}" by "{base_scope}:{value}"
            required_dataset_scopes.remove(pattern_scope)
            required_dataset_scopes.add(base_scope_prefix + value)
        return required_dataset_scopes

    def get_ml_dataset(self, ds_id: str) -> MultiLevelDataset:
        ml_dataset, _ = self._get_dataset_entry(ds_id)
        return ml_dataset

    def set_ml_dataset(self, ml_dataset: MultiLevelDataset):
        self._set_dataset_entry((ml_dataset,
                                 dict(Identifier=ml_dataset.ds_id,
                                      Hidden=True)))

    def get_dataset(self,
                    ds_id: str,
                    expected_var_names: Collection[str] = None) -> xr.Dataset:
        ml_dataset, _ = self._get_dataset_entry(ds_id)
        dataset = ml_dataset.base_dataset
        if expected_var_names:
            for var_name in expected_var_names:
                if var_name not in dataset:
                    raise ApiError.NotFound(
                        f'Variable "{var_name}" not found'
                        f' in dataset "{ds_id}"'
                    )
        return dataset

    def get_time_series_dataset(self,
                                ds_id: str,
                                var_name: str = None) -> xr.Dataset:
        dataset_config = self.get_dataset_config(ds_id)
        ts_ds_name = dataset_config.get('TimeSeriesDataset', ds_id)
        try:
            # Try to get more efficient, time-chunked dataset
            return self.get_dataset(ts_ds_name,
                                    expected_var_names=[var_name]
                                    if var_name else None)
        except ApiError.NotFound:
            # This happens, if the dataset pointed to by 'TimeSeriesDataset'
            # does not contain the variable given by var_name.
            return self.get_dataset(ds_id,
                                    expected_var_names=[var_name]
                                    if var_name else None)

    def get_variable_for_z(self,
                           ds_id: str,
                           var_name: str,
                           z_index: int) -> xr.DataArray:
        ml_dataset = self.get_ml_dataset(ds_id)
        index = ml_dataset.num_levels - 1 - z_index
        if index < 0 or index >= ml_dataset.num_levels:
            raise ApiError.NotFound(
                f'Variable "{var_name}" has no z-index {z_index}'
                f' in dataset "{ds_id}"'
            )
        dataset = ml_dataset.get_dataset(index)
        if var_name not in dataset:
            raise ApiError.NotFound(
                f'Variable "{var_name}" not found'
                f' in dataset "{ds_id}"'
            )
        return dataset[var_name]

    @classmethod
    def _maybe_assign_store_instance_ids(
            cls,
            dataset_configs: List[Dict[str, Any]],
            data_store_pool: DataStorePool,
            base_dir: str
    ) -> None:
        assignable_dataset_configs = [
            dc for dc in dataset_configs
            if 'StoreInstanceId' not in dc
               and dc.get('FileSystem', 'file') in NON_MEMORY_FILE_SYSTEMS
        ]
        # split into sub-lists according to file system and
        # non-root store params
        config_lists = []
        for config in assignable_dataset_configs:
            store_params = cls._get_other_store_params_than_root(config)
            file_system = config.get('FileSystem', 'file')
            appended = False
            for config_list in config_lists:
                if config_list[0] == file_system and \
                        config_list[1] == store_params:
                    config_list[2].append(config)
                    appended = True
                    break
            if not appended:
                config_lists.append((file_system, store_params, [config]))

        for file_system, store_params, config_list in config_lists:
            # Retrieve paths per configuration
            paths = [dc['Path'] for dc in config_list]
            list.sort(paths)
            # Determine common prefixes of paths (and call them roots)
            prefixes = _get_common_prefixes(paths)
            if len(prefixes) < 1:
                roots = ['']
            else:
                # perform further step to merge prefixes with same start
                prefixes = list(set(prefixes))
                prefixes.sort()
                roots = []
                root_candidate = prefixes[0]
                for root in prefixes[1:]:
                    common_root = os.path.commonprefix([root_candidate, root])
                    if _is_not_empty(common_root):
                        root_candidate = common_root
                    else:
                        roots.append(root_candidate)
                        root_candidate = root
                roots.append(root_candidate)
            for root in roots:
                # ensure root does not end with full or partial directory
                # or file name
                while not root.endswith("/") \
                        and not root.endswith("\\") \
                        and len(root) > 0:
                    root = root[:-1]
                if root.endswith("/") or root.endswith("\\"):
                    root = root[:-1]
                abs_root = root
                # For local file systems:
                # Determine absolute root from base dir
                fs_protocol = FS_TYPE_TO_PROTOCOL.get(file_system,
                                                      file_system)
                if fs_protocol == 'file' and not os.path.isabs(abs_root):
                    abs_root = os.path.join(base_dir, abs_root)
                    abs_root = os.path.normpath(abs_root)
                store_params_for_root = store_params.copy()
                store_params_for_root['root'] = abs_root
                # See if there already is a store with this configuration
                data_store_config = DataStoreConfig(
                    store_id=fs_protocol,
                    store_params=store_params_for_root
                )
                store_instance_id = data_store_pool. \
                    get_store_instance_id(data_store_config)
                if not store_instance_id:
                    # Create new store with new unique store instance id
                    counter = 1
                    while data_store_pool.has_store_instance(
                            f'{fs_protocol}_{counter}'):
                        counter += 1
                    store_instance_id = f'{fs_protocol}_{counter}'
                    data_store_pool.add_store_config(store_instance_id,
                                                     data_store_config)
                for config in config_list:
                    if config['Path'].startswith(root):
                        config['StoreInstanceId'] = store_instance_id
                        new_path = config['Path'][len(root):]
                        while new_path.startswith("/") or \
                                new_path.startswith("\\"):
                            new_path = new_path[1:]
                        config['Path'] = new_path

    @classmethod
    def _get_other_store_params_than_root(
            cls, dataset_config: DatasetConfig
    ) -> Dict:
        file_system = FS_TYPE_TO_PROTOCOL.get(
            dataset_config.get('FileSystem', 'file')
        )
        if file_system != 's3':
            return {}
        storage_options = dict()
        if 'Anonymous' in dataset_config:
            storage_options['anon'] = dataset_config['Anonymous']
        client_kwargs = dict(
        )
        if 'Endpoint' in dataset_config:
            client_kwargs['endpoint_url'] = dataset_config['Endpoint']
        if 'Region' in dataset_config:
            client_kwargs['region_name'] = dataset_config['Region']
        storage_options['client_kwargs'] = client_kwargs
        store_params = dict(storage_options=storage_options)
        return store_params

    @classmethod
    def get_dataset_configs_from_stores(
            cls,
            data_store_pool: DataStorePool
    ) -> List[DatasetConfig]:
        all_dataset_configs: List[DatasetConfig] = []
        for store_instance_id in data_store_pool.store_instance_ids:
            LOG.info(f'Scanning store {store_instance_id!r}')
            data_store_config = data_store_pool.get_store_config(
                store_instance_id
            )
            data_store = data_store_pool.get_store(store_instance_id)
            # Note by forman: This iterator chaining is inefficient.
            # Preferably, we should offer
            #
            # store_dataset_ids = data_store.get_data_ids(
            #     data_type=(DATASET_TYPE, MULTI_LEVEL_DATASET_TYPE)
            # )
            #
            store_dataset_ids = itertools.chain(
                data_store.get_data_ids(
                    data_type=DATASET_TYPE
                ),
                data_store.get_data_ids(
                    data_type=MULTI_LEVEL_DATASET_TYPE
                )
            )
            for store_dataset_id in store_dataset_ids:
                dataset_config_base = {}
                store_dataset_configs: List[ServerConfig] \
                    = data_store_config.user_data
                if store_dataset_configs:
                    for store_dataset_config in store_dataset_configs:
                        dataset_id_pattern = store_dataset_config.get(
                            'Path', '*'
                        )
                        if fnmatch.fnmatch(store_dataset_id,
                                           dataset_id_pattern):
                            dataset_config_base = store_dataset_config
                            break
                        else:
                            dataset_config_base = None
                if dataset_config_base is not None:
                    LOG.debug(f'Selected dataset {store_dataset_id!r}')
                    dataset_config = dict(
                        StoreInstanceId=store_instance_id,
                        **dataset_config_base
                    )
                    if dataset_config.get('Identifier') is not None:
                        if dataset_config['Path'] == store_dataset_id:
                            # we will use the preconfigured identifier
                            all_dataset_configs.append(dataset_config)
                            continue
                        raise ApiError.InvalidServerConfig(
                            'User-defined identifiers can only be assigned'
                            ' to datasets with non-wildcard paths.'
                        )
                    dataset_config['Path'] = store_dataset_id
                    dataset_config['Identifier'] = \
                        f'{store_instance_id}{STORE_DS_ID_SEPARATOR}' \
                        f'{store_dataset_id}'
                    all_dataset_configs.append(dataset_config)

        # # Just for testing:
        # debug_file = 'all_dataset_configs.json'
        # with open(debug_file, 'w') as stream:
        #     json.dump(all_dataset_configs, stream)
        #     LOG.debug(f'Wrote file {debug_file!r}')

        return all_dataset_configs

    def new_dataset_metadata(self,
                             store_instance_id: str,
                             dataset_id: str) -> Optional[DatasetDescriptor]:
        data_store = self._data_store_pool.get_store(store_instance_id)
        dataset_metadata = data_store.describe_data(
            dataset_id,
            data_type='dataset'
        )
        if dataset_metadata.crs is not None:
            crs = pyproj.CRS.from_string(dataset_metadata.crs)
            if not crs.is_geographic:
                LOG.warning(f'Ignoring dataset {dataset_id!r} from'
                            f' store instance {store_instance_id!r}'
                            f' because it uses a non-geographic CRS')
                return None
        # noinspection PyTypeChecker
        return dataset_metadata

    def get_dataset_config(self, ds_id: str) -> DatasetConfig:
        dataset_configs = self.get_dataset_configs()
        dataset_config = next(
            (dsd for dsd in dataset_configs if dsd['Identifier'] == ds_id),
            None
        )
        if dataset_config is None:
            raise ApiError.NotFound(f'Dataset "{ds_id}" not found')
        return dataset_config

    def get_dataset_configs(self) -> List[DatasetConfig]:
        assert self._dataset_configs is not None
        return self._dataset_configs

    def get_data_store_pool(self) -> DataStorePool:
        assert self._data_store_pool is not None
        return self._data_store_pool

    @classmethod
    def _process_dataset_configs(
            cls,
            config: ServerConfig,
            base_dir: str
    ) -> Tuple[DataStorePool, List[Dict[str, Any]]]:
        data_store_configs = config.get('DataStores', [])
        dataset_configs = config.get('Datasets', [])

        data_store_pool = DataStorePool()
        for data_store_config_dict in data_store_configs:
            store_instance_id = data_store_config_dict.get('Identifier')
            store_id = data_store_config_dict.get('StoreId')
            store_params = data_store_config_dict.get('StoreParams', {})
            store_dataset_configs = data_store_config_dict.get('Datasets')
            store_config = DataStoreConfig(store_id,
                                           store_params=store_params,
                                           user_data=store_dataset_configs)
            data_store_pool.add_store_config(store_instance_id,
                                             store_config)
        dataset_configs = \
            dataset_configs + cls.get_dataset_configs_from_stores(
                data_store_pool
            )
        # Allow dataset_configs to be writable, because
        # _maybe_assign_store_instance_ids() will change
        # entries:
        dataset_configs = [dict(c) for c in dataset_configs]
        cls._maybe_assign_store_instance_ids(dataset_configs,
                                             data_store_pool,
                                             base_dir)
        return data_store_pool, dataset_configs

    def get_rgb_color_mapping(
            self,
            ds_id: str,
            norm_range: Tuple[float, float] = (0., 1.)
    ) -> Tuple[List[Optional[str]], List[Tuple[float, float]]]:
        var_names = [None, None, None]
        norm_ranges = [norm_range, norm_range, norm_range]
        color_mappings = self.get_color_mappings(ds_id)
        if color_mappings:
            rgb_mapping = color_mappings.get('rgb')
            if rgb_mapping:
                components = 'Red', 'Green', 'Blue'
                for i in range(3):
                    component = components[i]
                    component_config = rgb_mapping.get(component, {})
                    var_name = component_config.get('Variable')
                    norm_vmin, norm_vmax = component_config.get('ValueRange',
                                                                norm_range)
                    var_names[i] = var_name
                    norm_ranges[i] = norm_vmin, norm_vmax
        return var_names, norm_ranges

    def get_color_mapping(self,
                          ds_id: str,
                          var_name: str) -> Tuple[str, Tuple[float, float]]:
        cmap_name = None
        cmap_vmin, cmap_vmax = None, None
        color_mappings = self.get_color_mappings(ds_id)
        if color_mappings:
            color_mapping = color_mappings.get(var_name)
            if color_mapping:
                assert 'ColorFile' not in color_mapping
                cmap_name = color_mapping.get('ColorBar')
                cmap_vmin, cmap_vmax = color_mapping.get('ValueRange',
                                                         (None, None))

        cmap_range = cmap_vmin, cmap_vmax
        if cmap_name is not None and None not in cmap_range:
            # noinspection PyTypeChecker
            return cmap_name, cmap_range

        ds = self.get_dataset(ds_id, expected_var_names=[var_name])
        var = ds[var_name]
        valid_range = get_var_valid_range(var)
        return get_var_cmap_params(var, cmap_name, cmap_range, valid_range)

    def _get_cm_styles(self) -> Tuple[Dict[str, Any], ColormapRegistry]:
        custom_colormaps = {}
        cm_styles = {}
        for style in self.config.get("Styles", []):
            style_id = style["Identifier"]
            color_mappings = dict()
            for var_name, color_mapping in style["ColorMappings"].items():
                if "ColorFile" not in color_mapping:
                    color_mappings[var_name] = dict(color_mapping)
                    continue
                custom_cmap_path = self.get_config_path(
                    color_mapping,
                    "ColorMappings",
                    path_entry_name="ColorFile"
                )
                custom_colormap = custom_colormaps.get(custom_cmap_path)
                if custom_colormap is None:
                    custom_colormap = load_custom_colormap(
                        custom_cmap_path
                    )
                    custom_colormaps[custom_cmap_path] = custom_colormap
                if custom_colormap is not None:
                    color_mappings[var_name] = {
                        "ColorBar": custom_colormap.cm_name,
                        "ValueRange": (custom_colormap.norm.vmin,
                                       custom_colormap.norm.vmax)
                    }
            cm_styles[style_id] = color_mappings

        return cm_styles, ColormapRegistry(*custom_colormaps.values())

    def get_color_mappings(self, ds_id: str) \
            -> Optional[Dict[str, Dict[str, Any]]]:
        dataset_config = self.get_dataset_config(ds_id)
        style_id = dataset_config.get('Style', 'default')
        return self._cm_styles.get(style_id, {})

    def _get_dataset_entry(self, ds_id: str) \
            -> Tuple[MultiLevelDataset, ServerConfig]:
        if ds_id not in self._dataset_cache:
            with self.rlock:
                self._set_dataset_entry(self._create_dataset_entry(ds_id))
        return self._dataset_cache[ds_id]

    def _set_dataset_entry(
            self,
            dataset_entry: Tuple[MultiLevelDataset, DatasetConfig]
    ):
        ml_dataset, dataset_config = dataset_entry
        self._dataset_cache[ml_dataset.ds_id] = ml_dataset, dataset_config

    def _create_dataset_entry(self, ds_id: str) \
            -> Tuple[MultiLevelDataset, DatasetConfig]:
        dataset_config = self.get_dataset_config(ds_id)
        ml_dataset = self._open_ml_dataset(dataset_config)
        return ml_dataset, dataset_config

    def _open_ml_dataset(self, dataset_config: DatasetConfig) \
            -> MultiLevelDataset:
        ds_id: str = dataset_config.get('Identifier')
        store_instance_id = dataset_config.get('StoreInstanceId')
        if store_instance_id:
            data_store_pool = self.get_data_store_pool()
            data_store = data_store_pool.get_store(store_instance_id)
            data_id = dataset_config.get('Path')
            open_params = dataset_config.get('StoreOpenParams') or {}
            # Inject chunk_cache_capacity into open parameters
            chunk_cache_capacity = self.get_dataset_chunk_cache_capacity(
                dataset_config
            )
            if chunk_cache_capacity \
                    and (data_id.endswith('.zarr')
                         or data_id.endswith('.levels')) \
                    and 'cache_size' not in open_params:
                open_params['cache_size'] = chunk_cache_capacity
            with self.measure_time(tag=f"Opened dataset {ds_id!r}"
                                       f" from data store"
                                       f" {store_instance_id!r}"):
                dataset = data_store.open_data(data_id, **open_params)
            if isinstance(dataset, MultiLevelDataset):
                ml_dataset: MultiLevelDataset = dataset
            else:
                ml_dataset = BaseMultiLevelDataset(dataset)
            ml_dataset.ds_id = ds_id
        else:
            fs_type = dataset_config.get('FileSystem')
            if fs_type != 'memory':
                raise ApiError.InvalidServerConfig(
                    f"Invalid FileSystem {fs_type!r}"
                    f" in dataset configuration"
                    f" {ds_id!r}"
                )
            with self.measure_time(tag=f"Opened dataset {ds_id!r}"
                                       f" from {fs_type!r}"):
                ml_dataset = _open_ml_dataset_from_python_code(
                    self, dataset_config
                )
        augmentation = dataset_config.get('Augmentation')
        if augmentation:
            script_path = self.get_config_path(
                augmentation,
                f"'Augmentation' of dataset configuration {ds_id}"
            )
            input_parameters = augmentation.get('InputParameters')
            callable_name = augmentation.get('Function', COMPUTE_VARIABLES)
            ml_dataset = augment_ml_dataset(
                ml_dataset,
                script_path,
                callable_name,
                self.get_ml_dataset,
                self.set_ml_dataset,
                input_parameters=input_parameters,
                exception_type=ApiError.InvalidServerConfig
            )
        return ml_dataset

    def get_legend_label(self, ds_id: str, var_name: str):
        dataset = self.get_dataset(ds_id)
        if var_name in dataset:
            ds = self.get_dataset(ds_id)
            units = ds[var_name].units
            return units
        raise ApiError.NotFound(
            f'Variable "{var_name}" not found in dataset "{ds_id}"'
        )

    def get_dataset_place_groups(self,
                                 ds_id: str,
                                 base_url: str,
                                 load_features=False) -> List[Dict]:
        dataset_config = self.get_dataset_config(ds_id)

        place_group_id_prefix = f"DS-{ds_id}-"

        def predicate(pg_id: str, _: Any):
            return pg_id.startswith(place_group_id_prefix)

        place_groups = self.places_ctx.get_cached_place_groups(
            predicate=predicate
        )
        if place_groups:
            # for a given dataset, all place groups cached,
            # so the returned list is complete
            return place_groups

        place_groups = self.places_ctx.load_place_groups(
            dataset_config.get("PlaceGroups", []),
            base_url,
            is_global=False,
            load_features=load_features
        )

        for place_group in place_groups:
            place_group_id = place_group_id_prefix + place_group["id"]
            self.places_ctx.set_cached_place_group(place_group_id,
                                                   place_group)

        return place_groups

    def get_dataset_place_group(self,
                                ds_id: str,
                                place_group_id: str,
                                base_url: str,
                                load_features=False) -> Dict:
        place_groups = self.get_dataset_place_groups(ds_id, base_url,
                                                     load_features=False)
        for place_group in place_groups:
            if place_group_id == place_group['id']:
                if load_features:
                    self.places_ctx.load_place_group_features(place_group)
                return place_group
        raise ApiError.NotFound(
            f'Place group "{place_group_id}" not found')

    def get_dataset_and_coord_variable(self, ds_name: str, dim_name: str):
        ds = self.get_dataset(ds_name)
        if dim_name not in ds.coords:
            raise ApiError.NotFound(
                f'Dimension {dim_name!r} has no coordinates'
                f' in dataset {ds_name!r}'
            )
        return ds, ds.coords[dim_name]

    @classmethod
    def get_var_indexers(cls,
                         ds_name: str,
                         var_name: str,
                         var: xr.DataArray,
                         dim_names: List[str],
                         query_params: Dict[str, str]) -> Dict[str, Any]:
        var_indexers = dict()
        for dim_name in dim_names:
            if dim_name not in var.coords:
                raise ApiError.BadRequest(
                    f'Dimension {dim_name!r} of variable {var_name!r}'
                    f' in dataset {ds_name!r} has no coordinates'
                )
            coord_var = var.coords[dim_name]
            dim_value_str = query_params.get(dim_name)
            try:
                if dim_value_str is None:
                    var_indexers[dim_name] = coord_var.values[0]
                elif dim_value_str == 'current':
                    var_indexers[dim_name] = coord_var.values[-1]
                elif np.issubdtype(coord_var.dtype, np.floating):
                    var_indexers[dim_name] = float(dim_value_str)
                elif np.issubdtype(coord_var.dtype, np.integer):
                    var_indexers[dim_name] = int(dim_value_str)
                elif np.issubdtype(coord_var.dtype, np.datetime64):
                    if '/' in dim_value_str:
                        date_str_1, date_str_2 = dim_value_str.split(
                            '/', maxsplit=1
                        )
                        var_indexer_1 = pd.to_datetime(date_str_1)
                        var_indexer_2 = pd.to_datetime(date_str_2)
                        var_indexers[dim_name] = var_indexer_1 + (
                                var_indexer_2 - var_indexer_1) / 2
                    else:
                        date_str = dim_value_str
                        var_indexers[dim_name] = pd.to_datetime(date_str)
                else:
                    raise ValueError(
                        f'unable to convert value'
                        f' {dim_value_str!r} to {coord_var.dtype!r}'
                    )
            except ValueError as e:
                raise ApiError.BadRequest(
                    f'{dim_value_str!r} is not a valid value'
                    f' for dimension {dim_name!r} '
                    f'of variable {var_name!r} of dataset {ds_name!r}'
                ) from e
        return var_indexers

    def get_dataset_chunk_cache_capacity(
            self,
            dataset_config: DatasetConfig
    ) -> Optional[int]:
        cache_size = self.get_chunk_cache_capacity(
            dataset_config, 'ChunkCacheSize'
        )
        if cache_size is None:
            cache_size = self.get_chunk_cache_capacity(
                self.config, 'DatasetChunkCacheSize'
            )
        return cache_size

    @classmethod
    def get_chunk_cache_capacity(
            cls,
            config: Mapping[str, Any],
            cache_size_key: str
    ) -> Optional[int]:
        cache_size = config.get(cache_size_key, None)
        if not cache_size:
            return None
        elif isinstance(cache_size, str):
            try:
                cache_size = parse_mem_size(cache_size)
            except ValueError:
                raise ApiError.InvalidServerConfig(
                    f'Invalid {cache_size_key}'
                )
        elif not isinstance(cache_size, int) or cache_size < 0:
            raise ApiError.InvalidServerConfig(f'Invalid {cache_size_key}')
        return cache_size


def _open_ml_dataset_from_python_code(
        ctx: DatasetsContext,
        dataset_config: DatasetConfig
) -> MultiLevelDataset:
    ds_id = dataset_config.get('Identifier')
    path = ctx.get_config_path(dataset_config,
                               f"dataset configuration {ds_id}")
    callable_name = dataset_config.get('Function', COMPUTE_DATASET)
    input_dataset_ids = dataset_config.get('InputDatasets', [])
    input_parameters = dataset_config.get('InputParameters', {})
    chunk_cache_capacity = ctx.get_dataset_chunk_cache_capacity(
        dataset_config
    )
    if chunk_cache_capacity:
        warnings.warn(
            'chunk cache size is not effective for'
            ' datasets computed from scripts')
    for input_dataset_id in input_dataset_ids:
        if not ctx.get_dataset_config(input_dataset_id):
            raise ApiError.InvalidServerConfig(
                f"Invalid dataset configuration {ds_id!r}: "
                f"Input dataset {input_dataset_id!r} of"
                f" callable {callable_name!r} "
                f"must reference another dataset")
    return open_ml_dataset_from_python_code(
        path,
        callable_name=callable_name,
        input_ml_dataset_ids=input_dataset_ids,
        input_ml_dataset_getter=ctx.get_ml_dataset,
        input_parameters=input_parameters,
        ds_id=ds_id,
        exception_type=ApiError.InvalidServerConfig
    )


def _is_not_empty(prefix):
    return prefix != '' and prefix != '/' and prefix != '\\'


def _get_common_prefixes(p):
    # Recursively examine a list of paths for common prefixes:
    # If no common prefix is found, split the list in half and
    # examine each half separately
    prefix = os.path.commonprefix(p)
    if _is_not_empty(prefix) or len(p) == 1:
        return [prefix]
    else:
        return _get_common_prefixes(p[:int(len(p) / 2)]) + \
               _get_common_prefixes(p[int(len(p) / 2):])


_MULTI_LEVEL_DATASET_OPENERS = {
    "memory": _open_ml_dataset_from_python_code,
}
