"""Persistent Home lookup page backed by the existing Dictionary flow and renderers."""
import tkinter as tk

from src.constants import LANGUAGES
from src.core.dictionary_prompt import dictionary_source_language
from src.ui import components as ui
from src.ui.dictionary_popup import DictionaryPopup
from src.ui.dictionary_result import DictionaryResultWindow
from src.ui.dictionary_mode import WordButtonFrame
from src.ui.custom_word_boxes import CustomWordBoxesFrame
from src.ui.ruby_text import NO_AUTOSTYLE


class HomeDictionary:
    def __init__(self, parent, root, config, service, toast, quick_manager, on_settings, on_open_translate,
                 *, compact=False, on_close=None):
        self.frame = ui.Frame(parent)
        self.frame.pack(fill='both', expand=True)
        self.config, self.service, self.toast = config, service, toast
        self.on_settings, self.on_open_translate = on_settings, on_open_translate
        self.compact, self.on_close = compact, on_close
        self.selection_area = None
        self.selection_notice = None
        self.busy = False
        self.word_frame = self.custom_boxes = self.result_view = None
        self.selection_context = None
        self.input_area = ui.ScrollArea(self.frame, stretch=True)
        self.input_area.pack(fill='both', expand=True)
        body = self.input_area.content
        px = ui.get().px
        ui.Label(body, 'Dictionary', kind='title').pack(anchor='w', pady=(px(12), px(8)))
        controls = ui.Frame(body)
        controls.pack(fill='x', pady=(0, px(12)))
        self.source = ui.SourceLanguagePicker(controls, label='Source language')
        self.source.pack(side='left')
        self.target = ui.LanguagePicker(controls, LANGUAGES, config.get_default_target_language(), on_change=lambda value: None, label='Explain in')
        self.target.pack(side='left', padx=px(8))
        self.input = tk.Text(body, width=1, height=4, wrap='word', font=ui.get().font('body'), **NO_AUTOSTYLE)
        self.input.pack(fill='x')
        self._unsubscribe = ui.get().manager.subscribe(self._retheme)
        self._retheme(ui.get().manager)
        self.input.bind('<Control-Return>', lambda event: self.lookup_phrase() or 'break')
        actions = ui.ActionBar(body)
        actions.pack(fill='x', pady=px(8))
        self.lookup_button = ui.Button(actions, 'Look up phrase', self.lookup_phrase, kind='accent', icon='search')
        actions.add(self.lookup_button)
        self.select_button = actions.add(ui.Button(actions, 'Select words from passage', self.analyze, kind='soft'))
        self.pack_button = ui.Button(actions, 'Language packs', lambda: on_settings('Dictionary'), kind='subtle')
        actions.add(self.pack_button)
        self.api_button = ui.Button(body, 'Open API keys', lambda: on_settings('API Key'), kind='subtle', icon='key')
        self.status = ui.Label(body, '', kind='muted', wraplength=px(600), justify='left')
        self.status.pack(fill='x', pady=(0, px(8)))
        self.word_host = ui.Frame(body)
        self.word_host.pack(fill='both', expand=True)
        self.flow = DictionaryPopup(root, config, service, toast, quick_manager)
        self.flow.configure_callbacks(on_show_settings_tab=on_settings,
                                       get_selected_language=lambda: self.target.value,
                                       on_selection=self.show_selection, on_result=self.receive_result)
        self.input_area.adopt()
        self.frame.bind('<Destroy>', self._destroyed, add='+')

    def activate(self):
        self._retheme(ui.get().manager)

    def _retheme(self, manager):
        p = manager.palette
        self.input.configure(bg=p.surface, fg=p.text, insertbackground=p.text,
                             selectbackground=p.selection, selectforeground=p.selection_text,
                             inactiveselectbackground=p.selection, highlightbackground=p.border,
                             highlightcolor=p.focus, highlightthickness=1, relief='flat',
                             font=ui.get().font('body'))

    def text(self):
        return self.input.get('1.0', 'end-1c')

    def receive_text(self, text, source_language=None, target_language=None):
        def apply():
            self.show_input()
            self.input.delete('1.0', 'end')
            self.input.insert('1.0', text)
            self.source.set(source_language or 'Auto')
            if target_language:
                self.target.set(target_language)
        if self.text().strip() and self.text() != text:
            dialog = ui.Dialog(self.frame, 'Replace the lookup text?',
                               'The Dictionary already contains text. Replace it with the selected text?')
            dialog.add_button('Cancel', default=True)
            dialog.add_button('Use selected text', apply)
            dialog.show()
            return dialog
        apply()

    def show_input(self):
        if self.selection_area is not None:
            self.selection_area.pack_forget()
        if self.result_view is not None:
            self.result_view.window.pack_forget()
        self.input_area.pack(fill='both', expand=True)

    def analyze(self):
        text = self.text()
        if not text.strip():
            self.status.configure(text='Enter a word, phrase or passage first.')
            return
        if self.source.value != 'Auto':
            from src.core.nlp_manager import nlp_manager
            from src.core.nlp_manager import LANGUAGE_PACKS
            if self.source.value not in LANGUAGE_PACKS:
                self.status.configure(text='Word selection is not available for this language. Use Look up phrase.')
                return
            if not nlp_manager.is_installed(self.source.value):
                self.status.configure(text='Install this language pack to select words. Phrase lookup remains available.')
                self.pack_button.focus_set()
                return
            self.flow._open_with_language(text, self.source.value, trusted=True,
                                          target_language=self.target.value)
        else:
            self.flow.open_from_text(text, target_language=self.target.value)

    def show_selection(self, original, language, trusted, target):
        if original != self.text():
            return
        if self.compact:
            self.input_area.pack_forget()
            self.selection_notice = None
            if self.selection_area is not None:
                self.selection_area.destroy()
            self.selection_area = ui.Frame(self.frame)
            self.selection_area.pack(fill='both', expand=True)
            header = ui.Frame(self.selection_area)
            header.pack(fill='x', pady=(0, ui.get().px(8)))
            ui.Button(header, 'Back', self.show_input, kind='subtle', icon='arrow-left').pack(side='right')
            ui.Chip(header, f'{language} → {target}').pack(side='left')
            ui.Label(self.selection_area, 'Pick words to look up', kind='strong').pack(anchor='w')
            self.word_host = ui.Frame(self.selection_area)
            self.word_host.pack(fill='both', expand=True)
        for child in self.word_host.winfo_children():
            child.destroy()
        self.selection_context = original
        source = dictionary_source_language(language, trusted)
        def selected(words):
            phrases = list(words) + self.custom_boxes.get_all_phrases()
            self.lookup_words(phrases, target, source, context=original)
        def custom_only():
            self.lookup_words(self.custom_boxes.get_all_phrases(), target, source, context=original)
        self.word_frame = WordButtonFrame(self.word_host, original, on_selection_change=lambda value: None,
                                           on_lookup=selected, on_no_selection=custom_only,
                                           language=language, furigana_enabled=self.config.get_furigana_enabled())
        self.word_frame.pack(fill='both', expand=True)
        self.word_frame.set_exit_callback(self.on_close if self.compact and self.on_close else self.show_input)
        self.word_frame.expand_btn.pack_forget()
        self.custom_boxes = CustomWordBoxesFrame(self.word_frame.frame, language=language,
                                                  furigana_enabled=self.config.get_furigana_enabled(), compact=self.compact)
        self.word_frame.insert_custom_widget(self.custom_boxes.frame)
        self.word_frame.set_drop_target(self.custom_boxes)
        self.input_area.adopt()

    def lookup_phrase(self):
        text = self.text().strip()
        source = self.source.value if self.source.value != 'Auto' else None
        self.lookup_words([text] if text else [], self.target.value, source, context=self.text())

    def _clear_lookup_notice(self):
        if self.selection_notice is not None:
            if self.selection_notice.winfo_exists():
                self.selection_notice.destroy()
            self.selection_notice = None

    def _lookup_notice(self, message, api=False):
        self.status.configure(text=message)
        if self.compact and self.selection_area is not None and self.selection_area.winfo_ismapped():
            self._clear_lookup_notice()
            self.selection_notice = ui.InfoBar(self.selection_area, message, kind='warning',
                action='Open API keys' if api else '',
                on_action=lambda: self.on_settings('API Key'), closable=True)
            self.selection_notice.pack(fill='x', before=self.word_host, pady=(ui.get().px(8), 0))

    def lookup_words(self, words, target, source=None, context=None):
        if self.busy:
            return
        if context is not None and context != self.text():
            self.stop_lookup_animation()
            self._lookup_notice('The text changed. Select words again.')
            return
        if not words:
            self.stop_lookup_animation()
            self._lookup_notice('Select a word or enter a phrase first.')
            return
        if not any(item.get('api_key', '').strip() for item in self.config.get_api_keys()):
            self.stop_lookup_animation()
            self._lookup_notice('Add your own API key to look up words.', api=True)
            self.api_button.pack(anchor='w')
            return
        self._clear_lookup_notice()
        self.busy = True
        self.lookup_button.configure(state='disabled')
        if self.word_frame is not None:
            self.word_frame.start_lookup_animation()
        self.status.configure(text='Looking up...')
        self.flow._do_lookup(words, target, source_language=source, context_text=context or self.text(), origin=self)

    def stop_lookup_animation(self):
        self.busy = False
        try:
            self.lookup_button.configure(state='normal')
            if self.word_frame is not None:
                self.word_frame.stop_lookup_animation()
        except tk.TclError:
            pass

    def receive_result(self, result, target, words, source, context):
        self.stop_lookup_animation()
        if not self.frame.winfo_exists() or context != self.text():
            return
        from src.core.result_status import is_error_text
        if is_error_text(result):
            from src.ui import popup_errors
            help_ = popup_errors.explain(result)
            self._lookup_notice(help_.message, api=help_.fix == popup_errors.FIX_SETTINGS)
            self.status.configure(text=result)
            return
        if self.result_view is not None:
            self.result_view.window.destroy()
        self.input_area.pack_forget()
        if self.selection_area is not None:
            self.selection_area.pack_forget()
        self.result_view = DictionaryResultWindow(self.frame, result, target, source_lang=source, words=words,
                              annotate=self.config.get_furigana_enabled(), container=self.frame,
                              on_close=self.show_input,
                              on_open_translate=lambda: self.on_open_translate(dict(original=context, translated='', target_lang=target, source_choice=source or '', dictionary_source_only=True)))
        self.status.configure(text='')

    def escape(self):
        if self.result_view is not None and self.result_view.window.winfo_ismapped():
            self.show_input()
            return True
        return False

    def _destroyed(self, event):
        if event.widget is self.frame:
            self._unsubscribe()
            runner = getattr(self.flow, '_runner', None)
            if runner is not None:
                runner.cancel()
