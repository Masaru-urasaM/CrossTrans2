"""
Tests for src/core/pack_catalog.py (TODO.md U8, step 5): what the Dictionary page shows and does about
language packs. Pure logic over a fake manager - nothing is ever pip-installed.
"""
from types import SimpleNamespace

import pytest

from src.core import furigana as F
from src.core import pack_catalog as pc


def pack(category='Asian', size=100, code='xx'):
    return SimpleNamespace(category=category, size_mb=size, code=code)


PACKS = {
    'Vietnamese': pack('Asian', 120, 'vi'),
    'Japanese': pack('Asian', 250, 'ja'),
    'english': pack('European', 25, 'en'),
    'German': pack('European', 20, 'de'),
    'Danish': pack('Scandinavian', 15, 'da'),
}


class FakeManager:
    """`install` / `uninstall` answer (success, error) from `answers` (default: success)."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []
        self._installed_cache = {'German': True, 'Danish': False}

    def _answer(self, verb, language, progress):
        self.calls.append((verb, language))
        if progress is not None:
            progress(f"{verb} {language}...", 50)
        answer = self.answers.get((verb, language), (True, ''))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def install(self, language, progress=None):
        return self._answer('install', language, progress)

    def uninstall(self, language, progress=None):
        return self._answer('uninstall', language, progress)


@pytest.fixture
def refreshed(monkeypatch):
    calls = []
    monkeypatch.setattr(F, 'refresh_providers', lambda: calls.append(1))
    return calls


class TestReaderNote:
    def test_accurate(self):
        assert 'accurate readings (UniDic' in pc.furigana_reader_note(True, True)

    def test_installed_but_not_loadable(self):
        note = pc.furigana_reader_note(False, True)
        assert 'basic readings' in note and 'could not be loaded' in note

    def test_not_installed_says_how_and_what_it_costs(self):
        note = pc.furigana_reader_note(False, False)
        assert 'Install the Japanese pack' in note and '250 MB' in note and 'no restart' in note


class TestApplyToFurigana:
    def test_the_japanese_pack_installed_or_removed(self, refreshed):
        pc.apply_to_furigana('Japanese', True)
        assert refreshed == [1]

    @pytest.mark.parametrize('language, success', [
        ('Japanese', False), ('Chinese (Simplified)', True), ('Korean', True)])
    def test_anything_else_leaves_furigana_alone(self, refreshed, language, success):
        pc.apply_to_furigana(language, success)
        assert refreshed == []

    def test_a_failed_refresh_never_stops_the_install(self, monkeypatch):
        def broken():
            raise RuntimeError("the tagger could not be built")
        monkeypatch.setattr(F, 'refresh_providers', broken)
        pc.apply_to_furigana('Japanese', True)                  # must not raise


class TestInstallAndRemove:
    def test_a_successful_install(self, refreshed):
        manager = FakeManager()
        outcome = pc.install_pack(manager, 'Japanese')
        assert outcome == pc.PackOutcome('Japanese', True, False, '')
        assert manager.calls == [('install', 'Japanese')]

    def test_the_manager_s_progress_reaches_the_caller(self, refreshed):
        steps = []
        pc.install_pack(FakeManager(), 'German', lambda message, percent: steps.append((message, percent)))
        assert steps == [('install German...', 50)]

    def test_basic_mode_is_an_install_that_worked(self, refreshed):
        outcome = pc.install_pack(FakeManager({('install', 'Thai'): (True, pc.BASIC_MODE)}), 'Thai')
        assert outcome.ok and outcome.basic and outcome.error == ''

    def test_a_failure_carries_its_error_and_is_not_basic(self, refreshed):
        outcome = pc.install_pack(FakeManager({('install', 'Thai'): (False, 'pip failed')}), 'Thai')
        assert outcome == pc.PackOutcome('Thai', False, False, 'pip failed')

    def test_a_failure_without_text_has_an_empty_error(self, refreshed):
        assert pc.install_pack(FakeManager({('install', 'Thai'): (False, None)}), 'Thai').error == ''

    def test_a_removal_is_never_basic(self, refreshed):
        outcome = pc.remove_pack(FakeManager({('uninstall', 'Thai'): (True, pc.BASIC_MODE)}), 'Thai')
        assert outcome.ok and not outcome.basic

    def test_a_removal_calls_uninstall(self, refreshed):
        manager = FakeManager()
        pc.remove_pack(manager, 'German')
        assert manager.calls == [('uninstall', 'German')]

    @pytest.mark.parametrize('verb', ['install', 'uninstall'])
    def test_every_install_and_removal_applies_it_to_furigana(self, refreshed, verb):
        work = pc.install_pack if verb == 'install' else pc.remove_pack
        work(FakeManager(), 'Japanese')
        assert refreshed == [1]

    @pytest.mark.parametrize('verb', ['install', 'uninstall'])
    def test_a_failure_leaves_furigana_alone(self, refreshed, verb):
        work = pc.install_pack if verb == 'install' else pc.remove_pack
        work(FakeManager({(verb, 'Japanese'): (False, 'no')}), 'Japanese')
        assert refreshed == []

    def test_a_success_forgets_what_was_known_about_every_pack(self, refreshed):
        manager = FakeManager()
        pc.install_pack(manager, 'German')
        assert manager._installed_cache == {}

    def test_a_failure_keeps_it(self, refreshed):
        manager = FakeManager({('install', 'German'): (False, 'x')})
        pc.install_pack(manager, 'German')
        assert manager._installed_cache == {'German': True, 'Danish': False}

    def test_a_manager_without_a_cache_is_fine(self, refreshed):
        manager = FakeManager()
        del manager._installed_cache
        assert pc.install_pack(manager, 'German').ok


class TestRunBatch:
    def test_each_pack_is_done_in_order(self, refreshed):
        manager = FakeManager()
        outcomes = pc.run_batch(manager, pc.INSTALL, ['German', 'Danish', 'Japanese'])
        assert manager.calls == [('install', 'German'), ('install', 'Danish'), ('install', 'Japanese')]
        assert [o.language for o in outcomes] == ['German', 'Danish', 'Japanese'] and all(o.ok for o in outcomes)

    def test_remove_uses_uninstall(self, refreshed):
        manager = FakeManager()
        pc.run_batch(manager, pc.REMOVE, ['German'])
        assert manager.calls == [('uninstall', 'German')]

    def test_one_failure_does_not_stop_the_rest(self, refreshed):
        manager = FakeManager({('install', 'Danish'): (False, 'x')})
        outcomes = pc.run_batch(manager, pc.INSTALL, ['German', 'Danish', 'Japanese'])
        assert [o.ok for o in outcomes] == [True, False, True]

    def test_a_pack_that_raises_is_that_pack_s_failure(self, refreshed):
        manager = FakeManager({('install', 'Danish'): RuntimeError('boom')})
        outcomes = pc.run_batch(manager, pc.INSTALL, ['German', 'Danish', 'Japanese'])
        assert [o.ok for o in outcomes] == [True, False, True] and outcomes[1].error == 'boom'

    def test_progress_starts_each_pack_and_carries_the_manager_s_steps(self, refreshed):
        seen = []
        pc.run_batch(FakeManager(), pc.INSTALL, ['German', 'Danish'],
                     progress=lambda *args: seen.append(args))
        assert seen == [(1, 2, 'German', '', 0), (1, 2, 'German', 'install German...', 50),
                        (2, 2, 'Danish', '', 0), (2, 2, 'Danish', 'install Danish...', 50)]

    def test_a_cancel_stops_before_the_next_pack(self, refreshed):
        manager = FakeManager()
        stop = {'now': False}

        def cancelled():
            return stop['now']
        seen = []

        def progress(index, total, language, message, percent):
            seen.append(language)
            if message:
                stop['now'] = True
        outcomes = pc.run_batch(manager, pc.INSTALL, ['German', 'Danish', 'Japanese'], progress, cancelled)
        assert [o.language for o in outcomes] == ['German'] and manager.calls == [('install', 'German')]

    def test_nothing_to_do_is_nothing_done(self):
        assert pc.run_batch(FakeManager(), pc.INSTALL, []) == []

    def test_it_works_without_progress_or_cancel(self, refreshed):
        assert len(pc.run_batch(FakeManager(), pc.INSTALL, ['German'])) == 1


class TestDescribeAndSummary:
    def test_a_pack_s_sentence(self):
        assert pc.describe(PACKS['Vietnamese']) == 'Asian, about 120 MB'

    def test_a_pack_without_a_size_is_just_its_category(self):
        assert pc.describe(pack('Other', 0)) == 'Other'

    def test_the_total_ignores_what_is_not_a_pack(self):
        assert pc.total_size(PACKS, ['German', 'Danish', 'Klingon']) == 35

    def test_none_installed(self):
        assert pc.summary([], PACKS) == 'No language packs installed yet.'

    def test_some_installed(self):
        assert pc.summary(['German', 'Japanese'], PACKS) == '2 installed, about 270 MB.'


class TestSplit:
    def test_installed_and_available_each_a_to_z_without_regard_to_case(self):
        installed, available = pc.split(PACKS, ['Japanese', 'english'])
        assert installed == ['english', 'Japanese']
        assert available == ['Danish', 'German', 'Vietnamese']

    def test_a_query_matches_the_name(self):
        assert pc.split(PACKS, [], 'ger') == ([], ['German'])

    def test_a_query_matches_the_category(self):
        assert pc.split(PACKS, ['Japanese'], 'asia') == (['Japanese'], ['Vietnamese'])

    def test_a_query_matches_the_code_exactly(self):
        assert pc.split(PACKS, [], 'ja') == ([], ['Japanese'])

    def test_only_the_whole_code_matches_not_a_part_of_it(self):
        packs = {'Zulu': pack('Other', 1, 'abc')}
        assert pc.split(packs, [], 'ab') == ([], [])
        assert pc.split(packs, [], 'abc') == ([], ['Zulu'])

    def test_case_and_spaces_do_not_matter(self):
        assert pc.split(PACKS, [], '  GERMAN ') == ([], ['German'])

    def test_a_blank_query_is_every_pack(self):
        installed, available = pc.split(PACKS, ['German'], '   ')
        assert installed == ['German'] and len(available) == 4

    def test_nothing_matching_is_empty(self):
        assert pc.split(PACKS, ['German'], 'zzz') == ([], [])

    def test_a_pack_without_a_code_is_still_found_by_name(self):
        packs = {'X': SimpleNamespace(category='Other', size_mb=1)}
        assert pc.split(packs, [], 'x') == ([], ['X'])

    def test_installed_names_that_are_not_packs_are_ignored(self):
        assert pc.split(PACKS, ['Klingon'])[0] == []


class TestWording:
    def test_progress_for_one(self):
        assert pc.progress_title(pc.INSTALL, 1, 1, 'Japanese') == 'Installing Japanese...'
        assert pc.progress_title(pc.REMOVE, 1, 1, 'Japanese') == 'Removing Japanese...'

    def test_progress_for_a_batch_says_which(self):
        assert pc.progress_title(pc.INSTALL, 2, 5, 'German') == 'Installing German (2 of 5)...'

    def test_install_all_says_the_size(self):
        title, message = pc.confirm_text(pc.INSTALL, ['German', 'Danish'], PACKS)
        assert title == 'Install 2 language packs?' and 'about 35 MB' in message and 'internet' in message

    def test_removing_one(self):
        title, message = pc.confirm_text(pc.REMOVE, ['German'], PACKS)
        assert title == 'Remove the German pack?' and 'install it again' in message
        assert 'Furigana' not in message

    def test_removing_all(self):
        title, message = pc.confirm_text(pc.REMOVE, ['German', 'Danish'], PACKS)
        assert title == 'Remove all 2 language packs?' and 'install them again' in message

    def test_removing_japanese_says_what_happens_to_furigana(self):
        assert 'Furigana goes back to basic readings.' in pc.confirm_text(pc.REMOVE, ['Japanese'], PACKS)[1]
        assert 'Furigana goes back' in pc.confirm_text(pc.REMOVE, ['German', 'Japanese'], PACKS)[1]

    def test_installing_japanese_does_not_mention_removal(self):
        assert 'Furigana' not in pc.confirm_text(pc.INSTALL, ['Japanese'], PACKS)[1]


class TestExplainFailure:
    @pytest.mark.parametrize('error,needle', [
        ('Python not found. Please install Python and add it to PATH.', 'Python was not found'),
        ('PermissionError: [WinError 5] Access is denied', 'would not let CrossTrans'),
        ('The process cannot access the file because it is being used by another process', 'would not let'),
        ('OSError: [Errno 28] No space left on device', 'free disk space'),
        ('ERROR: Could not find a version that satisfies the requirement foo', 'could not download'),
        ('Read timed out.', 'could not download'),
        ('Failed to establish a new connection', 'could not download'),
        ('SSL: CERTIFICATE_VERIFY_FAILED', 'could not download'),
    ])
    def test_the_classes(self, error, needle):
        assert needle in pc.explain_failure(error)

    def test_an_unknown_error_is_its_first_line(self):
        assert pc.explain_failure('\n\nsomething odd happened\nsecond line') == 'something odd happened'

    def test_a_long_first_line_is_cut(self):
        out = pc.explain_failure('x' * 400)
        assert len(out) == 160 and out.endswith('...')

    def test_a_line_at_the_limit_is_whole(self):
        assert pc.explain_failure('y' * 160) == 'y' * 160

    def test_no_error_text_says_to_look_in_the_log(self):
        assert 'log' in pc.explain_failure('') and 'log' in pc.explain_failure(None)

    def test_a_permission_problem_beats_a_network_word(self):
        assert 'would not let CrossTrans' in pc.explain_failure('Permission denied while a connection was open')


class TestResultNotice:
    def ok(self, language, basic=False):
        return pc.PackOutcome(language, True, basic)

    def bad(self, language, error=''):
        return pc.PackOutcome(language, False, False, error)

    def test_one_installed(self):
        assert pc.result_notice(pc.INSTALL, [self.ok('German')]) == (pc.SUCCESS, 'German is installed.')

    def test_one_removed(self):
        assert pc.result_notice(pc.REMOVE, [self.ok('German')]) == (pc.SUCCESS, 'German was removed.')

    def test_one_installed_with_basic_splitting(self):
        kind, text = pc.result_notice(pc.INSTALL, [self.ok('Thai', basic=True)])
        assert kind == pc.INFO and text == 'Thai is installed, with basic word splitting.'

    def test_one_install_that_failed_says_why(self):
        kind, text = pc.result_notice(pc.INSTALL, [self.bad('Thai', 'Read timed out')])
        assert kind == pc.DANGER and text.startswith('Could not install Thai. CrossTrans could not download')

    def test_one_removal_that_failed(self):
        kind, text = pc.result_notice(pc.REMOVE, [self.bad('Thai', 'Access is denied')])
        assert kind == pc.DANGER and text.startswith('Could not remove Thai. Windows would not let')

    def test_a_batch_that_all_worked(self):
        assert pc.result_notice(pc.INSTALL, [self.ok('A'), self.ok('B')]) == (pc.SUCCESS, 'Installed 2 language packs.')
        assert pc.result_notice(pc.REMOVE, [self.ok('A'), self.ok('B')]) == (pc.SUCCESS, 'Removed 2 language packs.')

    def test_a_batch_where_some_failed_names_them(self):
        kind, text = pc.result_notice(pc.INSTALL, [self.ok('A'), self.bad('B'), self.bad('C')])
        assert kind == pc.WARNING and text == 'Installed 1 of 3. Could not install: B, C.'

    def test_a_batch_where_all_failed_explains_the_first(self):
        kind, text = pc.result_notice(pc.INSTALL, [self.bad('A', 'Read timed out'), self.bad('B', 'x')])
        assert kind == pc.DANGER and text.startswith('Could not install any of the 2 language packs. CrossTrans')

    def test_a_batch_that_stopped_early_says_how_far_it_got(self):
        kind, text = pc.result_notice(pc.INSTALL, [self.ok('A')], requested=3)
        assert kind == pc.INFO and text == 'Installed 1 of 3.'

    def test_nothing_done(self):
        assert pc.result_notice(pc.INSTALL, []) == (pc.INFO, 'Nothing was done.')
        assert pc.result_notice(pc.INSTALL, [], requested=2) == (pc.INFO, 'Nothing was done.')
