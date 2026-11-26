"""
Tests for src/core/key_testing.py (TODO.md U8, step 4): which (provider, model) pairs a Test button
tries, the loop that tries them, and the sentences that report it. No network: the manager is a fake.
"""
import pytest

from src.core import key_testing as kt

MODEL_MAP = {
    'Google': ['gemini-a', 'gemini-b'],
    'Groq': ['llama-a', 'llama-b', 'llama-c'],
    'OpenAI': ['gpt-a'],
}
PATTERNS = {'AIza': 'Google', 'gsk_': 'Groq'}
DEFAULTS = {'Mistral': ['mistral-small'], 'Google': ['gemini-default']}


class FakeManager:
    """Answers for the pairs in `works`; everything else raises. Records every call."""

    def __init__(self, works=(), vision=True, error='HTTP 401'):
        self.works, self.vision, self.error = set(works), vision, error
        self.calls = []

    def test_connection(self, model, key, provider):
        self.calls.append(('connect', provider, model, key))
        if (provider, model) not in self.works:
            raise RuntimeError(self.error)
        return True

    def test_vision_connection(self, model, key, provider):
        self.calls.append(('vision', provider, model, key))
        return self.vision


class TestDetectProvider:
    def test_the_prefix_names_the_provider(self):
        assert kt.detect_provider('AIzaSyXYZ', PATTERNS) == 'Google'
        assert kt.detect_provider('gsk_abc', PATTERNS) == 'Groq'

    def test_an_unknown_prefix_names_nobody(self):
        assert kt.detect_provider('sk-123', PATTERNS) == ''

    def test_surrounding_spaces_do_not_matter(self):
        assert kt.detect_provider('  AIza123 \n', PATTERNS) == 'Google'

    def test_nothing_is_nobody(self):
        assert kt.detect_provider('', PATTERNS) == '' and kt.detect_provider(None, PATTERNS) == ''

    def test_the_first_matching_prefix_wins(self):
        assert kt.detect_provider('ab-1', {'ab': 'One', 'ab-': 'Two'}) == 'One'


class TestIsAuto:
    @pytest.mark.parametrize('value', [None, '', '   ', 'Auto', ' Auto '])
    def test_these_mean_auto(self, value):
        assert kt.is_auto(value)

    @pytest.mark.parametrize('value', ['Google', 'gpt-a', 'auto'])
    def test_these_do_not(self, value):
        assert not kt.is_auto(value)


class TestPlan:
    def test_both_auto_with_a_recognised_key_tries_that_providers_models(self):
        assert kt.plan('Auto', 'Auto', 'gsk_x', MODEL_MAP, PATTERNS) == [
            ('Groq', 'llama-a'), ('Groq', 'llama-b'), ('Groq', 'llama-c')]

    def test_both_auto_with_an_unknown_key_tries_everything_in_order(self):
        assert kt.plan('Auto', 'Auto', 'zzz', MODEL_MAP, PATTERNS) == [
            ('Google', 'gemini-a'), ('Google', 'gemini-b'), ('Groq', 'llama-a'), ('Groq', 'llama-b'),
            ('Groq', 'llama-c'), ('OpenAI', 'gpt-a')]

    def test_a_detected_provider_the_map_lacks_falls_back_to_everything(self):
        assert len(kt.plan('Auto', 'Auto', 'AIza', {'Groq': ['x']}, PATTERNS)) == 1
        assert kt.plan('Auto', 'Auto', 'AIza', {'Groq': ['x']}, PATTERNS) == [('Groq', 'x')]

    def test_a_provider_with_model_auto_tries_all_its_models(self):
        assert kt.plan('Groq', 'Auto', 'k', MODEL_MAP, PATTERNS) == [
            ('Groq', 'llama-a'), ('Groq', 'llama-b'), ('Groq', 'llama-c')]

    def test_an_empty_model_is_auto_too(self):
        assert kt.plan('OpenAI', '', 'k', MODEL_MAP, PATTERNS) == [('OpenAI', 'gpt-a')]

    def test_a_provider_the_map_lacks_uses_its_defaults(self):
        assert kt.plan('Mistral', 'Auto', 'k', MODEL_MAP, PATTERNS, DEFAULTS) == [('Mistral', 'mistral-small')]

    def test_the_defaults_come_before_the_rest_of_the_map(self):
        # Decision 102: what a real call with Model = Auto tries first is what Test tries first.
        assert kt.plan('Google', 'Auto', 'k', MODEL_MAP, PATTERNS, DEFAULTS) == [
            ('Google', 'gemini-default'), ('Google', 'gemini-a'), ('Google', 'gemini-b')]

    def test_a_provider_nobody_lists_has_nothing_to_try(self):
        assert kt.plan('Nobody', 'Auto', 'k', MODEL_MAP, PATTERNS, DEFAULTS) == []

    def test_a_model_with_a_recognised_key_goes_to_that_provider_only(self):
        assert kt.plan('Auto', 'my-model', 'AIza1', MODEL_MAP, PATTERNS) == [('Google', 'my-model')]

    def test_a_model_with_an_unknown_key_goes_to_every_provider(self):
        assert kt.plan('Auto', 'my-model', 'zzz', MODEL_MAP, PATTERNS) == [
            ('Google', 'my-model'), ('Groq', 'my-model'), ('OpenAI', 'my-model')]

    def test_both_given_is_exactly_that_pair(self):
        assert kt.plan('Groq', 'custom', 'gsk_1', MODEL_MAP, PATTERNS) == [('Groq', 'custom')]

    def test_the_model_is_trimmed(self):
        assert kt.plan('Groq', '  custom  ', 'k', MODEL_MAP, PATTERNS) == [('Groq', 'custom')]


