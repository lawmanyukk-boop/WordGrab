"""Runtime storage paths shared by the application and storage services.

The data directory can be changed while the app is running, so callers must
ask for the current value instead of importing a snapshot of ``DATA``.
"""

import os


_data_directory = None


def configure_data_directory(directory):
    global _data_directory
    _data_directory = os.path.abspath(os.path.expanduser(str(directory)))


def data_directory():
    if _data_directory is None:
        raise RuntimeError("storage paths have not been configured")
    return _data_directory


def item_directory(iid):
    return os.path.join(data_directory(), str(iid))
