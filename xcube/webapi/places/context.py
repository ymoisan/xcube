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


import glob
from typing import Any, Dict, List, Optional, Callable
from typing import Sequence

import fiona

from xcube.server.api import ApiError
from xcube.server.api import Context
from xcube.webapi.common.context import ResourcesContext

PlaceGroup = Dict[str, Any]
Feature = Dict[str, Any]

ALL_PLACES = "all"


class PlacesContext(ResourcesContext):
    _feature_index: int = 0

    def __init__(self, server_ctx: Context):
        super().__init__(server_ctx)
        self._place_group_cache: Dict[str, PlaceGroup] = dict()

    def on_dispose(self):
        if self._place_group_cache:
            self._place_group_cache.clear()

    def get_cached_place_group(self,
                               place_group_id: str) -> Optional[PlaceGroup]:
        return self._place_group_cache.get(place_group_id)

    def set_cached_place_group(self,
                               place_group_id: str,
                               place_group: PlaceGroup):
        self._place_group_cache[place_group_id] = place_group

    def get_cached_place_groups(
            self,
            predicate: Optional[Callable[[str, PlaceGroup], bool]] = None
    ) -> List[PlaceGroup]:
        return [
            v
            for k, v in self._place_group_cache.items()
            if predicate is None or predicate(k, v)
        ]

    def new_feature_id(self) -> str:
        with self.rlock:
            feature_id = str(self._feature_index)
            self._feature_index += 1
            return feature_id

    def get_global_place_groups(self,
                                base_url: str,
                                load_features=False) -> List[PlaceGroup]:
        return self.load_place_groups(self.config.get("PlaceGroups", []),
                                      base_url,
                                      is_global=True,
                                      load_features=load_features)

    def get_global_place_group(self,
                               place_group_id: str,
                               base_url: str,
                               load_features: bool = False) -> PlaceGroup:
        place_group_config = self._get_place_group_config(place_group_id)
        return self._load_place_group(place_group_config,
                                      base_url,
                                      is_global=True,
                                      load_features=load_features)

    def _get_place_group_config(self, place_group_id: str) -> Dict:
        place_group_configs = self.config.get("PlaceGroups", [])
        for place_group_config in place_group_configs:
            if place_group_config['Identifier'] == place_group_id:
                return place_group_config
        raise ApiError.NotFound(
            f'Place group "{place_group_id}" not found'
        )

    def load_place_groups(self,
                          place_group_configs: Dict,
                          base_url: str,
                          is_global: bool = False,
                          load_features: bool = False) -> List[PlaceGroup]:
        place_groups = []
        for place_group_config in place_group_configs:
            place_group = self._load_place_group(place_group_config,
                                                 base_url,
                                                 is_global=is_global,
                                                 load_features=load_features)
            place_groups.append(place_group)
        return place_groups

    def _load_place_group(self,
                          place_group_config: Dict[str, Any],
                          base_url: str,
                          is_global: bool = False,
                          load_features: bool = False) -> PlaceGroup:
        place_group_id = place_group_config.get("PlaceGroupRef")
        if place_group_id:
            if is_global:
                raise ApiError.InvalidServerConfig(
                    "'PlaceGroupRef' cannot be used in a global place group"
                )
            if len(place_group_config) > 1:
                raise ApiError.InvalidServerConfig(
                    "'PlaceGroupRef' if present,"
                    " must be the only entry in a 'PlaceGroups' item"
                )
            return self.get_global_place_group(place_group_id, base_url,
                                               load_features=load_features)

        place_group_id = place_group_config.get("Identifier")
        if not place_group_id:
            raise ApiError.InvalidServerConfig(
                "Missing 'Identifier' entry in a 'PlaceGroups' item"
            )

        place_group = self.get_cached_place_group(place_group_id)
        if place_group is None:
            place_group_title = place_group_config.get("Title", place_group_id)
            place_path_wc = self.get_config_path(place_group_config,
                                                 f"'PlaceGroups' item")
            source_paths = glob.glob(place_path_wc)
            source_encoding = place_group_config.get("CharacterEncoding",
                                                     "utf-8")

            join = None
            place_join = place_group_config.get("Join")
            if isinstance(place_join, dict):
                join_path = self.get_config_path(
                    place_join, "'Join' of a 'PlaceGroups' item"
                )
                join_property = place_join.get("Property")
                if not join_property:
                    raise ApiError.InvalidServerConfig(
                        "Missing 'Property' entry in 'Join'"
                        " of a 'PlaceGroups' item"
                    )
                join_encoding = place_join.get("CharacterEncoding", "utf-8")
                join = dict(path=join_path, property=join_property,
                            encoding=join_encoding)

            property_mapping = place_group_config.get("PropertyMapping")
            if property_mapping:
                property_mapping = dict(property_mapping)
                for key, value in property_mapping.items():
                    if isinstance(value, str) and '${base_url}' in value:
                        property_mapping[key] = value.replace('${base_url}',
                                                              base_url)

            place_group = dict(type="FeatureCollection",
                               features=None,
                               id=place_group_id,
                               title=place_group_title,
                               propertyMapping=property_mapping,
                               sourcePaths=source_paths,
                               sourceEncoding=source_encoding,
                               join=join)

            sub_place_group_configs = place_group_config.get("Places")
            if sub_place_group_configs:
                raise ApiError.InvalidServerConfig(
                    "Invalid 'Places' entry in a 'PlaceGroups' item:"
                    " not implemented yet"
                )

            # sub_place_group_configs = place_group_config.get("Places")
            # if sub_place_group_configs:
            #     sub_place_groups = self._load_place_groups(
            #         sub_place_group_configs
            #     )
            #     place_group["placeGroups"] = sub_place_groups

            self.set_cached_place_group(place_group_id, place_group)

        if load_features:
            self.load_place_group_features(place_group)

        return place_group

    def load_place_group_features(self, place_group: PlaceGroup) \
            -> List[Dict[str, Any]]:
        features = place_group.get('features')
        if features is not None:
            return features
        source_files = place_group['sourcePaths']
        source_encoding = place_group['sourceEncoding']
        features = []
        for source_file in source_files:
            with self.measure_time(f'Loading features from file {source_file}'):
                with fiona.open(source_file,
                                encoding=source_encoding) as feature_collection:
                    for feature in feature_collection:
                        self._remove_feature_id(feature)
                        feature["id"] = self.new_feature_id()
                        features.append(feature)

        join = place_group['join']
        if join:
            join_path = join['path']
            join_property = join['property']
            join_encoding = join['encoding']
            with fiona.open(join_path,
                            encoding=join_encoding) as feature_collection:
                indexed_join_features = self._get_indexed_features(
                    feature_collection, join_property)
            for feature in features:
                properties = feature.get('properties')
                if isinstance(properties, dict) \
                        and join_property in properties:
                    join_value = properties[join_property]
                    join_feature = indexed_join_features.get(join_value)
                    if join_feature:
                        join_properties = join_feature.get('properties')
                        if join_properties:
                            properties.update(join_properties)
                            feature['properties'] = properties

        place_group['features'] = features
        return features

    @classmethod
    def _get_indexed_features(cls,
                              features: Sequence[Feature],
                              property_name: str) -> Feature:
        feature_index = {}
        for feature in features:
            properties = feature.get('properties')
            if properties and property_name in properties:
                property_value = properties[property_name]
                feature_index[property_value] = feature
        return feature_index

    @classmethod
    def _remove_feature_id(cls, feature: Feature):
        if "id" in feature:
            del feature["id"]
        if "ID" in feature:
            del feature["ID"]