class TestRun:
    def test_the_first_pair_that_answers_wins(self):
        manager = FakeManager(works={('Groq', 'llama-b')})
        result = kt.run(manager, [('Groq', 'llama-a'), ('Groq', 'llama-b'), ('Groq', 'llama-c')], 'KEY')
        assert result.ok and (result.provider, result.model, result.tried) == ('Groq', 'llama-b', 2)
        assert [c[:3] for c in manager.calls] == [('connect', 'Groq', 'llama-a'), ('connect', 'Groq', 'llama-b'),
                                                  ('vision', 'Groq', 'llama-b')]

    def test_the_key_is_what_is_tested(self):
        manager = FakeManager(works={('Groq', 'a')})
        kt.run(manager, [('Groq', 'a')], 'SECRET')
        assert {c[3] for c in manager.calls} == {'SECRET'}

    def test_vision_is_reported_and_files_always(self):
        result = kt.run(FakeManager(works={('P', 'm')}, vision=True), [('P', 'm')], 'k')
        assert result.vision is True and result.files is True

    def test_a_model_without_vision_still_works(self):
        result = kt.run(FakeManager(works={('P', 'm')}, vision=False), [('P', 'm')], 'k')
        assert result.ok and result.vision is False and result.files is True

    def test_when_nothing_answers_the_last_error_is_kept(self):
        manager = FakeManager(error='HTTP 429')
        result = kt.run(manager, [('P', 'a'), ('P', 'b')], 'k')
        assert not result.ok and result.error == 'HTTP 429' and result.tried == 2
        assert all(call[0] == 'connect' for call in manager.calls)           # vision is never tried

    def test_no_pairs_is_an_outcome_not_an_exception(self):
        result = kt.run(FakeManager(), [], 'k')
        assert not result.ok and 'no model' in result.error.lower() and result.tried == 0

    def test_progress_is_told_before_each_step(self):
        seen = []
        manager = FakeManager(works={('P', 'b')})
        kt.run(manager, [('P', 'a'), ('P', 'b')], 'k', progress=lambda *args: seen.append(args))
        assert seen == [(1, 2, 'connect'), (2, 2, 'connect'), (2, 2, 'vision')]

    def test_cancelling_stops_before_the_next_pair(self):
        manager = FakeManager()
        flags = iter([False, True])
        result = kt.run(manager, [('P', 'a'), ('P', 'b'), ('P', 'c')], 'k', cancelled=lambda: next(flags))
        assert result.cancelled and not result.ok and result.tried == 1
        assert len(manager.calls) == 1

    def test_cancelling_at_once_tries_nothing(self):
        manager = FakeManager(works={('P', 'a')})
        result = kt.run(manager, [('P', 'a')], 'k', cancelled=lambda: True)
        assert result.cancelled and result.tried == 0 and manager.calls == []

    def test_a_working_pair_is_not_undone_by_cancelling_afterwards(self):
        calls = []
        manager = FakeManager(works={('P', 'a')})
        result = kt.run(manager, [('P', 'a')], 'k', cancelled=lambda: calls.append(1) or False)
        assert result.ok and len(calls) == 1


