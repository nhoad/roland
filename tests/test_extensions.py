from unittest.mock import patch, MagicMock

import pytest


class TestDownloadManager:
    def download_manager(self):
        from roland.extensions import DownloadManager
        dm = DownloadManager(roland=MagicMock())
        dm.save_location = '/path/to/downloads/'
        return dm

    @pytest.mark.parametrize('exists_list,expected_filepath', [
        ([False], 'foo'),
        ([True, False], 'foo.1'),
        ([True, True, False], 'foo.2'),
        ([True, True, True, False], 'foo.3'),
    ])
    def test_decide_destination(self, exists_list, expected_filepath):
        from gi.repository import WebKit2

        dm = self.download_manager()

        def path_exists(p):
            return exists_list.pop(0)

        download = MagicMock(WebKit2.Download())
        with patch('os.path.exists', path_exists):
            dm.decide_destination(download, 'foo')

        download.set_destination.assert_any_call('file:///path/to/downloads/' + expected_filepath)
