import itertools
from typing import Dict, Tuple, Iterable, Iterator

import numpy as np
import xarray as xr

from xcube.core.update import update_dataset_chunk_encoding


def chunk_dataset(dataset: xr.Dataset,
                  chunk_sizes: Dict[str, int] = None,
                  format_name: str = None) -> xr.Dataset:
    """
    Chunk *dataset* using *chunk_sizes* and optionally
    update encodings for given *format_name*.

    :param dataset: input dataset
    :param chunk_sizes: mapping from dimension name to new chunk size
    :param format_name: optional format, e.g. "zarr" or "netcdf4"
    :return: the (re)chunked dataset
    """
    dataset = dataset.chunk(chunks=chunk_sizes)
    if format_name:
        dataset = update_dataset_chunk_encoding(dataset,
                                                chunk_sizes=chunk_sizes,
                                                format_name=format_name)
    return dataset


def get_empty_dataset_chunks(dataset: xr.Dataset) \
        -> Iterator[Tuple[str, Iterator[Tuple[int, ...]]]]:
    """
    Identify empty dataset chunks and return their indices.

    :param dataset: The dataset.
    :return: An iterator that provides a stream of
        (variable name, block indices tuple) tuples.
    """
    return ((str(var_name), get_empty_var_chunks(dataset[var_name]))
            for var_name in dataset.data_vars)


def get_empty_var_chunks(var: xr.DataArray) -> Iterator[Tuple[int, ...]]:
    """
    Identify empty variable chunks and return their indices.

    :param var: The variable.
    :return: A list of block indices.
    """
    chunks = var.chunks
    if chunks is None:
        return None

    for chunk_index, chunk_slice in compute_chunk_slices(chunks):
        data_index = tuple(slice(start, end) for start, end in chunk_slice)
        data = var[data_index]
        if np.all(np.isnan(data)):
            # print(f'empty: {var.name}/{".".join(map(str, chunk_index))}')
            yield chunk_index


def compute_chunk_slices(chunks: Tuple[Tuple[int, ...], ...]) -> Iterable:
    chunk_indices = []
    for c in chunks:
        chunk_indices.append(tuple(i for i in range(len(c))))

    chunk_slices = []
    for c in chunks:
        x = []
        o = 0
        for s in c:
            x.append((o, o + s))
            o += s
        chunk_slices.append(tuple(x))

    return zip(itertools.product(*chunk_indices),
               itertools.product(*chunk_slices))