class TestSummary:
    def test_working_with_images_and_files(self):
        text = kt.summary(kt.Outcome(True, 'Groq', 'llama-a', vision=True, files=True, tried=1))
        assert text == 'Works with llama-a. It can read images and files.'

    def test_working_with_files_only(self):
        text = kt.summary(kt.Outcome(True, 'Groq', 'llama-a', vision=False, files=True, tried=1))
        assert text == 'Works with llama-a. It can read files.'

    def test_working_with_nothing_extra(self):
        assert kt.summary(kt.Outcome(True, 'P', 'm')) == 'Works with m.'

    def test_several_models_tried(self):
        assert kt.summary(kt.Outcome(False, error='x', tried=5)) == 'None of the 5 models answered.'

    def test_one_model_tried(self):
        assert kt.summary(kt.Outcome(False, error='x', tried=1)) == 'It did not answer.'

    def test_no_model_tried(self):
        assert kt.summary(kt.Outcome(False, error='x', tried=0)) == 'It did not answer.'

    def test_stopped(self):
        assert kt.summary(kt.Outcome(False, cancelled=True)) == 'Stopped.'


class TestSummaryReasons:
    def test_a_rejected_key_is_named(self):
        text = kt.summary(kt.Outcome(False, error='HTTP 401 Unauthorized', tried=1))
        assert text.startswith('It did not answer. The provider did not accept the key.')

    def test_with_many_models_tried_the_reason_follows_the_headline(self):
        text = kt.summary(kt.Outcome(False, error='403 Forbidden', tried=7))
        assert text.startswith('None of the 7 models answered. The provider did not accept the key.')

    def test_an_unrecognised_error_adds_nothing(self):
        assert kt.summary(kt.Outcome(False, error='boom', tried=1)) == 'It did not answer.'

    def test_a_success_never_carries_a_reason(self):
        assert 'provider' not in kt.summary(kt.Outcome(True, 'P', 'm', error='401', tried=2))


class TestReason:
    @pytest.mark.parametrize('error,needle', [
        ('Error code: 401 - invalid_api_key', 'did not accept the key'),
        ('API key not valid. Please pass a valid API key.', 'did not accept the key'),
        ('403 Forbidden', 'did not accept the key'),
        ('Incorrect API key provided: sk-abc', 'did not accept the key'),
        ('PERMISSION_DENIED', 'did not accept the key'),
        ('Error code: 429 - insufficient_quota', 'out of quota'),
        ('rate limit reached for requests', 'out of quota'),
        ('Too Many Requests', 'out of quota'),
        ('Read timed out. (read timeout=30)', 'too long'),
        ('Max retries exceeded: Connection aborted', 'could not reach'),
        ('getaddrinfo failed', 'could not reach'),
        ('SSL: CERTIFICATE_VERIFY_FAILED', 'could not reach'),
        ('404 model not found', 'does not know this model'),
        ('The model `x` does not exist', 'does not know this model'),
    ])
    def test_the_classes(self, error, needle):
        assert needle in kt.reason(error)

    def test_a_rejected_key_beats_a_missing_model(self):
        assert 'did not accept the key' in kt.reason('401: model not found for this key')

    def test_nothing_recognised_is_empty(self):
        assert kt.reason('boom') == '' and kt.reason('') == '' and kt.reason(None) == ''

    def test_a_missing_model_is_not_blamed_when_many_were_tried(self):
        assert kt.reason('404 model not found', many=True) == ''
        assert 'did not accept the key' in kt.reason('401', many=True)

    def test_the_search_is_case_insensitive(self):
        assert 'out of quota' in kt.reason('QUOTA EXCEEDED')


