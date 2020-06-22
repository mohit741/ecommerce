from __future__ import absolute_import, unicode_literals
import logging

import razorpay

from django.views.generic import RedirectView, View
from django.conf import settings
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.urls import reverse
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import six
from django.utils.translation import ugettext_lazy as _

from oscar.apps.payment.exceptions import UnableToTakePayment
from oscar.core.loading import get_class, get_model

from ecommerce.extensions.payment.processors import BaseClientSidePaymentProcessor, HandledProcessorResponse
from ecommerce.extensions.payment import facade
from ecommerce.extensions.payment.exceptions import (
    EmptyBasketException, MissingShippingAddressException,
    MissingShippingMethodException, InvalidBasket, RazorpayError)

from ecommerce.extensions.payment.models import RazorpayTransaction as Transaction
from ecommerce.extensions.payment.exceptions import RazorpayError

rz_client = razorpay.Client(
    auth=(settings.RAZORPAY_API_KEY, settings.RAZORPAY_API_SECRET)
)

class RazorPay(BaseClientSidePaymentProcessor):

    NAME = 'razorpay'
    template_name = 'payment/razorpay.html'
    DEFAULT_PROFILE_NAME = 'default'

    def __init__(self, site):
        """
        Constructs a new instance of the RazorPay processor.
        Raises:
            KeyError: If a required setting is not configured for this payment processor
        """
        super(RazorPay, self).__init__(site)

        # Number of times payment execution is retried after failure.
        self.retry_attempts = 2

    def get_transaction_parameters(self, basket, request=None, use_client_side_checkout=True, **kwargs):
        try:
            payment_page_url = str(reverse('razorpay:razorpay-success-response', kwargs={'basket_id': basket.id}))
            logger.info('Payment page url: [%s]', payment_page_url)
            return {'payment_page_url': payment_page_url}
        except Exception as e:
            raise NotImplementedError('The RazorPay payment processor does not support transaction parameters.')

    def handle_processor_response(self, response, basket=None):
        token = response
        logger.info('Response received from handle_processor_response:RazorPay [%s]',str(response))
        order_number = basket.order_number
        currency = basket.currency


        return HandledProcessorResponse(
            transaction_id= None,# transaction_id,
            total= None, # total,
            currency=currency,
            card_number= None, # card_number,
            card_type= None #card_type
        )

    def issue_credit(self, order_number, basket, reference_number, amount, currency):
        rz_id = None
        try:
            txn = Transaction.objects.get(basket_id=basket.id)
            rz_id = txn.rz_id
        except Transaction.DoesNotExist as e:
            logger.warning(
                "Unable to find transaction details for txnid %s: %s",
                txn_id, e)
            raise RazorpayError        
        try:
            refund = rz_client.payment.refund(rz_id, amount)
        except:
            msg = 'An error occurred while attempting to issue a credit (via RazorPay) for order [{}].'.format(
                order_number)
            logger.exception(msg)
            raise GatewayError(msg)

        transaction_id = refund.id

        # NOTE: Refund objects subclass dict so there is no need to do any data transformation
        # before storing the response in the database.
        self.record_processor_response(refund, transaction_id=transaction_id, basket=basket)

        return transaction_id

