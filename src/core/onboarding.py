"""Persistent first-run milestones, independent of Tk and translation providers."""

SAMPLE = 'Bonjour tout le monde.'
STEPS = 5


class OnboardingState:
    def __init__(self, config):
        self.config = config

    @property
    def should_show(self):
        return not bool(self.config.get('welcome_seen', False))

    @property
    def resume_step(self):
        value = self.config.get('setup_step', 0)
        return min(STEPS - 1, max(0, value)) if isinstance(value, int) else 0

    def finish(self):
        self.config.set('welcome_seen', True)

    def first_success(self, text, *, failed=False):
        if failed or not text.strip() or self.config.get('first_translation_done', False):
            return False
        self.config.set('first_translation_done', True)
        return True