class TestPersistedStatus:
    def test_a_tested_key_with_images_and_files(self):
        assert kt.persisted_status({'file_capable': True, 'vision_capable': True}, None) == (
            'Tested. It can read images and files.', kt.SUCCESS)

    def test_a_tested_key_with_files_only(self):
        assert kt.persisted_status({'file_capable': True, 'vision_capable': False}, None) == (
            'Tested. It can read files.', kt.SUCCESS)

    def test_a_failed_startup_check_wins(self):
        text, tone = kt.persisted_status({'file_capable': True, 'vision_capable': True}, False)
        assert tone == kt.DANGER and 'did not answer' in text

    def test_a_passed_startup_check_without_a_test(self):
        assert kt.persisted_status({}, True) == ('It answered the last time CrossTrans checked.', kt.SUCCESS)

    def test_a_test_with_both_flags_false_is_a_failed_test(self):
        assert kt.persisted_status({'file_capable': False, 'vision_capable': False}, None) == (
            'The last test did not pass.', kt.DANGER)

    def test_a_passed_check_outweighs_a_stale_failed_test(self):
        assert kt.persisted_status({'file_capable': False}, True)[1] == kt.SUCCESS

    def test_nothing_known_is_quiet(self):
        assert kt.persisted_status({}, None) == ('', kt.MUTED)
        assert kt.persisted_status({'model_name': 'm', 'api_key': 'k'}, None) == ('', kt.MUTED)


class TestProgressText:
    def test_several_pairs_count(self):
        assert kt.progress_text(2, 7, 'connect') == 'Trying 2 of 7...'

    def test_one_pair_just_tests(self):
        assert kt.progress_text(1, 1, 'connect') == 'Testing...'

    def test_the_image_check_is_named(self):
        assert kt.progress_text(3, 7, 'vision') == 'Connected. Checking images...'


class TestMergeRows:
    def test_rows_become_entries_in_order(self):
        assert kt.merge_rows([('Google', 'gemini-a', 'K1'), ('Groq', 'Auto', 'K2')], []) == [
            {'model_name': 'gemini-a', 'api_key': 'K1', 'provider': 'Google'},
            {'model_name': '', 'api_key': 'K2', 'provider': 'Groq'}]

    def test_a_row_with_neither_model_nor_key_is_dropped(self):
        assert kt.merge_rows([('Auto', 'Auto', ''), ('Auto', '', '  ')], []) == []

    def test_a_model_without_a_key_is_kept_and_so_is_a_key_without_a_model(self):
        rows = kt.merge_rows([('Auto', 'm', ''), ('Auto', 'Auto', 'K')], [])
        assert [(r['model_name'], r['api_key']) for r in rows] == [('m', ''), ('', 'K')]

    def test_auto_is_saved_as_an_empty_model_and_names_are_trimmed(self):
        rows = kt.merge_rows([('Groq', ' Auto ', ' K ')], [])
        assert rows == [{'model_name': '', 'api_key': 'K', 'provider': 'Groq'}]

    def test_a_missing_provider_is_auto(self):
        assert kt.merge_rows([('', 'm', 'K')], [])[0]['provider'] == 'Auto'

    def test_capability_flags_are_carried_over_for_the_same_key_and_model(self):
        existing = [{'api_key': 'K', 'model_name': 'm', 'provider': 'Google',
                     'vision_capable': True, 'file_capable': True}]
        row = kt.merge_rows([('Google', 'm', 'K')], existing)[0]
        assert row['vision_capable'] is True and row['file_capable'] is True

    def test_a_false_flag_is_carried_too(self):
        existing = [{'api_key': 'K', 'model_name': 'm', 'provider': 'Google',
                     'vision_capable': False, 'file_capable': False}]
        row = kt.merge_rows([('Google', 'm', 'K')], existing)[0]
        assert row['vision_capable'] is False and row['file_capable'] is False

    def test_flags_are_not_carried_to_another_model_or_key(self):
        existing = [{'api_key': 'K', 'model_name': 'm', 'vision_capable': True, 'file_capable': True}]
        rows = kt.merge_rows([('Google', 'other', 'K'), ('Google', 'm', 'K2')], existing)
        assert all('vision_capable' not in row and 'file_capable' not in row for row in rows)

    def test_only_a_flag_that_was_saved_is_carried(self):
        existing = [{'api_key': 'K', 'model_name': 'm', 'provider': 'Google', 'file_capable': True}]
        row = kt.merge_rows([('Google', 'm', 'K')], existing)[0]
        assert row['file_capable'] is True and 'vision_capable' not in row

    def test_the_existing_list_is_not_changed(self):
        existing = [{'api_key': 'K', 'model_name': 'm', 'vision_capable': True}]
        kt.merge_rows([('Google', 'm', 'K')], existing)
        assert existing == [{'api_key': 'K', 'model_name': 'm', 'vision_capable': True}]

    def test_an_existing_entry_with_missing_fields_does_not_crash(self):
        assert kt.merge_rows([('Google', 'm', 'K')], [{}, {'api_key': None}]) == [
            {'model_name': 'm', 'api_key': 'K', 'provider': 'Google'}]


