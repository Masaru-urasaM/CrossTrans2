"""Private updater security and stale-result contracts must kill these faults."""
MUTATIONS = [
    ('plaintext-token-saved', 'config.py',
     "self._config['github_update_token_encrypted'] = encrypted",
     "self._config['github_update_token_encrypted'] = token",
     ['tests/test_private_updates.py', '-k', 'encrypted_separately']),
    ('redirect-leaks-token', 'src/utils/github_updates.py',
     "            redirected.remove_header('Authorization')",
     "            pass",
     ['tests/test_private_updates.py', '-k', 'redirect_keeps_auth']),
    ('http-downgrade-allowed', 'src/utils/github_updates.py',
     "target.scheme != 'https' or target.username or target.password",
     "target.username or target.password",
     ['tests/test_private_updates.py', '-k', 'insecure_redirects']),
    ('private-browser-url-used', 'src/utils/updates.py',
     "asset.get('url') if self._access_token else asset.get('browser_download_url')",
     "asset.get('browser_download_url')",
     ['tests/test_private_updates.py', '-k', 'metadata_and_binary']),
    ('old-token-check-delivered', 'src/core/update_ui_manager.py',
     "        self._request_id += 1\n        self._checking = False\n        self._callbacks.clear()",
     "        self._callbacks.clear()",
     ['tests/test_private_updates.py', '-k', 'invalidates_old_result']),
]

MUTATIONS.extend([
    ('narrow-provider-fields-not-stacked', 'src/ui/settings/page_providers.py',
     'compact = room < px(520) * C.get().manager.text_scale',
     'compact = False',
     ['tests/test_v2_providers.py', '-k', 'narrow_key_card']),
    ('horizontal-canvas-clipping-ignored', 'tools/ui_gallery.py',
     "            if y < bottom and y + widget.winfo_height() > top and (x < left - 1 or x + widget.winfo_width() > right + 1):",
     "            if False:",
     ['tests/test_ui_gallery_tool.py', '-k', 'vertical_scroll_canvas']),
])
