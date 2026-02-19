"""
Dataset Metadata Processing Module

, support CSV, TSV, MAT, NPY . 
"""

import os
from typing import List, Any, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel

try:
    import scipy.io
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class ColumnMetadata(BaseModel):
    """"""
    name: str
    dtype: str
    sample_values: List[Any]
    null_count: int
    unique_count: int


class DatasetMetadata(BaseModel):
    """"""
    filename: str
    file_format: str
    file_size_bytes: int
    total_rows: int
    total_columns: int
    columns: List[ColumnMetadata]


class DataProcessor:
    """, """

    @staticmethod
    def _process_npy_file(file_path: str) -> DatasetMetadata:
        """ .npy file"""
        data = np.load(file_path, allow_pickle=True)

        columns_metadata = []
        dtype_str = str(data.dtype)

        # Handle structured arrays (with named fields)
        if data.dtype.names is not None:
            for name in data.dtype.names:
                field_data = data[name]
                flat = field_data.flatten()
                sample_vals = [x.item() if isinstance(x, np.generic) else x for x in flat[:5]]

                null_count = 0
                if np.issubdtype(field_data.dtype, np.number):
                    null_count = int(np.isnan(field_data).sum()) if field_data.size > 0 else 0

                unique_count = len(np.unique(flat)) if flat.size < 10000 else -1

                columns_metadata.append(ColumnMetadata(
                    name=name,
                    dtype=str(field_data.dtype),
                    sample_values=sample_vals,
                    null_count=null_count,
                    unique_count=unique_count
                ))
            total_rows = data.shape[0] if data.ndim > 0 else 1
        else:
            # Regular ndarray - treat dimensions as columns
            if data.ndim == 1:
                flat = data.flatten()
                sample_vals = [x.item() if isinstance(x, np.generic) else x for x in flat[:5]]
                null_count = int(np.isnan(data).sum()) if np.issubdtype(data.dtype, np.number) else 0
                unique_count = len(np.unique(flat)) if flat.size < 10000 else -1

                columns_metadata.append(ColumnMetadata(
                    name='data',
                    dtype=dtype_str,
                    sample_values=sample_vals,
                    null_count=null_count,
                    unique_count=unique_count
                ))
                total_rows = data.shape[0]
            elif data.ndim == 2:
                total_rows = data.shape[0]
                for i in range(data.shape[1]):
                    col_data = data[:, i]
                    sample_vals = [x.item() if isinstance(x, np.generic) else x for x in col_data[:5]]
                    null_count = int(np.isnan(col_data).sum()) if np.issubdtype(data.dtype, np.number) else 0
                    unique_count = len(np.unique(col_data)) if col_data.size < 10000 else -1

                    columns_metadata.append(ColumnMetadata(
                        name=f'col_{i}',
                        dtype=dtype_str,
                        sample_values=sample_vals,
                        null_count=null_count,
                        unique_count=unique_count
                    ))
            else:
                # Higher dimensional arrays - flatten and treat as single column
                flat = data.flatten()
                sample_vals = [x.item() if isinstance(x, np.generic) else x for x in flat[:5]]
                null_count = int(np.isnan(data).sum()) if np.issubdtype(data.dtype, np.number) else 0
                unique_count = len(np.unique(flat)) if flat.size < 10000 else -1

                columns_metadata.append(ColumnMetadata(
                    name=f'data (shape: {data.shape})',
                    dtype=dtype_str,
                    sample_values=sample_vals,
                    null_count=null_count,
                    unique_count=unique_count
                ))
                total_rows = data.shape[0]

        return DatasetMetadata(
            filename=os.path.basename(file_path),
            file_format='npy',
            file_size_bytes=os.path.getsize(file_path),
            total_rows=total_rows,
            total_columns=len(columns_metadata),
            columns=columns_metadata
        )

    @staticmethod
    def _process_mat_file(file_path: str) -> DatasetMetadata:
        """ .mat file"""
        if not HAS_SCIPY:
            raise ImportError("scipy is required to read .mat files")

        mat = scipy.io.loadmat(file_path)

        # Filter internal keys
        data = {k: v for k, v in mat.items() if not k.startswith('__')}

        columns_metadata = []
        max_rows = 0

        for key, value in data.items():
            sample_vals = []
            null_count = 0
            unique_count = 0
            dtype_str = 'unknown'

            if isinstance(value, np.ndarray):
                dtype_str = str(value.dtype)
                # Sample
                flat = value.flatten()
                # Handle non-serializable types for JSON (like numpy scalars)
                sample_vals = [x.item() if isinstance(x, np.generic) else x for x in flat[:5]]

                # Rows estimate (using first dimension)
                rows = value.shape[0] if value.ndim > 0 else 0
                max_rows = max(max_rows, rows)

                # Unique/Null
                if value.size > 0:
                    if np.issubdtype(value.dtype, np.number):
                        null_count = int(np.isnan(value).sum())
                    # Limit unique count calculation for performance
                    if value.size < 10000:
                        unique_count = len(np.unique(flat))
                    else:
                        unique_count = -1
            else:
                dtype_str = type(value).__name__
                sample_vals = [str(value)[:50]]
                max_rows = max(max_rows, 1)
                unique_count = 1

            columns_metadata.append(ColumnMetadata(
                name=key,
                dtype=dtype_str,
                sample_values=sample_vals,
                null_count=null_count,
                unique_count=unique_count
            ))

        return DatasetMetadata(
            filename=os.path.basename(file_path),
            file_format='mat',
            file_size_bytes=os.path.getsize(file_path),
            total_rows=max_rows,
            total_columns=len(columns_metadata),
            columns=columns_metadata
        )

    @staticmethod
    def get_metadata(file_path: str) -> DatasetMetadata:
        """
        Extract metadata from supported dataset files.

        Args:
            file_path: Dataset file path.

        Returns:
            Parsed dataset metadata.

        Raises:
            FileNotFoundError: File does not exist.
            ValueError: Unsupported format or parsing failure.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_ext = os.path.splitext(file_path)[1].lower()

        if file_ext == '.mat':
            return DataProcessor._process_mat_file(file_path)

        if file_ext == '.npy':
            return DataProcessor._process_npy_file(file_path)

        try:
            if file_ext == '.tsv':
                df = pd.read_csv(file_path, sep='\t')
            elif file_ext == '.csv':
                df = pd.read_csv(file_path)
            else:
                raise ValueError(f"Unsupported file format: {file_ext}. Only .csv, .tsv, .mat, and .npy are supported.")
        except Exception as e:
            raise ValueError(f"Failed to read file: {e}")

        columns_metadata = []
        for col in df.columns:
            # Get a sample of non-null values
            sample_vals = df[col].dropna().head(5).tolist()

            columns_metadata.append(ColumnMetadata(
                name=str(col),
                dtype=str(df[col].dtype),
                sample_values=sample_vals,
                null_count=int(df[col].isnull().sum()),
                unique_count=int(df[col].nunique())
            ))

        return DatasetMetadata(
            filename=os.path.basename(file_path),
            file_format=file_ext.lstrip('.'),
            file_size_bytes=os.path.getsize(file_path),
            total_rows=len(df),
            total_columns=len(df.columns),
            columns=columns_metadata
        )
