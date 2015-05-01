#!/usr/bin/env python3

import pytest

from unittest.mock import MagicMock


@pytest.fixture
def browser_commands():
    from roland.core import BrowserCommands
    commands = BrowserCommands()
    commands.roland = MagicMock()
    commands.webview = MagicMock()
    commands.entry_line = MagicMock()
    return commands


@pytest.fixture
def real_browser_commands():
    from roland.core import BrowserCommands, WebKit2
    commands = BrowserCommands()
    commands.webview = WebKit2.WebView()
    return commands


@pytest.fixture
def browser_window():
    from roland.core import BrowserWindow
    return BrowserWindow(roland=MagicMock())


class TestBrowserCommands:
    @pytest.mark.parametrize('url,new_window', [
        ('', False),
        (None, False),
        ('', True),
        (None, True),
        ('frozen brains tell no tales', True),
        ('frozen brains tell no tales', False),
    ])
    def test_open(self, url, new_window, browser_commands):
        if url is None:
            browser_commands.entry_line.display = lambda func, *args, **kwargs: func('cool search')
            url = 'cool search'
        else:
            browser_commands.entry_line.display = lambda func, *args, **kwargs: func(url)

        browser_commands.open(url, new_window)

        if new_window:
            browser_commands.roland.new_window.assert_call(url)
        else:
            browser_commands.webview.load_uri.assert_any_call(url)

    @pytest.mark.parametrize('command', [
        'back',
        'forward',
        'move',
        'reload',
        'reload_bypass_cache',
        'stop',
        'zoom_in',
        'zoom_out',
        'zoom_reset',
    ])
    def test_real_commands_exist(self, command, real_browser_commands):
        command = getattr(real_browser_commands, command)
        command()

    @pytest.mark.parametrize('text,forwards,case_insensitive', [
        (None, True, True),
        ('text', True, False),
        ('text', False, True),
        ('Text', False, None),
    ])
    def test_search_page(self, browser_commands, text, forwards, case_insensitive):
        from gi.repository import WebKit2
        if text is None:
            browser_commands.entry_line.display = lambda func, *args, **kwargs: func('cool search')
            text = 'cool search'
        else:
            browser_commands.entry_line.display = lambda func, *args, **kwargs: func(text)

        browser_commands.search_page(text, forwards=forwards, case_insensitive=case_insensitive)

        options = WebKit2.FindOptions.WRAP_AROUND
        if not forwards:
            options |= WebKit2.FindOptions.BACKWARDS

        if case_insensitive is None:
            case_insensitive = text.lower() != text

        if case_insensitive:
            options |= WebKit2.FindOptions.CASE_INSENSITIVE

        finder = browser_commands.webview.get_find_controller.return_value
        finder.search.assert_any_call(text, options, 1000)

    @pytest.mark.parametrize('forwards,search_forwards', [
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    ])
    def test_next_search_result(self, browser_commands, forwards, search_forwards):
        browser_commands.search_forwards = search_forwards

        finder = browser_commands.webview.get_find_controller.return_value

        browser_commands.next_search_result(forwards)

        if forwards == search_forwards:
            finder.search_next.assert_has_call()
        else:
            finder.search_prev.assert_has_call()


class TestBrowserWindow:
    @pytest.mark.parametrize('command,expected_exist', [
        ('cool_function', False),
        ('cool_function', True),
    ])
    def test_run_command(self, command, expected_exist, browser_window):
        if expected_exist:
            setattr(browser_window, command, MagicMock(side_effect=Exception('lol no')))

        browser_window.run_command(command)
        browser_window.roland.notify.assert_has_call("No such command '{}'".format(command))

        if expected_exist:
            browser_window.roland.notify.assert_has_call("Error calling '{}': {}'".format(command, 'lol no'))

    def test_failed_to_find_text(self, browser_window):
        finder = MagicMock()
        finder.get_search_text.return_value = None

        browser_window.failed_to_find_text(finder)

        assert not browser_window.roland.notify.mock_calls
