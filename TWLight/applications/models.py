from datetime import date
import reversion

from django.core.urlresolvers import reverse_lazy
from django.db import models
from django.utils.translation import ugettext_lazy as _

from TWLight.resources.models import Partner, Stream
from TWLight.users.models import Editor


class Application(models.Model):
    class Meta:
        app_label = 'applications'
        verbose_name = 'application'
        verbose_name_plural = 'applications'
        ordering = ['-date_created', 'editor', 'partner']


    PENDING = 0
    QUESTION = 1
    APPROVED = 2
    NOT_APPROVED = 3

    STATUS_CHOICES = (
        # Translators: This is the status of an application that has not yet been reviewed.
        (PENDING, _('Pending')),
        # Translators: This is the status of an application that reviewers have asked questions about.
        (QUESTION, _('Under discussion')),
        (APPROVED, _('Approved')),
        (NOT_APPROVED, _('Not approved')),
    )

    status = models.IntegerField(choices=STATUS_CHOICES, default=PENDING)
    date_created = models.DateField(auto_now_add=True)

    # Will be set on save() if status changes from PENDING/QUESTION to
    # APPROVED/NOT APPROVED.
    date_closed = models.DateField(blank=True, null=True,
        help_text=_('Do not override this field! Its value is set automatically '
                  'when the application is saved, and overriding it may have '
                  'undesirable results.'))

    # Will be set on save() if status changes from PENDING/QUESTION to
    # APPROVED/NOT APPROVED.
    # In Django 1.8+ we won't need this - we can use F expressions with
    # annotate/aggregate to get all the metrics we want. But that feature isn't
    # added until 1.8, and the view code we need to write without it is pretty
    # messy (and requires us to do lots of stuff in Python rather than in the
    # database). So we'll precompute here.
    days_open = models.IntegerField(blank=True, null=True,
        help_text=_('Do not override this field! Its value is set automatically '
                  'when the application is saved, and overriding it may have '
                  'undesirable results.'))

    # Will be set on save() based on date_closed and partner access grant
    # lengths. In practice, because access grants are triggered manually
    # after review on TWLight, the real expiry date is likely to be later.
    earliest_expiry_date = models.DateField(blank=True, null=True,
        help_text=_('Do not override this field! Its value is set automatically '
                  'when the application is saved, and overriding it may have '
                  'undesirable results.'))

    editor = models.ForeignKey(Editor, related_name='applications')
    partner = models.ForeignKey(Partner, related_name='applications')

    rationale = models.TextField(blank=True)
    specific_title = models.CharField(max_length=128, blank=True)
    specific_stream = models.ForeignKey(Stream,
                            related_name='applications',
                            blank=True, null=True)
    comments = models.TextField(blank=True)
    agreement_with_terms_of_use = models.BooleanField(default=False)


    def __str__(self):
        return '{self.editor} - {self.partner}'.format(self=self)


    def get_absolute_url(self):
        return reverse_lazy('applications:evaluate', kwargs={'pk': self.pk})

    # Every single save to this model should create a revision.
    # You can access two models this way: REVISIONS and VERSIONS.
    # Versions contain the model data at the time, accessible via
    # version.field_dict['field_name']. Revisions contain metadata about the
    # version (like when it was saved).
    # See http://django-reversion.readthedocs.io/en/release-1.8/.
    # See TWLight/applications/templatetags/version_tags for how to display
    # version-related information in templates; the API is not always
    # straightforward so we wrap it there.
    @reversion.create_revision()
    def save(self, *args, **kwargs):
        version = self.get_latest_version()
        count = self.get_version_count()
        if count >= 2:
            orig_status = version.field_dict['status']
            if (orig_status in [self.PENDING, self.QUESTION]
                and self.status in [self.APPROVED, self.NOT_APPROVED]
                and not self.date_closed):

                self.date_closed = date.today()
                self.days_open = (date.today() - self.date_created).days
        else:
            # If somehow we've created an Application whose status is final
            # at the moment of creation, set its date-closed-type parameters
            # too.
            # Note that this block executes if count == 1 and also if
            # count == None, and the *latter* is what we expect on first save;
            # get_version_count will return None for reasons it documents.
            if (self.status in [self.APPROVED, self.NOT_APPROVED]
                and not self.date_closed):

                self.date_closed = date.today()
                self.days_open = 0

        if self.date_closed and not self.earliest_expiry_date:
            self.earliest_expiry_date = self.date_closed + self.partner.access_grant_term

        super(Application, self).save(*args, **kwargs)


    LABELMAKER = {
        PENDING: '-primary',
        QUESTION: '-warning',
        APPROVED: '-success',
        NOT_APPROVED: '-danger',
    }

    def get_bootstrap_class(self):
        """
        What class should be applied to Bootstrap labels, buttons, alerts, etc.
        for this application?

        Returns a string like '-default'; the template is responsible for
        prepending 'label' or 'button', etc., as appropriate to the HTML object.
        """
        try:
            return self.LABELMAKER[self.status]
        except KeyError:
            return None


    def get_version_count(self):
        try:
            return len(reversion.get_for_object(self))
        except TypeError:
            # When we call this the *first* time we save an object, it will fail
            # as the object properties that reversion is looking for are not
            # yet set.
            return None


    def get_latest_version(self):
        try:
            return reversion.get_for_object(self)[0]
        except (TypeError, IndexError):
            # If no versions yet...
            return None


    def get_latest_revision(self):
        version = self.get_latest_version()

        if version:
            return version.revision
        else:
            return None


    def get_latest_reviewer(self):
        revision = self.get_latest_revision()

        if revision:
            return revision.user
        else:
            return None


    def get_latest_review_date(self):
        revision = self.get_latest_revision()

        if revision:
            return revision.date_created
        else:
            return None


    def get_num_days_open(self):
        """
        If the application has status PENDING or QUESTION, return the # of days
        since the application was initiated. Otherwise, get the # of days
        elapsed from application initiation to final status determination.
        """
        if self.status in [self.PENDING, self.QUESTION]:
            return (date.today() - self.date_created).days
        else:
            assert self.status in [self.APPROVED, self.NOT_APPROVED]
            return (self.date_closed - self.date_created).days


    def is_probably_expired(self):
        if self.earliest_expiry_date:
            if self.earliest_expiry_date <= date.today():
                return True

        return False


    def get_num_days_since_expiration(self):
        if self.earliest_expiry_date:
            if self.earliest_expiry_date <= date.today():
                return (date.today() - self.earliest_expiry_date).days

        return None


    def get_num_days_until_expiration(self):
        if self.earliest_expiry_date:
            if self.earliest_expiry_date > date.today():
                return (self.earliest_expiry_date - date.today()).days

        return None