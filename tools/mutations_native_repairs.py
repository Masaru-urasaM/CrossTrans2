"""Guard first-render colours and measured state-aware contrast/resource coverage."""
MUTATIONS = [
    ('new-history-keeps-dark-default', 'src/ui/history_dialog.py',
     "            p = self.ctx.palette\n            box.recolour(bg=p.surface, base_fg=p.text, kanji_fg=p.text, ruby_fg=p.ruby, selection=p.selection)",
     "            p = self.ctx.palette",
     ['tests/test_v2_home.py', '-k', 'new_history_detail_uses_current_palette']),
    ('selected-foreground-ignored', 'tools/ui_gallery.py',
     "style.lookup(name or widget.winfo_class(), option, state=widget.state())",
     "style.lookup(name or widget.winfo_class(), option)",
     ['tests/test_ui_gallery_tool.py', '-k', 'foreground_of_the_selected']),
    ('pdf-layout-resources-ignored', 'tools/verify_ui_bundle.py',
     "            if path.is_file() and '__pycache__' not in path.parts}",
     "            if False}",
     ['tests/test_ui_bundle.py', '-k', 'installed_pdf_layout_resources']),
]
