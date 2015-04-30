import pytest


@pytest.mark.parametrize('bytecount,expected_output', [
    (1000, '1000b'),
    (1024, '1kb'),
    (10240, '10kb'),
    (102400, '100kb'),
    (1024*1024, '1mb'),
    (1024*1024*512, '512mb'),
    (1024*1024*1024, '1gb'),
    (1024*1024*1024*512, '512gb'),
    (1024*1024*1024*1024, '1tb'),
])
def test_pretty_size(bytecount, expected_output):
    from roland.utils import get_pretty_size
    assert get_pretty_size(bytecount) == expected_output
