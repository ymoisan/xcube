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

from xcube.server.api import ApiHandler, ApiError
from .api import api
from .context import PlacesContext
from .controllers import find_places


@api.route('/places')
class PlaceGroupsHandler(ApiHandler[PlacesContext]):
    @api.operation(operationId='getPlaceGroups')
    def get(self):
        place_groups = self.ctx.get_global_place_groups(self.request.base_url)
        self.response.finish({"placeGroups": place_groups})


# noinspection PyPep8Naming
@api.route('/places/{placeGroupId}')
class FindPlacesHandler(ApiHandler):
    """Find places within a known place group."""

    @api.operation(operationId='findPlacesInPlaceGroup',
                   summary='Find places in a given place group.')
    def get(self, placeGroupId: str):
        query_expr = self.request.get_query_arg("query", default=None)
        geom_wkt = self.request.get_query_arg("geom", default=None)
        box_coords = self.request.get_query_arg("bbox", default=None)
        comb_op = self.request.get_query_arg("comb", default="and")
        if geom_wkt and box_coords:
            raise ApiError.BadRequest(
                'Only one of "geom" and "bbox" may be given'
            )
        places = find_places(self.ctx,
                             placeGroupId,
                             self.request.base_url,
                             query_geometry=box_coords or geom_wkt or None,
                             query_expr=query_expr, comb_op=comb_op)
        self.response.finish({"places": places})

    @api.operation(operationId='findPlacesInPlaceGroup',
                   summary='Find places in a given place group'
                           ' for a GeoJSON object.')
    def post(self, placeGroupId: str):
        query_expr = self.request.get_query_arg("query", default=None)
        comb_op = self.request.get_query_arg("comb", default="and")
        geojson_obj = self.request.json
        places = find_places(self.ctx,
                             placeGroupId,
                             self.request.base_url,
                             query_geometry=geojson_obj,
                             query_expr=query_expr, comb_op=comb_op)
        self.response.finish({"places": places})