class TestAnyWorking:
    KEYS = [{'api_key': 'AIza-one', 'model_name': '', 'provider': 'Auto'},
            {'api_key': 'gsk_two', 'model_name': '', 'provider': 'Auto'}]

    def check(self, manager, keys=None, **kw):
        return kt.any_working(manager, keys if keys is not None else self.KEYS, MODEL_MAP, PATTERNS, **kw)

    def test_the_first_working_key_ends_the_search(self):
        manager = FakeManager(works={('Google', 'gemini-a')})
        ok, status = self.check(manager)
        assert ok and status == {'AIza-one': True}
        assert all(call[3] == 'AIza-one' for call in manager.calls)

    def test_a_later_key_can_be_the_one_that_works(self):
        manager = FakeManager(works={('Groq', 'llama-a')})
        ok, status = self.check(manager)
        assert ok and status == {'AIza-one': False, 'gsk_two': True}

    def test_when_none_works_every_key_is_marked(self):
        ok, status = self.check(FakeManager())
        assert not ok and status == {'AIza-one': False, 'gsk_two': False}

    def test_a_key_tries_at_most_per_key_pairs(self):
        manager = FakeManager()
        self.check(manager, keys=[{'api_key': 'gsk_x', 'model_name': '', 'provider': 'Auto'}], per_key=2)
        assert [c[2] for c in manager.calls if c[0] == 'connect'] == ['llama-a', 'llama-b']

    def test_an_entry_without_a_key_is_skipped(self):
        manager = FakeManager()
        ok, status = self.check(manager, keys=[{'api_key': '  ', 'model_name': 'x'}, {'model_name': 'y'}])
        assert not ok and status == {} and manager.calls == []

    def test_no_keys_at_all_is_not_working(self):
        assert self.check(FakeManager(), keys=[]) == (False, {})

    def test_the_stored_provider_and_model_are_used(self):
        manager = FakeManager(works={('OpenAI', 'gpt-a')})
        ok, status = self.check(manager, keys=[{'api_key': 'k', 'model_name': 'gpt-a', 'provider': 'OpenAI'}])
        assert ok and [c[:3] for c in manager.calls][0] == ('connect', 'OpenAI', 'gpt-a')

    def test_a_key_with_surrounding_spaces_is_tested_trimmed(self):
        manager = FakeManager()
        ok, status = self.check(manager, keys=[{'api_key': ' gsk_x ', 'model_name': '', 'provider': 'Auto'}])
        assert 'gsk_x' in status and manager.calls[0][3] == 'gsk_x'

    def test_progress_counts_the_keys_being_tried(self):
        seen = []
        self.check(FakeManager(), progress=lambda done, total: seen.append((done, total)))
        assert seen == [(1, 2), (2, 2)]

    def test_cancelling_stops_the_search(self):
        manager = FakeManager(works={('Google', 'gemini-a')})
        ok, status = self.check(manager, cancelled=lambda: True)
        assert not ok and status == {} and manager.calls == []

    def test_the_trial_check_works_with_a_real_bool_returning_manager(self):
        """The old code unpacked `success, _, _, _ = manager.test_connection(...)`, but it returns True
        or raises - so every key counted as failed and the trial always came on."""
        manager = FakeManager(works={('Google', 'gemini-a')})
        ok, status = self.check(manager)
        assert ok is True
