# The MIT License (MIT)
# Copyright (c) 2021 by the xcube development team and contributors
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

from xcube.util.jsonschema import JsonBooleanSchema
from xcube.util.jsonschema import JsonObjectSchema
from xcube.util.jsonschema import JsonStringSchema
from ..accessor import COMMON_STORAGE_OPTIONS_SCHEMA_PROPERTIES
from ..accessor import FsAccessor


class FileFsAccessor(FsAccessor):

    @classmethod
    def get_protocol(cls) -> str:
        return 'file'

    @classmethod
    def get_storage_options_schema(cls) -> JsonObjectSchema:
        return JsonObjectSchema(
            properties=dict(
                auto_mkdirs=JsonBooleanSchema(
                    description='Whether, when opening a file, the directory'
                                ' containing it should be created (if it'
                                ' doesn\'t already exist).'),
                **COMMON_STORAGE_OPTIONS_SCHEMA_PROPERTIES
            ),
            additional_properties=True,
        )


class MemoryFsAccessor(FsAccessor):

    @classmethod
    def get_protocol(cls) -> str:
        return 'memory'


class S3FsAccessor(FsAccessor):

    @classmethod
    def get_protocol(cls) -> str:
        return 's3'

    @classmethod
    def get_storage_options_schema(cls) -> JsonObjectSchema:
        # We may use here AWS S3 defaults as described in
        #   https://boto3.amazonaws.com/v1/documentation/api/
        #   latest/guide/configuration.html
        return JsonObjectSchema(
            properties=dict(
                anon=JsonBooleanSchema(
                    title='Whether to anonymously connect to AWS S3.'
                ),
                key=JsonStringSchema(
                    min_length=1,
                    title='AWS access key identifier.',
                    description='Can also be set in profile section'
                                ' of ~/.aws/config, or by environment'
                                ' variable AWS_ACCESS_KEY_ID.'
                ),
                secret=JsonStringSchema(
                    min_length=1,
                    title='AWS secret access key.',
                    description='Can also be set in profile section'
                                ' of ~/.aws/config, or by environment'
                                ' variable AWS_SECRET_ACCESS_KEY.'
                ),
                token=JsonStringSchema(
                    min_length=1,
                    title='Session token.',
                    description='Can also be set in profile section'
                                ' of ~/.aws/config, or by environment'
                                ' variable AWS_SESSION_TOKEN.'
                ),
                use_ssl=JsonBooleanSchema(
                    description='Whether to use SSL in connections to S3;'
                                ' may be faster without, but insecure.',
                    default=True,
                ),
                requester_pays=JsonBooleanSchema(
                    description='If "RequesterPays" buckets are supported.',
                    default=False,
                ),
                s3_additional_kwargs=JsonObjectSchema(
                    description='parameters that are used when calling'
                                ' S3 API methods. Typically used for'
                                ' things like "ServerSideEncryption".',
                    additional_properties=True,
                ),
                client_kwargs=JsonObjectSchema(
                    description='Parameters for the botocore client.',
                    properties=dict(
                        endpoint_url=JsonStringSchema(
                            min_length=1,
                            format='uri',
                            title='Alternative endpoint URL.'
                        ),
                        # bucket_name=JsonStringSchema(
                        #     min_length=1,
                        #     title='Name of the bucket'
                        # ),
                        profile_name=JsonStringSchema(
                            min_length=1,
                            title='Name of the AWS configuration profile',
                            description='Section name with within'
                                        ' ~/.aws/config file,'
                                        ' which provides AWS configurations'
                                        ' and credentials.'
                        ),
                        region_name=JsonStringSchema(
                            min_length=1,
                            title='AWS storage region name'
                        ),
                    ),
                    additional_properties=True,
                ),
                **COMMON_STORAGE_OPTIONS_SCHEMA_PROPERTIES,
            ),
            additional_properties=True,
        )
