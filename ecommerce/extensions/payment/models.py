from __future__ import absolute_import, unicode_literals
from uuid import uuid4
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _
from django_extensions.db.models import TimeStampedModel
from jsonfield import JSONField
from oscar.apps.payment.abstract_models import AbstractSource
from solo.models import SingletonModel

from ecommerce.extensions.payment.constants import CARD_TYPE_CHOICES

def generate_id():
    return uuid4().hex[:28]

class PaymentProcessorResponse(models.Model):
    """ Auditing model used to save all responses received from payment processors. """

    processor_name = models.CharField(max_length=255, verbose_name=_('Payment Processor'))
    transaction_id = models.CharField(max_length=255, verbose_name=_('Transaction ID'), null=True, blank=True)
    basket = models.ForeignKey('basket.Basket', verbose_name=_('Basket'), null=True, blank=True,
                               on_delete=models.SET_NULL)
    response = JSONField()
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        get_latest_by = 'created'
        index_together = ('processor_name', 'transaction_id')
        verbose_name = _('Payment Processor Response')
        verbose_name_plural = _('Payment Processor Responses')


class Source(AbstractSource):
    card_type = models.CharField(max_length=255, choices=CARD_TYPE_CHOICES, null=True, blank=True)


class PaypalWebProfile(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    name = models.CharField(max_length=255, unique=True)


class PaypalProcessorConfiguration(SingletonModel):
    """ This is a configuration model for PayPal Payment Processor"""
    retry_attempts = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_(
            'Number of times to retry failing Paypal client actions (e.g., payment creation, payment execution)'
        )
    )

    class Meta:
        verbose_name = "Paypal Processor Configuration"


@python_2_unicode_compatible
class SDNCheckFailure(TimeStampedModel):
    """ Record of SDN check failure. """
    full_name = models.CharField(max_length=255)
    username = models.CharField(max_length=255)
    city = models.CharField(max_length=32, default='')
    country = models.CharField(max_length=2)
    site = models.ForeignKey('sites.Site', verbose_name=_('Site'), null=True, blank=True, on_delete=models.SET_NULL)
    products = models.ManyToManyField('catalogue.Product', related_name='sdn_failures')
    sdn_check_response = JSONField()

    def __str__(self):
        return 'SDN check failure [{username}]'.format(
            username=self.username
        )

    class Meta:
        verbose_name = 'SDN Check Failure'


class EnterpriseContractMetadata(TimeStampedModel):
    """ Record of contract details for a particular customer transaction """
    PERCENTAGE = 'Percentage'
    FIXED = 'Absolute'
    DISCOUNT_TYPE_CHOICES = [
        (PERCENTAGE, _('Percentage')),
        (FIXED, _('Absolute')),
    ]
    amount_paid = models.DecimalField(null=True, decimal_places=2, max_digits=12)
    discount_value = models.DecimalField(null=True, decimal_places=5, max_digits=15)
    discount_type = models.CharField(max_length=255, choices=DISCOUNT_TYPE_CHOICES, default=PERCENTAGE)

    def clean(self):
        """
        discount_value can hold two types of things conceptually: percentages
        and fixed amounts. We want to add extra validation here on top of the
        normal field validation DecimalField gives us.
        """
        super(EnterpriseContractMetadata, self).clean()

        if self.discount_value is not None:
            if self.discount_type == self.FIXED:
                self._validate_fixed_value()
            else:
                self._validate_percentage_value()

    def _validate_fixed_value(self):
        before_decimal, __, after_decimal = str(self.discount_value).partition('.')

        if len(before_decimal) > 10:
            raise ValidationError(_(
                "More than 10 digits before the decimal "
                "not allowed for fixed value."
            ))

        if len(after_decimal) > 2:
            raise ValidationError(_(
                "More than 2 digits after the decimal "
                "not allowed for fixed value."
            ))

    def _validate_percentage_value(self):

        if Decimal(self.discount_value) > Decimal('100.00000'):
            raise ValidationError(_(
                "Percentage greater than 100 not allowed."
            ))

@python_2_unicode_compatible
class RazorpayTransaction(models.Model):
    date_created = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    email = models.EmailField(null=True, blank=True)
    txnid = models.CharField(
        max_length=32, db_index=True, default=generate_id
    )
    basket_id = models.CharField(
        max_length=12, null=True, blank=True, db_index=True
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True,
                                 blank=True)
    currency = models.CharField(max_length=8, null=True, blank=True)

    # TODO: Make sure razorpay's status strings match these.
    INITIATED, CAPTURED, AUTHORIZED, CAPTURE_FAILED, AUTH_FAILED = (
        "initiated", "captured", "authorized", "capfailed", "authfailed"
    )
    status = models.CharField(max_length=32)

    rz_id = models.CharField(
        max_length=32, null=True, blank=True, db_index=True
    )
    order_id = models.CharField(
        max_length=32, null=True, blank=True, db_index=True
    )
    error_code = models.CharField(max_length=32, null=True, blank=True)
    error_message = models.CharField(max_length=256, null=True, blank=True)

    class Meta:
        ordering = ('-date_created',)
        # app_label = 'rzpay'

    @property
    def is_successful(self):
        return self.status == self.CAPTURED

    @property
    def is_pending(self):
        return self.status == self.AUTHORIZED

    @property
    def is_failed(self):
        # TODO: Probably mark abandoned transactions as failed in batch
        return self.status not in (
            self.CAPTURED, self.AUTHORIZED, self.INITIATED
        )

    def __str__(self):
        return 'razorpay payment: %s' % self.rz_id

# noinspection PyUnresolvedReferences
from oscar.apps.payment.models import *  # noqa isort:skip pylint: disable=ungrouped-imports, wildcard-import,unused-wildcard-import,wrong-import-position,wrong-import-order
