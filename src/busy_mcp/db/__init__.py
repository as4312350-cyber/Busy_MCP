"""Database access: discovery of BUSY companies/years and read-only connections."""

from .connection import BusyDB, Master, clean_value
from .discovery import (
    Target,
    choose_target,
    data_dir_rank,
    data_folder_problem,
    discover_targets,
    find_data_dirs,
    fy_bounds,
    fy_label,
    has_company_folders,
    is_network_path,
    is_unc_path,
    open_companies,
    resolve_backend,
    scan_for_company_folders,
)
from .drivers import access_conn_str, sqlserver_conn_str

__all__ = [
    "BusyDB",
    "Master",
    "Target",
    "access_conn_str",
    "choose_target",
    "clean_value",
    "data_dir_rank",
    "data_folder_problem",
    "discover_targets",
    "find_data_dirs",
    "fy_bounds",
    "fy_label",
    "has_company_folders",
    "is_network_path",
    "is_unc_path",
    "open_companies",
    "resolve_backend",
    "scan_for_company_folders",
    "sqlserver_conn_str",
]
